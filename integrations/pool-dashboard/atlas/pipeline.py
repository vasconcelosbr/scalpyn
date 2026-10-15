"""Durable daily state machine. A claimed stage is never automatically invoked again."""
from datetime import datetime,timezone,timedelta
import json
import os
import sqlite3
import threading
from zoneinfo import ZoneInfo
from . import store,sources,specialized
from .provider import BoundedMessages,ProviderError,budget_cents
from .schema import AGENTS,Opinion,Report,prompt

ZONE=ZoneInfo('America/Sao_Paulo')

def parse_time(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None: raise ValueError('TIMEZONE_REQUIRED')
    return d

def validate_opinion(code,raw,inputs):
    opinion=Opinion.model_validate(raw).model_dump(mode='json')
    assets={a['asset_id']:a for a in inputs['quant']['assets']}
    evidence={e['evidence_id']:e for e in inputs['evidence']}
    if inputs['snapshot']:
        evidence['snapshot_'+inputs['snapshot']['sha256']]={'source_tier':'PERSISTED_SNAPSHOT'}
    cutoff=parse_time(inputs['cutoff'])
    v2=bool(inputs.get('risk_policy'))
    if v2 and code=='MAPPER' and set(opinion['evidence_dispositions'])!=set(evidence)-({'snapshot_'+inputs['snapshot']['sha256']} if inputs['snapshot'] else set()):
        raise ValueError('EVIDENCE_COVERAGE_MISMATCH')
    for finding in opinion['findings']:
        if not set(finding['asset_ids'])<=assets.keys() or not set(finding['evidence_ids'])<=evidence.keys(): raise ValueError('INVALID_REFERENCE')
        if finding['risk_level'] in ('HIGH','CRITICAL') and not finding['evidence_ids']: raise ValueError('EVIDENCE_REQUIRED')
        if finding['association']=='DIRECT' and finding['asset_ids']:
            verified=set().union(*(set(evidence[e].get('verified_asset_ids',[])) for e in finding['evidence_ids']))
            if not set(finding['asset_ids'])<=verified: raise ValueError('DIRECT_IDENTITY_UNPROVEN')
        if v2:
            if finding['risk_level']!='UNKNOWN': raise ValueError('SEVERITY_POLICY_UNCONFIGURED')
            if not finding['evidence_quotes']: raise ValueError('LITERAL_SUPPORT_REQUIRED')
            for eid,quote in finding['evidence_quotes'].items():
                if eid not in finding['evidence_ids'] or not quote.strip() or not any(quote in evidence.get(eid,{}).get(k,'') for k in ('title','excerpt')):
                    raise ValueError('LITERAL_SUPPORT_MISMATCH')
        if finding['scheduled_at']: parse_time(finding['scheduled_at'])
        if finding['occurrence']=='CONFIRMED' and not any(evidence[e]['source_tier']=='VERIFIED_PRIMARY' for e in finding['evidence_ids']):
            raise ValueError('PRIMARY_SOURCE_REQUIRED')
    if code!='ATLAS' and (opinion['asset_risks'] or opinion['directives']): raise ValueError('ROLE_VIOLATION')
    if code=='ATLAS':
        ids=[a['asset_id'] for a in opinion['asset_risks']]
        if len(ids)!=len(set(ids)) or set(ids)!=assets.keys(): raise ValueError('ASSET_COVERAGE_MISMATCH')
        for a in opinion['asset_risks']:
            if v2 and a['risk_level']!='UNKNOWN': raise ValueError('SEVERITY_POLICY_UNCONFIGURED')
            if not set(a['evidence_ids'])<=evidence.keys(): raise ValueError('INVALID_REFERENCE')
            if assets[a['asset_id']]['coverage_status']!='COMPLETE':
                a['coverage_status']=assets[a['asset_id']]['coverage_status']
                if a['alert_status']=='NO_ALERT': raise ValueError('NO_ALERT_WITH_INCOMPLETE_DATA')
            if a['risk_level']!='UNKNOWN' and not a['evidence_ids']: raise ValueError('EVIDENCE_REQUIRED')
        for d in opinion['directives']:
            if v2 and d['risk_level']!='UNKNOWN': raise ValueError('SEVERITY_POLICY_UNCONFIGURED')
            if not set(d['asset_ids'])<=assets.keys() or not set(d['evidence_ids'])<=evidence.keys(): raise ValueError('INVALID_REFERENCE')
            if d['scope']=='ASSET' and not d['asset_ids']: raise ValueError('EMPTY_ASSET_SCOPE')
            if d['scope']=='GLOBAL' and d['asset_ids']: raise ValueError('INVALID_GLOBAL_SCOPE')
            if d['action']=='PAUSE_NEW_BUYS_GLOBAL' and d['scope']!='GLOBAL': raise ValueError('INVALID_ACTION_SCOPE')
            if d['action'] in ('PAUSE_NEW_BUYS_ASSET','APPLY_SCORE_PENALTY','PRIORITIZE_ASSET') and d['scope']!='ASSET': raise ValueError('INVALID_ACTION_SCOPE')
            if d['action']!='MONITOR_ONLY' and not d['evidence_ids']: raise ValueError('EVIDENCE_REQUIRED')
            expiry=parse_time(d['expires_at'])
            if not cutoff<expiry<=cutoff+timedelta(days=1): raise ValueError('INVALID_EXPIRY')
            if d['action']=='APPLY_SCORE_PENALTY':
                maximum=os.environ.get('ATLAS_MAX_PROPOSED_PENALTY')
                if not maximum or d['score_delta'] is None or abs(d['score_delta'])>float(maximum): raise ValueError('PENALTY_POLICY_UNCONFIGURED')
            elif d['score_delta'] is not None: raise ValueError('UNEXPECTED_SCORE_DELTA')
            if d['action']=='PRIORITIZE_ASSET':
                maximum=os.environ.get('ATLAS_MAX_PROPOSED_PRIORITY')
                if not maximum or d['priority_delta'] is None or d['priority_delta']>float(maximum): raise ValueError('PRIORITY_POLICY_UNCONFIGURED')
            elif d['priority_delta'] is not None: raise ValueError('UNEXPECTED_PRIORITY_DELTA')
            if d['action']=='SET_RISK_MODE' and d['risk_mode'] is None: raise ValueError('RISK_MODE_REQUIRED')
    # Membership completeness is separate from coverage of all risk domains.
    opinion['coverage_status']='PARTIAL'
    return opinion

def finish_agent(run_id,code,status,output=None,usage=None,error=None):
    with store.db(True) as c:
        c.execute('UPDATE agents SET status=?,finished_at=?,output=?,usage=?,error_code=? WHERE run_id=? AND code=?',
            (status,store.now(),store.encode(output) if output is not None else None,
             store.encode(usage) if usage is not None else None,error,run_id,code))
        store.audit(c,'agent_finished',run_id+':'+code,{'status':status,'output_hash':store.digest(output),'error':error})

def agent_rows(run_id):
    with store.db() as c: rows=c.execute('SELECT * FROM agents WHERE run_id=? ORDER BY rowid',(run_id,)).fetchall()
    result=[]
    for row in rows:
        r=dict(row)
        for key in ('output','usage'): r[key]=json.loads(r[key]) if r[key] else None
        result.append(r)
    return result

def cycle(cutoff=None,provider=None,feed_loader=None,snapshot_loader=None,resume_blocked=False,revision=0):
    store.init()
    cutoff=cutoff or datetime.now(timezone.utc)
    day=cutoff.astimezone(ZONE).date().isoformat()
    if revision not in (0,1,2): raise ValueError('INVALID_REVISION')
    run_id=day+':America_Sao_Paulo:ATLAS_V1'+({0:'',1:':REVALIDATION_V2',2:':RECOVERY_V3'}[revision])
    frozen=None
    with store.db(True) as c:
        if revision==2:
            parent=c.execute('SELECT id,status,inputs,input_hash FROM cycles WHERE day=? AND revision=1',(day,)).fetchone()
            if not parent or parent['status']!='INTERRUPTED': raise ValueError('RECOVERY_REQUIRES_INTERRUPTED_REVISION')
            prior_calls=c.execute("SELECT code,session_id FROM agents WHERE run_id=? AND code!='QUANT' AND started_at IS NOT NULL",(parent['id'],)).fetchall()
            if len(prior_calls)!=1 or prior_calls[0]['code']!='MACRO' or not (prior_calls[0]['session_id'] or '').startswith('messages:'):
                raise ValueError('RECOVERY_AUTHORIZATION_SCOPE')
            frozen=json.loads(parent['inputs'])
            if store.digest(frozen)!=parent['input_hash']: raise ValueError('INPUT_INTEGRITY')
        try:
            if c.execute("SELECT 1 FROM cycles WHERE status IN ('COLLECTING','RUNNING') AND id!=?",(run_id,)).fetchone(): raise ValueError('ANOTHER_CYCLE_RUNNING')
            c.execute('INSERT INTO cycles(id,day,started_at,finished_at,status,inputs,input_hash,error_code,revision) VALUES (?,?,?,?,?,?,?,?,?)',(run_id,day,store.now(),None,'COLLECTING','{}',store.digest({}),None,revision))
        except sqlite3.IntegrityError:
            row=c.execute('SELECT status,inputs FROM cycles WHERE id=?',(run_id,)).fetchone()
            called=c.execute('SELECT count(*) FROM agents WHERE run_id=? AND code!=? AND (started_at IS NOT NULL OR session_id IS NOT NULL)',(run_id,'QUANT')).fetchone()[0]
            if not resume_blocked or row['status']!='BLOCKED' or called: return run_id
            frozen=json.loads(row['inputs'])
            c.execute("UPDATE cycles SET status='COLLECTING',finished_at=NULL,error_code=NULL WHERE id=?",(run_id,))
            c.execute("UPDATE agents SET status='PENDING',error_code=NULL,finished_at=NULL WHERE run_id=? AND code!='QUANT'",(run_id,))
            store.audit(c,'configuration_unblocked_no_prior_inference',run_id,{'inputs_unchanged':True})
        else:
            for code in AGENTS: c.execute('INSERT INTO agents(run_id,code,status) VALUES (?,?,?)',(run_id,code,'PENDING'))
            store.audit(c,'cycle_claimed',run_id,{'day':day,'revision':revision,'authorization':'operator_explicit_revalidation' if revision else 'daily'})
    try:
        if frozen:
            inputs=frozen;cutoff=parse_time(inputs['cutoff'])
            snap,evidence,health,quant=(inputs[k] for k in ('snapshot','evidence','source_health','quant'))
        else:
            snap=(snapshot_loader or sources.snapshot)(cutoff)
            if snap and snap.get('pool_context') and not snapshot_loader:
                cutoff=datetime.now(timezone.utc)
            evidence,health=(feed_loader or sources.collect_feeds)(cutoff)
            quant=sources.quant(snap,cutoff)
            if not feed_loader:
                extra,extra_health,identities=specialized.collect(quant['assets'],cutoff)
                evidence.extend(extra);health.extend(extra_health)
                cutoff=datetime.now(timezone.utc)
                quant=sources.quant(snap,cutoff)
                for asset in quant['assets']:
                    identity=identities.get(asset['asset_id'])
                    if identity:
                        asset.update(identity=identity,identity_status=identity['status'])
                        asset['limitations']=['Identidade verificada apenas na exchange; vesting e projeto não verificados independentemente.','Snapshot de decisão separado dos indicadores recentes.']
                with store.db(True) as c:
                    for e in extra: c.execute('INSERT OR IGNORE INTO evidence VALUES (?,?,?,?,?)',(e['evidence_id'],e['url'],e['hash'],store.encode(e),e['retrieved_at']))
            inputs={'cutoff':cutoff.isoformat(),'snapshot':snap,'evidence':evidence,'source_health':health,'quant':quant,
               'limitations':quant['limitations']+['Cobertura de unlocks/vesting e incidentes por projeto não é universal. Ausência de evento não prova ausência de risco.'],
               'facts':deterministic_facts(quant,evidence,cutoff),
               'risk_policy':'UNKNOWN; no calibrated severity policy is configured.'}
        with store.db(True) as c:
            c.execute('UPDATE cycles SET inputs=?,input_hash=?,status=? WHERE id=?',(store.encode(inputs),store.digest(inputs),'RUNNING',run_id))
        if not frozen or revision==2: finish_agent(run_id,'QUANT','SUCCEEDED',quant,{'input_tokens':0,'output_tokens':0,'list_cost':{'amount':'0','currency':'USD'}})
        p=provider or BoundedMessages()
        try:
            budget=budget_cents(); p.preflight()
        except ProviderError as exc:
            for code in AGENTS:
                if code!='QUANT': finish_agent(run_id,code,'BLOCKED',error=str(exc))
            with store.db(True) as c: c.execute('UPDATE cycles SET status=?,error_code=?,finished_at=? WHERE id=?',('BLOCKED',str(exc),store.now(),run_id))
            store.state('preflight',{'status':str(exc),'checked_at':store.now()})
            return run_id
        # Fixed allocation is reserved before calls. Unknown cost never frees a reservation.
        with store.db() as c:
            prior=[dict(r) for r in c.execute('SELECT a.usage,a.status,a.session_id,a.reserved_cents,a.started_at FROM agents a JOIN cycles c ON c.id=a.run_id WHERE c.day=? AND c.id!=?',(day,run_id))]
        used=0
        for previous in prior:
            cost=(json.loads(previous['usage']) or {}).get('list_cost') if previous['usage'] else None
            if cost and cost.get('currency')=='USD': used+=int(cost['amount'])
            elif previous['started_at']:
                if (previous['session_id'] or '').startswith('messages:') and previous['reserved_cents']>0:
                    used+=previous['reserved_cents']  # Retain the full hard request ceiling; never release unknown cost.
                else:
                    used=budget;break
        allocation=max(0,(budget-used)//7)
        if allocation<1: used=budget
        compact={'cutoff':inputs['cutoff'],'source_health':health,'evidence':[{k:v for k,v in e.items() if k not in ('source_record','structured')} for e in evidence],
          'snapshot_sha256':snap['sha256'] if snap else None,'limitations':inputs['limitations'],
          'facts':inputs.get('facts',{}),'risk_policy':inputs.get('risk_policy'),
          'quant':dict(quant,assets=[compact_asset(a) for a in quant['assets']],pool_inactive=None)}
        for code in AGENTS:
            if code=='QUANT': continue
            if isinstance(p,BoundedMessages):
                weights={'MACRO':1,'GEO':1,'SECURITY':1,'TOKEN':1,'MAPPER':1,'AUDITOR':2,'ATLAS':4}
                remaining=list(AGENTS)[list(AGENTS).index(code):]
                allocation=max(0,(budget-used)*weights[code]//sum(weights.get(c,0) for c in remaining))
            if allocation<1 or used+allocation>budget:
                finish_agent(run_id,code,'BLOCKED',error='DAILY_BUDGET_EXHAUSTED'); continue
            predecessors=agent_rows(run_id)
            context=dict(compact,previous_opinions=[{'code':a['code'],'status':a['status'],'output':a['output'],'error_code':a['error_code']} for a in predecessors if a['status']!='PENDING' and a['code']!='QUANT'])
            if len(store.encode(context).encode())>int(os.environ.get('ATLAS_MAX_CONTEXT_BYTES','750000')):
                finish_agent(run_id,code,'BLOCKED',error='CONTEXT_LIMIT_NO_SILENT_TRUNCATION'); continue
            with store.db(True) as c:
                changed=c.execute("UPDATE agents SET status='RUNNING',started_at=?,model=?,prompt_hash=?,reserved_cents=? WHERE run_id=? AND code=? AND status='PENDING'",
                  (store.now(),p.model(code),store.digest(prompt(code)),allocation,run_id,code)).rowcount
            if not changed: continue
            used+=allocation
            def save_session(sid):
                with store.db(True) as c: c.execute('UPDATE agents SET session_id=? WHERE run_id=? AND code=?',(sid,run_id,code))
            try:
                result=p.run(code,context,allocation,save_session)
                cost=(result.get('usage') or {}).get('list_cost')
                if not cost or cost.get('currency')!='USD':
                    used=budget  # Unknown metering prevents starting another billable session.
                else:
                    used+=int(cost['amount'])-allocation if isinstance(p,BoundedMessages) else max(0,int(cost['amount'])-allocation)
                # Persist rejected output too: the portal labels it INVALID, never a validated decision.
                try:
                    raw=json.loads(result['text']); opinion=validate_opinion(code,raw,inputs)
                except (ValueError,TypeError):
                    finish_agent(run_id,code,'INVALID',{'raw_text':result['text']},result['usage'],'SCHEMA_OR_POLICY_REJECTED'); continue
                finish_agent(run_id,code,'SUCCEEDED',opinion,result['usage'])
            except Exception as exc:
                error=str(exc) if isinstance(exc,ProviderError) else 'PROVIDER_FAILURE_UNCERTAIN'
                finish_agent(run_id,code,'FAILED',error=error)
                # Uncertain live session may still be spending. Do not create another session.
                for remaining in AGENTS:
                    with store.db() as c: pending=c.execute('SELECT status FROM agents WHERE run_id=? AND code=?',(run_id,remaining)).fetchone()[0]
                    if pending=='PENDING': finish_agent(run_id,remaining,'BLOCKED',error='PREVIOUS_SESSION_UNCERTAIN')
                break
        rows=agent_rows(run_id); by={a['code']:a for a in rows}
        auditor=by['AUDITOR']; atlas=by['ATLAS']
        publish=all(a['status']=='SUCCEEDED' for a in rows) and auditor['output']['publication_allowed'] and atlas['output']['publication_allowed']
        with store.db(True) as c:
            if publish:
                report_id='atlas_'+day+('_r'+str(revision) if revision else '')
                report={'contract_version':'atlas_v1','report_id':report_id,'run_id':run_id,'authorization_mode':'SHADOW_ONLY',
                  'generated_at':store.now(),'data_cutoff_at':cutoff.isoformat(),'expires_at':(cutoff+timedelta(days=1)).isoformat(),
                  'report_status':'PARTIAL','coverage':{'status':'PARTIAL','membership_status':quant['pool_membership_status'],'pool_total':quant['pool_total'],'evaluated':len(quant['assets'])},
                  'editorial':atlas['output'],'agents':rows,'quant':quant,'source_health':health,'evidence':evidence,
                  'snapshot':{k:v for k,v in (snap or {}).items() if k not in ('data','pool_context')},'limitations':inputs['limitations']}
                for i,d in enumerate(report['editorial']['directives']):
                    d.update(directive_id=report_id+':'+str(i),status='PROPOSED',authorization_mode='SHADOW_ONLY',report_id=report_id,
                      policy_eligibility='NOT_EVALUATED',execution_enabled=False)
                report=Report.model_validate(report).model_dump(mode='json')
                c.execute('INSERT INTO reports VALUES (?,?,?,?,?,?)',(report_id,run_id,report['generated_at'],report['expires_at'],store.encode(report),store.digest(report)))
                store.audit(c,'report_published',report_id,{'sha256':store.digest(report)})
            c.execute('UPDATE cycles SET status=?,finished_at=? WHERE id=?',('PUBLISHED_PARTIAL' if publish else 'REVIEW_REQUIRED',store.now(),run_id))
        store.state('preflight',{'status':'OK','checked_at':store.now()})
        return run_id
    except Exception:
        with store.db(True) as c:
            c.execute("UPDATE cycles SET status='FAILED',finished_at=?,error_code='CYCLE_FAILED' WHERE id=?",(store.now(),run_id))
            c.execute("UPDATE agents SET status='BLOCKED',error_code='CYCLE_FAILED' WHERE run_id=? AND status='PENDING'",(run_id,))
        return run_id

def scheduler(stop):
    # Single service/volume. SQLite unique day also protects multiple processes/concurrent callers.
    while not stop.is_set():
        try:
            if os.environ.get('ATLAS_ENABLED')=='true':
                local=datetime.now(ZONE)
                if local.hour>=int(os.environ.get('ATLAS_HOUR_LOCAL','19')):
                    cycle()
        except Exception:
            store.state('scheduler',{'status':'ERROR','checked_at':store.now()})
        stop.wait(30)


def deterministic_facts(quant,evidence,cutoff):
    changes=[a['price_change_24h'] for a in quant['assets'] if a['price_change_24h'] is not None]
    return {'cutoff':cutoff.isoformat(),'observed_asset_count':len(quant['assets']),
      'pool_total':quant['pool_total'],'watchlist_total':quant['observed_watchlist_total'],
      'evidence_count':len(evidence),'evidence_ids':[e['evidence_id'] for e in evidence],
      'change_24h':{'positive':sum(x>0 for x in changes),'negative':sum(x<0 for x in changes),
                   'zero':sum(x==0 for x in changes),'missing':len(quant['assets'])-len(changes),
                   'method':'Count persisted price_change_24h; not an intraday return or causal test.'},
      'freshness_method':'Per-reading statuses are server-calculated. Do not infer freshness from timeframe names.'}


def compact_asset(asset):
    """Explicit editorial projection; full persisted readings remain in QUANT/portal."""
    fields=('asset_id','symbol','market_type','watchlist_asset_id','identity_status','coverage_status',
        'risk_level','current_price','price_change_24h','volume_24h','market_cap','alpha_score','refreshed_at')
    result={k:asset.get(k) for k in fields}
    identity=asset.get('identity')
    if identity: result['identity']={k:identity.get(k) for k in ('status','project_name','base','quote','evidence_id','scope')}
    result['readings']=[{'group':r.get('scheduler_group'),'timeframe':r.get('timeframe'),
        'timestamp':r.get('timestamp'),'age_seconds':r.get('age_seconds'),'freshness':r.get('status')}
        for r in asset.get('freshness_by_reading',[])]
    result['projection']='Persisted scalar metrics and all reading timestamps; full indicator values and frozen decision remain in QUANT, not inferred here.'
    return result

"""Durable daily state machine. A claimed stage is never automatically invoked again."""
from datetime import datetime,timezone,timedelta
import json
import os
import sqlite3
import threading
from zoneinfo import ZoneInfo
from . import store,sources
from .provider import Managed,ProviderError,budget_cents
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
    for finding in opinion['findings']:
        if not set(finding['asset_ids'])<=assets.keys() or not set(finding['evidence_ids'])<=evidence.keys(): raise ValueError('INVALID_REFERENCE')
        if finding['risk_level'] in ('HIGH','CRITICAL') and not finding['evidence_ids']: raise ValueError('EVIDENCE_REQUIRED')
        if finding['association']=='DIRECT' and finding['asset_ids']:
            # Source contract has no immutable token/project identity. Do not accept ticker-only proof.
            raise ValueError('DIRECT_IDENTITY_UNPROVEN')
        if finding['scheduled_at']: parse_time(finding['scheduled_at'])
        if finding['occurrence']=='CONFIRMED' and not any(evidence[e]['source_tier']=='VERIFIED_PRIMARY' for e in finding['evidence_ids']):
            raise ValueError('PRIMARY_SOURCE_REQUIRED')
    if code!='ATLAS' and (opinion['asset_risks'] or opinion['directives']): raise ValueError('ROLE_VIOLATION')
    if code=='ATLAS':
        ids=[a['asset_id'] for a in opinion['asset_risks']]
        if len(ids)!=len(set(ids)) or set(ids)!=assets.keys(): raise ValueError('ASSET_COVERAGE_MISMATCH')
        for a in opinion['asset_risks']:
            if not set(a['evidence_ids'])<=evidence.keys(): raise ValueError('INVALID_REFERENCE')
            if assets[a['asset_id']]['coverage_status']!='COMPLETE':
                a['coverage_status']=assets[a['asset_id']]['coverage_status']
                if a['alert_status']=='NO_ALERT': raise ValueError('NO_ALERT_WITH_INCOMPLETE_DATA')
            if a['risk_level']!='UNKNOWN' and not a['evidence_ids']: raise ValueError('EVIDENCE_REQUIRED')
        for d in opinion['directives']:
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
    # This read-only integration does not prove full Pool membership.
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

def cycle(cutoff=None,provider=None,feed_loader=None,snapshot_loader=None,resume_blocked=False):
    store.init()
    cutoff=cutoff or datetime.now(timezone.utc)
    day=cutoff.astimezone(ZONE).date().isoformat()
    run_id=day+':America_Sao_Paulo:ATLAS_V1'
    frozen=None
    with store.db(True) as c:
        try: c.execute('INSERT INTO cycles VALUES (?,?,?,?,?,?,?,?)',(run_id,day,store.now(),None,'COLLECTING','{}',store.digest({}),None))
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
            store.audit(c,'cycle_claimed',run_id,{'day':day})
    try:
        if frozen:
            inputs=frozen;cutoff=parse_time(inputs['cutoff'])
            snap,evidence,health,quant=(inputs[k] for k in ('snapshot','evidence','source_health','quant'))
        else:
            snap=(snapshot_loader or sources.snapshot)(cutoff)
            evidence,health=(feed_loader or sources.collect_feeds)(cutoff)
            quant=sources.quant(snap,cutoff)
            inputs={'cutoff':cutoff.isoformat(),'snapshot':snap,'evidence':evidence,'source_health':health,'quant':quant,
               'limitations':['Universo completo do Pool indisponível; membros observados são da watchlist.',
               'Fontes especializadas SECURITY/TOKEN e calendários completos ainda sem contratos validados.']}
        with store.db(True) as c:
            c.execute('UPDATE cycles SET inputs=?,input_hash=?,status=? WHERE id=?',(store.encode(inputs),store.digest(inputs),'RUNNING',run_id))
        if not frozen: finish_agent(run_id,'QUANT','SUCCEEDED',quant,{'input_tokens':0,'output_tokens':0,'list_cost':{'amount':'0','currency':'USD'}})
        p=provider or Managed()
        try:
            budget=budget_cents(); p.preflight()
        except ProviderError as exc:
            for code in AGENTS:
                if code!='QUANT': finish_agent(run_id,code,'BLOCKED',error=str(exc))
            with store.db(True) as c: c.execute('UPDATE cycles SET status=?,error_code=?,finished_at=? WHERE id=?',('BLOCKED',str(exc),store.now(),run_id))
            store.state('preflight',{'status':str(exc),'checked_at':store.now()})
            return run_id
        # Fixed allocation is reserved before calls. Unknown cost never frees a reservation.
        allocation=budget//7
        used=0
        compact={'cutoff':inputs['cutoff'],'source_health':health,'evidence':evidence,
          'snapshot_sha256':snap['sha256'] if snap else None,'limitations':inputs['limitations'],
          'quant':dict(quant,assets=[{k:v for k,v in a.items() if k not in ('decision_snapshot','indicator_readings')} for a in quant['assets']])}
        for code in AGENTS:
            if code=='QUANT': continue
            if used+allocation>budget:
                finish_agent(run_id,code,'BLOCKED',error='DAILY_BUDGET_EXHAUSTED'); continue
            predecessors=agent_rows(run_id)
            context=dict(compact,previous_opinions=[{'code':a['code'],'status':a['status'],'output':a['output'],'error_code':a['error_code']} for a in predecessors if a['status']!='PENDING' and a['code']!='QUANT'])
            if len(store.encode(context).encode())>int(os.environ.get('ATLAS_MAX_CONTEXT_BYTES','250000')):
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
                    used+=max(0,int(cost['amount'])-allocation)
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
                report_id='atlas_'+day
                report={'contract_version':'atlas_v1','report_id':report_id,'run_id':run_id,'authorization_mode':'SHADOW_ONLY',
                  'generated_at':store.now(),'data_cutoff_at':cutoff.isoformat(),'expires_at':(cutoff+timedelta(days=1)).isoformat(),
                  'report_status':'PARTIAL','coverage':{'status':'PARTIAL','pool_total':None,'evaluated':len(quant['assets'])},
                  'editorial':atlas['output'],'agents':rows,'quant':quant,'source_health':health,'evidence':evidence,
                  'snapshot':{k:v for k,v in (snap or {}).items() if k!='data'},'limitations':inputs['limitations']}
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

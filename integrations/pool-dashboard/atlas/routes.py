"""Authenticated editorial and service read API. ACK only records shadow feedback."""
from datetime import datetime,timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import time
from fastapi import APIRouter,Depends,HTTPException,Request,Response,Query
from fastapi.responses import FileResponse
from pydantic import Field
from typing import Literal
from . import store
from .schema import AGENTS,Strict,Report
from .pipeline import agent_rows,parse_time

def report_read(report_id):
    with store.db() as c:
        row=c.execute('SELECT body,sha256 FROM reports '+('ORDER BY generated_at DESC LIMIT 1' if report_id=='latest' else 'WHERE id=?'),() if report_id=='latest' else (report_id,)).fetchone()
    if not row: raise HTTPException(404,'Nenhum relatório validado publicado')
    body=json.loads(row[0])
    if store.digest(body)!=row[1]: raise HTTPException(503,'Integridade não confirmada')
    return body,row[1]

def configure(app,authenticated,root):
    rate={}
    def access(scope):
        def check(request:Request):
            # Service tokens are explicit scopes; ingestion key and Scalpyn JWT never work here.
            if request.headers.get('authorization'):
                try: clients=json.loads(os.environ.get('ATLAS_API_CLIENTS_JSON','[]'))
                except ValueError: raise HTTPException(503,'Configuração inválida')
                token=request.headers['authorization']
                if not token.startswith('Bearer '): raise HTTPException(401,'Credencial inválida')
                digest=hashlib.sha256(token[7:].encode()).hexdigest()
                for client in clients:
                    if hmac.compare_digest(digest,client.get('sha256','')):
                        if scope not in client.get('scopes',[]): raise HTTPException(403,'Escopo insuficiente')
                        minute=int(time.time()//60)
                        key=(client['id'],minute)
                        for old in list(rate):
                            if old[1]<minute: del rate[old]
                        rate[key]=rate.get(key,0)+1
                        if rate[key]>int(os.environ.get('ATLAS_API_RATE_PER_MINUTE','60')): raise HTTPException(429,'Limite de consultas')
                        return {'consumer':client['id'],'service':True}
                raise HTTPException(401,'Credencial inválida')
            user=authenticated(request)
            if scope=='ack:write': raise HTTPException(403,'Feedback exclusivo de consumidor autorizado')
            return user
        return check
    router=APIRouter(prefix='/api/v1/atlas')

    @router.get('/health',dependencies=[Depends(access('reports:read'))])
    def health():
        with store.db() as c:
            last=c.execute('SELECT id,day,revision,started_at,finished_at,status,error_code FROM cycles ORDER BY day DESC,revision DESC LIMIT 1').fetchone()
            count=c.execute('SELECT count(*) FROM reports').fetchone()[0]
            successful=c.execute('SELECT id,generated_at,expires_at FROM reports ORDER BY generated_at DESC LIMIT 1').fetchone()
            inputs=c.execute('SELECT inputs FROM cycles WHERE id=?',(last['id'],)).fetchone() if last else None
            frozen=json.loads(inputs[0]) if inputs else {}
            q=frozen.get('quant',{})
        return {'authorization_mode':'SHADOW_ONLY','execution_enabled':False,'enabled':os.environ.get('ATLAS_ENABLED')=='true',
          'schedule':'19:00 America/Sao_Paulo','last_cycle':dict(last) if last else None,'published_reports':count,
          'last_successful_report':dict(successful) if successful else None,
          'preflight':store.get_state('preflight',{'status':'NOT_CHECKED'}),'daily_budget_usd':os.environ.get('ATLAS_DAILY_BUDGET_USD'),
          'gaps':(['POOL_UNIVERSE_NOT_PROVEN'] if q.get('pool_membership_status')!='COMPLETE' else [])+['PROJECT_VESTING_NOT_UNIVERSALLY_VERIFIED','SPECIALIST_FEEDS_PARTIAL'],
          'pool_membership_status':q.get('pool_membership_status','UNKNOWN'),
          'inference_mode':'single_bounded_messages_request',
          'agents':list(AGENTS)}

    @router.get('/runs',dependencies=[Depends(access('reports:read'))])
    def runs(limit:int=Query(50,ge=1,le=100),cursor:str=''):
        with store.db() as c: rows=c.execute('SELECT id,day,revision,started_at,finished_at,status,error_code FROM cycles WHERE id>? ORDER BY id LIMIT ?', (cursor,limit+1)).fetchall()
        return {'items':[dict(r) for r in rows[:limit]],'next_cursor':rows[limit-1]['id'] if len(rows)>limit else None}

    @router.get('/runs/{run_id}',dependencies=[Depends(access('reports:read'))])
    def run(run_id:str):
        with store.db() as c: row=c.execute('SELECT * FROM cycles WHERE id=?',(run_id,)).fetchone()
        if not row: raise HTTPException(404,'Ciclo não encontrado')
        result=dict(row); inputs=json.loads(result.pop('inputs'))
        result.update(agents=agent_rows(run_id),quant=inputs.get('quant'),evidence=inputs.get('evidence',[]),
          source_health=inputs.get('source_health',[]),cutoff=inputs.get('cutoff'),limitations=inputs.get('limitations',[]))
        return result

    @router.get('/reports',dependencies=[Depends(access('reports:read'))])
    def reports(limit:int=Query(50,ge=1,le=100),cursor:str=''):
        with store.db() as c: rows=c.execute('SELECT id,run_id,generated_at,expires_at,sha256 FROM reports WHERE id>? ORDER BY id LIMIT ?', (cursor,limit+1)).fetchall()
        return {'items':[dict(r) for r in rows[:limit]],'next_cursor':rows[limit-1]['id'] if len(rows)>limit else None}

    @router.get('/reports/{report_id}',response_model=Report,dependencies=[Depends(access('reports:read'))])
    def report(report_id:str,response:Response):
        body,digest=report_read(report_id)
        response.headers['ETag']='"'+digest+'"'
        response.headers['X-Atlas-Expired']=str(parse_time(body['expires_at'])<=datetime.now(timezone.utc)).lower()
        return body

    @router.get('/reports/{report_id}/agents',dependencies=[Depends(access('reports:read'))])
    def agents(report_id:str):
        body,_=report_read(report_id)
        return {'report_id':body['report_id'],'agents':body['agents']}

    @router.get('/reports/{report_id}/export',dependencies=[Depends(access('reports:read'))])
    def export(report_id:str,format:Literal['json','md']='json'):
        body,_=report_read(report_id)
        if format=='json': return Response(store.encode(body),media_type='application/json')
        # Plain Markdown export, never rendered as trusted HTML.
        out=['# '+body['editorial']['title'],body['report_id'],'SHADOW_ONLY',body['editorial']['summary'],body['editorial']['analysis']]
        for a in body['agents']: out.extend(['## '+a['code'],store.encode(a)])
        out.extend(['## Decisões propostas',store.encode(body['editorial']['directives']),'## Evidências',store.encode(body['evidence'])])
        return Response('\n\n'.join(out),media_type='text/markdown')

    @router.get('/risks',dependencies=[Depends(access('risks:read'))])
    def risks(report_id:str='latest',risk_level:str|None=None,asset_id:str|None=None,limit:int=Query(50,ge=1,le=500),cursor:int=Query(0,ge=0)):
        body,_=report_read(report_id)
        rows=[r for r in body['editorial']['asset_risks'] if (not risk_level or r['risk_level']==risk_level) and (not asset_id or r['asset_id']==asset_id)]
        return {'report_id':body['report_id'],'items':rows[cursor:cursor+limit],'next_cursor':cursor+limit if len(rows)>cursor+limit else None}

    @router.get('/assets/{asset_id}/risks',dependencies=[Depends(access('risks:read'))])
    def asset_risks(asset_id:str,limit:int=Query(50,ge=1,le=100),cursor:str=''):
        with store.db() as c: rows=c.execute('SELECT body FROM reports WHERE id>? ORDER BY id LIMIT ?',(cursor,limit+1)).fetchall()
        reports=[json.loads(r[0]) for r in rows]
        return {'asset_id':asset_id,'items':[{'report_id':r['report_id'],'risks':[a for a in r['editorial']['asset_risks'] if a['asset_id']==asset_id]} for r in reports[:limit]],
          'next_cursor':reports[limit-1]['report_id'] if len(reports)>limit else None}

    @router.get('/directives',dependencies=[Depends(access('directives:read'))])
    def directives(report_id:str='latest',status:str|None=None,action:str|None=None,limit:int=Query(50,ge=1,le=500),cursor:int=Query(0,ge=0)):
        body,_=report_read(report_id); items=[]
        with store.db() as c: feedback=[dict(r) for r in c.execute('SELECT * FROM feedback ORDER BY received_at')]
        for d in body['editorial']['directives']:
            row=dict(d,effective_status='EXPIRED' if parse_time(d['expires_at'])<=datetime.now(timezone.utc) else 'PROPOSED',
              feedback=[dict(f,payload=json.loads(f['payload'])) for f in feedback if f['directive_id']==d['directive_id']])
            if (not status or row['effective_status']==status) and (not action or d['action']==action): items.append(row)
        return {'report_id':body['report_id'],'items':items[cursor:cursor+limit],'next_cursor':cursor+limit if len(items)>cursor+limit else None}

    class Ack(Strict):
        consumer_environment:Literal['shadow']
        status:Literal['SIMULATED','REJECTED']
        applied_at:None=None
        reason_code:str=Field(min_length=1,max_length=100)
        policy_version:str=Field(min_length=1,max_length=100)
        scalpyn_reference_id:str|None=None

    @router.post('/directives/{directive_id}/ack')
    def ack(directive_id:str,body:Ack,request:Request,client=Depends(access('ack:write'))):
        key=request.headers.get('idempotency-key','')
        if not 8<=len(key)<=128: raise HTTPException(422,'Idempotency-Key obrigatório')
        payload=store.encode(body.model_dump())
        with store.db(True) as c:
            prior=c.execute('SELECT directive_id,payload FROM feedback WHERE consumer=? AND idempotency_key=?',(client['consumer'],key)).fetchone()
            if prior:
                if prior[0]!=directive_id or prior[1]!=payload: raise HTTPException(409,'Reutilização divergente da chave')
                return {'recorded':True,'execution_enabled':False}
            found=None
            for row in c.execute('SELECT body FROM reports'):
                for d in json.loads(row[0])['editorial']['directives']:
                    if d['directive_id']==directive_id: found=d
            if not found: raise HTTPException(404,'Diretiva não encontrada')
            if parse_time(found['expires_at'])<=datetime.now(timezone.utc): raise HTTPException(409,'Diretiva expirada')
            c.execute('INSERT INTO feedback VALUES (?,?,?,?,?)',(client['consumer'],key,directive_id,payload,store.now()))
            store.audit(c,'shadow_feedback',directive_id,body.model_dump())
        return {'recorded':True,'execution_enabled':False}

    @router.get('/evidence/{evidence_id}',dependencies=[Depends(access('reports:read'))])
    def evidence(evidence_id:str):
        with store.db() as c: row=c.execute('SELECT body FROM evidence WHERE id=?',(evidence_id,)).fetchone()
        if not row: raise HTTPException(404,'Evidência não encontrada')
        return json.loads(row[0])

    app.include_router(router)
    @app.get('/api/v1/atlas/openapi.json',dependencies=[Depends(access('reports:read'))])
    def openapi():
        schema=app.openapi()
        schema['paths']={k:v for k,v in schema['paths'].items() if k.startswith('/api/v1/atlas')}
        schema.setdefault('components',{})['securitySchemes']={
          'ServiceToken':{'type':'http','scheme':'bearer','description':'Hashed client key with explicit scopes; not a Scalpyn JWT.'},
          'OwnerSession':{'type':'apiKey','in':'cookie','name':'__Host-pool_session'}}
        schema['security']=[{'ServiceToken':[]},{'OwnerSession':[]}]
        return schema
    @app.get('/atlas')
    @app.get('/atlas/{page:path}')
    def portal(page:str=''):
        return FileResponse(root/'static'/'atlas.html')

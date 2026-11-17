"""Owner-scoped historical reads remain valid after watchlist removal."""
from uuid import UUID, uuid4
from datetime import datetime
from pydantic import ValidationError
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import text
from ..database import get_db
from .config import get_current_user_id
from ..schemas.mtf_history import HistoryConfig, HistoryFilter, HistoryExport
from ..services import mtf_history as service

router = APIRouter(prefix='/api/mtf/observations',tags=['MTF observation history'])


def history_filters(watchlist_id:str|None=None,symbol:str|None=None,profile_id:str|None=None,
                    status:str|None=None,trend:str|None=None,quality:str|None=None,
                    start:datetime|None=None,end:datetime|None=None):
    try:
        return HistoryFilter(watchlist_id=watchlist_id,symbol=symbol,profile_id=profile_id,
                             status=status,trend=trend,quality=quality,start=start,end=end)
    except ValidationError as exc:
        raise HTTPException(422,'INVALID_DATE_RANGE_OR_TIMEZONE') from exc


@router.get('/capture-health')
async def capture_health(db=Depends(get_db),uid=Depends(get_current_user_id)):
    rows=(await db.execute(text('''SELECT status,count(*) AS jobs,min(scheduled_at) AS oldest,
        count(*) FILTER (WHERE error IS NOT NULL) AS with_error FROM mtf_history_capture_jobs
        WHERE user_id=:uid GROUP BY status'''),{'uid':uid})).mappings().all()
    return {'jobs':[dict(r) for r in rows],'operational_effect':False}


@router.get('/settings')
async def settings(db=Depends(get_db),uid=Depends(get_current_user_id)):
    return HistoryConfig.model_validate(await service.config_service.get_config(db,'mtf_history',uid) or {})


@router.put('/settings')
async def update_settings(payload:HistoryConfig,db=Depends(get_db),uid=Depends(get_current_user_id)):
    return await service.config_service.update_config(db,'mtf_history',uid,payload.model_dump(),uid,
        change_description='Histórico MTF: captura e horizontes analíticos, sem efeito operacional')


@router.get('')
async def observations(response:Response, filters:HistoryFilter=Depends(history_filters),before:str|None=None,
                       limit:int=Query(50,ge=1,le=200),db=Depends(get_db),uid=Depends(get_current_user_id)):
    response.headers['Cache-Control']='private, no-store'
    try:
        return await service.list_observations(db,uid,filters,before,limit)
    except ValueError as exc:
        raise HTTPException(422,'INVALID_CURSOR') from exc


@router.post('/exports',status_code=202)
async def export(payload:HistoryExport,db=Depends(get_db),uid=Depends(get_current_user_id)):
    eid=uuid4()
    await db.execute(text('INSERT INTO mtf_history_exports(id,user_id,request) VALUES(:id,:uid,CAST(:request AS jsonb))'),
        {'id':eid,'uid':uid,'request':payload.model_dump_json()})
    await db.commit()
    return {'id':eid,'status':'PENDING'}


@router.get('/exports/{eid}')
async def export_status(eid:UUID,db=Depends(get_db),uid=Depends(get_current_user_id)):
    row=(await db.execute(text('SELECT id,status,row_count,error,created_at,completed_at FROM mtf_history_exports WHERE id=:id AND user_id=:uid'),
        {'id':eid,'uid':uid})).mappings().first()
    if not row: raise HTTPException(404,'EXPORT_NOT_FOUND')
    return dict(row)


@router.get('/exports/{eid}/download')
async def download(eid:UUID,db=Depends(get_db),uid=Depends(get_current_user_id)):
    row=(await db.execute(text('SELECT status,request,content FROM mtf_history_exports WHERE id=:id AND user_id=:uid'),
        {'id':eid,'uid':uid})).mappings().first()
    if not row: raise HTTPException(404,'EXPORT_NOT_FOUND')
    if row['status']!='READY': raise HTTPException(409,'EXPORT_NOT_READY')
    xlsx=row['request']['format']=='xlsx'
    return Response(bytes(row['content']),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if xlsx else 'application/zip',
        headers={'Cache-Control':'private, no-store','Content-Disposition':f'attachment; filename="mtf-history-{eid}.{ "xlsx" if xlsx else "zip"}"'})


@router.get('/{oid}')
async def observation(oid:UUID,response:Response,db=Depends(get_db),uid=Depends(get_current_user_id)):
    response.headers['Cache-Control']='private, no-store'
    result=await service.detail(db,uid,oid)
    if result is None: raise HTTPException(404,'OBSERVATION_NOT_FOUND')
    return result

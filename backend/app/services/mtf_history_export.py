"""Background, consistent exports; no UI pagination cap or silent truncation."""
import csv
import io
import json
import tempfile
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import text
from ..schemas.mtf_history import HistoryExport
from .mtf_history import FILTER_SQL, filter_params, detail, serial, utc


def cell(value):
    if isinstance(value,(dict,list)):
        value = serial(value)
    if isinstance(value,datetime):
        value = value.isoformat()
    if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')):
        return "'"+value
    return value


HEADERS = {
    'observations': ['observation_id','symbol','watchlist_id','profile_id','observed_at_utc','observed_at_local','timezone',
        'status','trend','quality','observed_price','price_source_at','price_received_at','price_quality','candle_close_price','candle_close_at',
        'score','engine_version','profile_hash','config_hash','data_reasons','next_status_change','snapshot_json'],
    'indicators': ['observation_id','name','timeframe','value','source_at','producer','role','quality','metadata_json'],
    'outcomes': ['observation_id','horizon_minutes','due_at_utc','status','result_json'],
}


def export_rows(record, zone):
    p = record['payload']
    a = p['analysis']
    oid = str(record['id'])
    rows = {'observations': [[oid,record['symbol'],record['watchlist_id'],record['profile_id'],record['observed_at'],
        utc(record['observed_at']).astimezone(ZoneInfo(zone)).isoformat(),zone,record['status'],record['trend'],record['quality'],
        p.get('observed_price'),p.get('price_source_at'),p.get('price_received_at'),p.get('price_quality'),p.get('candle_close_price'),p.get('candle_close_at'),
        a.get('score'),a.get('engine_version'),a.get('profile_hash'),a.get('config_hash'),p.get('data_reasons'),
        record.get('next_status_change'),p]], 'indicators':[], 'outcomes':[]}
    for i in p['indicators']:
        rows['indicators'].append([oid]+[i.get(k) for k in ('name','timeframe','value','source_at','producer','role','quality')]+[i])
    for r in record['outcomes']:
        rows['outcomes'].append([oid,r['horizon_minutes'],r['due_at'],r['status'],r['payload']])
    return rows


async def build(db,uid,request,cutoff):
    request = HistoryExport.model_validate(request)
    params = filter_params(uid,request.filters) | {'cutoff':cutoff}
    # Export cutoff is fixed at request time; cursor order is deterministic.
    result = await db.stream(text('SELECT o.id FROM mtf_history_observations o WHERE '+FILTER_SQL+
        ' AND o.observed_at<=:cutoff ORDER BY o.observed_at,o.id'),params)
    count = 0
    with tempfile.TemporaryDirectory(prefix='mtf-export-') as folder:
        from pathlib import Path
        paths = {name:Path(folder)/(name+'.csv') for name in HEADERS}
        handles = {name:path.open('w',encoding='utf-8-sig',newline='') for name,path in paths.items()}
        writers = {name:csv.writer(handle) for name,handle in handles.items()}
        for name,header in HEADERS.items():
            writers[name].writerow(header)
        try:
            async for row in result:
                record = await detail(db,uid,row.id)
                for name,rows in export_rows(record,request.timezone).items():
                    for values in rows:
                        writers[name].writerow([cell(v) for v in values])
                count += 1
        finally:
            for handle in handles.values():
                handle.close()
            await result.close()
        output = io.BytesIO()
        if request.format == 'csv':
            with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
                for name,path in paths.items():
                    archive.write(path,name+'.csv')
                archive.writestr('README.txt','UTC and selected timezone are explicit. Missing values are empty. Outcomes are separate future labels. No trading authority. snapshot_json preserves the full original evidence.')
        else:
            from openpyxl import Workbook
            from openpyxl.cell import WriteOnlyCell
            # Full snapshots exceed the CSV parser's default field limit.
            # Use the portable C long maximum; Excel cells are chunked below.
            csv.field_size_limit(2147483647)
            workbook = Workbook(write_only=True)
            def append(sheet, values):
                cells=[]
                for value in values:
                    c=WriteOnlyCell(sheet,value=value)
                    if isinstance(value,str): c.data_type='s'
                    cells.append(c)
                sheet.append(cells)
            metadata = workbook.create_sheet('metadata_chunks')
            metadata.append(['reference','chunk_index','text'])
            ref_count = 0
            for name,path in paths.items():
                sheet = workbook.create_sheet(name)
                n,part = 0,1
                with path.open(encoding='utf-8-sig',newline='') as handle:
                    for row in csv.reader(handle):
                        if n == 1048576:
                            part += 1
                            sheet = workbook.create_sheet(f'{name}_{part}')
                            sheet.append(HEADERS[name]); n = 1
                        # Preserve oversized JSON losslessly in ordered chunks.
                        for column,value in enumerate(row):
                            if len(value)>30000:
                                ref_count += 1
                                reference = f'metadata:{ref_count}'
                                for index,start in enumerate(range(0,len(value),30000)):
                                    append(metadata,[reference,index,value[start:start+30000]])
                                row[column] = reference
                        append(sheet,row); n += 1
            workbook.save(output)
        return output.getvalue(), count


async def process_pending(db):
    await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ'))
    row = (await db.execute(text("SELECT * FROM mtf_history_exports WHERE status='PENDING' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"))).mappings().first()
    if not row:
        return 0
    try:
        content,count = await build(db,row['user_id'],row['request'],row['created_at'])
        await db.execute(text("UPDATE mtf_history_exports SET status='READY',content=:content,row_count=:count,completed_at=now() WHERE id=:id"),
                         {'id':row['id'],'content':content,'count':count})
        await db.commit()
    except Exception as exc:
        await db.rollback()
        await db.execute(text("UPDATE mtf_history_exports SET status='FAILED',error=:error,completed_at=now() WHERE id=:id AND status='PENDING'"),
                         {'id':row['id'],'error':str(exc)[:500]})
        await db.commit()
        raise
    return 1

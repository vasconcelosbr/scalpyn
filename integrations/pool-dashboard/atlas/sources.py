"""Bounded fixed-origin feeds; external text is never executable input."""
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlsplit
import httpx
from defusedxml import ElementTree
from archive_contract import unpack
from . import store

# No article URL is fetched. New sources require a reviewed configuration change.
FEEDS = (
 ('FED','https://www.federalreserve.gov/feeds/press_all.xml','VERIFIED_PRIMARY','MACRO'),
 ('THE_BLOCK','https://www.theblock.co/rss.xml','CORROBORATED_REPORT','GENERAL'),
)

class Plain(HTMLParser):
    def __init__(self): super().__init__(); self.parts=[]
    def handle_data(self,data): self.parts.append(data)

def plain(text):
    p=Plain(); p.feed(text or '')
    return ' '.join(' '.join(p.parts).split())

def safe_url(url):
    try:
        p=urlsplit(url)
        return p.scheme=='https' and bool(p.hostname) and not p.username and not p.password
    except ValueError: return False

def parse_feed(raw, source, url, tier, domain, cutoff):
    root=ElementTree.fromstring(raw)
    items=root.findall('.//item') or root.findall('{http://www.w3.org/2005/Atom}entry')
    result=[]
    for item in items:
        def value(name):
            node=item.find(name)
            if node is None: node=item.find('{http://www.w3.org/2005/Atom}'+name)
            return (node.text or '') if node is not None else ''
        link=value('link')
        if not link:
            node=item.find('{http://www.w3.org/2005/Atom}link')
            link=node.get('href','') if node is not None else ''
        if not safe_url(link): continue
        stamp=value('pubDate') or value('published') or value('updated')
        published=None
        try:
            d=parsedate_to_datetime(stamp) if ',' in stamp else datetime.fromisoformat(stamp.replace('Z','+00:00'))
            if d.tzinfo is not None: published=d.astimezone(timezone.utc).isoformat()
        except (ValueError,TypeError): pass
        # A feed publication time is a source claim, not an independently verified event time.
        if published and datetime.fromisoformat(published)>cutoff: continue
        title=plain(value('title'))
        excerpt=plain(value('description') or value('summary'))
        canonical={'url':link,'title':title,'excerpt':excerpt,'published_at':published}
        hash_=store.digest(canonical)
        result.append(dict(canonical,evidence_id='ev_'+hash_,hash=hash_,source_id=source,
           source_feed=url,source_tier=tier,domain=domain,retrieved_at=store.now(),
           publication_verification='SOURCE_REPORTED' if published else 'UNKNOWN',
           full_article_available=False,trust='UNTRUSTED_CONTENT',text_truncated=False))
    return result

def collect_feeds(cutoff):
    evidence,health=[],[]
    for name,url,tier,domain in FEEDS:
        try:
            for attempt in range(3):
                try:
                    with httpx.Client(timeout=25,follow_redirects=False) as client:
                        with client.stream('GET',url,headers={'User-Agent':'ATLAS-Intelligence/1.0 RSS reader'}) as res:
                            res.raise_for_status()
                            raw=b''
                            for chunk in res.iter_bytes():
                                raw+=chunk
                                if len(raw)>2097152: raise ValueError('SOURCE_TOO_LARGE')
                    break
                except (httpx.TransportError,httpx.HTTPStatusError) as exc:
                    retry=not isinstance(exc,httpx.HTTPStatusError) or exc.response.status_code==429 or exc.response.status_code>=500
                    if not retry or attempt==2: raise
                    time.sleep(2**attempt)
            parsed=parse_feed(raw,name,url,tier,domain,cutoff)
            evidence.extend(parsed)
            health.append({'source_id':name,'url':url,'status':'OK' if parsed else 'EMPTY',
              'retrieved_at':store.now(),'items':len(parsed),'response_sha256':hashlib.sha256(raw).hexdigest()})
        except Exception:
            health.append({'source_id':name,'url':url,'status':'UNAVAILABLE','retrieved_at':store.now(),'items':None})
    with store.db(True) as c:
        for e in evidence:
            old=c.execute('SELECT id FROM evidence WHERE url=? ORDER BY first_seen_at DESC LIMIT 1',(e['url'],)).fetchone()
            e['supersedes_evidence_id']=old[0] if old and old[0]!=e['evidence_id'] else None
            c.execute('INSERT OR IGNORE INTO evidence VALUES (?,?,?,?,?)',(e['evidence_id'],e['url'],e['hash'],store.encode(e),e['retrieved_at']))
        # Include evidence across cycles; unknown publication is retained explicitly.
        rows=c.execute('SELECT body,first_seen_at FROM evidence ORDER BY first_seen_at,id').fetchall()
        upcoming_ids=set()
        for row in c.execute('SELECT body FROM reports'):
            for agent in json.loads(row[0])['agents']:
                for finding in (agent.get('output') or {}).get('findings',[]):
                    stamp=finding.get('scheduled_at')
                    if finding.get('occurrence')=='UPCOMING' and stamp and datetime.fromisoformat(stamp.replace('Z','+00:00'))>=cutoff:
                        upcoming_ids.update(finding['evidence_ids'])
    lookback=cutoff-timedelta(days=int(os.environ.get('ATLAS_EVIDENCE_LOOKBACK_DAYS','7')))
    selected=[]
    for r in rows:
        e=json.loads(r[0]); e['first_seen_at']=r[1]
        if e['evidence_id'] in upcoming_ids or e['published_at'] is None or datetime.fromisoformat(e['published_at'])>=lookback: selected.append(e)
    return selected,health

def snapshot(cutoff):
    p=Path(os.environ['SNAPSHOT_DIR'])/'dashboard.sqlite3'
    c=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True)
    try: row=c.execute('SELECT day,captured_at,sha256,payload_gzip FROM snapshots WHERE captured_at<=? ORDER BY day DESC LIMIT 1',(cutoff.isoformat(),)).fetchone()
    finally: c.close()
    if not row: return None
    raw=unpack((row[2],row[3]))
    return {'day':row[0],'captured_at':row[1],'sha256':row[2],'data':json.loads(raw)}

def quant(snap,cutoff):
    rows=[]
    max_age=int(os.environ.get('ATLAS_QUANT_MAX_AGE_SECONDS','3600'))
    for a in (snap or {}).get('data',{}).get('assets',[]):
        readings=a.get('indicator_readings') or []
        stamps=[r.get('timestamp') for r in readings]
        stale=False
        for t in stamps+[a.get('refreshed_at')]:
            try:
                age=(cutoff-datetime.fromisoformat(t.replace('Z','+00:00'))).total_seconds()
                stale=stale or age<0 or age>max_age
            except (ValueError,TypeError,AttributeError): stale=True
        rows.append({'asset_id':a['id'],'symbol':a['symbol'],'identity_status':'UNRESOLVED_PROJECT',
          'coverage_status':'STALE' if stale else 'PARTIAL','risk_level':'UNKNOWN',
          'current_price':a.get('current_price'),'price_change_24h':a.get('price_change_24h'),
          'volume_24h':a.get('volume_24h'),'market_cap':a.get('market_cap'),'alpha_score':a.get('alpha_score'),
          'refreshed_at':a.get('refreshed_at'),'decision_snapshot':a.get('analysis_snapshot'),
          'indicator_readings':readings,
          'quant_context':[{'scheduler_group':r.get('scheduler_group'),'timeframe':r.get('timeframe'),
             'timestamp':r.get('timestamp'),'selection':'explicit_context_fields_full_snapshot_retained',
             'values':{k:({f:v.get(f) for f in ('value','period','status','unit') if f in v} if isinstance(v,dict) else v) for k,v in (r.get('values') or {}).items() if k in (
               'rsi','adx','atr_pct','volume','volume_24h','volume_delta','cvd','taker_ratio','spread_pct',
               'orderbook_depth','bid_ask_imbalance','ema9','ema21','ema50','ema200','macd','vwap','price','close')},
             'full_metadata_available_in_snapshot':True}
             for r in readings],
          'evidence_ids':['snapshot_'+snap['sha256']],
          'limitations':['Identidade de projeto/rede não fornecida.','Snapshot de decisão separado dos indicadores recentes.']})
    return {'title':'Leitura quantitativa persistida','summary':'Valores originais, sem recálculo de score ou avaliação de trading.',
       'analysis':'A análise quantitativa conserva valores e timeframes. Não há séries anteriores suficientes para inferir aceleração ou causalidade. UNKNOWN não equivale a ausência de risco.',
       'coverage_status':'PARTIAL','assets':rows,'freshness_policy':{'max_age_seconds':max_age},
       'pool_total':None,'observed_watchlist_total':len(rows),'pool_membership_status':'BLOCKED_BY_SOURCE_CONTRACT',
       'pool_only':None,'watchlist_only':None,'matched':None,
       'limitations':['A fonte disponível é a watchlist; o denominador completo do Pool não foi recebido.']}

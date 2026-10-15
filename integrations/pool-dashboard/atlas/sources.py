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
 ('THE_BLOCK','https://www.theblock.co/rss.xml','SINGLE_SOURCE_REPORT','GENERAL'),
 ('SEC','https://www.sec.gov/news/pressreleases.rss','VERIFIED_PRIMARY','GEO'),
 ('ECB','https://www.ecb.europa.eu/rss/press.html','VERIFIED_PRIMARY','MACRO'),
 ('BLS','https://www.bls.gov/feed/bls_latest.rss','VERIFIED_PRIMARY','MACRO'),
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
        hash_=store.digest(dict(canonical,source_tier=tier,extractor_version=2))
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
    selected={}
    for r in rows:
        e=json.loads(r[0]); e['first_seen_at']=r[1]
        if e.get('source_id') not in {f[0] for f in FEEDS}: continue
        if e.get('source_tier')=='CORROBORATED_REPORT': continue
        if e['evidence_id'] in upcoming_ids or e['published_at'] is None or datetime.fromisoformat(e['published_at'])>=lookback:
            selected[(e['source_id'],e['url'])]=e
    return list(selected.values()),health

def snapshot(cutoff):
    from .pool_source import fetch_context
    return fetch_context(cutoff)


def quant(snap,cutoff):
    rows=[]
    context=(snap or {}).get('pool_context')
    original=(snap or {}).get('data',{}).get('assets',[])
    market=(snap or {}).get('data',{}).get('market_mode')
    members={(market,a['symbol']):a for a in original}
    active=[c for c in context['coins'] if c['is_active']] if context else []
    source_rows=[]
    for coin in active:
        matched=members.get((coin['market_type'],coin['symbol']))
        a=dict(matched or {});a.update(id=coin['id'],symbol=coin['symbol'],market_type=coin['market_type'],
          watchlist_asset_id=(matched or {}).get('id'),pool_coin=coin,indicator_readings=coin['indicator_readings'])
        source_rows.append(a)
    if not context: source_rows=original
    max_age=int(os.environ.get('ATLAS_QUANT_MAX_AGE_SECONDS','3600'))
    for a in source_rows:
        readings=a.get('indicator_readings') or []
        stamps=[r.get('timestamp') for r in readings]
        stale=False
        for t in stamps+[a.get('refreshed_at')]:
            try:
                age=(cutoff-datetime.fromisoformat(t.replace('Z','+00:00'))).total_seconds()
                stale=stale or age<0 or age>max_age
            except (ValueError,TypeError,AttributeError): stale=True
        freshness=[]
        for r in readings:
            try:
                age=(cutoff-datetime.fromisoformat(r['timestamp'].replace('Z','+00:00'))).total_seconds()
                status='FRESH' if 0<=age<=max_age else 'STALE'
            except (ValueError,TypeError,KeyError,AttributeError): age=None;status='UNKNOWN'
            freshness.append({'scheduler_group':r.get('scheduler_group'),'timeframe':r.get('timeframe'),'timestamp':r.get('timestamp'),'age_seconds':age,'status':status,'formula':'cutoff - persisted timestamp'})
        if not readings: stale=True
        rows.append({'market_type':a.get('market_type',market),'watchlist_asset_id':a.get('watchlist_asset_id',a['id'] if not context else None),'pool_coin':a.get('pool_coin'),'freshness_by_reading':freshness,'asset_id':a['id'],'symbol':a['symbol'],'identity_status':'UNRESOLVED_PROJECT',
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
       'pool_total':len(active) if context else None,'observed_watchlist_total':len(original),
       'pool_membership_status':'COMPLETE' if context else 'BLOCKED_BY_SOURCE_CONTRACT',
       'pool_row_count':context['row_count'] if context else None,
       'pool_inactive':[c for c in context['coins'] if not c['is_active']] if context else None,
       'pool_only':[c['id'] for c in active if (c['market_type'],c['symbol']) not in members] if context else None,
       'watchlist_only':[a['id'] for a in original if (market,a['symbol']) not in {(c['market_type'],c['symbol']) for c in active}] if context else None,
       'matched':[c['id'] for c in active if (c['market_type'],c['symbol']) in members] if context else None,
       'limitations':(['Cobertura de membros completa não comprova atualidade, identidade ou ausência de risco.'] if context else ['A fonte disponível é a watchlist; o denominador completo do Pool não foi recebido.'])}

"""Fixed-origin public evidence. Metadata/status only: no exchange trading calls."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
import re
import calendar
import os
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup
from . import store

GATE = 'https://api.gateio.ws/api/v4'


def request(url, body=None):
    # Call sites are fixed reviewed URLs. Never accept URLs from models or articles.
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        with client.stream('GET' if body is None else 'POST', url, json=body,
                           headers={'User-Agent':'ATLAS-Intelligence/2 public research'}) as r:
            r.raise_for_status()
            raw = b''
            for chunk in r.iter_bytes():
                raw += chunk
                if len(raw) > 8388608: raise ValueError('SOURCE_TOO_LARGE')
    return raw


def evidence(source, url, title, excerpt, domain, published=None, **extra):
    canonical = dict(url=url, title=title, excerpt=excerpt, published_at=published)
    digest = store.digest(dict(canonical, **extra))
    return dict(canonical, evidence_id='ev_'+digest, hash=digest, source_id=source,
                source_feed=url, source_tier='VERIFIED_PRIMARY', domain=domain,
                retrieved_at=store.now(), publication_verification='SOURCE_REPORTED' if published else 'UNKNOWN',
                full_article_available=False, trust='UNTRUSTED_CONTENT', text_truncated=False,
                corroboration='NOT_INDEPENDENTLY_CORROBORATED', **extra)


def gate_metadata(assets):
    pairs = json.loads(request(GATE+'/spot/currency_pairs'))
    currencies = json.loads(request(GATE+'/spot/currencies'))
    if not isinstance(pairs,list) or not isinstance(currencies,list): raise ValueError('GATE_SCHEMA')
    pairs = {p['id']:p for p in pairs}
    currencies = {c['currency']:c for c in currencies}
    items, identities = [], {}
    for a in assets:
        pair = pairs.get(a['symbol']) if a.get('market_type','spot')=='spot' else None
        currency = currencies.get(pair['base']) if pair else None
        if not pair or not currency or not currency.get('name'): continue
        # Exchange pair + issuer name + chain/address metadata, never ticker inference.
        metadata = {k:currency.get(k) for k in ('currency','name','chain','chains','delisted',
                    'trade_disabled','deposit_disabled','withdraw_disabled','total_supply')}
        metadata['pair'] = {k:pair.get(k) for k in ('id','base','quote','trade_status','type')}
        ev=evidence('GATE_METADATA',GATE+'/spot/currencies/'+currency['currency'],
          'Metadados e estado na Gate: '+currency['name'],store.encode(metadata),'SECURITY_TOKEN',
          structured=metadata, verified_asset_ids=[a['asset_id']], fact_type='EXCHANGE_STATUS_AND_IDENTITY',
          source_updated_at=None, observation_at=store.now())
        items.append(ev)
        identities[a['asset_id']]={'status':'EXCHANGE_VERIFIED','exchange':'gate',
          'pair':pair['id'],'base':pair['base'],'quote':pair['quote'],'project_name':currency['name'],
          'chains':currency.get('chains'), 'evidence_id':ev['evidence_id'],
          'scope':'Exchange identity only; no claim of independent project or vesting verification.'}
    return items, identities


def gate_announcements():
    result, seen, total = [], set(), None
    for page in range(1,51):
        data=json.loads(request(GATE+'/ann/list_article',{'page':str(page),'size':'100','lang':'en','timer':'7'}))
        if data.get('code')!=0 or not isinstance(data.get('data',{}).get('list'),list): raise ValueError('GATE_ANN_SCHEMA')
        batch=data['data']['list']; total=data['data']['total']
        if not batch: break
        before=len(seen)
        for a in batch:
            if a['id'] in seen: continue
            seen.add(a['id'])
            # Only classify topical relevance. This does NOT assign severity or confirm an exploit.
            title=a['title']; text=title+' '+a.get('brief','')
            if not re.search(r'delist|suspend|resum|mainten|security|exploit|hack|swap|migrat|unlock|vesting|supply|burn|upgrade|governance',text,re.I): continue
            relative=a.get('url','')
            if not re.fullmatch(r'/announcements/article/\d+',relative): raise ValueError('ANN_URL')
            stamp=a.get('release_timestamp')
            published=datetime.fromtimestamp(int(stamp),timezone.utc).isoformat() if stamp else None
            ev=evidence('GATE_ANNOUNCEMENTS',urljoin('https://www.gate.com',relative),title,a.get('brief',''),
               'SECURITY_TOKEN',published,source_record=a,publication_date=a.get('release_time'),
               fact_type='EXCHANGE_ANNOUNCEMENT')
            ev['text_truncated']=text.endswith(('...','…'))
            result.append(ev)
        if len(seen)>=total: return result, {'total_received':len(seen),'total_reported':total,'pagination_complete':True}
        if len(seen)==before: raise ValueError('ANN_PAGINATION_STALLED')
    return result, {'total_received':len(seen),'total_reported':total,'pagination_complete':False}


def bea_calendar():
    url='https://www.bea.gov/news/schedule'
    soup=BeautifulSoup(request(url),'html.parser'); table=soup.select_one('#release-schedule-table')
    if table is None: raise ValueError('BEA_SCHEMA')
    year=re.search(r'\b20\d{2}\b',table.select_one('thead').get_text()).group()
    result=[]
    for row in table.select('tbody tr'):
        date=row.select_one('.release-date'); time=row.select_one('small'); title=row.select_one('.release-title')
        if not title: raise ValueError('BEA_ROW_SCHEMA')
        if not date:
            if 'To Be Announced' not in row.get_text(): raise ValueError('BEA_ROW_SCHEMA')
            result.append(evidence('BEA_CALENDAR',url,title.get_text(' ',strip=True),row.get_text(' ',strip=True),
                'MACRO',scheduled_at=None,scheduled_date=None,fact_type='OFFICIAL_CALENDAR',schedule_status='TO_BE_ANNOUNCED',consensus_value=None))
            continue
        literal=date.get_text(' ',strip=True)+' '+year
        # BEA schedule explicitly declares Eastern Time; preserve source text and DST conversion.
        stamp=None
        if time and re.search(r'Eastern',soup.get_text(),re.I):
            stamp=datetime.strptime(literal+' '+time.get_text(' ',strip=True),'%B %d %Y %I:%M %p').replace(tzinfo=ZoneInfo('America/New_York')).isoformat()
        result.append(evidence('BEA_CALENDAR',url,title.get_text(' ',strip=True),row.get_text(' ',strip=True),
          'MACRO',scheduled_at=stamp,scheduled_date=datetime.strptime(literal,'%B %d %Y').date().isoformat(),
          event_status='UPCOMING',fact_type='OFFICIAL_CALENDAR',source_timezone='America/New_York' if stamp else None,consensus_value=None))
    return result


def fomc_calendar():
    url='https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm'
    soup=BeautifulSoup(request(url),'html.parser'); result=[]
    for panel in soup.select('.panel'):
        heading=panel.select_one('.panel-heading')
        match=re.search(r'\b(20\d{2})\b',heading.get_text()) if heading else None
        if not match: continue
        year=int(match.group(1))
        for meeting in panel.select('.fomc-meeting'):
            month=meeting.select_one('.fomc-meeting__month'); date=meeting.select_one('.fomc-meeting__date')
            if not month or not date: raise ValueError('FOMC_SCHEMA')
            literal=month.get_text(' ',strip=True)+' '+date.get_text(' ',strip=True)+' '+str(year)
            # Date ranges and unscheduled meetings retained verbatim. No invented release hour.
            result.append(evidence('FOMC_CALENDAR',url,'FOMC: '+literal,literal,'MACRO',
                scheduled_at=None,scheduled_date_literal=literal,scheduled_date=fomc_date(year,month.get_text(' ',strip=True),date.get_text(' ',strip=True)),fact_type='OFFICIAL_CALENDAR',consensus_value=None))
    if not result: raise ValueError('FOMC_EMPTY')
    return result


def fomc_date(year,month,days):
    name=month.split('/')[-1].strip()
    months={n.lower():i for i,n in enumerate(calendar.month_name) if n}
    months.update({n.lower():i for i,n in enumerate(calendar.month_abbr) if n})
    numbers=re.findall(r'\d+',days)
    if name.lower() not in months or not numbers: return None
    return datetime(year,months[name.lower()],int(numbers[-1])).date().isoformat()


def ofac_actions():
    url='https://ofac.treasury.gov/recent-actions'
    soup=BeautifulSoup(request(url),'html.parser');result=[];seen=set()
    for a in soup.select('a[href]'):
        path=a.get('href',''); match=re.fullmatch(r'/recent-actions/(\d{8})(?:_\d+)?',path)
        if not match or path in seen: continue
        seen.add(path)
        day=datetime.strptime(match.group(1),'%Y%m%d').date().isoformat()
        result.append(evidence('OFAC',urljoin(url,path),a.get_text(' ',strip=True),a.get_text(' ',strip=True),'GEO',
            publication_date=day,fact_type='OFFICIAL_SANCTIONS_NOTICE'))
    if not result: raise ValueError('OFAC_SCHEMA')
    return result


def solana_incidents():
    url='https://status.solana.com/api/v2/incidents.json';data=json.loads(request(url)); result=[]
    if data.get('page',{}).get('name')!='Solana' or not isinstance(data.get('incidents'),list): raise ValueError('SOLANA_SCHEMA')
    for incident in data['incidents']:
        if not re.fullmatch(r'[a-z0-9]+',incident['id']): raise ValueError('INCIDENT_ID')
        result.append(evidence('SOLANA_STATUS','https://status.solana.com/incidents/'+incident['id'],incident['name'],
          '\n'.join(x['body'] for x in incident['incident_updates']),'SECURITY',incident.get('created_at'),
          structured=incident,fact_type='OFFICIAL_INCIDENT',reported_impact=incident.get('impact')))
    return result


def collect(assets,cutoff):
    result, health, identities = [], [], {}
    loaders=[('GATE_METADATA',lambda:gate_metadata(assets)),('GATE_ANNOUNCEMENTS',gate_announcements),
             ('BEA_CALENDAR',bea_calendar),('FOMC_CALENDAR',fomc_calendar),('OFAC',ofac_actions),('SOLANA_STATUS',solana_incidents)]
    for source,loader in loaders:
        try:
            value=loader(); details={}
            if source=='GATE_METADATA': items,identities=value
            elif source=='GATE_ANNOUNCEMENTS': items,details=value
            else: items=value
            result.extend(items)
            health.append({'source_id':source,'status':'PARTIAL' if details.get('pagination_complete') is False else 'OK',
                'items':len(items),'retrieved_at':store.now(),'response_sha256':store.digest(items),**details})
        except Exception:
            health.append({'source_id':source,'status':'UNAVAILABLE','items':None,'retrieved_at':store.now()})
    # Timestamp-less announcements remain date-only; no manufactured midnight publication.
    lower=cutoff-timedelta(days=7)
    upper=cutoff+timedelta(days=int(os.environ.get('ATLAS_CALENDAR_HORIZON_DAYS','45')))
    selected=[]
    for e in result:
        if e.get('published_at'):
            stamp=datetime.fromisoformat(e['published_at'].replace('Z','+00:00'))
            if not lower<=stamp<=cutoff: continue
        if e.get('fact_type')=='OFFICIAL_CALENDAR':
            day=e.get('scheduled_date')
            if day is not None and not cutoff.date().isoformat()<=day<=upper.date().isoformat(): continue
        if e.get('publication_date') and e['publication_date']<lower.date().isoformat(): continue
        if e.get('scheduled_at') and datetime.fromisoformat(e['scheduled_at'])<cutoff: continue
        selected.append(e)
    return selected,health,identities

"""Explicit PoE2 economy schemas: primaryValue is in core.primary currency.

Category IDs verified against poe.ninja's own PoE2 economy pages. Stash and
exchange rates belong to their individual responses and are never mixed.
"""
import asyncio
import math
import os
import time
from datetime import datetime, timezone
import httpx

_BASE='https://poe.ninja/poe2/api'
# category -> (API type, page slug, API family)
_TYPES={
 'currency':('Currency','currency','exchange'),
 'fragment':('Fragments','fragments','exchange'),
 'abyss':('Abyss','abyssal-bones','exchange'),
 'uncutgem':('UncutGems','uncut-gems','exchange'),
 'lineagegem':('LineageSupportGems','lineage-support-gems','exchange'),
 'essence':('Essences','essences','exchange'),
 'soulcore':('SoulCores','soul-cores','exchange'),
 'idol':('Idols','idols','exchange'),
 'rune':('Runes','runes','exchange'),
 'omen':('Ritual','omens','exchange'),
 'expedition':('Expedition','expedition','exchange'),
 'emotion':('Delirium','liquid-emotions','exchange'),
 'catalyst':('Breach','breach-catalyst','exchange'),
 'verisium':('Verisium','verisium','exchange'),
 'uniqueweapon':('UniqueWeapons','unique-weapons','stash'),
 'uniquearmour':('UniqueArmours','unique-armours','stash'),
 'uniqueaccessory':('UniqueAccessories','unique-accessories','stash'),
 'uniqueflask':('UniqueFlasks','unique-flasks','stash'),
 'uniquecharm':('UniqueCharms','unique-charms','stash'),
 'uniquejewel':('UniqueJewels','unique-jewels','stash'),
 'uniquerelic':('UniqueSanctumRelics','unique-relics','stash'),
 'uniquetablet':('UniqueTablets','unique-tablets','stash'),
 'tablet':('PrecursorTablets','precursor-tablets','stash'),
}
_ALIASES={'gem':['lineagegem','uncutgem'],'unique':[k for k in _TYPES if k.startswith('unique')],
          'delirium':['emotion'],'liquidemotion':['emotion'],'map':['tablet','uniquetablet']}
_cache={}


def _request_json(url, params=None):
    r=httpx.get(url,params=params,timeout=30,follow_redirects=True,
                headers={'User-Agent':'poe-data-mcp-poe2/0.4 (+https://github.com/pilattao/poe-data-mcp)'})
    r.raise_for_status()
    return r.json()


def index():
    key=('index',)
    if key not in _cache or time.time()-_cache[key][0]>900:
        data=_request_json(_BASE+'/data/index-state')
        if not isinstance(data.get('economyLeagues'),list):raise ValueError('Unexpected PoE2 league index schema')
        _cache[key]=(time.time(),data)
    return _cache[key][1]


def resolve_league(league):
    requested=(league or os.environ.get('POE_LEAGUE','')).strip()
    leagues=index()['economyLeagues']
    if requested:
        found=[x['name'] for x in leagues if requested.casefold() in {x['name'].casefold(),x['url'].casefold()}]
        if len(found)!=1:raise ValueError(f'Unknown or inactive PoE2 economy league {requested!r}; available: '+', '.join(x['name'] for x in leagues))
        return found[0]
    candidates=[x['name'] for x in leagues if not x.get('hardcore') and x['name'] not in {'Standard','Hardcore'}]
    if len(candidates)!=1:raise ValueError('Choose an explicit PoE2 league: '+', '.join(candidates))
    return candidates[0]


def _number(value):
    return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value)


def normalize(data):
    if not isinstance(data,dict) or not isinstance(data.get('lines'),list) or not isinstance(data.get('core'),dict):
        raise ValueError('Unexpected PoE2 economy response schema')
    core=data['core'];primary=core.get('primary');rates=core.get('rates')
    if not isinstance(primary,str) or not isinstance(rates,dict):raise ValueError('Missing primary currency in PoE2 economy schema')
    items={x['id']:x['name'] for x in data.get('items',[])+core.get('items',[]) if 'id' in x and 'name' in x}
    result=[]
    for line in data['lines']:
        price=line.get('primaryValue')
        if not _number(price) or price<0:raise ValueError('Invalid primaryValue in PoE2 economy response')
        name=line.get('name') or items.get(line.get('id'))
        if not name:raise ValueError('Missing item name in PoE2 economy response')
        conversions={primary:price}
        for currency,rate in rates.items():
            if currency!=primary and _number(rate) and rate>0:conversions[currency]=price*rate
        record={'name':name,'primary_currency':primary,'primary_value':price,'values':conversions,
                'details_id':line.get('detailsId'),'base_type':line.get('baseType'),
                'trend_7d':(line.get('sparkline') or line.get('sparkLine') or {}).get('totalChange')}
        for orig,key in [('listingCount','listing_count'),('volumePrimaryValue','volume_primary_value'),('corrupted','corrupted'),('levelRequired','level_required'),('gemLevel','gem_level'),('gemQuality','gem_quality'),('variant','variant')]:
            if orig in line:record[key]=line[orig]
        result.append(record)
    return result


def fetch(league,type_name,family='exchange'):
    key=(league,type_name,family)
    if key not in _cache or time.time()-_cache[key][0]>900:
        tail='exchange/current/overview' if family=='exchange' else 'stash/current/item/overview'
        url=_BASE+'/economy/'+tail
        data=_request_json(url,{'league':league,'type':type_name})
        rows=normalize(data)  # failure is not cached as a successful empty result
        _cache[key]=(time.time(),{'rows':rows,'fetched_at':datetime.now(timezone.utc).isoformat(),'source':url})
    return _cache[key][1]


def _format(record):
    prices='; '.join(f'{value:,.6g} {currency}' for currency,value in record['values'].items())
    info=[]
    for key in ['base_type','level_required','gem_level','gem_quality','variant','corrupted','listing_count']:
        if key in record and record[key] is not None:info.append(f'{key}: {record[key]}')
    if 'volume_primary_value' in record:info.append(f"exchange volume: {record['volume_primary_value']:,.6g} {record['primary_currency']} (not listing count)")
    if record.get('trend_7d') is not None:info.append(f"7d change: {record['trend_7d']:+g}%")
    return f"## {record['name']}\n{prices}\n"+'; '.join(info)


def _search(query,league,category):
    if not query.strip():raise ValueError('Provide an item name or keyword')
    cat=category.casefold().replace(' ','').replace('-','')
    if cat:
        keys=_ALIASES.get(cat,[cat])
        if any(k not in _TYPES for k in keys):raise ValueError('Unknown PoE2 category. Use '+', '.join(_TYPES)+', gem, unique')
    else:
        keys=['currency','uniqueaccessory','lineagegem']+[k for k in _TYPES if k not in {'currency','uniqueaccessory','lineagegem'}]
    league=resolve_league(league);found=[];sources=[];q=query.strip().casefold()
    for key in keys:
        type_name,slug,family=_TYPES[key];data=fetch(league,type_name,family)
        matches=[r for r in data['rows'] if q in r['name'].casefold()]
        if matches:
            found.extend(matches);sources.append(f"{type_name}: {data['source']} (fetched {data['fetched_at']})")
        if not cat and any(r['name'].casefold()==q for r in matches):break
    found.sort(key=lambda r:(r['name'].casefold()!=q,r['name'],r['primary_value']))
    lines=[f'# PoE2 price check — {league}',f'{len(found)} matches; showing up to 25.']
    lines.extend(_format(r) for r in found[:25])
    if not found:lines.append('The selected PoE2 datasets contain no matching price. This does not mean zero value or no items for sale.')
    lines.extend(sources)
    lines.append('Retrieval time is not the source snapshot time. Exchange data can lag by an hour; these are aggregate prices, not a quote for a specific roll or skill level.')
    return '\n\n'.join(lines)


async def price_check(query: str, league: str = '', category: str = '') -> str:
    """Price PoE2 currencies, lineage gems, uniques or crafting items with explicit units.

    category: currency, gem, unique, uniqueaccessory, uniquecharm, rune, essence,
    soulcore, idol, omen, emotion, catalyst, verisium, tablet, fragment, expedition.
    league: explicit league name/slug or POE_LEAGUE. Ambiguity is an error.
    """
    return await asyncio.to_thread(_search,query,league,category)


async def currency_overview(league: str = '') -> str:
    """Get PoE2 currency exchange values in each currency supplied by the source."""
    league=await asyncio.to_thread(resolve_league,league)
    data=await asyncio.to_thread(fetch,league,'Currency')
    rows=sorted(data['rows'],key=lambda r:-r['primary_value'])[:25]
    return f'# PoE2 currency overview — {league}\n\n'+'\n\n'.join(_format(r) for r in rows)+f"\n\nSource: {data['source']}\nFetched: {data['fetched_at']}; source snapshot timestamp unavailable, exchange data can lag by an hour."

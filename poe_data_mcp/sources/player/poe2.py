"""PoE2 player lookups backed by installed definitions and verified PoE2DB markup."""
import json
import re
from datetime import datetime, timezone
from urllib.parse import quote
from poe_data_mcp.sources.poe2_data import get_data, values
from poe_data_mcp.sources.common import fetch_page


def _source(meta):
    return f"Source: {meta['source']} / {meta['file']} (file modified {meta['modified_at']}; this is not a patch release date)."


def _descriptions():
    descriptions = {}
    for p in sorted((get_data().root/'Data/Skills').glob('*.lua')):
        text = p.read_text(encoding='utf-8-sig')
        blocks = re.split(r'^skills\["([^"\n]+)"\]\s*=\s*\{', text, flags=re.M)
        for i in range(1,len(blocks),2):
            m=re.search(r'\bdescription\s*=\s*("(?:[^"\\]|\\.)*")', blocks[i+1])
            if m:
                # Generated descriptions use JSON-compatible quoted string escapes.
                try: descriptions[blocks[i]]=json.loads(m[1])
                except ValueError: descriptions[blocks[i]]=m[1][1:-1]
    return descriptions


def search_gem(query: str) -> str:
    """Search PoE2 gem names and skill descriptions in the installed PoB2 data."""
    if not query.strip(): raise ValueError('A non-empty gem name or keyword is required')
    data=get_data(); desc=_descriptions(); q=query.casefold(); found=[]
    for gem in data.gems():
        detail=desc.get(gem.get('grantedEffectId'),'')
        name=gem['name'].casefold()
        score=3 if name==q else 2 if q in name else 1 if q in detail.casefold() else 0
        if score: found.append((score,gem,detail))
    found.sort(key=lambda x:(-x[0],x[1]['name']))
    lines=[f'PoE2 gems: {len(found)} matches; showing up to 30.']
    for _,g,d in found[:30]:
        lines += [f"## {g['name']}", f"Type: {g.get('gemType','support' if g.get('tags',{}).get('support') else 'skill')}; {g.get('tagString','')}",d,
                  f"Gem ID: {g['id']}; skill ID: {g.get('grantedEffectId','unknown')}"]
    lines.append(_source(data.metadata('Data/Gems.lua')))
    return '\n'.join(lines)


def get_gem_detail(gem_name: str) -> str:
    """Read exact PoE2 gem tooltips and selected level rows, separating skill definitions."""
    url='https://poe2db.tw/us/'+quote(gem_name.strip().replace(' ','_'),safe='_')
    soup=fetch_page(url); matches=[]; seen=set()
    for popup in soup.select('.newItemPopup.GemPopup'):
        title=popup.select_one('.itemName')
        if not title or title.get_text(' ',strip=True).casefold()!=gem_name.strip().casefold(): continue
        text=popup.get_text(' ',strip=True)
        if text in seen: continue
        seen.add(text)
        pane=popup.find_parent(class_='tab-pane')
        identity=pane.get('id','') if pane else ''
        matches.append(f'## {gem_name} ({identity or "skill definition"})\n{text}')
        if pane:
            for header in pane.select('.card-header'):
                if 'Level Effect' not in header.get_text(): continue
                card=header.find_parent(class_='card')
                if card:
                    for table in card.find_all('table'):
                        rows=[]
                        for tr in table.find_all('tr'):
                            cells=[x.get_text(' ',strip=True) for x in tr.find_all(['td','th'])]
                            if cells and (tr.find('th') or cells[0] in {'1','10','20','30','40'}): rows.append(' | '.join(cells))
                        matches.append('Level scaling (selected rows):\n'+'\n'.join(rows))
                break
    if not matches: raise ValueError(f'No matching PoE2 gem tooltip found at {url}; page content must be checked')
    return '\n\n'.join(matches)+f'\n\nSource: {url}\nFetched: {datetime.now(timezone.utc).isoformat()}\nEach definition is shown separately; these are not character-calculated values.'


def _variants(item):
    current=[v for v in item['variants'] if v['name'].casefold()=='current']
    return current or item['variants']


def search_item(query: str) -> str:
    """Search PoE2 uniques by name, base or modifier using local PoB2 definitions."""
    if not query.strip(): raise ValueError('A non-empty item name or keyword is required')
    q=query.casefold(); found=[]
    for it in get_data().uniques():
        name=it['name'].casefold()
        text=' '.join(v['name']+' '+' '.join(v['modifiers']) for v in _variants(it)).casefold()
        score=3 if name==q else 2 if q in name else 1 if q in it['base_type'].casefold() or q in text else 0
        if score:found.append((score,it))
    found.sort(key=lambda x:(-x[0],x[1]['name']))
    lines=[f'PoE2 uniques: {len(found)} matches; showing up to 20.']
    for _,it in found[:20]:
        lines.append(f"## {it['name']} ({it['base_type']})")
        for v in _variants(it): lines.extend([f"Variant: {v['name']}",*v['modifiers']])
        lines.append(_source(it['source']))
    return '\n'.join(lines)


def get_item_detail(item_name: str) -> str:
    """Get local PoE2 unique definitions with current and legacy variants separated."""
    candidates=[it for it in get_data().uniques() if it['name'].casefold()==item_name.strip().casefold()]
    if not candidates: return f'No exact unique item {item_name!r} in installed PoB2 data. Use search_item to find its name.'
    lines=[]
    for it in candidates:
        lines.extend([f"# {it['name']}",f"Base: {it['base_type']}"])
        for variant in sorted(it['variants'],key=lambda v:v['name'].casefold()!='current'):
            lines.append(f"## Variant: {variant['name']}")
            lines.extend('- '+s for s in variant['modifiers'])
        lines.extend([_source(it['source']),'PoB definitions can include legacy or alternate variants; the sections above must not be combined.'])
    return '\n'.join(lines)


def load_tree(version=''):
    data=get_data().tree(version); nodes={}
    for nid,node in data['nodes'].items():
        n=dict(node); n['stats']=values(n.get('stats')); n['masteryEffects']=values(n.get('masteryEffects'))
        n['_id']=str(nid); n['out']=[]; n['in']=[]
        n['_type']='ascendancy' if n.get('ascendancyName') else 'keystone' if n.get('isKeystone') else 'notable' if n.get('isNotable') else 'jewel_socket' if n.get('isJewelSocket') else 'small'
        nodes[str(nid)]=n
    for nid,n in nodes.items():
        for connection in values(n.get('connections')):
            target=str(connection.get('id') if isinstance(connection,dict) else connection)
            if target in nodes:
                n['out'].append(target);nodes[target]['in'].append(nid)
    return {'all_nodes':list(nodes.values()),'by_id':nodes,'version':data['version']}

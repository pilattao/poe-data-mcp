"""Native PoB2 mod pools, with first-matching ordered spawn-weight rules."""
import re

from poe_data_mcp.sources.poe2_data import get_data


def _sequence(table):
    if isinstance(table, dict):
        return [table[key] for key in sorted(table) if isinstance(key, int)]
    return list(table or [])


def _normalize(text):
    text = text.casefold().replace('_', ' ').replace('/', ' ').replace('-', ' ')
    text = text.replace('strength', 'str').replace('dexterity', 'dex').replace('intelligence', 'int')
    text = text.replace('staves', 'staff').replace('stave', 'staff').replace('warstaff', 'quarterstaff')
    return ' '.join(word.rstrip('s') for word in text.split())


def select_bases(bases: list[dict], item_type: str) -> list[dict]:
    query = _normalize(item_type)
    if not query:
        return []
    exact = [base for base in bases if _normalize(base['name']) == query]
    if exact:
        return exact
    attribute = re.search(r'\b(str|dex|int)(?: (?:str|dex|int))*$', query)
    attrs = set(attribute[0].split()) if attribute else set()
    kind = query[:attribute.start()].strip() if attribute else query
    result = []
    for base in bases:
        if base.get('hidden'):
            continue
        base_kind = _normalize(base['type'])
        if base.get('subType') == 'Warstaff':
            base_kind = 'quarterstaff'
        names = {base_kind}
        if base['type'] == 'Flask':
            names.add(_normalize(base.get('subType', '') + ' flask'))
        if base['type'] == 'Jewel':
            names.add(_normalize(base['name']))
            names.add(_normalize(base['name'] + ' jewel'))
        if kind not in names:
            continue
        if attrs:
            armour_tag = '_'.join(a for a in ('str', 'dex', 'int') if a in attrs) + '_armour'
            if not base.get('tags', {}).get(armour_tag):
                continue
        result.append(base)
    return result


def _weight(mod: dict, base: dict):
    tags = {tag for tag, enabled in base.get('tags', {}).items() if enabled}
    tags.add('default')
    keys, weights = _sequence(mod.get('weightKey')), _sequence(mod.get('weightVal'))
    if len(keys) != len(weights):
        raise ValueError('PoB2 modifier has mismatched spawn tag/weight arrays')
    for key, weight in zip(keys, weights):
        if key in tags:
            return weight
    return 0


def _text(mod: dict) -> str:
    return '; '.join(mod[key] for key in sorted(k for k in mod if isinstance(k, int))
                     if isinstance(mod[key], str))


def search_mods(item_type: str, query: str = '') -> str:
    data = get_data()
    bases = select_bases(data.bases(), item_type)
    if not bases:
        return (f'Unknown or unavailable PoE2 item type/base: {item_type}. '
                'Use an exact base name or class: crossbow, quarterstaff, charm, ring, '
                'helmet str, body armour dex int, life flask, Ruby, Focus.')
    domains: dict[str, list] = {}
    for base in bases:
        file = {'Charm': 'ModCharm', 'Flask': 'ModFlask', 'Jewel': 'ModJewel',
                'Transcendent Limb': 'ModIncursionLimb'}.get(base['type'], 'ModItem')
        domains.setdefault(f'Data/{file}.lua', []).append(base)
    if (data.root / 'Data/ModCorrupted.lua').is_file():
        domains['Data/ModCorrupted.lua'] = bases
    matches = []
    sources = []
    for file, candidates in domains.items():
        mods = data.load(file)
        if not mods:
            raise ValueError(f'Empty PoB2 mod table: {file}')
        sources.append(data.metadata(file))
        for mid, mod in mods.items():
            # Special definitions without an affix type are not ordinary rolls.
            if mod.get('type') not in {'Prefix', 'Suffix', 'Corrupted'}:
                continue
            text = _text(mod)
            searchable = ' '.join([text, mid, mod.get('affix', ''), mod.get('group', ''),
                                   ' '.join(_sequence(mod.get('modTags')))])
            if query.casefold() not in searchable.casefold():
                continue
            eligible = [base for base in candidates if _weight(mod, base) > 0]
            if not eligible:
                continue
            matches.append((mid, mod, text, eligible))
    matches.sort(key=lambda row: (row[1]['type'], row[1].get('group', ''), -row[1].get('level', 0), row[0]))
    lines = [f'## PoE2 modifiers on {item_type}', f'{len(matches)} matching modifiers.',
             'Local PoB2 spawn weights establish eligibility only; they are not measured probabilities.',
             'Class searches include a modifier if at least one matching base is eligible.']
    for mid, mod, text, eligible in matches[:150]:
        lines.append(f"- **{mid}** ({mod['type']}, min iLvl {mod.get('level', '?')}, "
                     f"group {mod.get('group', '?')}): {text}")
        if len(eligible) != len(bases):
            sample = ', '.join(base['name'] for base in sorted(eligible, key=lambda b: b['name'])[:3])
            lines.append(f'  Applies to {len(eligible)}/{len(bases)} selected bases, e.g. {sample}.')
    if len(matches) > 150:
        lines.append('Showing first 150; narrow the query or specify an exact base.')
    if not matches:
        lines.append('No matching eligible modifiers in the installed PoB2 tables.')
    for source in sources:
        lines.append(f"Source: local PoB2 | {source['file']} | modified {source['modified_at']}")
    return '\n'.join(lines)

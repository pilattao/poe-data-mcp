"""PoE2 world definitions and map-device items from native sources."""
from poe_data_mcp.sources.poe2_data import get_data, values
from poe_data_mcp.sources.crafting import poe2 as coe

CATEGORIES = ('maps', 'areas', 'tablets', 'waystones')
ALIASES = {'map': 'maps', 'area': 'areas', 'zone': 'areas', 'zones': 'areas',
           'tablet': 'tablets', 'waystone': 'waystones'}
SCARAB_GAP = ('Unsupported in PoE2: scarabs are not a supported PoE2 category. '
              'Use category="tablets" for actual PoE2 map-content items. '
              'Tablets have their own mechanics; no one-to-one scarab mapping is defined.')


def _world_areas() -> list[dict]:
    data = get_data()
    world = data.load('Data/WorldAreas.lua', seed=True)
    if not world:
        raise ValueError('Empty local PoB2 WorldAreas data')
    meta = data.metadata('Data/WorldAreas.lua')
    source = f"Source: local PoB2 | {meta['file']} | modified {meta['modified_at']}"
    return [dict(area, id=aid, category='maps' if area.get('isMap') else 'areas', source=source)
            for aid, area in world.items() if area.get('name') and area.get('level', 0) > 0
            and not area.get('isHideout')]


def _device_items(category: str) -> list[dict]:
    bundle = coe.load_bundle()
    classes = coe.index(bundle, 'classes')
    mods = coe.index(bundle, 'mods')
    result = []
    for item in coe.entries(bundle, 'items'):
        cls = classes.get(str(item['class']))
        if not cls or cls.get('legacy'):
            continue
        name = coe.label(bundle, item['label'])
        kind = coe.label(bundle, cls['label']).lower()
        if category == 'tablets' and 'tablet' not in kind:
            continue
        map_info = bundle['data']['items'].get('maps', {}).get(str(item['id']))
        if category == 'waystones' and not (map_info and 'waystone' in name.lower()):
            continue
        effects = [coe.mod_text(bundle, mods[str(mid)]) for mid in item.get('implicits', [])]
        description = bundle['data']['items'].get('descriptions', {}).get(str(item['id']), {})
        if description.get('description') is not None:
            effects.append(coe.label(bundle, description['description']))
        result.append({'id': str(item['id']), 'name': name, 'category': category,
                       'drop_level': item.get('drop'), 'tier': map_info.get('tier') if map_info else None,
                       'effect': '; '.join(effects), 'source': coe.source_note(bundle)})
    if not result:
        raise ValueError(f'No {category} in the verified PoE2 source snapshot; source schema or coverage may have changed')
    return result


def _search_text(entry):
    return ' '.join([entry['name'], entry.get('baseName', ''), entry['id'],
                     entry.get('description', ''), entry.get('effect', ''),
                     ' '.join(values(entry.get('tags', {}))),
                     ' '.join(values(entry.get('bossVarieties', {}))),
                     ' '.join(values(entry.get('monsterVarieties', {})))])


def _summary(entry):
    result = f"- **{entry['name']}** (`{entry['id']}`)"
    bosses = values(entry.get('bossVarieties', {}))
    if bosses:
        result += ' — Boss: ' + ', '.join(bosses)
    if entry.get('effect'):
        result += ' — ' + entry['effect']
    if entry.get('tier') is not None:
        result += f" — Waystone tier {entry['tier']}"
    return result


def env_search(query: str, category: str = '') -> str:
    raw = category.strip().casefold()
    if raw in {'scarab', 'scarabs'}:
        return SCARAB_GAP
    resolved = ALIASES.get(raw, raw)
    if resolved and resolved not in CATEGORIES:
        return f'Unknown PoE2 category {category!r}. Valid categories: {", ".join(CATEGORIES)}.'
    chosen = [resolved] if resolved else list(CATEGORIES)
    sections, errors = [], []
    world = None
    for cat in chosen:
        try:
            if cat in {'maps', 'areas'}:
                if world is None:
                    world = _world_areas()
                entries = [area for area in world if area['category'] == cat]
            else:
                entries = _device_items(cat)
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(f'{cat} unavailable: {exc}')
            continue
        terms = query.casefold().split()
        matches = [entry for entry in entries if all(term in _search_text(entry).casefold() for term in terms)]
        matches.sort(key=lambda entry: (entry['name'].casefold() != query.casefold(), entry['name'], entry['id']))
        if matches:
            sections += [f'### PoE2 {cat} ({len(matches)} matches)', *[_summary(entry) for entry in matches[:20]]]
            if len(matches) > 20:
                sections.append('Showing first 20; narrow your search.')
            sections.append(matches[0]['source'])
    if not sections:
        sections.append(f'No PoE2 results matching {query!r} in the available categories.')
    if errors:
        sections += ['Coverage gaps:', *errors]
    return '\n'.join(sections)


def _detail(entry):
    lines = [f"# {entry['name']} (PoE2)", f"ID: {entry['id']}"]
    if entry['category'] in {'maps', 'areas'}:
        lines.append(f"Area definition level: {entry['level']}")
        if entry['category'] == 'maps':
            lines.append('Actual map level depends on the Waystone and other area modifiers; this is the stored area definition.')
        else:
            lines.append(f"Act: {entry.get('act', '?')}")
        for field, title in [('bossVarieties', 'Bosses'), ('monsterVarieties', 'Monsters'), ('tags', 'Area tags')]:
            elements = values(entry.get(field, {}))
            if elements:
                lines.append(title + ': ' + ', '.join(elements))
        if entry.get('description'):
            lines.append(entry['description'])
    else:
        if entry.get('tier') is not None:
            lines.append(f"Waystone tier: {entry['tier']}")
        if entry.get('drop_level') is not None:
            lines.append(f"Drop level: {entry['drop_level']}")
        if entry.get('effect'):
            lines.append('Source effect: ' + entry['effect'])
    lines.append(entry['source'])
    return '\n'.join(lines)


def env_detail(name: str) -> str:
    query = name.strip().casefold()
    if 'scarab' in query:
        return SCARAB_GAP
    errors = []
    try:
        areas = _world_areas()
    except (OSError, ValueError, RuntimeError) as exc:
        areas = []
        errors.append(f'maps/areas unavailable: {exc}')
    exact = [entry for entry in areas if query in {entry['name'].casefold(), entry['id'].casefold()}]
    matches = exact or [entry for entry in areas if entry.get('baseName', '').casefold() == query]
    if len(matches) == 1:
        return _detail(matches[0])
    if len(matches) > 1:
        return 'Multiple PoE2 areas match. Use the full name or ID:\n' + '\n'.join(_summary(entry) for entry in matches)
    for category in ('tablets', 'waystones'):
        try:
            matches = [entry for entry in _device_items(category)
                       if query in {entry['name'].casefold(), entry['id'].casefold()}]
            if len(matches) == 1:
                return _detail(matches[0])
            if len(matches) > 1:
                return 'Multiple PoE2 items match. Use the ID:\n' + '\n'.join(_summary(entry) for entry in matches)
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(f'{category} unavailable: {exc}')
    return f'Could not find {name!r} in available PoE2 sources. Try env_search.\n' + '\n'.join(errors)

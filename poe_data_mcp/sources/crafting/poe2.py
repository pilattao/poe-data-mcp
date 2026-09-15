"""Adapter for the CoE beta PoE2 data schema, verified 2026-09-15.

The page supplies versioned coedata={...} and coelang=[...] script resources.
These are public site assets, not an API. See README.md for schema and gaps.
Only the user's local cache contains the downloaded game data.
"""
from __future__ import annotations

import html
import json
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

SITE = 'https://beta.craftofexile.com/'
SOURCE = SITE + '?game=poe2'
CHECK_INTERVAL = 43200
DOWNLOAD_DELAY = 1.5
HEADERS = {'User-Agent': 'poe-data-mcp/0.2 (personal PoE2 crafting lookup)'}
_lock = threading.RLock()
_last_request = 0.0
_retry_at = 0.0
_errors: dict[str, tuple[float, str]] = {}
_memory: dict[str, tuple[tuple[int, int], dict]] = {}


def cache_file() -> Path:
    override = os.environ.get('POE_DATA_MCP_CACHE_DIR')
    if override:
        root = Path(override).expanduser()
    else:
        from platformdirs import user_cache_dir
        root = Path(user_cache_dir('poe-data-mcp', appauthor=False)) / 'craftofexile'
    return root / 'poe2' / 'bundle.json'


def parse_assignment(text: str, variable: str):
    # Parse data only: never evaluate remote JavaScript.
    match = re.fullmatch(r'\s*(?:(?:var|let|const)\s+)?' + re.escape(variable)
                         + r'\s*=\s*(.*?)\s*;?\s*', text, re.S)
    if not match:
        raise ValueError(f'Expected {variable} JavaScript data assignment')
    return json.loads(match[1])


def discover_files(page: str) -> tuple[str, dict[str, str]]:
    candidates: dict[str, dict[str, str]] = {}
    for script in BeautifulSoup(page, 'html.parser').find_all('script', src=True):
        url = urljoin(SITE, script['src'])
        match = re.fullmatch(re.escape(SITE) + r'json/poe2/([\d.]+)/(data\.json|localization/english\.json)(?:\?v=\d+)?', url)
        if match:
            name = 'data' if match[2] == 'data.json' else 'lang'
            candidates.setdefault(match[1], {})[name] = url
    complete = [(patch, files) for patch, files in candidates.items() if set(files) == {'data', 'lang'}]
    if len(complete) != 1:
        raise ValueError('No unambiguous PoE2 patch with matching data and English localization on CoE beta')
    return complete[0]


def validate_bundle(bundle: dict) -> None:
    if bundle.get('game') != 'poe2' or bundle.get('source') != SOURCE:
        raise ValueError('Cache provenance does not identify Craft of Exile PoE2')
    patch = bundle.get('patch', '')
    if not re.fullmatch(r'\d+(?:\.\d+)+', patch):
        raise ValueError('Missing CoE PoE2 patch identifier')
    files = bundle.get('files', {})
    for key, suffix in [('data', 'data.json'), ('lang', 'localization/english.json')]:
        if not re.fullmatch(re.escape(SITE + f'json/poe2/{patch}/{suffix}') + r'(?:\?v=\d+)?', files.get(key, '')):
            raise ValueError('Cache files do not belong to the same PoE2 patch')
    data, lang = bundle.get('data'), bundle.get('lang')
    if not isinstance(lang, list) or not lang or not all(isinstance(s, str) for s in lang):
        raise ValueError('CoE English localization must be a nonempty string array')
    if not isinstance(data, dict):
        raise ValueError('CoE data must be an object')
    for key in ('classes', 'items', 'mods', 'modgroups', 'essences'):
        if not isinstance(data.get(key), dict) or not isinstance(data[key].get('entries'), list) or not data[key]['entries']:
            raise ValueError(f'Missing or empty CoE {key}.entries')
        for entry in data[key]['entries']:
            if not isinstance(entry, dict) or 'id' not in entry:
                raise ValueError(f'Invalid CoE {key} entry')
            label = entry.get('label')
            if label is not None and (not isinstance(label, int) or not 0 <= label < len(lang)):
                raise ValueError(f'Invalid CoE localization index in {key}')
    if not isinstance(data.get('classmods'), dict) or not data['classmods']:
        raise ValueError('Missing CoE classmods mapping')
    if not isinstance(data.get('enums', {}).get('types'), dict):
        raise ValueError('Missing CoE modifier type enum')
    class_ids = {str(entry['id']) for entry in data['classes']['entries']}
    mod_ids = {str(entry['id']) for entry in data['mods']['entries']}
    group_ids = {str(entry['id']) for entry in data['modgroups']['entries']}
    essence_ids = {entry['id'] for entry in data['essences']['entries']}
    for item in data['items']['entries']:
        # Some currency items intentionally have no crafting class.
        if item.get('class') is not None and str(item['class']) not in class_ids:
            raise ValueError('CoE item references an unknown class')
    for cls, weights in data['classmods'].items():
        if cls not in class_ids or not isinstance(weights, dict):
            raise ValueError('Invalid CoE class mod pool')
        for mid, weight in weights.items():
            if mid not in mod_ids or not isinstance(weight, (int, float)):
                raise ValueError('Invalid CoE modifier reference or weight')
    for mod in data['mods']['entries']:
        if str(mod.get('group')) not in group_ids or not isinstance(mod.get('stats'), list):
            raise ValueError('Invalid CoE modifier group or stats')
        for stat in mod['stats']:
            idx = stat.get('label')
            if not isinstance(idx, int) or not 0 <= idx < len(lang):
                raise ValueError('Invalid CoE stat localization index')
            bounds = stat.get('range')
            if bounds is not False and (not isinstance(bounds, list) or len(bounds) != 2
                                       or not all(isinstance(n, (int, float)) for n in bounds)):
                raise ValueError('Invalid CoE modifier value range')
    for name in ('basemods', 'classmods'):
        mappings = data['essences'].get(name)
        if not isinstance(mappings, dict):
            raise ValueError(f'Missing CoE essence {name} mapping')
        for mapping in mappings.values():
            if not isinstance(mapping, dict) or any(mid not in mod_ids or eid not in essence_ids
                                                     for mid, eid in mapping.items()):
                raise ValueError('Invalid CoE essence modifier relationship')


def _read() -> dict | None:
    p = cache_file()
    if not p.exists():
        return None
    stat = p.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    remembered = _memory.get(str(p))
    if remembered and remembered[0] == stamp:
        return remembered[1]
    bundle = json.loads(p.read_text(encoding='utf-8'))
    validate_bundle(bundle)
    _memory[str(p)] = (stamp, bundle)
    return bundle


def _save(bundle: dict) -> None:
    p = cache_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Publish one complete patch snapshot, so failed downloads cannot mix patches.
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=p.parent,
                                     prefix='.bundle-', suffix='.tmp', delete=False) as f:
        temporary = Path(f.name)
        try:
            json.dump(bundle, f, ensure_ascii=False)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(p)
    finally:
        temporary.unlink(missing_ok=True)
    _memory.pop(str(p), None)


def _fetch(url: str) -> str:
    global _last_request, _retry_at
    if time.time() < _retry_at:
        raise RuntimeError(f'Craft of Exile rate limit: retry after {datetime.fromtimestamp(_retry_at, timezone.utc).isoformat()}')
    time.sleep(max(0, DOWNLOAD_DELAY - (time.monotonic() - _last_request)))
    _last_request = time.monotonic()
    response = httpx.get(url, headers=HEADERS, timeout=60, follow_redirects=True)
    if response.status_code in {429, 503}:
        retry = response.headers.get('Retry-After', '60')
        try:
            delay = float(retry)
        except ValueError:
            from email.utils import parsedate_to_datetime
            try:
                delay = parsedate_to_datetime(retry).timestamp() - time.time()
            except (ValueError, TypeError, OverflowError):
                delay = 60
        _retry_at = time.time() + max(60, delay)
    response.raise_for_status()
    return response.text


def ensure_cache(force: bool = False) -> dict:
    with _lock:
        try:
            existing = _read()
        except (ValueError, OSError):
            existing = None
        now = time.time()
        last_error = _errors.get(str(cache_file()))
        if not force and last_error and now - last_error[0] < 60:
            return {'checked': False, 'updated': False, 'error': last_error[1], 'cached': bool(existing)}
        if not force and existing and now - existing.get('checked_at', 0) < CHECK_INTERVAL:
            return {'checked': False, 'updated': False, 'cached': True}
        try:
            patch, files = discover_files(_fetch(SOURCE))
            if existing and not force and existing['files'] == files:
                snapshot = dict(existing, checked_at=now)
                updated = False
            else:
                data = parse_assignment(_fetch(files['data']), 'coedata')
                lang = parse_assignment(_fetch(files['lang']), 'coelang')
                snapshot = {'game': 'poe2', 'source': SOURCE, 'patch': patch, 'files': files,
                            'fetched_at': now, 'checked_at': now, 'data': data, 'lang': lang}
                validate_bundle(snapshot)
                updated = True
            _save(snapshot)
            _errors.pop(str(cache_file()), None)
            return {'checked': True, 'updated': updated, 'cached': True}
        except (httpx.HTTPError, ValueError, OSError, RuntimeError) as exc:
            _errors[str(cache_file())] = (now, str(exc))
            return {'checked': False, 'updated': False, 'cached': bool(existing), 'error': str(exc)}


def load_bundle() -> dict:
    result = ensure_cache()
    if not result.get('cached'):
        raise RuntimeError('PoE2 Craft of Exile data unavailable: ' + result.get('error', 'no valid snapshot'))
    return _read()


def source_note(bundle: dict) -> str:
    fetched = datetime.fromtimestamp(bundle['fetched_at'], timezone.utc).isoformat()
    note = f"Source: {SOURCE} | PoE2 dataset version {bundle['patch']} | fetched {fetched}"
    error = _errors.get(str(cache_file()))
    if error:
        note += f'\nRefresh error; using previous snapshot: {error[1]}'
    return note


def craftofexile_cache_status() -> str:
    try:
        bundle = _read()
    except (OSError, ValueError) as exc:
        return f'## PoE2 Craft of Exile Cache\nInvalid cache: {exc}\nRun update_craftofexile_cache().'
    if not bundle:
        return f'## PoE2 Craft of Exile Cache\nMissing: {cache_file()}\nRun update_craftofexile_cache().'
    age = int((time.time() - bundle['checked_at']) / 60)
    return (f'## PoE2 Craft of Exile Cache\nCache: {cache_file()}\n'
            f'Last checked: {age} minutes ago\n' + source_note(bundle))


def update_craftofexile_cache(force: bool = False) -> str:
    result = ensure_cache(force)
    status = 'Updated' if result['updated'] else 'Unchanged'
    if result.get('error'):
        status = 'Error refreshing PoE2 cache: ' + result['error']
    return status + '\n' + craftofexile_cache_status()


def clean(text: str) -> str:
    text = re.sub(r'\[([^\]|]+)\|([^\]]+)\]', r'\2', text)
    text = re.sub(r'\[([^\]]+)\]', r'\1', text)
    return html.unescape(re.sub(r'<[^>]*>', '', text)).replace('\\n', '; ').replace('\n', '; ').replace('|', '\\|').strip()


def label(bundle: dict, index) -> str:
    if index is None:
        return ''
    return clean(bundle['lang'][index])


def entries(bundle: dict, key: str) -> list[dict]:
    return bundle['data'][key]['entries']


def index(bundle: dict, key: str) -> dict[str, dict]:
    return {str(entry['id']): entry for entry in entries(bundle, key)}


def _normalize(text: str) -> str:
    text = re.sub(r'[_()/\-]', ' ', text.casefold())
    text = text.replace('strength', 'str').replace('dexterity', 'dex').replace('intelligence', 'int')
    return ' '.join(text.split())


def classes(bundle: dict, query: str = '') -> list[dict]:
    available = [entry for entry in entries(bundle, 'classes') if not entry.get('legacy')]
    q = _normalize(query)
    if not q:
        return available
    # Exact class names take precedence: bow must not silently include crossbow.
    exact = [entry for entry in available if _normalize(label(bundle, entry['label'])).rstrip('s') == q.rstrip('s')]
    if exact:
        return exact
    base_classes = {str(item['class']) for item in entries(bundle, 'items')
                    if _normalize(label(bundle, item['label'])) == q}
    if base_classes:
        return [entry for entry in available if str(entry['id']) in base_classes]
    return [entry for entry in available if all(part in _normalize(label(bundle, entry['label'])) for part in q.split())]


def mod_text(bundle: dict, mod: dict) -> str:
    # CoE's buildModName deduplicates labels, then applyValuesToMod fills
    # placeholders in stat order. Flat damage has two stats sharing one label.
    texts = {}
    for stat in mod.get('stats', []):
        texts.setdefault(stat['label'], label(bundle, stat['label']))
    text = '; '.join(texts.values())
    last_value = None
    for stat in mod.get('stats', []):
        bounds = stat.get('range')
        if isinstance(bounds, list) and stat.get('values') is not False:
            lo, hi = bounds
            last_value = str(lo) if lo == hi else f'{lo}–{hi}'
            text = text.replace('#', last_value, 1)
    if last_value is not None:
        text = text.replace('#', last_value)
    lines = [text] if text else []
    if mod.get('desc') is not None:
        lines.append(label(bundle, mod['desc']))
    return '; '.join(lines) or label(bundle, mod.get('label')) or mod['key']


def pool_name(bundle: dict, group: dict) -> str:
    influence = group.get('influence', 6)
    # gameConstants.influenceLabels in the verified package_poe2.js.
    names = {6: 'ordinary', 1000: 'Desecrated', 1001: 'Essence', 1002: 'Chronomancy',
             1003: 'Soul', 1004: 'Berserking', 1005: 'Marksman', 1006: 'Decay',
             1007: 'Destruction', 1008: 'Emotions', 1009: 'Genesis Tree', 1010: 'Otherworldly'}
    name = names.get(influence, f'pool {influence}')
    return name if influence == 6 else f'{name} (method-specific)'


def _mod_rows(bundle: dict, query: str, item_class: str):
    mods = index(bundle, 'mods')
    groups = index(bundle, 'modgroups')
    for cls in classes(bundle, item_class):
        weights = bundle['data']['classmods'].get(str(cls['id']), {})
        for mid, weight in weights.items():
            if float(weight) <= 0:
                continue
            mod = mods[str(mid)]
            group = groups[str(mod['group'])]
            text = mod_text(bundle, mod)
            if query.casefold() in (text + ' ' + mod['key'] + ' ' + label(bundle, mod.get('label'))).casefold():
                yield cls, mod, group, weight, text


def search_craft_mods(query: str, item_class: str = '') -> str:
    bundle = load_bundle()
    rows: dict[str, tuple[dict, dict, str, set[str]]] = {}
    for cls, mod, group, weight, text in _mod_rows(bundle, query, item_class):
        mid = str(mod['id'])
        if mid not in rows:
            rows[mid] = (mod, group, text, set())
        rows[mid][3].add(label(bundle, cls['label']))
    lines = [f"## PoE2 crafting mods: {query}", f'{len(rows)} matching modifiers.']
    for mod, group, text, class_names in list(rows.values())[:100]:
        kind = bundle['data']['enums']['types'].get(str(group['type']), str(group['type']))
        lines.append(f"- **{mod['key']}**: {text} | {kind} | min iLvl {mod['minlvl']} | "
                     f"{pool_name(bundle, group)} | {', '.join(sorted(class_names))}")
    if len(rows) > 100:
        lines.append('Showing first 100; narrow query or item_class.')
    if not rows:
        lines.append('No matching rollable modifiers for the requested class and query.')
    lines.append(source_note(bundle))
    return '\n'.join(lines)


def get_craft_tiers(base_type: str, query: str) -> str:
    bundle = load_bundle()
    if not base_type.strip() or not classes(bundle, base_type):
        raise ValueError(f'Unknown PoE2 item base or class: {base_type!r}; use get_craft_base_items')
    wanted = {(cls['id'], group['id'])
              for cls, mod, group, weight, text in _mod_rows(bundle, query, base_type)}
    grouped: dict[tuple, list] = {}
    for row in _mod_rows(bundle, '', base_type):
        cls, mod, group, weight, text = row
        if (cls['id'], group['id']) in wanted:
            grouped.setdefault((cls['id'], group['id']), []).append(row)
    lines = [f'## PoE2 crafting tiers: {query} on {base_type}',
             'Weights are estimated by Craft of Exile; they are not measured in-game probabilities.',
             'Tiers are ranked within each class/mod group by minimum level, then source power, highest first.']
    for rows in grouped.values():
        rows.sort(key=lambda row: (row[1]['minlvl'], row[1].get('power') or 0), reverse=True)
        cls, mod, group, _, _ = rows[0]
        kind = bundle['data']['enums']['types'].get(str(group['type']), str(group['type']))
        lines += [f"### {label(bundle, cls['label'])} | {kind} | group {group['id']} | {pool_name(bundle, group)}",
                  '| Tier | Min iLvl | Max iLvl | Modifier / values | Source weight | ID |',
                  '|------|----------|----------|-------------------|---------------|----|']
        for tier, (_, mod, _, weight, text) in enumerate(rows, 1):
            if group.get('influence') in {1000, 1009, 1010}:
                weight = f'unknown (source marker {weight})'
            lines.append(f"| T{tier} | {mod['minlvl']} | {mod.get('maxlvl', '?')} | {text} | {weight} | {mod['key']} |")
    if not grouped:
        lines.append('No matching rollable tiers. Check the item class and modifier query.')
    lines.append(source_note(bundle))
    return '\n'.join(lines)


def get_fossil_info(fossil_name: str) -> str:
    return ('Unsupported in PoE2: the verified Craft of Exile PoE2 dataset has no fossil affinity system. '
            'No one-to-one fossil replacement is defined. Use get_essence_mods for actual essence outcomes; '
            'essences and omens have different mechanics.\nSource: ' + SOURCE)


def get_essence_mods(essence_name: str, item_type: str = '') -> str:
    bundle = load_bundle()
    essence_data = bundle['data']['essences']
    mods = index(bundle, 'mods')
    found = [e for e in essence_data['entries'] if essence_name.casefold() in label(bundle, e['label']).casefold()]
    lines = ['## PoE2 essence modifier outcomes',
             'Multiple listed modifiers for a class are alternative outcomes, not simultaneous guarantees.']
    for essence in found:
        lines.append('### ' + label(bundle, essence['label']))
        matched = False
        for cls in classes(bundle, item_type):
            # basemods uses CoE class IDs; byessences uses the game's enum IDs.
            mapping = dict(essence_data['basemods'].get(str(cls['id']), {}))
            mapping.update(essence_data.get('classmods', {}).get(str(cls['class']), {}))
            ids = [mid for mid, eid in mapping.items() if eid == essence['id']]
            if not ids:
                continue
            matched = True
            for mid in ids:
                mod = mods[str(mid)]
                lines.append(f"- **{label(bundle, cls['label'])}**: {mod_text(bundle, mod)} ({mod['key']})")
        if not matched:
            lines.append('No mapped modifier outcome for this item class in this source. This does not establish that the essence has no effect.')
    if not found:
        lines.append('No matching PoE2 essence. Search the current essence name, e.g. Abrasion or the Mind.')
    lines.append(source_note(bundle))
    return '\n'.join(lines)


def get_craft_base_items(query: str = '', item_class: str = '') -> str:
    bundle = load_bundle()
    selected = {str(c['id']): c for c in classes(bundle, item_class)}
    matches = [item for item in entries(bundle, 'items')
               if str(item['class']) in selected and query.casefold() in label(bundle, item['label']).casefold()]
    matches.sort(key=lambda item: label(bundle, item['label']))
    lines = ['## PoE2 Craft of Exile base items', f'{len(matches)} matching items.',
             '| Base | Item class | Drop level |', '|------|------------|------------|']
    for item in matches[:200]:
        lines.append(f"| {label(bundle, item['label'])} | {label(bundle, selected[str(item['class'])]['label'])} | {item.get('drop', '?')} |")
    if len(matches) > 200:
        lines.append('Showing first 200; narrow query or item_class.')
    lines.append(source_note(bundle))
    return '\n'.join(lines)

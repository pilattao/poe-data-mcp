"""Hand-authored fixtures follow CoE beta's observed PoE2 schema (2026-09-15)."""
import json
import time

import httpx
import pytest

from poe_data_mcp.sources.crafting import craftofexile as tools


@pytest.fixture
def bundle():
    # IDs deliberately differ from localization indices and enum class indices.
    return {
        'game': 'poe2', 'source': 'https://beta.craftofexile.com/?game=poe2',
        'patch': '4.5.5.1.5', 'checked_at': time.time(), 'fetched_at': time.time(),
        'files': {'data': 'https://beta.craftofexile.com/json/poe2/4.5.5.1.5/data.json?v=1',
                  'lang': 'https://beta.craftofexile.com/json/poe2/4.5.5.1.5/localization/english.json?v=1'},
        'lang': ['Crossbows', 'Rings', 'Test Crossbow', 'Test Ring', 'Fleet',
                 '#% increased Attack Speed', 'Lesser Essence of Test',
                 'Test Tablet', 'Area contains # additional Encounters'],
        'data': {
            'classes': {'entries': [{'id': 58, 'label': 0, 'class': 80, 'legacy': False},
                                    {'id': 33, 'label': 1, 'class': 5, 'legacy': False},
                                    {'id': 83, 'label': 7, 'class': 107, 'legacy': False}]},
            'items': {'entries': [{'id': 2204, 'label': 2, 'class': 58, 'drop': 4},
                                 {'id': 7, 'label': 3, 'class': 33, 'drop': 2},
                                 {'id': 4396, 'label': 7, 'class': 83, 'drop': 65, 'implicits': [99]}],
                      'maps': {}, 'descriptions': {}},
            'mods': {'entries': [
                {'id': 10, 'key': 'AttackSpeed1', 'label': 4, 'group': 9, 'minlvl': 1,
                 'maxlvl': 100, 'power': 5, 'stats': [{'label': 5, 'range': [4, 6]}]},
                {'id': 11, 'key': 'AttackSpeed2', 'label': 4, 'group': 9, 'minlvl': 20,
                 'maxlvl': 100, 'power': 10, 'stats': [{'label': 5, 'range': [9, 11]}]},
                {'id': 12, 'key': 'AttackSpeedDisabled', 'label': 4, 'group': 9, 'minlvl': 80,
                 'maxlvl': 100, 'power': 30, 'stats': [{'label': 5, 'range': [29, 31]}]},
                {'id': 99, 'key': 'TabletEffect', 'label': None, 'group': 10, 'minlvl': 1,
                 'maxlvl': 100, 'power': 2, 'stats': [{'label': 8, 'range': [2, 2]}]},
            ]},
            'modgroups': {'entries': [{'id': 9, 'type': 2, 'influence': 6, 'families': [1]},
                                      {'id': 10, 'type': 3, 'influence': 6, 'families': [2]}]},
            'classmods': {'58': {'10': 1000, '11': 500, '12': 0}, '33': {}, '83': {}},
            'essences': {'entries': [{'id': 0, 'item': 87, 'label': 6, 'type': 0}],
                         'basemods': {'58': {'11': 0}}, 'classmods': {}, 'byessences': {'0': {'80': [11]}}},
            'enums': {'types': {'1': 'PREFIX', '2': 'SUFFIX', '3': 'UNIQUE'},
                      'classes': [''] * 108},
        },
    }


@pytest.fixture
def cached(tmp_path, monkeypatch, bundle):
    monkeypatch.setenv('POE_GAME', 'poe2')
    monkeypatch.setenv('POE_DATA_MCP_CACHE_DIR', str(tmp_path))
    p = tmp_path / 'poe2' / 'bundle.json'
    p.parent.mkdir()
    p.write_text(json.dumps(bundle))
    return p


def test_default_game_does_not_read_old_poe1_cache(cached, monkeypatch):
    monkeypatch.delenv('POE_GAME')
    result = tools.get_craft_base_items(item_class='crossbow')
    assert 'Test Crossbow' in result
    assert 'Test Ring' not in result
    assert 'PoE2' in result


def test_mod_search_joins_classes_and_localized_stats(cached):
    result = tools.search_craft_mods('attack speed', 'crossbow')
    assert 'AttackSpeed1' in result
    assert 'AttackSpeedDisabled' not in result
    assert 'AttackSpeed1' not in tools.search_craft_mods('attack speed', 'ring')


def test_tiers_use_class_weights_ranges_and_skip_zero(cached):
    result = tools.get_craft_tiers('crossbow', 'attack speed')
    assert '9–11' in result and '500' in result and '20' in result
    assert '4–6' in result and '1000' in result
    assert '29–31' not in result
    assert 'estimated' in result.lower()


def test_essences_join_base_class_ids_not_enum_ids(cached):
    result = tools.get_essence_mods('test', 'crossbow')
    assert '9–11' in result and 'Attack Speed' in result
    assert '9–11' not in tools.get_essence_mods('test', 'ring')


def test_fossils_are_explicit_gap_without_fetch(monkeypatch):
    monkeypatch.setenv('POE_GAME', 'poe2')
    assert 'unsupported' in tools.get_fossil_info('pristine').lower()
    assert 'PoE2' in tools.get_fossil_info('pristine')


@pytest.mark.parametrize('call', [lambda: tools.craftofexile_cache_status(),
    lambda: tools.update_craftofexile_cache(), lambda: tools.search_craft_mods('life'),
    lambda: tools.get_craft_tiers('ring', 'life'), lambda: tools.get_essence_mods('test'),
    lambda: tools.get_fossil_info('test'), lambda: tools.get_craft_base_items()])
def test_unsupported_game_never_falls_back(monkeypatch, call):
    monkeypatch.setenv('POE_GAME', 'other')
    with pytest.raises(ValueError, match='POE_GAME'):
        call()


def test_update_discovers_patch_urls_and_preserves_atomic_bundle(cached, monkeypatch, bundle):
    from poe_data_mcp.sources.crafting import poe2
    page = '<script src="json/poe2/4.5.5.1.6/data.json?v=2"></script>' \
           '<script src="json/poe2/4.5.5.1.6/localization/english.json?v=2"></script>'
    urls = []
    def request(url, **kwargs):
        urls.append(url)
        if 'game=poe2' in url: text = page
        elif '/localization/' in url: text = 'coelang=' + json.dumps(bundle['lang']) + ';'
        else: text = 'coedata=' + json.dumps(bundle['data']) + ';'
        return httpx.Response(200, text=text, request=httpx.Request('GET', url))
    monkeypatch.setattr(poe2.httpx, 'get', request)
    monkeypatch.setattr(poe2, 'DOWNLOAD_DELAY', 0)
    result = tools.update_craftofexile_cache(force=True)
    assert '4.5.5.1.6' in result
    assert all('poe2' in url for url in urls)
    saved = cached.read_text()
    def broken(url, **kwargs):
        return httpx.Response(200, text=page if 'game=' in url else '<html>blocked</html>',
                              request=httpx.Request('GET', url))
    monkeypatch.setattr(poe2.httpx, 'get', broken)
    result = tools.update_craftofexile_cache(force=True)
    assert 'error' in result.lower()
    assert cached.read_text() == saved


def test_poe1_page_cannot_replace_poe2_snapshot(cached, monkeypatch):
    from poe_data_mcp.sources.crafting import poe2
    before = cached.read_bytes()
    monkeypatch.setattr(poe2, 'DOWNLOAD_DELAY', 0)
    monkeypatch.setattr(poe2.httpx, 'get', lambda url, **kw: httpx.Response(200,
        text='<script src="json/poe1/3.29/data.json"></script>', request=httpx.Request('GET', url)))
    assert 'error' in tools.update_craftofexile_cache(force=True).lower()
    assert cached.read_bytes() == before


def test_essence_combines_enum_class_and_special_base_overrides(cached, bundle):
    bundle['data']['essences']['basemods'] = {'58': {}}
    bundle['data']['essences']['classmods'] = {'80': {'11': 0}}
    cached.write_text(json.dumps(bundle))
    assert '9–11' in tools.get_essence_mods('test', 'crossbow')
    assert '9–11' not in tools.get_essence_mods('test', 'ring')


def test_multistat_ranges_fill_distinct_placeholders_and_non_numeric_effects(cached, bundle):
    bundle['lang'] += ['Adds # to # Fire Damage', 'Cannot be Stunned']
    mod = bundle['data']['mods']['entries'][0]
    mod['stats'] = [{'label': 9, 'range': [2, 4]}, {'label': 9, 'range': [7, 9]},
                    {'label': 10, 'range': False}]
    cached.write_text(json.dumps(bundle))
    result = tools.search_craft_mods('fire', 'crossbow')
    assert 'Adds 2–4 to 7–9 Fire Damage' in result
    assert result.count('Fire Damage') == 1
    assert 'Cannot be Stunned' in result


def test_source_rate_limit_is_honored_even_for_forced_refresh(cached, monkeypatch):
    from poe_data_mcp.sources.crafting import poe2
    requests = []
    def limited(url, **kwargs):
        requests.append(url)
        return httpx.Response(429, headers={'Retry-After': '120'}, request=httpx.Request('GET', url))
    monkeypatch.setattr(poe2, '_retry_at', 0)
    monkeypatch.setattr(poe2, 'DOWNLOAD_DELAY', 0)
    monkeypatch.setattr(poe2.httpx, 'get', limited)
    before = cached.read_bytes()
    assert 'error' in tools.update_craftofexile_cache(force=True).lower()
    assert 'rate limit' in tools.update_craftofexile_cache(force=True).lower()
    assert len(requests) == 1
    assert cached.read_bytes() == before
    assert 'previous snapshot' in tools.search_craft_mods('attack speed', 'crossbow').lower()


def test_patch_mismatch_cannot_be_used_as_poe2_cache(cached, bundle, monkeypatch):
    from poe_data_mcp.sources.crafting import poe2
    bundle['files']['lang'] = 'https://beta.craftofexile.com/json/poe1/3.29/localization/english.json'
    cached.write_text(json.dumps(bundle))
    monkeypatch.setattr(poe2, 'DOWNLOAD_DELAY', 0)
    monkeypatch.setattr(poe2.httpx, 'get', lambda url, **kw: httpx.Response(503, request=httpx.Request('GET', url)))
    monkeypatch.setattr(poe2, '_retry_at', 0)
    with pytest.raises(RuntimeError, match='unavailable'):
        tools.get_craft_base_items()


def test_value_specific_tier_query_keeps_group_ranking(cached):
    result = tools.get_craft_tiers('crossbow', 'AttackSpeed1')
    assert '| T1 | 20 |' in result
    assert '| T2 | 1 |' in result


@pytest.mark.parametrize('broken', ['missing_essence_mapping', 'missing_class_reference', 'invalid_stat_label'])
def test_invalid_source_graph_never_replaces_valid_snapshot(cached, bundle, monkeypatch, broken):
    from poe_data_mcp.sources.crafting import poe2
    if broken == 'missing_essence_mapping':
        del bundle['data']['essences']['basemods']
    elif broken == 'missing_class_reference':
        bundle['data']['items']['entries'][0]['class'] = 99999
    else:
        bundle['data']['mods']['entries'][0]['stats'][0]['label'] = 99999
    page='<script src="json/poe2/4.5.5.1.5/data.json?v=1"></script><script src="json/poe2/4.5.5.1.5/localization/english.json?v=1"></script>'
    def request(url, **kw):
        text = page if 'game=' in url else ('coelang='+json.dumps(bundle['lang']) if '/localization/' in url else 'coedata='+json.dumps(bundle['data']))
        return httpx.Response(200,text=text,request=httpx.Request('GET',url))
    monkeypatch.setattr(poe2.httpx,'get',request)
    monkeypatch.setattr(poe2,'DOWNLOAD_DELAY',0)
    before = cached.read_bytes()
    assert 'error' in tools.update_craftofexile_cache(force=True).lower()
    assert cached.read_bytes() == before

def test_tiers_resolve_exact_base_names_to_their_native_class(cached):
    result=tools.get_craft_tiers('Test Crossbow','attack speed')
    assert 'AttackSpeed1' in result and 'AttackSpeed2' in result
    assert 'No matching' not in result

def test_unknown_base_is_not_reported_as_an_empty_mod_pool(cached):
    with pytest.raises(ValueError,match='Unknown'):
        tools.get_craft_tiers('Missing Base','life')

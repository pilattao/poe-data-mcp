import pytest
from poe_data_mcp.sources.env import env_search, env_detail
from poe_data_mcp.sources.poe2_data import LocalPoBData


@pytest.fixture
def local_data(tmp_path, monkeypatch):
    (tmp_path / 'Data/Bases').mkdir(parents=True)
    (tmp_path / 'TreeData/0_5').mkdir(parents=True)
    (tmp_path / 'Data/Gems.lua').write_text('return {}')
    (tmp_path / 'Data/WorldAreas.lua').write_text('''local areas=...
areas.MapTest={name="Test Coast (Map)",baseName="Test Coast",isMap=true,level=65,act=10,
 tags={"map","beach"},bossVarieties={"Test Captain"},monsterVarieties={"Test Crab"}}
areas.Campaign={name="Test Coast (Act 1)",baseName="Test Coast",isMap=false,level=3,act=1,
 tags={"beach"},monsterVarieties={"Test Crab"}}
areas.Hideout={name="Hidden",isMap=false,isHideout=true,level=0}
''')
    monkeypatch.setenv('POE_GAME', 'poe2')
    monkeypatch.setenv('POB_INSTALL_DIR', str(tmp_path))
    return LocalPoBData(tmp_path)


def test_maps_use_native_world_areas_and_bosses(local_data):
    result = env_search('Test Captain', 'maps')
    assert 'Test Coast' in result and 'Test Captain' in result
    assert '(Act 1)' not in result
    assert 'local PoB2' in result


def test_map_detail_keeps_area_level_separate_from_tier(local_data):
    result = env_detail('Test Coast (Map)')
    assert '65' in result and 'Test Crab' in result and 'Test Captain' in result
    assert 'Tier: 65' not in result
    assert 'Connected maps' not in result


def test_duplicate_display_names_require_selection(local_data):
    result = env_detail('Test Coast')
    assert '(Map)' in result and '(Act 1)' in result
    assert 'multiple' in result.lower()


def test_campaign_areas_and_scarab_gap(local_data):
    assert '(Act 1)' in env_search('Test Coast', 'areas')
    result = env_search('breach', 'scarabs')
    assert 'unsupported' in result.lower() and 'tablets' in result.lower()


@pytest.mark.parametrize('call',[lambda:env_search('x'), lambda:env_detail('x')])
def test_env_rejects_unsupported_selection(monkeypatch, call):
    monkeypatch.setenv('POE_GAME', 'wrong')
    with pytest.raises(ValueError, match='POE_GAME'): call()


# Reuse the hand-authored source fixture; no live network traffic in unit tests.
from test_poe2_crafting import bundle, cached


def test_tablet_search_and_details_render_real_implicit_relationships(local_data, cached):
    result = env_search('encounters', 'tablets')
    assert 'Test Tablet' in result and '2 additional Encounters' in result
    assert '2 additional Encounters' in env_detail('Test Tablet')
    assert 'PoE2' in result


def test_waystone_tier_is_taken_from_item_mapping(local_data, cached, bundle):
    import json
    bundle['lang'][7] = 'Waystone (Tier 15)'
    bundle['data']['items']['maps'] = {'4396': {'tier': 15, 'up': 4397, 'down': 4395}}
    cached.write_text(json.dumps(bundle))
    assert 'Waystone tier 15' in env_search('Tier 15', 'waystones')
    assert 'Waystone tier: 15' in env_detail('4396')


def test_empty_remote_categories_report_coverage_gap(local_data, cached, bundle):
    import json
    bundle['lang'][7] = 'Ordinary Item'
    cached.write_text(json.dumps(bundle))
    result = env_search('test', 'tablets')
    assert 'unavailable' in result and 'schema or coverage' in result

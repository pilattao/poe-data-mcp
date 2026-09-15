import pytest
from poe_data_mcp.sources.mods.item_mods import search_mods
from test_poe2_env import local_data


@pytest.fixture
def mod_data(local_data):
    root = local_data.root
    (root/'Data/Bases/test.lua').write_text('''local bases=...
bases["Test Crossbow"]={type="Crossbow",tags={crossbow=true,weapon=true,default=true},req={}}
bases["Test Bow"]={type="Bow",tags={bow=true,weapon=true,default=true},req={}}
bases["Test Charm"]={type="Charm",tags={charm=true,default=true},req={}}
bases["Test Helmet"]={type="Helmet",tags={helmet=true,str_armour=true,default=true},req={}}
''')
    (root/'Data/ModItem.lua').write_text('''return {
Speed={type="Suffix",affix="of Speed",[1]="10% increased Reload Speed",level=20,group="Speed",
 weightKey={"crossbow","default"},weightVal={1,0},modTags={"speed"}},
Excluded={type="Prefix",[1]="Excluded modifier",level=1,group="Exclude",
 weightKey={"crossbow","weapon","default"},weightVal={0,1,0}},
Helmet={type="Prefix",[1]="20 to maximum Life",level=5,group="Life",
 weightKey={"helmet","default"},weightVal={1,0}}
}''')
    (root/'Data/ModCharm.lua').write_text('''return {Charges={type="Suffix",[1]="25% increased Charges",level=1,
 group="Charges",weightKey={"default"},weightVal={1}}}''')
    return local_data


def test_crossbow_mods_respect_first_matching_spawn_tag(mod_data):
    result = search_mods('crossbow', '')
    assert 'Reload Speed' in result
    assert 'Excluded modifier' not in result and 'maximum Life' not in result
    assert 'Reload Speed' not in search_mods('bow', '')
    assert 'probabilities' in result.lower()


def test_charm_uses_charm_domain_not_generic_items(mod_data):
    result = search_mods('charms', 'charges')
    assert '25% increased Charges' in result
    assert 'Data/ModCharm.lua' in result


def test_attribute_aliases_match_actual_base_tags(mod_data):
    assert 'maximum Life' in search_mods('Helmets_strength', 'life')
    assert 'maximum Life' not in search_mods('helmets dex', 'life')


def test_mods_unsupported_game_raises(monkeypatch):
    monkeypatch.setenv('POE_GAME', 'poe3')
    with pytest.raises(ValueError, match='POE_GAME'): search_mods('ring')


def test_explicit_poe1_selection_uses_poe1_endpoint_after_poe2_import(monkeypatch):
    import httpx, json
    from poe_data_mcp.sources import common
    monkeypatch.setattr(common, 'BASE_URL', 'https://poe2db.tw/us')
    monkeypatch.setenv('POE_GAME', 'poe1')
    urls=[]
    payload={'normal':[{'Name':'Test','Level':'1','ModGenerationTypeID':'1','str':'Legacy test life','ModFamilyList':['Life']}], 'corrupted':[]}
    def response(url, **kwargs):
        urls.append(url)
        return httpx.Response(200,text='new ModsView('+json.dumps(payload)+');}', request=httpx.Request('GET',url))
    monkeypatch.setattr(httpx,'get',response)
    result=search_mods('ring','life')
    assert urls == ['https://poedb.tw/us/Rings']
    assert 'Legacy test life' in result

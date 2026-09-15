import pytest
from pathlib import Path
from poe_data_mcp.sources.poe2_data import LocalPoBData

@pytest.fixture
def native(tmp_path):
    (tmp_path/'Data/Bases').mkdir(parents=True)
    (tmp_path/'Data/Uniques').mkdir()
    (tmp_path/'TreeData/0_5').mkdir(parents=True)
    (tmp_path/'Data/Gems.lua').write_text('return {["gem:spark"]={name="Spark",grantedEffectId="SparkPlayer",tags={spell=true},naturalMaxLevel=20}}')
    (tmp_path/'Data/Bases/test.lua').write_text('local bases=...; bases["Test Charm"]={type="Charm",tags={charm=true},req={level=2}}')
    (tmp_path/'Data/Uniques/test.lua').write_text('return { [[Test Choir\nTest Amulet\nVariant: Old\nVariant: Current\nImplicits: 1\n+10 Dexterity\n{variant:1}Old modifier\n{variant:2}Current modifier\nCommon modifier]] }')
    (tmp_path/'TreeData/0_5/tree.lua').write_text('return {nodes={["12"]={name="Test Notable",stats={[1]="5% increased Speed"},connections={[1]={id=14}}},["14"]={name="Small"}}}')
    return LocalPoBData(tmp_path)

def test_installed_native_tables_and_uniques(native):
    assert native.gems()[0]['name']=='Spark'
    assert native.bases()[0]['name']=='Test Charm'
    item=native.uniques()[0]
    assert item['variants'][1]['name']=='Current'
    assert item['variants'][1]['modifiers']==['+10 Dexterity','Current modifier','Common modifier']
    assert 'Old modifier' not in item['variants'][1]['modifiers']
    assert native.tree()['nodes']['12']['stats'][1]=='5% increased Speed'

def test_source_layout_and_source_bound_cache(native):
    assert native.metadata('Data/Gems.lua')['game']=='poe2'
    p=native.root/'Data/Gems.lua'
    p.write_text('return {["gem:other"]={name="Changed"}}')
    assert native.gems()[0]['name']=='Changed'

def test_refuses_poe1_tree_and_path_escape(native):
    with pytest.raises(ValueError): native.tree('3_29')
    with pytest.raises(ValueError): native.load('../foreign.lua')

def test_missing_install_is_explicit(tmp_path):
    with pytest.raises(FileNotFoundError,match='PoB2'): LocalPoBData(tmp_path)

def test_unique_without_implicit_marker_is_not_dropped(native):
    (native.root/'Data/Uniques/plain.lua').write_text('return { [[No Implicit Armour\nTest Body\nVariant: Old\nVariant: Current\n{variant:1}+10 Life\n{variant:2}+20 Life]] }')
    item=next(x for x in native.uniques() if x['name']=='No Implicit Armour')
    assert item['implicit_count']==0
    assert item['variants'][1]['modifiers']==['+20 Life']

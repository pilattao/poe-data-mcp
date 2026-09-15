import base64,zlib
import xml.etree.ElementTree as ET
import pytest
from poe_data_mcp.sources.player.pob import _decode_pob,_parse_items,_parse_build_info,_parse_passives

def test_active_item_set_only_and_charms():
    root=ET.fromstring('''<PathOfBuilding2><Items activeItemSet="2"><Item id="1">Rarity: RARE\nInactive\nHat\nImplicits: 0\n+10 Life</Item><Item id="2">Rarity: MAGIC\nActive Charm\nImplicits: 1\nUsed when Frozen</Item><ItemSet id="2"><Slot name="Charm 1" itemId="2"/></ItemSet><ItemSet id="1"><Slot name="Charm 1" itemId="1"/></ItemSet></Items></PathOfBuilding2>''')
    assert _parse_items(root)[0]['name']=='Active Charm'

def test_decode_rejects_wrong_game_and_unrelated_xml():
    for xml in ['<Other/>','<PathOfBuilding><Build/></PathOfBuilding>']:
        code=base64.urlsafe_b64encode(zlib.compress(xml.encode())).decode()
        with pytest.raises(ValueError):_decode_pob(code)

def test_poe2_resources_and_no_pantheon():
    root=ET.fromstring('<PathOfBuilding2><Build bandit="None" pantheonMajorGod="TheBrineKing"><PlayerStat stat="Spirit" value="144"/><PlayerStat stat="SpiritUnreserved" value="4"/></Build></PathOfBuilding2>')
    b=_parse_build_info(root)
    assert b['stats']['Spirit']==144 and b['stats']['SpiritUnreserved']==4
    assert not b['pantheon_major'] and not b['bandit']

def test_passive_lookup_uses_selected_tree_version(monkeypatch):
    from poe_data_mcp.sources.player import passives
    called=[]
    def tree(v=''):
        called.append(v);return {'by_id':{'12':{'_type':'notable','name':'Current Notable'}}}
    monkeypatch.setattr(passives,'_load_tree',tree)
    r=ET.fromstring('<PathOfBuilding2><Tree activeSpec="2"><Spec treeVersion="0_4" nodes="1"/><Spec treeVersion="0_5" nodes="12"/></Tree></PathOfBuilding2>')
    assert _parse_passives(r)['notables']==['Current Notable']
    assert called==['0_5']

def test_skill_groups_do_not_treat_equipment_runes_as_gem_links():
    import json
    from poe_data_mcp.sources.player.pob import parse_pob_skill_groups
    xml='''<PathOfBuilding2><Build/><Items activeItemSet="1"><Item id="1">Rarity: RARE\nStaff\nTest Staff\nSockets: S S\nImplicits: 0</Item><ItemSet id="1"><Slot name="Weapon 1" itemId="1"/></ItemSet></Items><Skills activeSkillSet="1"><SkillSet id="1"><Skill slot="Weapon 1"><Gem nameSpec="Spark"/></Skill></SkillSet></Skills></PathOfBuilding2>'''
    code=base64.urlsafe_b64encode(zlib.compress(xml.encode())).decode()
    result=json.loads(parse_pob_skill_groups(code))
    assert 'item_sockets' not in result['groups'][0]
    assert result['equipment_sockets']=={'Weapon 1':'S S'}
    assert result['game']=='poe2'

def test_explicit_missing_skill_set_does_not_silently_use_active():
    from poe_data_mcp.sources.player.pob import parse_pob_skill_groups
    xml='<PathOfBuilding2><Build/><Skills activeSkillSet="1"><SkillSet id="1" title="Default"/></Skills></PathOfBuilding2>'
    code=base64.urlsafe_b64encode(zlib.compress(xml.encode())).decode()
    with pytest.raises(ValueError,match='skill set'):parse_pob_skill_groups(code,'Does not exist')

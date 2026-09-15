from bs4 import BeautifulSoup

from poe_data_mcp.sources.player import poe2


def test_gem_definitions_are_distinct_from_debug_popups_and_include_implicits(monkeypatch):
    # PoE2DB renders normal/debug popups per pane and a separate implicit table.
    html = '''<div class="tab-pane" id="SparkSkillGemSpark">
      <div class="newItemPopup GemPopup"><div class="itemName">Spark</div>Base Deals Lightning Damage. Cold-Infused Deals Cold Damage.</div>
      <div class="newItemPopup GemPopup"><div class="itemName">Spark</div>Base Deals Lightning Damage. base_is_projectile [1]</div>
      <div class="card"><h5 class="card-header">Level Effect /40</h5>
        <table><tr><th>Implicit</th></tr><tr><td>Projectile duration is 2 seconds</td></tr></table>
        <table><tr><th>Level</th><th>Mana</th></tr><tr><td>1</td><td>5</td></tr><tr><td>20</td><td>56</td></tr></table>
      </div></div>
      <div class="tab-pane" id="SparkSkillGemTriggered"><div class="newItemPopup GemPopup"><div class="itemName">Spark</div>Triggered skill</div></div>'''
    monkeypatch.setattr(poe2, 'fetch_page', lambda _: BeautifulSoup(html, 'html.parser'))
    result = poe2.get_gem_detail('Spark')
    assert result.count('## Spark (SparkSkillGemSpark)') == 1
    assert '## Spark (SparkSkillGemTriggered)' in result
    assert 'base_is_projectile' not in result
    assert 'Projectile duration is 2 seconds' in result
    assert '20 | 56' in result
    assert 'multiple modes' in result

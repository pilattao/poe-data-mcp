import pytest
from poe_data_mcp.sources.economy import poe2

def payload(primary='divine'):
    return {'core':{'primary':primary,'rates':{'exalted':410,'chaos':9.1},'items':[{'id':'divine','name':'Divine Orb'}]},'items':[{'id':'alch','name':'Orb of Alchemy'}],'lines':[{'id':'alch','primaryValue':.5,'volumePrimaryValue':100}]}

def test_primary_value_is_not_assumed_chaos():
    r=poe2.normalize(payload())[0]
    assert r['primary_currency']=='divine' and r['primary_value']==.5
    assert r['values']=={'divine':.5,'exalted':205,'chaos':4.55}
    assert 'listing_count' not in r

def test_source_error_cannot_be_empty_market(monkeypatch):
    monkeypatch.setattr(poe2,'_request_json',lambda *a,**k: {'error':'denied'})
    poe2._cache.clear()
    with pytest.raises(ValueError,match='schema'):poe2.fetch('Test','Currency')

def test_ambiguous_leagues_not_standard_fallback(monkeypatch):
    monkeypatch.delenv('POE_LEAGUE',raising=False)
    monkeypatch.setattr(poe2,'index',lambda:{'economyLeagues':[{'name':'A','url':'a','hardcore':False},{'name':'B','url':'b','hardcore':False}]})
    with pytest.raises(ValueError,match='explicit'):poe2.resolve_league('')
    assert poe2.resolve_league('b')=='B'

def test_preserves_primary_even_missing_conversion():
    d=payload('exalted');d['core']['rates']={}
    assert poe2.normalize(d)[0]['values']=={'exalted':.5}

def test_stash_primary_currency_and_variant_metadata():
    d=payload();d['lines']=[{'id':4,'name':'Choir of the Storm','primaryValue':2,'listingCount':4,'corrupted':True,'levelRequired':78}]
    r=poe2.normalize(d)[0]
    assert r['name']=='Choir of the Storm' and r['values']['divine']==2
    assert r['listing_count']==4 and r['corrupted'] is True and r['level_required']==78

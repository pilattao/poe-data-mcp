import json
import subprocess
import urllib.error
from pathlib import Path

import pytest

from poe_data_mcp.sources import reddit, youtube

VIDEO = 'https://www.youtube.com/watch?v=82CGiyshJ0c'
GUIDES = 'https://mobalytics.gg/poe-2/builds/example https://maxroll.gg/poe2/build-guides/example'


def test_youtube_description_is_public_only_and_preserves_poe2_links(monkeypatch):
    calls = []
    def run(cmd, timeout):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, 'Example' if '--get-title' in cmd else GUIDES, '')
    monkeypatch.setattr(youtube, '_run', run)
    result = youtube.fetch_youtube_description(VIDEO)
    assert '## Extracted Links' in result
    assert '**mobalytics:** https://mobalytics.gg/poe-2/' in result
    assert '**maxroll:** https://maxroll.gg/poe2/' in result
    for cmd in calls:
        for flag in ['--ignore-config', '--no-cache-dir', '--no-playlist', '--skip-download']:
            assert flag in cmd
        assert cmd[-2:] == ['--', VIDEO]
        assert '--extractor-args' not in cmd


def test_youtube_transcript_isolated_subtitle_only(monkeypatch):
    calls = []
    def run(cmd, timeout):
        calls.append(cmd)
        if '--write-auto-subs' in cmd:
            p = Path(cmd[cmd.index('-o') + 1] + '.en.json3')
            p.write_text(json.dumps({'events': [{'tStartMs': 2000, 'segs': [{'utf8': 'Synthetic caption'}]}]}))
        return subprocess.CompletedProcess(cmd, 0, 'Example', '')
    monkeypatch.setattr(youtube, '_run', run)
    result = youtube.fetch_youtube_transcript(VIDEO, True)
    assert 'Synthetic caption' in result
    assert '00:02' in result
    assert len(calls) == 3
    for cmd in calls:
        assert '--ignore-config' in cmd and '--skip-download' in cmd
        assert cmd[-2:] == ['--', VIDEO]


@pytest.mark.parametrize('url', ['--cookies=private', 'file:///etc/passwd', 'https://youtube.com.example/watch?v=abc', 'https://user:secret@youtube.com/watch?v=abc'])
def test_youtube_rejects_non_public_video_arguments(monkeypatch, url):
    monkeypatch.setattr(youtube, '_run', lambda *a, **k: pytest.fail('unexpected subprocess'))
    with pytest.raises(ValueError, match='YouTube'):
        youtube.fetch_youtube_description(url)


@pytest.mark.parametrize('url', [
    'https://old.reddit.com/r/PathOfExile2/comments/abc/topic.json?sort=top',
    'https://m.reddit.com/r/PathOfExile2/comments/abc/topic/?sort=top#comments',
])
def test_reddit_json_suffix_and_mobile_urls(url):
    assert reddit._to_json_url(url) == 'https://www.reddit.com/r/PathOfExile2/comments/abc/topic.json?sort=top'


def test_reddit_403_is_not_reported_as_retryable_rate_limit(monkeypatch):
    def blocked(*a, **k):
        raise urllib.error.HTTPError(VIDEO, 403, 'Forbidden', {}, None)
    monkeypatch.setattr(reddit.urllib.request, 'urlopen', blocked)
    result = reddit.fetch_reddit_post('https://www.reddit.com/r/PathOfExile2/comments/abc/')
    assert '403' in result
    assert 'try again' not in result and 'rate limiting' not in result


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'https://reddit.com.example/r/test/comments/abc', 'https://user:secret@reddit.com/r/test/comments/abc'])
def test_reddit_rejects_non_public_post_arguments(url):
    with pytest.raises(ValueError, match='Reddit'):
        reddit._to_json_url(url)


def test_reddit_extracts_poe2_guides():
    links = reddit._extract_links(GUIDES)
    assert set(links) == {'mobalytics', 'maxroll'}


def test_suite_info_documents_public_poe2_sources():
    from poe_data_mcp.server import poe_mcp_suite_info
    info = poe_mcp_suite_info()
    assert 'POESESSID' not in info
    assert 'POE_GAME=poe2' in info
    assert 'POB_INSTALL_DIR' in info
    assert 'POE_DATA_MCP_CACHE_DIR' in info


def test_passive_search_empty_input_is_not_a_broad_match(monkeypatch):
    from poe_data_mcp.sources.player import passives
    monkeypatch.setattr(passives, '_load_tree', lambda: pytest.fail('unexpected data load'))
    with pytest.raises(ValueError, match='query'):
        passives.search_passive('  ')


def test_passive_results_include_native_source(monkeypatch):
    from poe_data_mcp.sources.player import passives
    tree = {'version': '0_5', 'source': 'Source: installed PoB2 / TreeData/0_5/tree.lua',
            'all_nodes': [{'_type': 'keystone', 'name': 'Chaos Inoculation', 'stats': ['Maximum Life becomes 1']}], 'by_id': {}}
    monkeypatch.setattr(passives, '_load_tree', lambda: tree)
    assert tree['source'] in passives.search_passive('Chaos Inoculation')
    assert tree['source'] in passives.get_passive_detail('Chaos Inoculation')


@pytest.mark.parametrize('flavour', ['Give up everything', ['Give up', 'everything']])
def test_passive_flavour_accepts_native_string_and_legacy_list(monkeypatch, flavour):
    from poe_data_mcp.sources.player import passives
    monkeypatch.setattr(passives, '_load_tree', lambda: {'all_nodes': [
        {'_type': 'keystone', 'name': 'Chaos Inoculation', 'flavourText': flavour}]})
    result = passives.get_passive_detail('Chaos Inoculation')
    assert 'Give up everything' in result

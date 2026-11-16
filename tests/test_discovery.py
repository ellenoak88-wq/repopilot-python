import asyncio
import json

import httpx
import pytest

from repopilot.discovery import DiscoveryService, DiscoveryUnavailable, parse_trending
from repopilot.checklist import ChecklistService
from repopilot.server import DEMO, create_app
from repopilot.store import Store

ARTICLE = '''<article class="Box-row"><h2><a href="/demo/fieldnotes"><span>demo /</span>fieldnotes</a></h2>
<p>A &amp; B <b>notes</b><script>bad()</script></p><span itemprop="programmingLanguage">Python</span>
<a href="/demo/fieldnotes/stargazers"><svg><path/></svg>12,345</a><a href="/demo/fieldnotes/forks">1.2k</a>
<img src="x"><span class="d-inline-block float-sm-right">234 stars today</span></article>'''


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / 'discovery.db')
    yield value
    value.close()


def test_trending_parser_and_untrusted_links():
    items = parse_trending(ARTICLE + ARTICLE + ARTICLE.replace('/demo/fieldnotes', '//evil.example/repo'))
    assert len(items) == 1
    assert items[0] == dict(id='demo/fieldnotes', url='https://github.com/demo/fieldnotes', description='A & B notes',
                            language='Python', rank=1, stars=12345, forks=1200, periodStars=234)
    with pytest.raises(DiscoveryUnavailable):
        parse_trending('<html>Please sign in</html>')
    assert parse_trending('It looks like we don’t have any trending repositories') == []
    assert parse_trending(ARTICLE.replace('234 stars today', '789 stars this week'))[0]['periodStars'] == 789


@pytest.mark.asyncio
async def test_cache_refresh_stale_coalescing_and_imported_state(store, monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'test-token')
    clock, calls, failing = [10000], [], [False]
    async def upstream(request):
        calls.append(request)
        assert 'authorization' not in request.headers
        await asyncio.sleep(0.01)
        return httpx.Response(503 if failing[0] else 200, text=ARTICLE)
    service = DiscoveryService(store, transport=httpx.MockTransport(upstream), clock=lambda: clock[0])
    first, duplicate = await asyncio.gather(service.get(), service.get(refresh=True))
    assert len(calls) == 1 and not first['cached'] and duplicate['cached']
    assert not first['items'][0]['importedId']
    store.save_repository(DEMO['meta'], DEMO['files'])
    assert (await service.get())['items'][0]['importedId'] == 'demo/fieldnotes'
    clock[0] += 61
    assert not (await service.get(refresh=True))['cached'] and len(calls) == 2
    clock[0] += 3601
    failing[0] = True
    stale = await service.get()
    assert stale['stale'] and stale['warning'] and len(stale['items']) == 1
    assert (await service.get(refresh=True))['stale'] and len(calls) == 3
    # A new service instance reads persistent cache after restart.
    other = DiscoveryService(store, transport=httpx.MockTransport(upstream), clock=lambda: clock[0])
    assert (await other.get())['fetchedAt'] == stale['fetchedAt']
    clock[0] += 61
    failing[0] = False
    assert not (await service.get(refresh=True))['stale']


@pytest.mark.asyncio
async def test_popular_query_public_only_and_partial(store, monkeypatch):
    monkeypatch.setenv('GITHUB_TOKEN', 'test-token')
    def upstream(request):
        assert request.url.host == 'api.github.com'
        assert request.headers['authorization'] == 'Bearer test-token'
        assert request.url.params['sort'] == 'stars' and request.url.params['order'] == 'desc'
        assert 'language:C++' in request.url.params['q'] and 'topic:rag' in request.url.params['q']
        assert 'is:public' in request.url.params['q'] and 'pushed:>=' in request.url.params['q']
        item = dict(full_name='owner/public', private=False, stargazers_count=1234, forks_count=15, description='<img onerror=evil()>')
        return httpx.Response(200, json={'incomplete_results': True, 'items': [item, {**item, 'private': True}, item]})
    result = await DiscoveryService(store, transport=httpx.MockTransport(upstream)).get(source='popular', language='C++', topic='rag')
    assert result['partial'] and len(result['items']) == 1 and result['items'][0]['stars'] == 1234
    assert result['items'][0]['url'] == 'https://github.com/owner/public'


@pytest.mark.asyncio
async def test_error_cooldown_and_bad_payload(store):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(429)
    service = DiscoveryService(store, transport=httpx.MockTransport(upstream))
    for _ in range(2):
        with pytest.raises(DiscoveryUnavailable, match='限制'):
            await service.get()
    assert len(calls) == 1
    malformed = DiscoveryService(store, transport=httpx.MockTransport(lambda _: httpx.Response(200, json={'items': None})))
    with pytest.raises(DiscoveryUnavailable, match='格式'):
        await malformed.get(source='popular')
    oversized = DiscoveryService(store, transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'x'*3_000_001)))
    with pytest.raises(DiscoveryUnavailable, match='过大'):
        await oversized.get()
    assert store.db.execute('SELECT COUNT(*) FROM discovery_cache').fetchone()[0] == 0


@pytest.mark.asyncio
async def test_routes_validation_and_reuse_preserves_existing_data(store):
    repo = store.save_repository(DEMO['meta'], DEMO['files'])
    session = store.default_session(repo['id'])
    store.add_chat(repo['id'], {'question': 'saved', 'answer': 'answer'}, session['id'])
    plan = ChecklistService(store).generate(repo, None, 0)['plan']
    async def forbidden(*args):
        raise AssertionError('Existing repository must not be imported again')
    app = create_app(store=store, importer=forbidden, discovery_transport=httpx.MockTransport(lambda _: httpx.Response(200, text=ARTICLE)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:3211') as client:
        response = await client.get('/api/discover')
        assert response.status_code == 200 and response.json()['items'][0]['importedId'] == repo['id']
        for query in ('source=bad', 'language=../secret', 'topic=rag', 'refresh=yes', 'since=yearly'):
            assert (await client.get('/api/discover?' + query)).status_code == 400
        reused = await client.post('/api/import', json={'url': 'DEMO/FIELDNOTES', 'reuseExisting': True})
        assert reused.status_code == 200 and reused.json()['state'] == 'done'
        assert (await client.post('/api/import', json={'url': repo['id'], 'reuseExisting': 'yes'})).status_code == 400
        assert len(store.history(repo['id'], session['id'])) == 1
        assert ChecklistService(store).view(repo)['plan']['id'] == plan['id']
    broken = create_app(store=store, discovery_transport=httpx.MockTransport(lambda _: httpx.Response(502)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=broken), base_url='http://127.0.0.1:3211') as client:
        assert (await client.get('/api/discover?language=Rust')).status_code == 503


@pytest.mark.asyncio
async def test_reuse_imports_new_repository_through_existing_job(store):
    calls = []
    async def importer(value, progress):
        calls.append(value)
        return DEMO
    app = create_app(store=store, importer=importer)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1:3211') as client:
        response = await client.post('/api/import', json={'url': 'demo/fieldnotes', 'reuseExisting': True})
        assert response.status_code == 202
        await asyncio.sleep(0)
        job = (await client.get('/api/jobs/' + response.json()['id'])).json()
        assert job['state'] == 'done' and calls == ['demo/fieldnotes']
        assert store.get('demo/fieldnotes')['fileCount'] == len(DEMO['files'])

import asyncio
import json

import httpx
import pytest

from repopilot.agent import ask_agent
from repopilot.hybrid import HybridRetrieval, fuse, normalize
from repopilot.server import create_app
from repopilot.store import Store

FILES = [{'path': 'README.md', 'content': 'Launch the web server using python app.py'},
         {'path': 'src/calc.py', 'content': 'subtotal = price * quantity\ntax = subtotal * 0.05'}]
META = dict(id='test/hybrid', sha='first', name='hybrid', description='test', demo=True, language='Python')


class FakeEmbedder:
    identity = 'test:1'
    name = 'Test vectors'

    def __init__(self):
        self.document_calls, self.query_calls = 0, 0
        self.fail = False

    async def documents(self, texts):
        self.document_calls += 1
        return list(range(len(texts))), [[1., 0.] if 'Launch' in t else [0., 1.] for t in texts]

    async def query(self, text):
        self.query_calls += 1
        if self.fail:
            raise RuntimeError('backend failed')
        return [1., 0.]


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / 'hybrid.db')
    repo = store.save_repository(META, FILES)
    embedder = FakeEmbedder()
    service = HybridRetrieval(store, embedder)
    yield store, repo, embedder, service
    store.close()


def test_rrf_combines_ranks_not_score_scales_and_deduplicates():
    chunks = [dict(path=f'{i}.py', start=1, end=2, text=str(i), score=1e9) for i in range(3)]
    result = fuse(chunks[:2], [chunks[1], chunks[2]], 5)
    assert result[0]['path'] == '1.py' and result[0]['channels'] == ['bm25', 'vector']
    assert result[0]['score'] == pytest.approx(1/62 + 1/61)
    assert len(fuse([chunks[0], chunks[0]], [], 5)) == 1


@pytest.mark.parametrize('vector', [[], [0., 0.], [float('nan')], [float('inf')], [True], ['1']])
def test_invalid_embeddings(vector):
    with pytest.raises(ValueError):
        normalize(vector)


async def test_semantic_recall_persistence_and_bm25_opt_out(state):
    store, repo, embedder, service = state
    chunks = store.chunks(repo['id'])
    absent = await service.retrieve(repo, chunks, '怎样让服务可用')
    assert absent['actual'] == 'bm25' and absent['warning'] and not embedder.query_calls
    await service.build(repo)
    result = await service.retrieve(repo, chunks, '怎样让服务可用')
    assert result['actual'] == 'hybrid' and result['items'][0]['path'] == 'README.md'
    assert result['items'][0]['channels'] == ['vector']
    assert result['items'][0]['text'] == FILES[0]['content']
    await service.build(repo)
    assert embedder.document_calls == 1
    count = embedder.query_calls
    bm25 = await service.retrieve(repo, chunks, 'subtotal', mode='bm25')
    assert bm25['items'][0]['path'] == 'src/calc.py' and embedder.query_calls == count
    other = Store(store.db.execute('PRAGMA database_list').fetchone()[2])
    assert HybridRetrieval(other, FakeEmbedder()).status(repo)['state'] == 'ready'
    other.close()


async def test_stale_content_model_and_repository_isolation(state):
    store, repo, embedder, service = state
    await service.build(repo)
    other = store.save_repository({**META, 'id': 'other/repo'}, FILES)
    assert service.status(other)['state'] == 'missing'
    embedder.identity = 'test:2'
    assert service.status(repo)['state'] == 'stale'
    embedder.identity = 'test:1'
    changed = store.save_repository(META, [*FILES, {'path':'new.py','content':'new'}])
    assert service.status(changed)['state'] == 'stale'
    result = await service.retrieve(changed, store.chunks(repo['id']), 'Launch')
    assert result['actual'] == 'bm25' and '过期' in result['warning']


async def test_path_scoping_failure_and_query_dimension(state):
    store, repo, embedder, service = state
    await service.build(repo)
    chunks = store.chunks(repo['id'])
    result = await service.retrieve(repo, chunks, 'subtotal', path_filter='src/')
    assert all(c['path'].startswith('src/') for c in result['items'])
    embedder.fail = True
    assert (await service.retrieve(repo, chunks, 'Launch'))['actual'] == 'bm25'
    async def invalid(_): return [1, 2, 3]
    embedder.query = invalid
    result = await service.retrieve(repo, chunks, 'Launch')
    assert result['warning'] and result['items'][0]['path'] == 'README.md'


async def test_cancelled_build_never_commits_partial_index(state):
    store, repo, embedder, service = state
    entered = asyncio.Event()
    async def hanging(texts):
        entered.set()
        await asyncio.Event().wait()
    embedder.documents = hanging
    task = asyncio.create_task(service.build(repo))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert service.status(repo)['state'] == 'missing'


async def test_concurrent_build_and_incomplete_vectors(state):
    _, repo, embedder, service = state
    await asyncio.gather(service.build(repo), service.build(repo))
    assert embedder.document_calls == 1
    async def partial(_): return [0], [[1., 0.]]
    embedder.documents = partial
    with pytest.raises(ValueError): await service.build(repo, force=True)
    assert service.status(repo)['state'] == 'ready'


async def test_changed_repository_during_build_is_rejected(state):
    store, repo, embedder, service = state
    original = embedder.documents
    async def changed(texts):
        store.save_repository({**META,'sha':'second'}, FILES)
        return await original(texts)
    embedder.documents = changed
    with pytest.raises(ValueError, match='变化'): await service.build(repo)
    assert service.status(store.get(repo['id']))['state'] == 'missing'


async def test_hybrid_routes_job_search_and_chat_citations(state, offline_agent):
    store, repo, embedder, _ = state
    app = create_app(store=store, embedder=embedder, agent=offline_agent)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:3211') as client:
        assert (await client.get('/api/retrieval/status',params={'repo':repo['id']})).json()['state'] == 'missing'
        job = (await client.post('/api/retrieval/build',json={'repo':repo['id']})).json()
        await asyncio.sleep(0)
        assert (await client.get('/api/jobs/'+job['id'])).json()['state'] == 'done'
        response = await client.get('/api/search',params={'repo':repo['id'],'q':'怎样让服务可用','mode':'hybrid'})
        result = response.json()
        assert result['retrieval']['actual'] == 'hybrid' and result['results'][0]['preview'][0]['number'] == 1
        assert result['retrieval']['candidateCount'] >= len(result['results'])
        answer = (await client.post('/api/chat',json={'repo':repo['id'],'question':'怎样让服务可用','retrieval':'hybrid'})).json()
        assert answer['sources'][0]['path'] == 'README.md' and 'RRF' in answer['trace'][0]['detail']
        assert '本地重排' in answer['trace'][0]['detail']
        for params in ({'q':''},{'q':'test','mode':'bad'},{'q':'test','limit':'0'}):
            assert (await client.get('/api/search',params={'repo':repo['id'],**params})).status_code == 400
        assert (await client.post('/api/chat',json={'repo':repo['id'],'question':'x','retrieval':[]})).status_code == 400
        assert (await client.post('/api/retrieval/build',json={'repo':repo['id'],'force':'yes'})).status_code == 400


async def test_agent_uses_same_retriever_in_initial_and_tool_search():
    queries = []
    async def retrieve(query, limit):
        queries.append(query)
        return dict(items=[dict(path='README.md',start=1,end=1,text='evidence')],actual='hybrid',warning='')
    calls = []
    def provider(request):
        calls.append(request)
        msg = {'tool_calls':[{'id':'t1','function':{'name':'search_code','arguments':json.dumps({'query':'followup'})}}]} if len(calls)==1 else {'content':'Answer [S1]'}
        return httpx.Response(200,json={'choices':[{'message':msg}]})
    result = await ask_agent(repo=META,files=FILES,chunks=[],question='first',retriever=retrieve,
        config={'key':'test','base':'https://provider.test/v1','model':'gpt-4.1-mini'},transport=httpx.MockTransport(provider))
    assert queries == ['first','followup'] and len(result['sources']) == 1

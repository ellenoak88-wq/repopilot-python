import asyncio
import hashlib

import httpx
import pytest

from repopilot.checklist import ChecklistService
from repopilot.github import import_github
from repopilot.server import create_app
from repopilot.store import Store

META = dict(id='acme/repo', sha='first', demo=False, url='https://github.com/acme/repo')
FILES = [dict(path='README.md', content='# Installation\nRun python app.py'), dict(path='app.py', content='print(1)')]


def test_sync_preserves_chats_sessions_and_checklist_and_versions():
    store = Store(':memory:')
    first = store.save_repository(META, FILES)
    sid = store.create_session(META['id'], '学习经历')['id']
    chat = store.add_chat(META['id'], dict(question='安装', answer='旧回答', sources=[dict(text='旧摘录')]), sid)
    checklists = ChecklistService(store)
    checklists.generate(first, None, 0)
    before = store.db.execute('SELECT * FROM checklists').fetchall()
    second = store.save_repository({**META, 'sha':'second'}, [dict(path='README.md', content='# Install\nNew command')])
    assert store.history(META['id'], sid) == [chat]
    assert chat['indexVersion'] == first['indexVersion'] != second['indexVersion']
    assert store.get_session(META['id'],sid)['title'] == '学习经历'
    assert store.db.execute('SELECT * FROM checklists').fetchall() == before
    assert checklists.view(second)['stale'] is True
    assert second['lastSync']['removed'] == 1
    assert second['lastSync']['updated'] == 1
    assert store.file(META['id'],'app.py') is None
    third = store.save_repository({**META, 'sha':'second'}, store.files(META['id']))
    assert third['indexVersion'] == second['indexVersion']
    assert third['lastSync']['unchanged'] == 1
    store.close()


async def test_sync_reuses_only_matching_git_blobs():
    cached = FILES[0]['content'].encode()
    digest = hashlib.sha1(f'blob {len(cached)}\0'.encode()+cached).hexdigest()
    reads = []
    def handle(request):
        if request.url.host == 'raw.githubusercontent.com':
            reads.append(request.url.path)
            return httpx.Response(200, content=b'print(2)')
        if '/commits/' in request.url.path:
            return httpx.Response(200, json={'sha':'new'})
        if '/trees/' in request.url.path:
            return httpx.Response(200, json={'tree':[
                dict(path='README.md', type='blob', size=len(cached), sha=digest),
                dict(path='app.py', type='blob', size=8, sha='different')]})
        return httpx.Response(200,json=dict(private=False,default_branch='main',name='repo'))
    result = await import_github('acme/repo',existing=FILES,transport=httpx.MockTransport(handle))
    assert reads == ['/acme/repo/new/app.py']
    assert result['meta']['reusedFiles'] == 1
    assert result['files'][0] == FILES[0]
    assert result['files'][1]['content'] == 'print(2)'


async def test_old_context_not_used_and_cannot_regenerate():
    store=Store(':memory:'); store.save_repository(META,FILES)
    old=store.add_chat(META['id'],dict(question='安装',answer='old'))
    store.save_repository({**META,'sha':'new'},FILES)
    async def agent(**kwargs):
        assert kwargs['history'] == []
        return dict(answer='new',sources=[],trace=[],mode='agent')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(store=store,agent=agent)),base_url='http://127.0.0.1:3211') as client:
        response=await client.post('/api/chat',json={'repo':META['id'],'regenerateId':old['id']})
        assert response.status_code == 409
        response=await client.post('/api/chat',json={'repo':META['id'],'question':'安装'})
        assert response.status_code == 200
        assert response.json()['repoSha'] == 'new'
    store.close()


@pytest.mark.parametrize('fails', [False, True])
async def test_sync_job_atomic_preserves_case_identity(fails):
    store=Store(':memory:'); first=store.save_repository(META,FILES)
    old=store.add_chat(META['id'],dict(question='安装',answer='old'))
    async def importer(value,progress):
        if fails: raise ValueError('offline')
        return {'meta':{**META,'id':'Acme/Repo','sha':'new'},'files':FILES}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(store=store,importer=importer)),base_url='http://127.0.0.1:3211') as client:
        response=await client.post('/api/repo/sync',json={'repo':META['id']})
        assert response.status_code == 202
        for _ in range(20):
            job=(await client.get('/api/jobs/'+response.json()['id'])).json()
            if job['state'] != 'running': break
            await asyncio.sleep(.01)
        assert job['state'] == ('error' if fails else 'done')
    assert store.history(META['id']) == [old]
    assert len(store.list()) == 1
    assert store.get(META['id'])['sha'] == ('first' if fails else 'new')
    if fails: assert store.get(META['id']) == first
    store.close()



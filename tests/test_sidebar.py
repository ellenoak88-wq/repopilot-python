import httpx
import pytest

from repopilot.learning import LearningService, LearningConflict
from repopilot.sections import SectionConflict
from repopilot.server import create_app
from repopilot.store import Store


def setup(path=':memory:'):
    store=Store(path)
    for name in ('a','b'):
        store.save_repository(dict(id=f'test/{name}',sha='one',demo=True),[dict(path='README.md',content='pytest testing')])
    learning=LearningService(store)
    return store,learning,learning.sections


def test_repository_pin_independent_of_sessions_and_survives_sync_and_restart(tmp_path):
    path=tmp_path/'data.db';store,_,sections=setup(path)
    repo=store.get('test/a');session=store.default_session(repo['id'])
    sections.pin_repository(repo,True)
    assert sections.repositories()[0]['id']=='test/a'
    assert not store.get_session(repo['id'],session['id'])['pinned']
    store.save_repository(repo,store.files(repo['id']));store.close()
    store=Store(path);sections=LearningService(store).sections
    assert sections.annotate(store.get(repo['id']))['pinned']
    sections.pin_repository(store.get(repo['id']),False)
    assert not sections.annotate(store.get(repo['id']))['pinned']
    store.close()


def test_delete_repository_keeps_learning_and_other_projects_and_rejects_old_version():
    store,learning,sections=setup();repo=store.get('test/a')
    section=sections.create('学习')
    sections.assign(dict(repo=repo['id'],sectionId=section['id'],expectedSectionId=None))
    chat=store.add_chat(repo['id'],dict(question='pytest',answer='原文',sources=[]))
    record=learning.from_chat(repo,chat['id'])
    record=learning.update(dict(id=record['id'],revision=1,status='verified',applicability='pytest projects'))
    before=store.sessions('test/b')
    with pytest.raises(SectionConflict):sections.delete_repository(repo,'old')
    assert store.get(repo['id'])
    sections.delete_repository(repo,repo['indexVersion'])
    assert store.get(repo['id']) is None and store.files(repo['id'])==[]
    assert store.get_chat(repo['id'],chat['id']) is None
    assert store.sessions('test/b')==before and sections.membership(repo['id']) is None
    saved=learning.get(record['id']);assert saved['stale'] and saved['origin']['answer']=='原文'
    with pytest.raises(LearningConflict):learning.update(dict(id=saved['id'],revision=2))
    archived=learning.update(dict(id=saved['id'],revision=2,status='archived'))
    assert archived['status']=='archived'
    assert sections.get(section['id'])['name']=='学习'
    store.close()


async def test_sidebar_api_validation_and_cleanup():
    store,_,_=setup()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(store=store)),base_url='http://127.0.0.1:3211') as c:
        for v in [None,1,'true',[]]:
            assert (await c.post('/api/repos/pin',json=dict(repo='test/a',pinned=v))).status_code==400
        assert (await c.post('/api/repos/pin',json=dict(repo='missing/repo',pinned=True))).status_code==404
        assert (await c.post('/api/repos/pin',json=dict(repo='test/a',pinned=True))).json()['pinned']
        assert (await c.get('/api/repos')).json()[0]['id']=='test/a'
        assert (await c.post('/api/repos/delete',json=dict(repo='test/a'))).status_code==409
        # Companion rows are removed atomically with the local repository.
        store.db.execute('INSERT INTO checklists VALUES (?,?)',('test/a','{}'))
        store.db.execute('INSERT INTO vector_indexes VALUES (?,?,?,?,?,?,?)',('test/a','sig','model',1,1,'now','{}'));store.db.commit()
        assert (await c.post('/api/repos/delete',json=dict(repo='test/a',indexVersion=store.get('test/a')['indexVersion']))).status_code==200
        for table in ('files','sessions','chats','repo_preferences','repo_sections','checklists','vector_indexes'):
            assert not store.db.execute(f'SELECT * FROM {table} WHERE repo=?',('test/a',)).fetchall()
        assert (await c.get('/api/repo',params={'id':'test/a'})).status_code==404
    store.close()

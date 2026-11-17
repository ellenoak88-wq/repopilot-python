import json
import sqlite3

import httpx
import pytest

from repopilot.learning import LearningService
from repopilot.server import create_app
from repopilot.store import Store


def make_store(path=':memory:'):
    store=Store(path)
    for repo in ('a/b','c/d'):
        store.save_repository(dict(id=repo,sha='one',demo=True),[dict(path='README.md',content='pytest')])
    return store


def test_pin_order_search_and_unpin_preserve_messages():
    store=make_store();old=store.default_session('a/b');new=store.create_session('a/b','较新会话')
    chat=store.add_chat('a/b',dict(question='pytest',answer='原回答'),old['id'])
    store.db.execute('UPDATE sessions SET updated_at=? WHERE id=?',('2100-01-01',new['id']));store.db.commit()
    assert store.sessions('a/b')[0]['id']==new['id']
    before=store.get_session('a/b',old['id'])['updated_at']
    store.pin_session('a/b',old['id'],True)
    assert store.sessions('a/b')[0]['id']==old['id']
    assert store.default_session('a/b')['id']==old['id']
    assert store.sessions('a/b','pytest')[0]['pinned']==1
    assert store.get_session('a/b',old['id'])['updated_at']==before
    assert store.history('a/b',old['id'])==[chat]
    store.pin_session('a/b',old['id'],False)
    assert store.sessions('a/b')[0]['id']==new['id']
    with pytest.raises(ValueError):store.pin_session('c/d',old['id'],True)
    store.close()


async def test_session_menu_api_isolation_validation_and_last_delete():
    store=make_store();sid=store.default_session('a/b')['id'];learning=LearningService(store)
    chat=store.add_chat('a/b',dict(question='测试',answer='保留经历',sources=[]),sid)
    record=learning.from_chat(store.get('a/b'),chat['id'])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(store=store)),base_url='http://127.0.0.1:3211') as c:
        for invalid in [None,0,1,'true',[],{}]:
            assert (await c.post('/api/sessions/pin',json=dict(repo='a/b',sessionId=sid,pinned=invalid))).status_code==400
        assert (await c.post('/api/sessions/pin',json=dict(repo='c/d',sessionId=sid,pinned=True))).status_code==404
        result=await c.post('/api/sessions/pin',json=dict(repo='a/b',sessionId=sid,pinned=True))
        assert result.status_code==200 and result.json()['sessions'][0]['pinned']==1
        assert (await c.post('/api/sessions/delete',json=dict(repo='c/d',sessionId=sid))).status_code==404
        result=await c.post('/api/sessions/delete',json=dict(repo='a/b',sessionId=sid))
        assert result.status_code==200
        sessions=result.json()['sessions'];assert len(sessions)==1 and sessions[0]['id']!=sid
        assert sessions[0]['pinned']==0
        assert learning.get(record['id'])['origin']['answer']=='保留经历'
    store.close()


def test_existing_session_migration_and_pin_persistence(tmp_path):
    path=tmp_path/'legacy.db'
    db=sqlite3.connect(path)
    db.executescript('CREATE TABLE sessions(id TEXT PRIMARY KEY,repo TEXT,title TEXT,created_at TEXT,updated_at TEXT); CREATE TABLE repositories(id TEXT PRIMARY KEY,metadata TEXT);')
    db.execute('INSERT INTO sessions VALUES (?,?,?,?,?)',('old','a/b','原会话','t1','t2'))
    db.execute('INSERT INTO repositories VALUES (?,?)',('a/b',json.dumps(dict(id='a/b',sha='one'))));db.commit();db.close()
    store=Store(path);assert store.get_session('a/b','old')['pinned']==0
    store.pin_session('a/b','old',True);store.close()
    store=Store(path);assert store.get_session('a/b','old')['pinned']==1
    assert store.get_session('a/b','old')['title']=='原会话'
    store.create_session('a/b');store.close()

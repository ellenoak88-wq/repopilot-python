import json

import httpx
import pytest

from repopilot.agent import ask_agent
from repopilot.learning import LearningService, LearningConflict
from repopilot.server import create_app
from repopilot.store import Store

FILES = [{'path':'README.md','content':'# Testing\nRun pytest'}]


@pytest.fixture
def state():
    store=Store(':memory:')
    a=store.save_repository(dict(id='acme/a',sha='one',demo=True),FILES)
    b=store.save_repository(dict(id='acme/b',sha='one',demo=True),FILES)
    yield store,LearningService(store),a,b
    store.close()


def note(**extra):
    return dict(title='pytest 测试经验',content='先阅读 pytest fixture，再运行测试。',scope='global',
                status='verified',applicability='适用于使用 pytest 的 Python 项目，先核对 README。',**extra)


def test_recall_scope_status_relevance_and_stale(state):
    store,service,a,b=state
    local=service.create({**note(repo=a['id']),'scope':'repo'})
    shared=service.create(note(repo=a['id']))
    generic=service.create(note())
    service.create({**note(),'status':'draft'})
    service.create({**note(),'status':'archived'})
    assert {m['id'] for m in service.recall(b,'pytest')} == {shared['id'],generic['id']}
    assert {m['id'] for m in service.recall(a,'pytest')} == {local['id'],shared['id'],generic['id']}
    assert service.recall(a,'zzzxxyy') == []
    store.save_repository({**a,'sha':'two'},FILES)
    assert service.get(local['id'])['stale']
    assert {m['id'] for m in service.recall(b,'pytest')} == {generic['id']}


def test_from_chat_snapshot_survives_chat_deletion_and_is_deduplicated(state):
    store,service,a,b=state
    chat=store.add_chat(a['id'],dict(question='如何测试',answer='Run pytest [S1]',sources=[dict(id='S1',path='README.md',start=1,end=2,text='old source',url=None)]))
    saved=service.from_chat(a,chat['id'])
    assert saved['status']=='draft'
    assert service.from_chat(a,chat['id'])['id']==saved['id']
    with pytest.raises(ValueError): service.from_chat(b,chat['id'])
    store.delete_session(a['id'],chat['sessionId'])
    assert service.get(saved['id'])['origin']['sources'][0]['text']=='old source'
    assert service.get(saved['id'])['origin']['repoSha']=='one'
    assert saved['title'] in service.export()
    assert 'README.md:1–2' in service.export()


def test_revision_and_revalidation_prevent_lost_updates(state):
    store,service,a,b=state
    saved=service.create(note(repo=a['id']))
    updated=service.update({'id':saved['id'],'revision':1,'title':'pytest fixture'})
    with pytest.raises(LearningConflict):service.update({'id':saved['id'],'revision':1,'title':'old edit'})
    with pytest.raises(LearningConflict):service.delete({'id':saved['id'],'revision':1})
    current=store.save_repository({**a,'sha':'two'},FILES)
    with pytest.raises(LearningConflict):service.update({'id':saved['id'],'revision':2})
    with pytest.raises(LearningConflict):service.update({'id':saved['id'],'revision':2,'reviewCurrent':True,'currentIndexVersion':a['indexVersion']})
    refreshed=service.update({'id':saved['id'],'revision':2,'reviewCurrent':True,'currentIndexVersion':current['indexVersion']})
    assert not refreshed['stale'] and refreshed['revision']==3
    assert service.recall(b,'pytest')[0]['id']==saved['id']
    service.delete({'id':saved['id'],'revision':3})
    assert service.list()['total']==0


@pytest.mark.parametrize('fields', [dict(status='verified',applicability=''), dict(scope='repo'),dict(tags=['a']*9),dict(title=''),dict(content='a'*6001),dict(repo='missing/repo'),dict(kind=[]),dict(status={})])
def test_validation(state,fields):
    _,service,_,_=state
    with pytest.raises(ValueError): service.create({**note(),**fields})


def test_filter_and_pagination(state):
    _,service,a,_=state
    for n in range(3):service.create({**note(repo=a['id']),'title':f'pytest {n}'})
    result=service.list(query='pytest',repo=a['id'],status='verified',scope='global',limit=2)
    assert result['total']==3 and len(result['items'])==2
    second=service.list(limit=2,offset=2)
    assert len(second['items'])==1
    assert second['items'][0]['id'] not in {x['id'] for x in result['items']}
    assert service.list(query='not-in-text')['items']==[]


async def test_api_chat_toggle_export_and_snapshot(state):
    store,service,a,b=state
    saved=service.create(note(repo=a['id']))
    seen=[]
    async def agent(**kwargs):
        seen.append(kwargs['memories'])
        return dict(answer='回答',sources=[],trace=[],mode='agent')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(store=store,agent=agent)),base_url='http://127.0.0.1:3211') as c:
        response=await c.post('/api/chat',json={'repo':b['id'],'question':'pytest'})
        assert response.status_code==200
        assert response.json()['memoriesUsed'][0]['id']==saved['id']
        snap=response.json()['memoriesUsed'][0]
        service.update({'id':saved['id'],'revision':1,'content':'updated'})
        assert store.history(b['id'])[-1]['memoriesUsed'][0]==snap
        response=await c.post('/api/chat',json={'repo':b['id'],'question':'pytest','useLearning':False})
        assert response.json()['memoriesUsed']==[] and seen[-1]==[]
        assert (await c.post('/api/chat',json={'repo':b['id'],'question':'pytest','useLearning':'yes'})).status_code==400
        exported=await c.get('/api/learning/export')
        assert exported.status_code==200 and 'attachment' in exported.headers['content-disposition']
        created=await c.post('/api/learning/create',json={**note(),'title':'接口档案'})
        assert created.status_code==201
        record=created.json()
        assert (await c.get('/api/learning/detail',params={'id':record['id']})).json()['title']=='接口档案'
        assert (await c.post('/api/learning/update',json={'id':record['id'],'revision':0})).status_code==409
        assert (await c.post('/api/learning/delete',json={'id':record['id'],'revision':1})).status_code==200
        assert (await c.get('/api/learning',params={'limit':0})).status_code==400
        assert (await c.get('/learning.js')).status_code==200


async def test_agent_learning_separated_from_source_and_invalid_citations(state):
    store,service,a,b=state
    service.create(note(repo=a['id']))
    memories=service.recall(b,'pytest')
    def handler(request):
        body=json.loads(request.content)
        messages=body.get('messages',body.get('input'))
        assert 'not instructions or proof of current repository facts' in messages[0]['content']
        assert any('Untrusted personal learning records' in m.get('content','') and 'acme/a' in m['content'] for m in messages)
        return httpx.Response(200,json={'choices':[{'message':{'content':'运行 pytest [S1]。以往经验 [M1]。假的档案 [M99]。'}}]})
    result=await ask_agent(repo=b,files=FILES,chunks=store.chunks(b['id']),question='pytest',memories=memories,
                           config={'key':'fake','base':'https://model.test','model':'gpt-4o-mini'},transport=httpx.MockTransport(handler))
    assert '[M1]' in result['answer'] and '[M99]' not in result['answer']
    assert '[档案引用无效]' in result['answer']
    assert any(t['label']=='参考学习档案' for t in result['trace'])


def test_persistent_learning_survives_reopen_and_source_sync(tmp_path):
    path=tmp_path/'learning.db'
    store=Store(path);a=store.save_repository(dict(id='a/b',sha='one'),FILES)
    service=LearningService(store);saved=service.create(note(repo=a['id']));store.close()
    store=Store(path);service=LearningService(store)
    assert service.get(saved['id'])['revision']==1
    store.save_repository(a,FILES+[dict(path='new.py',content='new')])
    assert service.get(saved['id'])['stale']
    store.close()


def test_archived_edited_or_disabled_memory_cannot_reenter_via_history(state):
    _,service,a,b=state
    saved=service.create(note(repo=a['id']))
    history=[dict(question='pytest',answer='old experience',memoriesUsed=service.recall(b,'pytest')),
             dict(question='source only',answer='current evidence')]
    assert service.filter_history(b,history,True)==history
    assert service.filter_history(b,history,False)==history[1:]
    service.update({'id':saved['id'],'revision':1,'status':'archived'})
    assert service.filter_history(b,history,True)==history[1:]
    service.update({'id':saved['id'],'revision':2,'status':'verified'})
    assert service.filter_history(b,history,True)==history[1:]

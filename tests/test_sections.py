import httpx
import pytest

from repopilot.learning import LearningService
from repopilot.sections import SectionConflict
from repopilot.server import create_app
from repopilot.store import Store


@pytest.fixture
def setup():
    store=Store(':memory:')
    for name in ('a','b','c','d'):
        store.save_repository(dict(id=f'acme/{name}',sha='first',demo=True),[dict(path='README.md',content='pytest testing')])
    learning=LearningService(store)
    yield store,learning,learning.sections
    store.close()


def note(**extra):
    return dict(title='pytest 学习',content='测试前阅读 pytest fixture',applicability='适用于 pytest 项目',status='verified',**extra)


def assign(sections,repo,section):
    return sections.assign(dict(repo=repo,sectionId=section,expectedSectionId=sections.membership(repo)))


def test_section_sharing_does_not_broaden_private_or_unassigned_records(setup):
    store,learning,sections=setup
    one=sections.create('Python')['id'];two=sections.create('Frontend')['id']
    assign(sections,'acme/a',one);assign(sections,'acme/b',one);assign(sections,'acme/c',two)
    shared=learning.create(note(repo='acme/a',scope='section',sectionId=one))
    private=learning.create(note(repo='acme/a',scope='repo'))
    global_note=learning.create(note(scope='global'))
    assert {m['id'] for m in learning.recall(store.get('acme/b'),'pytest')}=={shared['id'],global_note['id']}
    for repo in ['acme/c','acme/d']:
        assert {m['id'] for m in learning.recall(store.get(repo),'pytest')}=={global_note['id']}
    assert private['id'] in {m['id'] for m in learning.recall(store.get('acme/a'),'pytest')}
    assert learning.list(section_id=one)['items'][0]['sectionName']=='Python'


def test_move_restricts_history_but_does_not_move_shared_records(setup):
    store,learning,sections=setup
    one=sections.create('第一分区')['id'];two=sections.create('第二分区')['id']
    assign(sections,'acme/a',one);assign(sections,'acme/b',one)
    saved=learning.create(note(repo='acme/a',scope='section',sectionId=one))
    b=store.get('acme/b');history=[dict(memoriesUsed=learning.recall(b,'pytest'))]
    assert learning.filter_history(b,history,True)==history
    assign(sections,'acme/b',two)
    assert learning.recall(b,'pytest')==[]
    assert learning.filter_history(b,history,True)==[]
    assign(sections,'acme/a',two)
    assert learning.get(saved['id'])['sectionId']==one
    assert learning.recall(store.get('acme/a'),'pytest')==[]
    assert sections.membership('acme/a')==two
    store.save_repository(store.get('acme/a'),store.files('acme/a'))
    assert sections.membership('acme/a')==two


def test_section_validation_conflicts_and_deletion(setup):
    _,learning,sections=setup
    one=sections.create('Python')
    with pytest.raises(SectionConflict):sections.create('python')
    with pytest.raises(ValueError):sections.create(' ')
    with pytest.raises(ValueError):sections.create('x'*61)
    with pytest.raises(ValueError):learning.create(note(scope='section',sectionId='missing'))
    assign(sections,'acme/a',one['id'])
    with pytest.raises(SectionConflict):sections.assign(dict(repo='acme/a',sectionId=None,expectedSectionId=None))
    with pytest.raises(SectionConflict):sections.delete(one)
    shared=learning.create(note(scope='section',sectionId=one['id']))
    assign(sections,'acme/a',None)
    with pytest.raises(SectionConflict):sections.delete(one)
    changed=sections.rename({**one,'name':'Backend'})
    assert learning.get(shared['id'])['sectionName']=='Backend'
    with pytest.raises(SectionConflict):sections.rename({**one,'name':'Old'})
    learning.update({'id':shared['id'],'revision':1,'scope':'global'})
    sections.delete(changed)
    assert sections.list()==[]


def test_from_chat_defaults_to_current_section_and_keeps_origin(setup):
    store,learning,sections=setup
    one=sections.create('学习')['id'];assign(sections,'acme/a',one)
    chat=store.add_chat('acme/a',dict(question='pytest',answer='参考源码',sources=[]))
    record=learning.from_chat(store.get('acme/a'),chat['id'])
    assert record['scope']=='section' and record['sectionId']==one
    assert record['status']=='draft'
    store.delete_session('acme/a',chat['sessionId'])
    assert learning.get(record['id'])['origin']['answer']=='参考源码'


async def test_section_api_and_invalid_assignments(setup):
    store,_,_=setup
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(store=store)),base_url='http://127.0.0.1:3211') as c:
        created=await c.post('/api/sections/create',json={'name':'学习'})
        assert created.status_code==201
        section=created.json()
        assert (await c.post('/api/sections/assign',json={'repo':'acme/a','sectionId':section['id'],'expectedSectionId':None})).status_code==200
        repo=(await c.get('/api/repo',params={'id':'acme/a'})).json()['repo']
        assert repo['sectionId']==section['id']
        assert (await c.get('/api/sections')).json()[0]['repoCount']==1
        assert (await c.post('/api/sections/delete',json=section)).status_code==409
        for value in [True,[],{},'missing']:
            assert (await c.post('/api/sections/assign',json={'repo':'acme/b','sectionId':value,'expectedSectionId':None})).status_code==400


def test_groups_persist_on_restart(tmp_path):
    path=tmp_path/'db'
    store=Store(path);store.save_repository(dict(id='a/b',sha='x'),[])
    service=LearningService(store);section=service.sections.create('持久化')
    assign(service.sections,'a/b',section['id']);store.close()
    store=Store(path);service=LearningService(store)
    assert service.sections.membership('a/b')==section['id']
    assert service.sections.list()[0]['repoCount']==1
    store.close()

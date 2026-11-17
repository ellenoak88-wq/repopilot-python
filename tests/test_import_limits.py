import httpx
import pytest

from repopilot.github import import_github, import_limits, select_files
from repopilot.server import create_app
from repopilot.store import Store


def test_defaults_and_validation(monkeypatch):
    monkeypatch.delenv('REPO_MAX_FILES',raising=False)
    monkeypatch.delenv('REPO_MAX_TOTAL_BYTES',raising=False)
    assert import_limits()==dict(maxFiles=1000,maxTotalBytes=50_000_000,maxFileBytes=80000)
    for bad in ('0','5001','not-int'):
        monkeypatch.setenv('REPO_MAX_FILES',bad)
        with pytest.raises(ValueError):import_limits()
    monkeypatch.setenv('REPO_MAX_FILES','5000')
    for bad in ('999999','200000001','not-int'):
        monkeypatch.setenv('REPO_MAX_TOTAL_BYTES',bad)
        with pytest.raises(ValueError):import_limits()


def test_selection_keeps_priority_and_fits_smaller_files():
    candidates=[{'path':str(i),'size':s} for i,s in enumerate([6,6,3,1,1])]
    selected,file_skipped,byte_skipped=select_files(candidates,dict(maxFiles=3,maxTotalBytes=10))
    assert [f['path'] for f in selected]==['0','2','3']
    assert (file_skipped,byte_skipped)==(1,1)


def upstream(file_count, *, reported_size=8, content=b'# hello\n', fail=None, truncated=False):
    def handler(request):
        path=request.url.path
        if request.url.host=='raw.githubusercontent.com':
            return httpx.Response(503 if path.endswith(fail or '\x00') else 200,content=content)
        if path.endswith('/acme/repo'): data=dict(private=False,default_branch='main',name='repo')
        elif '/commits/' in path: data={'sha':'fixed'}
        elif '/trees/' in path:
            data={'truncated':truncated,'tree':[dict(path=f'src/{i:04}.py',size=reported_size,type='blob',sha=f'b{i}') for i in range(file_count)]}
        else:return httpx.Response(500)
        return httpx.Response(200,json=data)
    return httpx.MockTransport(handler)


async def test_import_can_read_more_than_one_hundred_files(monkeypatch):
    monkeypatch.setenv('REPO_MAX_FILES','1000')
    result=await import_github('acme/repo',transport=upstream(105))
    assert len(result['files'])==105 and not result['meta']['partial']
    assert result['meta']['indexedBytes']==105*8


async def test_configured_limit_and_failures_are_reported(monkeypatch):
    monkeypatch.setenv('REPO_MAX_FILES','3')
    result=await import_github('acme/repo',transport=upstream(5,fail='0001.py',truncated=True))
    assert len(result['files'])==2
    assert result['meta']['skippedFileLimit']==2 and result['meta']['skippedByteLimit']==0
    assert result['meta']['failedFiles']==['src/0001.py'] and result['meta']['treeTruncated']
    assert result['meta']['partial']


@pytest.mark.parametrize('reported_size,expected_skipped',[(80000,2),(1,2)])
async def test_planned_and_actual_byte_budgets(monkeypatch,reported_size,expected_skipped):
    monkeypatch.setenv('REPO_MAX_FILES','100')
    monkeypatch.setenv('REPO_MAX_TOTAL_BYTES','1000000')
    result=await import_github('acme/repo',transport=upstream(14,reported_size=reported_size,content=b'x'*80000))
    assert len(result['files'])==12 and result['meta']['indexedBytes']==960000
    assert result['meta']['skippedByteLimit']==expected_skipped and result['meta']['partial']


async def test_status_exposes_effective_import_settings(monkeypatch):
    monkeypatch.setenv('REPO_MAX_FILES','2345')
    store=Store(':memory:')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(store=store)),base_url='http://127.0.0.1:3211') as client:
        result=(await client.get('/api/status')).json()
        assert result['importLimits']['maxFiles']==2345
    store.close()

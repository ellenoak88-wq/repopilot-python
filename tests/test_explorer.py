import httpx
import pytest

from repopilot.explorer import search_files
from repopilot.server import create_app
from repopilot.store import Store

FILES = [
    {"path": "src/main.py", "content": 'def add_note(title):\n    notes.append(title)\n    return notes\n\n# add_note demo'},
    {"path": "README.md", "content": '# Getting started\nInstall Python\nRun python -m app'},
]


def test_literal_context_merging_and_path_scope():
    result = search_files(FILES, "ADD_NOTE", path_filter="src/")
    assert result["total"] == 1 and result["fileCount"] == 1
    hit = result["results"][0]
    assert hit["matchedLines"] == [1, 5]
    assert hit["focusStart"] == 1 and hit["focusEnd"] == 5
    assert hit["preview"][0] == {"number": 1, "text": "def add_note(title):"}
    assert search_files(FILES, "add_note", path_filter="README")["total"] == 0
    assert search_files(FILES, "notes.append(title)")["total"] == 1
    assert search_files(FILES, ".*")["total"] == 0  # Literal, never regex.


def test_ranked_chinese_queries_and_bounded_results():
    result = search_files(FILES, "如何安装启动", mode="ranked")
    assert result["results"][0]["path"] == "README.md"
    files = [{"path": f"{i}.py", "content": "needle"} for i in range(30)]
    result = search_files(files, "needle", limit=3)
    assert result["total"] == 30 and result["truncated"] and len(result["results"]) == 3
    assert result["fileCount"] == 30


def test_preview_keeps_long_line_match_and_uses_source_line_numbers():
    result = search_files([{"path": "long.py", "content": "start\n" + "x" * 1000 + "needle" + "y" * 1000}], "needle")
    preview = result["results"][0]["preview"][-1]
    assert preview["number"] == 2 and "needle" in preview["text"]
    assert len(preview["text"]) <= 322


@pytest.mark.parametrize("kwargs", [{"query": " "}, {"query": "a" * 201}, {"query": "x", "mode": "bad"},
    {"query": "x", "limit": 0}, {"query": "x", "limit": 51}, {"query": "x", "path_filter": "a" * 201}])
def test_search_validation(kwargs):
    with pytest.raises(ValueError):
        search_files(FILES, **kwargs)


async def test_search_api_is_scoped_to_indexed_repo_and_never_calls_agent():
    store = Store(":memory:")
    store.save_repository({"id": "a/repo", "sha": "fixed", "partial": True}, FILES)
    store.save_repository({"id": "b/repo", "sha": "other"}, [{"path": "secret.py", "content": "separate_only"}])
    async def agent(**kwargs):
        raise AssertionError("Search must not invoke the model")
    app = create_app(store=store, agent=agent, port=3211)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:3211") as client:
        response = await client.get("/api/search", params={"repo": "a/repo", "q": "add_note"})
        data = response.json()
        assert response.status_code == 200 and data["sha"] == "fixed" and data["partial"]
        assert data["results"][0]["path"] == "src/main.py"
        assert (await client.get("/api/search", params={"repo": "a/repo", "q": "separate_only"})).json()["total"] == 0
        assert (await client.get("/api/search", params={"repo": "missing", "q": "x"})).status_code == 404
        assert (await client.get("/api/search", params={"repo": "a/repo", "q": "x", "limit": "oops"})).status_code == 400
        assert (await client.get("/api/search", params={"repo": "a/repo", "q": ""})).status_code == 400
        assert store.history("a/repo") == []
    store.close()

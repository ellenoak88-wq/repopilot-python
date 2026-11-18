import asyncio
import json

import httpx
import pytest

from repopilot.auto_wiki import AutoWiki
from repopilot.server import create_app, DEMO
from repopilot.store import Store
from repopilot.wiki_author import normalize_article, write_articles


async def test_auto_wiki_lifecycle_across_sessions_and_sync(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test")
    calls = []
    async def author(packets, topics, config):
        calls.append(packets)
        return {k: {"title": topics[k], "facts": [{"text": "综合说明 " + s["path"], "sources": [s["id"]]} for s in sources],
                    "links": [{"to": "implementation", "reason": "入口负责派发请求", "sources": ["S1"]}] if k == "overview" else []}
                for k, sources in packets.items()}

    store = Store(":memory:")
    meta = {**DEMO["meta"], "id": "org/example", "url": "https://github.com/org/example", "demo": False}
    files = DEMO["files"] + [{"path": "src/big.py", "content": "\n".join(f"line {i}" for i in range(100))}]
    repo = store.save_repository(meta, files)
    async def agent(**kwargs):
        return {"answer": "私有聊天文本", "sources": [{"id":"S1", "path":"src/big.py", "start":65, "end":72}],
                "mode": "agent", "trace": []}
    async def importer(value, progress):
        return {"meta": {**meta, "sha":"v2"}, "files": [f for f in files if f["path"] != "src/big.py"]}
    app = create_app(store=store, wiki_author=author, agent=agent, importer=importer, port=3211)
    params = {"repo":repo["id"]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:3211") as c:
        async def ready():
            for _ in range(50):
                state = (await c.get("/api/wiki/auto", params=params)).json()
                if not state["running"] and state["pages"]:
                    return state
                await asyncio.sleep(.005)
            raise AssertionError("Wiki did not finish")

        await c.get("/api/repo", params={"id":repo["id"]})
        first = await ready()
        assert first["pages"]["overview"]["related"][0]["key"] == "implementation"
        assert any(l["key"] == "overview" for l in first["pages"]["implementation"]["related"])
        assert len(calls) == 1
        for _ in range(2):
            sid = (await c.post("/api/sessions/create", json={**params, "title":"学习"})).json()["id"]
            answer = await c.post("/api/chat", json={**params, "sessionId":sid, "question":"架构？"})
            assert answer.status_code == 200
            second = await ready()
        assert len(calls) == 2 and set(calls[1]) == {"implementation"}
        assert "私有聊天文本" not in json.dumps(calls, ensure_ascii=False)
        assert second["pages"]["implementation"]["revision"] == 2
        assert second["pages"]["overview"]["revision"] == 1
        job = (await c.post("/api/repo/sync", json=params)).json()
        for _ in range(50):
            if (await c.get("/api/jobs/"+job["id"])).json()["state"] == "done":
                break
            await asyncio.sleep(.005)
        updated = await ready()
        assert updated["version"] != first["version"]
        assert "src/big.py" not in json.dumps(updated["pages"])
        await c.post("/api/repos/delete", json={**params, "indexVersion":updated["version"]})
        assert not store.db.execute("SELECT repo FROM wiki_editions").fetchone()
    store.close()


async def test_retry_offline_and_retention():
    store = Store(":memory:")
    files = [{"path": f"src/{i}.py", "content":f"def function_{i}(): pass"} for i in range(9)]
    meta = store.save_repository(DEMO["meta"], files)
    config = {"key":"", "model":"test"}
    async def author(*args):
        raise RuntimeError("unavailable")
    wiki = AutoWiki(store, author, lambda:config)
    async def finish(sources=()):
        wiki.request(meta, sources)
        await asyncio.gather(*wiki.workers.values())
        return wiki.view(meta)
    a = await finish()
    assert not a["error"] and not a["pages"]["implementation"]["engine"]
    await finish([{"path":f"src/{i}.py", "start":1} for i in (5,6,7)])
    b = await finish([{"path":"src/8.py", "start":1}])
    assert {s["path"] for s in b["pages"]["implementation"]["sources"]} == {f"src/{i}.py" for i in range(9)}
    config["key"] = "fixture"
    failed = await finish()
    assert failed["error"] and not failed["running"]
    wiki.request(meta)
    assert not wiki.workers
    meta = store.save_repository({**DEMO["meta"],"sha":"next"}, files[:6])
    updated = await finish()
    assert "src/7.py" not in json.dumps(updated)
    store.close()


@pytest.mark.parametrize("model", ["gpt-5.4-mini", "deepseek-flash"])
async def test_wiki_provider_and_invalid_refs(model):
    requests = []
    def handler(request):
        body = json.loads(request.content)
        inputs = body.get("input", body.get("messages"))
        payload = json.loads(inputs[-1]["content"])
        requests.append(payload)
        claim = "已按源码纠正" if "candidate" in payload else "初稿"
        articles = {"articles":{"overview":{"title":"用途","facts":[{"text":claim,"sources":["S1"]}]}}}
        raw = json.dumps(articles)
        if model.startswith("gpt"):
            assert request.url.path.endswith("/responses")
            return httpx.Response(200, json={"output":[{"type":"message","content":[{"type":"output_text","text":raw}]}]})
        assert request.url.path.endswith("/chat/completions")
        return httpx.Response(200,json={"choices":[{"message":{"content":raw}}]})
    sources = [{"id":"S1"}]
    out = await write_articles({"overview":sources}, {"overview":"用途"}, {"model":model,"key":"test","base":"https://provider.test"},
                               transport=httpx.MockTransport(handler))
    page = out["overview"]
    assert len(requests) == 2 and requests[1]["candidate"]["overview"]["facts"][0]["text"] == "初稿"
    assert page["facts"][0]["text"] == "已按源码纠正"
    assert normalize_article(page, sources, {"overview"}, "overview")["facts"][0]["sources"] == ["S1"]
    page["facts"][0]["sources"] = ["S99"]
    with pytest.raises(ValueError):
        normalize_article(page, sources, {"overview"}, "overview")


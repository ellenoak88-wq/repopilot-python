import asyncio
import json

import httpx
import pytest

from repopilot.server import DEMO, create_app
from repopilot.store import Store

BASE = "http://127.0.0.1:3211"
REPO = DEMO["meta"]["id"]


@pytest.fixture
def store():
    s = Store(":memory:")
    s.save_repository(DEMO["meta"], DEMO["files"])
    yield s
    s.close()


async def test_api_reader_sessions_and_stream(store):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(store=store, port=3211)), base_url=BASE) as c:
        assert (await c.get("/api/health")).json() == {"status": "ok"}
        assert (await c.get("/api/status")).json()["backend"] == "python"
        assert (await c.get("/app.js")).headers["content-type"].startswith("text/javascript")
        assert (await c.get("/.env")).status_code == 404
        reader = await c.get("/api/file", params={"repo": REPO, "path": "README.md"})
        assert reader.json()["lines"] == 18
        assert (await c.get("/api/file", params={"repo": REPO, "path": "../../.env"})).status_code == 404
        session = (await c.post("/api/sessions/create", json={"repo": REPO, "title": "测试会话"})).json()
        payload = {"repo": REPO, "sessionId": session["id"], "question": "安装启动", "mode": "extractive"}
        stream = await c.post("/api/chat/stream", json=payload)
        events = [json.loads(s[6:]) for s in stream.text.split("\n\n") if s.startswith("data: ")]
        assert events[-1]["type"] == "done"
        answer = events[-1]["result"]
        again = (await c.post("/api/chat", json={**payload, "regenerateId": answer["id"]})).json()
        assert again["id"] == answer["id"]
        data = (await c.get("/api/repo", params={"id": REPO, "session": session["id"]})).json()
        assert len(data["history"]) == 1
        assert (await c.post("/api/sessions/rename", json={"repo": REPO, "sessionId": session["id"], "title": "已改名"})).status_code == 200
        assert len((await c.get("/api/sessions", params={"repo": REPO, "q": "已改名"})).json()) == 1
        assert (await c.post("/api/sessions/delete", json={"repo": REPO, "sessionId": session["id"]})).status_code == 200
        assert (await c.get("/api/repo", params={"id": REPO, "session": session["id"]})).status_code == 404


async def test_validation_and_security(store):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(store=store, port=3211)), base_url=BASE) as c:
        assert (await c.get("/api/repos", headers={"Host": "evil.test"})).status_code == 403
        assert (await c.post("/api/demo", json={}, headers={"Origin": "https://evil.test"})).status_code == 403
        assert (await c.post("/api/demo", content="{}")).status_code == 415
        assert (await c.post("/api/demo", json=[])).status_code == 400
        assert (await c.post("/api/demo", content="{", headers={"content-type": "application/json"})).status_code == 400
        assert (await c.post("/api/demo", json={"x": "a" * 20001})).status_code == 413
        for field in [{"question": ""}, {"sessionId": []}, {"model": []}, {"effort": {}}, {"regenerateId": True}]:
            result = await c.post("/api/chat", json={"repo": REPO, "question": "test", "mode": "extractive", **field})
            assert result.status_code in (400, 404), result.text


async def test_import_job_and_busy_lock(store):
    gate = asyncio.Event()
    async def importer(value, progress):
        await gate.wait()
        progress("done", 90)
        return DEMO
    app = create_app(store=store, importer=importer, port=3211)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        response = await c.post("/api/import", json={"url": "acme/repo"})
        assert response.status_code == 202
        assert (await c.post("/api/demo", json={})).status_code == 409
        gate.set()
        for _ in range(20):
            job = (await c.get(f"/api/jobs/{response.json()['id']}")).json()
            if job["state"] == "done":
                break
            await asyncio.sleep(.01)
        assert job["repo"]["id"] == REPO


async def test_stream_error_is_not_saved_and_releases_lock(store):
    async def agent(**kwargs):
        kwargs["on_event"]({"type": "delta", "delta": "partial"})
        raise ValueError("upstream failed")
    app = create_app(store=store, agent=agent, port=3211)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        response = await c.post("/api/chat/stream", json={"repo": REPO, "question": "test"})
        events = [json.loads(s[6:]) for s in response.text.split("\n\n") if s.startswith("data: ")]
        assert events[-1] == {"type": "error", "error": "upstream failed"}
        assert store.history(REPO) == []
        assert (await c.post("/api/demo", json={})).status_code == 200


@pytest.mark.parametrize("streaming", [True, False])
async def test_disconnect_cancels_agent_keeps_old_answer_and_unlocks(store, streaming):
    started, cancelled = asyncio.Event(), asyncio.Event()
    sid = store.default_session(REPO)["id"]
    old = store.add_chat(REPO, {"question": "启动", "answer": "原回答"}, sid)
    async def agent(**kwargs):
        started.set()
        try:
            if kwargs.get("on_event"):
                kwargs["on_event"]({"type": "delta", "delta": "未完成"})
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
    app = create_app(store=store, agent=agent, port=3211)
    incoming = asyncio.Queue()
    raw = json.dumps({"repo": REPO, "sessionId": sid, "regenerateId": old["id"]}).encode()
    incoming.put_nowait({"type": "http.request", "body": raw, "more_body": False})
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"}, "method": "POST", "scheme": "http",
             "path": "/api/chat/stream" if streaming else "/api/chat", "raw_path": b"/api/chat", "query_string": b"", "root_path": "",
             "headers": [(b"host", b"127.0.0.1:3211"), (b"content-type", b"application/json")], "server": ("127.0.0.1", 3211), "client": ("127.0.0.1", 50000), "http_version": "1.1"}
    async def send(_):
        pass
    task = asyncio.create_task(app(scope, incoming.get, send))
    await asyncio.wait_for(started.wait(), 2)
    incoming.put_nowait({"type": "http.disconnect"})
    await asyncio.wait_for(task, 2)
    assert cancelled.is_set()
    assert store.history(REPO, sid)[0]["answer"] == "原回答"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE) as c:
        assert (await c.post("/api/sessions/create", json={"repo": REPO, "title": "解锁"})).status_code == 201

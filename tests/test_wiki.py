import httpx

from repopilot.server import DEMO, create_app
from repopilot.store import Store


async def test_wiki_lifecycle(tmp_path):
    filename = tmp_path / "wiki.db"
    store = Store(filename)
    meta = store.save_repository(DEMO["meta"], DEMO["files"])
    repo = meta["id"]
    other = store.save_repository({**DEMO["meta"], "id": "other/repo"}, DEMO["files"])["id"]
    source = {"id": "S1", "path": "README.md", "start": 1, "end": 2, "text": "原始摘录"}
    one = store.add_chat(repo, {"question": "如何启动？", "answer": "入口 [S1]", "sources": [source]})
    two = store.add_chat(repo, {"question": "路由呢？", "answer": "路由 [S1]", "sources": [{**source, "path": "app.py"}]},
                         store.create_session(repo)["id"])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(store=store, port=3211)),
                                 base_url="http://127.0.0.1:3211") as client:
        async def draft(chat):
            return (await client.post("/api/wiki/draft", json={"repo": repo, "chatId": chat["id"]})).json()

        a, b = await draft(one), await draft(two)
        assert (await client.get("/api/wiki", params={"repo": repo})).json() == []
        create = {"repo": repo, "chatId": one["id"], "originKey": a["originKey"], "title": "请求流程"}
        created = await client.post("/api/wiki/create", json=create)
        assert created.status_code == 201
        page = created.json()["id"]
        assert (await client.post("/api/wiki/create", json=create)).status_code == 409
        merge = {"repo": repo, "pageId": page, "revision": 1, "chatId": two["id"],
                 "originKey": b["originKey"], "content": "路由的位置 [S1]"}
        assert (await client.post("/api/wiki/merge", json={**merge, "repo": other})).status_code == 400
        assert (await client.get("/api/wiki/detail", params={"repo": other, "id": page})).status_code == 400
        assert (await client.post("/api/wiki/merge", json=merge)).json()["revision"] == 2
        assert (await client.post("/api/wiki/merge", json=merge)).status_code == 409
        assert (await client.post("/api/wiki/merge", json={**merge, "revision": 2})).status_code == 409
        store.add_chat(repo, {"question": "路由呢？", "answer": "更新回答"}, two["sessionId"], two["id"])
        assert (await client.post("/api/wiki/merge", json={**merge, "revision": 2})).status_code == 409
        store.delete_session(repo, one["sessionId"])
        store.delete_session(repo, two["sessionId"])
        store.save_repository({**DEMO["meta"], "sha": "updated"}, DEMO["files"])
    store.close()

    store = Store(filename)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(store=store, port=3211)),
                                 base_url="http://127.0.0.1:3211") as client:
        assert len((await client.get("/api/wiki", params={"repo": repo})).json()) == 1
        detail = (await client.get("/api/wiki/detail", params={"repo": repo, "id": page})).json()
        first, second = detail["entries"]
        assert first["sources"] == [source]
        assert second["content"] == merge["content"] and second["answer"] == "路由 [S1]"
        assert second["sources"][0]["path"] == "app.py"
        assert first["indexVersion"] != detail["currentIndexVersion"]
        response = await client.post("/api/repos/delete", json={"repo": repo, "indexVersion": detail["currentIndexVersion"]})
        assert response.status_code == 200
        assert not store.db.execute("SELECT 1 FROM wiki_entries").fetchone()
        store.save_repository(DEMO["meta"], DEMO["files"])
        assert (await client.get("/api/wiki", params={"repo": repo})).json() == []
    store.close()


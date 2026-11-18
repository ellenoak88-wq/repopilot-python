import json

import httpx

from repopilot.agent import ask_agent
from repopilot.learning import LearningService
from repopilot.server import DEMO, create_app
from repopilot.store import Store


async def test_long_session_retrieves_old_topic_and_excludes_other_session_and_old_index():
    store = Store(":memory:")
    repo = store.save_repository(DEMO["meta"], DEMO["files"])
    repo_id = repo["id"]
    session = store.create_session(repo_id, "main")
    other = store.create_session(repo_id, "other")
    store.add_chat(repo_id, {"question": "oldindexword", "answer": "wrong version",
                             "indexVersion": "old"}, session["id"])
    store.add_chat(repo_id, {"question": "otherthreadword", "answer": "wrong session"}, other["id"])
    store.add_chat(repo_id, {"question": "ttlcache key", "answer": "Use workspace ID [S1]."}, session["id"])
    for number in range(25):
        store.add_chat(repo_id, {"question": f"topic {number}", "answer": f"answer {number}"}, session["id"])

    requests = []

    def provider(request):
        body = json.loads(request.content)
        requests.append(body["messages"])
        return httpx.Response(200, json={"choices": [{"message": {"content": "Follow-up [S1]"}}]})

    async def agent(**kwargs):
        config = {"base": "https://model.test", "key": "test", "model": "gpt-4.1-mini"}
        return await ask_agent(**{**kwargs, "config": config}, transport=httpx.MockTransport(provider))

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(store=store, agent=agent)),
                                 base_url="http://127.0.0.1:3211") as client:
        response = await client.post("/api/chat", json={"repo": repo_id, "sessionId": session["id"],
                                     "question": "ttlcache oldindexword otherthreadword", "retrieval": "bm25"})
        assert response.status_code == 200
        answer = response.json()
        summary = answer["sessionSummary"]
        assert summary["olderTurns"] == 22
        assert len(summary["topics"]) == 6
        assert {"question": "ttlcache key", "answer": "Use workspace ID ."} in summary["topics"]
        assert any("Use workspace ID" in message["content"] for message in requests[0])
        assert not any("wrong session" in message["content"] or "wrong version" in message["content"]
                       for message in requests[0])
        assert sum(message["role"] == "assistant" for message in requests[0]) == 4

        regenerated = await client.post("/api/chat", json={
            "repo": repo_id, "sessionId": session["id"], "regenerateId": answer["id"], "retrieval": "bm25",
        })
        assert regenerated.status_code == 200
        assert regenerated.json()["sessionSummary"] == summary
        assert all(message["content"] != "Follow-up " for message in requests[1])
    store.close()


async def test_summary_dependencies_are_carried_and_removed_after_archiving():
    store = Store(":memory:")
    repo = store.save_repository(DEMO["meta"], DEMO["files"])
    repo_id = repo["id"]
    service = LearningService(store)
    memory = service.create({"title": "zebra resume", "content": "zebra resume detail",
                             "scope": "global", "status": "verified", "applicability": "while debugging"})
    session_id = store.default_session(repo_id)["id"]
    usage = service.recall(repo, "zebra")
    store.add_chat(repo_id, {"question": "zebra", "answer": "from learning file",
                             "memoriesUsed": usage}, session_id)
    for number in range(4):
        store.add_chat(repo_id, {"question": f"topic {number}", "answer": "answer"}, session_id)

    received = []

    async def agent(**kwargs):
        received.append(kwargs)
        return {"answer": "derived from earlier context", "sources": [], "trace": [], "mode": "agent"}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(store=store, agent=agent)),
                                 base_url="http://127.0.0.1:3211") as client:
        payload = {"repo": repo_id, "sessionId": session_id, "question": "unrelatedkeyword"}
        first = (await client.post("/api/chat", json=payload)).json()
        assert first["memoriesUsed"] == []
        assert first["sessionSummary"]["topics"][0]["question"] == "zebra"
        assert first["contextMemoryRefs"] == [{"id": memory["id"], "revision": memory["revision"]}]

        switched_off = (await client.post("/api/chat", json={**payload, "useLearning": False})).json()
        assert "sessionSummary" not in switched_off
        assert all(turn["id"] != first["id"] for turn in received[-1]["history"])

        service.update({"id": memory["id"], "revision": memory["revision"], "status": "archived"})
        await client.post("/api/chat", json=payload)
        assert all(turn["id"] != first["id"] for turn in received[-1]["history"])
        assert all(topic["question"] != "zebra" for topic in received[-1]["summary"]["topics"])
    store.close()

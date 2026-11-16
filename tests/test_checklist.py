import httpx
import pytest

from repopilot.checklist import ChecklistConflict, ChecklistService, build_tasks
from repopilot.server import DEMO, create_app
from repopilot.store import Store


@pytest.fixture
def state(tmp_path):
    store = Store(tmp_path / "checklist.db")
    repo = store.save_repository(DEMO["meta"], DEMO["files"])
    service = ChecklistService(store)
    yield store, repo, service
    store.close()


def test_tasks_have_real_evidence_and_stable_ids():
    tasks, gaps = build_tasks(DEMO["files"])
    assert not gaps
    assert {t["stage"] for t in tasks} == {"understand", "setup", "code", "test", "contribute"}
    assert [t["id"] for t in tasks] == [t["id"] for t in build_tasks(list(reversed(DEMO["files"])))[0]]
    content = {f["path"]: f["content"] for f in DEMO["files"]}
    for task in tasks:
        source = task["source"]
        lines = content[source["path"]].split("\n")
        assert 1 <= source["start"] <= source["end"] <= len(lines)
        assert source["text"] == "\n".join(lines[source["start"] - 1:source["end"]])[:2400]
        assert task["completed"] is False and task["note"] == ""


def test_missing_evidence_is_marked_without_invented_commands():
    tasks, gaps = build_tasks([{"path": "random.py", "content": "print('hello')"}])
    assert len(tasks) == 1 and tasks[0]["stage"] == "code"
    assert {gap["stage"] for gap in gaps} == {"understand", "setup", "test", "contribute"}
    assert not any("pip install" in t["description"] for t in tasks)


def test_progress_notes_persist_and_regeneration_preserves_same_evidence(state):
    store, repo, service = state
    assert service.view(repo)["plan"] is None
    plan = service.generate(repo, None, 0)["plan"]
    body = dict(planId=plan["id"], revision=1, taskId=plan["tasks"][0]["id"], completed=True, note="已阅读，准备运行测试")
    updated = service.update(repo, body)["plan"]
    assert updated["revision"] == 2 and updated["tasks"][0]["completed"]
    # Another SQLite connection observes the committed progress.
    filename = store.db.execute("PRAGMA database_list").fetchone()[2]
    other = Store(filename)
    assert ChecklistService(other).view(repo)["plan"]["tasks"][0]["note"] == body["note"]
    other.close()
    again = service.generate(repo, updated["id"], updated["revision"])["plan"]
    assert again["id"] != updated["id"]
    assert again["tasks"][0]["completed"] and again["tasks"][0]["note"] == body["note"]


def test_conflicts_do_not_overwrite_progress(state):
    _, repo, service = state
    plan = service.generate(repo, None, 0)["plan"]
    body = dict(planId=plan["id"], revision=1, taskId=plan["tasks"][0]["id"], completed=True)
    service.update(repo, body)
    with pytest.raises(ChecklistConflict):
        service.update(repo, {**body, "completed": False})
    with pytest.raises(ChecklistConflict):
        service.generate(repo, None, 0)
    assert service.view(repo)["plan"]["tasks"][0]["completed"]


def test_reindex_marks_plan_stale_until_explicit_regeneration(state):
    store, repo, service = state
    plan = service.generate(repo, None, 0)["plan"]
    changed = store.save_repository({**repo, "sha": "new-commit"}, DEMO["files"])
    assert service.view(changed)["stale"]
    assert service.view(changed)["plan"]["id"] == plan["id"]
    with pytest.raises(ChecklistConflict):
        service.update(changed, dict(planId=plan["id"], revision=1, taskId=plan["tasks"][0]["id"], completed=True))
    refreshed = service.generate(changed, plan["id"], 1)
    assert not refreshed["stale"] and refreshed["plan"]["sha"] == "new-commit"
    assert not any(t["completed"] for t in refreshed["plan"]["tasks"])


def test_changed_coverage_at_same_commit_also_marks_stale(state):
    store, repo, service = state
    service.generate(repo, None, 0)
    repo = store.save_repository(repo, DEMO["files"][:1])
    assert service.view(repo)["stale"]


@pytest.mark.parametrize("change", [{"completed": "yes"}, {"note": "x" * 1001}, {"revision": True}, {"taskId": "missing"}])
def test_bad_updates_leave_plan_unchanged(state, change):
    _, repo, service = state
    plan = service.generate(repo, None, 0)["plan"]
    with pytest.raises(ValueError):
        service.update(repo, {"planId": plan["id"], "revision": 1, "taskId": plan["tasks"][0]["id"], "completed": True, **change})
    assert service.view(repo)["plan"] == plan


async def test_api_repo_isolation_conflict_and_no_model_calls(state):
    store, repo, _ = state
    store.save_repository({**DEMO["meta"], "id": "other/repo"}, DEMO["files"])
    async def agent(**kwargs):
        raise AssertionError("Checklist generation must not call a model")
    app = create_app(store=store, agent=agent, port=3211)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:3211") as client:
        assert (await client.get("/api/checklist", params={"repo": repo["id"]})).json()["plan"] is None
        plan = (await client.post("/api/checklist/generate", json={"repo": repo["id"], "planId": None, "revision": 0})).json()["plan"]
        body = {"repo": repo["id"], "planId": plan["id"], "revision": 1, "taskId": plan["tasks"][0]["id"], "note": "checked"}
        assert (await client.post("/api/checklist/update", json=body)).status_code == 200
        assert (await client.post("/api/checklist/update", json=body)).status_code == 409
        assert (await client.post("/api/checklist/update", json={**body, "repo": "other/repo"})).status_code == 409
        assert (await client.get("/api/checklist", params={"repo": "other/repo"})).json()["plan"] is None
        assert (await client.get("/api/checklist", params={"repo": "missing"})).status_code == 404
        assert store.history(repo["id"]) == []

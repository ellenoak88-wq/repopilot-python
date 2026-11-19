import asyncio
import hashlib
import json
from types import SimpleNamespace

import httpx
import pytest

from eval import run_loopforge as runner
from repopilot.hybrid import HybridRetrieval
from repopilot.server import create_app
from repopilot.store import Store


def test_union_coverage_respects_source_gaps_alternatives_and_top_five():
    def span(path, start, end):
        return dict(path=path, start=start, end=end)

    case = {"id": "coverage", "evidence_groups": [
        {"any_of": [span("a", 2, 7)]},
        {"any_of": [span("a", 2, 10)]},
        {"any_of": [span("b", 1, 7), span("a", 20, 25)]},
        {"any_of": [span("c", 1, 3), span("a", 6, 7)]},
    ]}
    # Reverse source order, an overlapping duplicate, a hole at line 8, and a sixth result.
    items = [span("a", 5, 7), span("a", 2, 4), span("a", 3, 4),
             span("a", 9, 10), span("b", 1, 4), span("a", 1, 25)]
    row = runner.score(case, items)
    assert row["union_ranks"] == [2, None, None, 1]
    assert row["ranks"] == [None, None, None, 1]
    assert row["union_recall_at_5"] == .5
    assert row["single_chunk_recall_at_5"] == row["recall"] == .25
    assert row["results"] == items[:5]
    assert runner.score(case, [])["union_recall_at_5"] == 0
    # Two partially covered alternatives within one file still prove no complete reference.
    assert runner.score({"id": "alt", "evidence_groups": [{"any_of": [span("a", 1, 4), span("a", 8, 11)]}]},
                        [span("a", 1, 2), span("a", 10, 11)])["union_ranks"] == [None]


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    store = Store(tmp_path / "live.db")
    repo = store.save_repository(dict(id="local/eval",name="sample",sha="abc",demo=True),
                                 [dict(path="a.py",content="value = 1")])
    snapshot = dict(repo=repo["id"],repo_sha=repo["sha"],index_version=repo["indexVersion"])
    evidence = dict(path="a.py",start=1,end=1,sha256=hashlib.sha256(b"value = 1").hexdigest())
    cases = [dict(id=split,question="value "+split,split=split,answerable=True,
                  evidence_groups=[dict(fact="PRIVATE FACT",any_of=[evidence])]) for split in ("dev","test")]
    (tmp_path/"loopforge.snapshot.json").write_text(json.dumps(snapshot))
    (tmp_path/"loopforge.jsonl").write_text("\n".join(map(json.dumps,cases)))
    monkeypatch.setenv("REPOPILOT_EVAL_DIR",str(tmp_path))
    HybridRetrieval(store,SimpleNamespace(identity="fixture",name="fixture"))
    yield repo, store, tmp_path
    store.close()


async def test_combined_report_is_atomic_and_holdout_is_aggregate_only(corpus, monkeypatch):
    repo, store, folder = corpus
    queries = []
    def status(self,*args): return {"state":"ready"}
    async def retrieve(self,selected,chunks,question,limit,mode):
        assert selected["id"] == repo["id"] and question in ("value dev","value test")
        assert [c["path"] for c in chunks] == ["a.py"]
        assert self.store.db.execute("SELECT COUNT(*) FROM chats").fetchone()[0] == 0
        queries.append((question,mode))
        return {"actual":mode,"items":chunks}
    with store.db:
        store.db.execute("INSERT INTO chats(repo,payload) VALUES (?,?)",(repo["id"],'{"answer":"PRIVATE FACT"}'))
    monkeypatch.setattr(HybridRetrieval,"status",status)
    monkeypatch.setattr(HybridRetrieval,"retrieve",retrieve)
    for split in ("dev","test"):
        args=SimpleNamespace(db=folder/"live.db",split=split,mode="compare",output=None)
        await runner.run(args)
        path=folder/"evaluations"/f"loopforge-{split}-compare.json"
        report=json.loads(path.read_text("utf-8"))
        assert set(report["results"]) == {"bm25","hybrid"}
        assert report["scoringVersion"] == 2
        for result in report["results"].values():
            assert result["metrics"]["union_recall_at_5"] == result["metrics"]["single_chunk_recall_at_5"] == 1
            assert result["evidenceCounts"] == {"total": 1, "union": 1, "single_chunk": 1}
        assert all(result["metrics"]["recall"]==1 for result in report["results"].values())
        assert ("cases" in report["results"]["hybrid"]) == (split=="dev")
        if split=="test":
            assert all(word not in path.read_text("utf-8") for word in ("value test","PRIVATE FACT","a.py","ranks"))
        assert len(report["datasetSha256"])==64
    assert queries == [(f"value {s}",m) for s in ("dev","test") for m in ("bm25","hybrid")]
    previous=path.read_bytes()
    monkeypatch.setattr(HybridRetrieval,"status",lambda *a: {"state":"missing"})
    with pytest.raises(ValueError,match="索引"):
        await runner.run(args)
    assert path.read_bytes()==previous


async def test_evaluation_routes_and_duplicate_run(corpus,monkeypatch):
    from repopilot.evaluation import Evaluation
    repo,store,folder=corpus
    release=asyncio.Event()
    async def worker(self,job,output):
        await release.wait()
        runner.write_report(output, {"split":job["split"],"metrics":{},"snapshot":self.snapshot,"datasetSha256":self.digest})
        job.update(state="done")
    monkeypatch.setattr(Evaluation,"_run",worker)
    app=create_app(store=store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://127.0.0.1:3211") as client:
        args={"repo":repo["id"],"split":"dev"}
        assert (await client.get("/api/evaluation/report",params=args)).status_code==404
        assert (await client.post("/api/evaluation/run",json={**args,"split":"other"})).status_code==400
        assert (await client.post("/api/evaluation/run",json=args)).status_code==202
        assert (await client.post("/api/evaluation/run",json=args)).status_code==409
        release.set()
        await asyncio.sleep(0)
        status=(await client.get("/api/evaluation/status",params=args)).json()
        assert status["reports"]==["dev"] and status["job"]["state"]=="done"
        response=await client.get("/api/evaluation/report",params=args)
        assert response.json()["split"]=="dev" and "attachment" in response.headers["content-disposition"]
        runner.write_report(folder/"evaluations"/"loopforge-dev-compare.json", {"split":"dev","datasetSha256":"older-set"})
        assert (await client.get("/api/evaluation/report",params=args)).status_code==404
        assert (await client.get("/eval/loopforge.jsonl")).status_code==404

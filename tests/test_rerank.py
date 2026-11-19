import json
from pathlib import Path

from repopilot.embeddings import LocalEmbedder
from repopilot.hybrid import HybridRetrieval
from repopilot.rerank import rerank
from repopilot.store import Store


def test_definition_can_overtake_mentions_and_keep_provenance():
    candidates = [
        dict(path="README.md", text="Use addNote", start=10, end=10, score=4, channels=["bm25"]),
        dict(path="src/router.js", text="return addNote(title)", start=5, end=5, score=3, channels=["bm25"]),
        dict(path="src/store.js", text="function addNote(title) {}", start=2, end=2, score=2, channels=["vector"]),
    ]
    first, = rerank("addNote 在哪里定义？", candidates, 1)
    assert first["path"] == "src/store.js" and first["retrievalRank"] == 3
    assert first["text"] == candidates[2]["text"] and first["channels"] == ["vector"]
    assert candidates[2]["score"] == first["retrievalScore"] == 2
    assert rerank("addNote 的使用文档", candidates, 1)[0]["path"] == "README.md"
    assert rerank("打开 src/router.js", candidates, 1)[0]["path"] == "src/router.js"


async def test_demo_fallback_finds_validation_and_storage_and_honors_path(tmp_path):
    demo = json.loads((Path(__file__).parents[1] / "repopilot/demo.json").read_text(encoding="utf-8"))
    store = Store(":memory:")
    try:
        repo = store.save_repository(demo["meta"], demo["files"])
        chunks = store.chunks(repo["id"])
        service = HybridRetrieval(store, LocalEmbedder(tmp_path))
        query = "创建笔记时，标题在哪里校验，校验通过后又在哪里保存？"
        baseline = await service.retrieve(repo, chunks, query, mode="bm25")
        result = await service.retrieve(repo, chunks, query, limit=5)
        assert not baseline["items"]
        assert result["warning"] and result["actual"] == "bm25"
        assert result["candidateCount"] >= 2
        assert {"src/router.js", "src/store.js"} <= {item["path"] for item in result["items"]}
        scoped = await service.retrieve(repo, chunks, query, path_filter="src/store.js", limit=1)
        assert [item["path"] for item in scoped["items"]] == ["src/store.js"]
        empty = await service.retrieve(repo, chunks, query, path_filter="missing/")
        assert empty["items"] == [] and empty["candidateCount"] == 0
    finally:
        store.close()

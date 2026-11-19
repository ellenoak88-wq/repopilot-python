"""Validate the pinned LoopForge gold set or evaluate the production retriever."""
import argparse
import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from statistics import mean

from repopilot.chunking import CHUNKER_VERSION
from repopilot.embeddings import make_embedder
from repopilot.hybrid import HybridRetrieval
from repopilot.store import Store


HERE = Path(__file__).resolve().parent
CUTOFFS = (5, 50)


def validate(db, snapshot, cases):
    repo_id = snapshot["repo"]
    row = db.execute("SELECT metadata FROM repositories WHERE id=?", (repo_id,)).fetchone()
    if not row:
        raise ValueError(f"请先导入 {repo_id}。")
    repo = json.loads(row[0])
    if (repo["sha"], repo["indexVersion"]) != (snapshot["repo_sha"], snapshot["index_version"]):
        raise ValueError("仓库 commit 或 indexVersion 与数据集不符，请使用指定快照。")
    ids = set()
    questions = set()
    for case in cases:
        if case["id"] in ids or case["question"] in questions or case["split"] not in ("dev", "test"):
            raise ValueError(f"重复或无效题目：{case['id']}")
        ids.add(case["id"])
        questions.add(case["question"])
        groups = case["evidence_groups"]
        if bool(groups) != case["answerable"] or (not groups and not case.get("missing_evidence")):
            raise ValueError(f"缺少证据或不可回答的理由：{case['id']}")
        for group in groups:
            if not group["fact"] or not group["any_of"]:
                raise ValueError(f"空证据组：{case['id']}")
            for ref in group["any_of"]:
                row = db.execute("SELECT content FROM files WHERE repo=? AND path=?", (repo_id, ref["path"])).fetchone()
                lines = row[0].split("\n") if row else []
                a, b = ref["start"], ref["end"]
                excerpt = "\n".join(lines[a - 1:b])
                if not 1 <= a <= b <= len(lines) or hashlib.sha256(excerpt.encode()).hexdigest() != ref["sha256"]:
                    raise ValueError(f"证据不匹配：{case['id']} {ref['path']} L{a}–{b}")


def covers_reference(items, ref):
    """Check one inclusive source span; overlaps and adjacent chunks can join."""
    cursor = ref["start"]
    spans = sorted((item["start"], item["end"]) for item in items if item["path"] == ref["path"])
    for start, end in spans:
        if start > cursor:
            break
        cursor = max(cursor, end + 1)
        if cursor > ref["end"]:
            return True
    return False


def score(case, items):
    items = items[:max(CUTOFFS)]
    ranks = []
    union_ranks = []
    for group in case["evidence_groups"]:
        first = next((rank for rank, item in enumerate(items, 1)
                      if any(item["path"] == ref["path"] and item["start"] <= ref["start"]
                             and item["end"] >= ref["end"] for ref in group["any_of"])), None)
        ranks.append(first)
        union_ranks.append(next((k for k in range(1, len(items) + 1)
                                 if any(covers_reference(items[:k], ref) for ref in group["any_of"])), None))
    results = [{key: item[key] for key in ("path", "start", "end")} for item in items]
    row = dict(id=case["id"], ranks_at_50=ranks, union_ranks_at_50=union_ranks, results_at_50=results)
    for k in CUTOFFS:
        single_hits = [rank for rank in ranks if rank is not None and rank <= k]
        union_hits = sum(rank is not None and rank <= k for rank in union_ranks)
        row[f"single_chunk_recall_at_{k}"] = len(single_hits) / len(ranks)
        row[f"union_recall_at_{k}"] = union_hits / len(ranks)
        row[f"hit_rate_at_{k}"] = bool(union_hits)
    # Preserve the original per-case fields and their Top-5 meaning.
    row.update(ranks=[r if r is not None and r <= 5 else None for r in ranks],
               union_ranks=[r if r is not None and r <= 5 else None for r in union_ranks],
               results=results[:5], recall=row["single_chunk_recall_at_5"],
               complete=row["single_chunk_recall_at_5"] == 1)
    first = min((r for r in row["ranks"] if r is not None), default=None)
    row["reciprocal_rank"] = 1 / first if first else 0
    return row


def evidence_counts(rows, k):
    single = [rank for row in rows for rank in row["ranks_at_50"]]
    union = [rank for row in rows for rank in row["union_ranks_at_50"]]
    return dict(total=len(single), union=sum(r is not None and r <= k for r in union),
                single_chunk=sum(r is not None and r <= k for r in single))


def dataset_dir():
    return Path(os.environ.get("REPOPILOT_EVAL_DIR") or HERE)


def read_dataset():
    directory = dataset_dir()
    snapshot = json.loads((directory / "loopforge.snapshot.json").read_text(encoding="utf-8"))
    data = (directory / "loopforge.jsonl").read_bytes()
    cases = [json.loads(line) for line in data.splitlines() if line.strip()]
    return snapshot, cases, hashlib.sha256(data).hexdigest()


def copy_corpus(db, source_db, repo_id, include_vectors):
    """Copy only the selected repository and its active index in a single read snapshot."""
    db.execute("ATTACH DATABASE ? AS src", (source_db.as_uri() + "?mode=ro",))
    try:
        with db:
            db.execute("INSERT INTO repositories SELECT * FROM src.repositories WHERE id=?", (repo_id,))
            db.execute("INSERT INTO files SELECT * FROM src.files WHERE repo=?", (repo_id,))
            if include_vectors:
                tables = {row[0] for row in db.execute("SELECT name FROM src.sqlite_master WHERE type='table'")}
                if "vector_indexes" in tables:
                    db.execute("INSERT INTO vector_indexes SELECT * FROM src.vector_indexes WHERE repo=?", (repo_id,))
                if "vector_batches" in tables:
                    db.execute("""INSERT INTO vector_batches SELECT b.* FROM src.vector_batches b
                                  JOIN vector_indexes i ON i.repo=b.repo AND json_extract(i.payload,'$.generation')=b.generation
                                  WHERE b.repo=?""", (repo_id,))
    finally:
        db.execute("DETACH DATABASE src")


async def evaluate(store, retriever, snapshot, cases, mode, split):
    repo = store.get(snapshot["repo"])
    chunks = store.chunks(repo["id"])
    if mode == "hybrid" and retriever.status(repo, chunks)["state"] != "ready":
        raise ValueError("此快照的向量索引未就绪。请完成构建后评估 hybrid。")
    selected = [case for case in cases if case["split"] == split]
    scored = [case for case in selected if case["answerable"]]
    if not scored:
        raise ValueError("当前划分没有可评分的题目。")
    rows = []
    for number, case in enumerate(scored, 1):
        result = await retriever.retrieve(repo, chunks, case["question"], limit=max(CUTOFFS), mode=mode)
        if result["actual"] != mode:
            raise ValueError(f"检索降级：{result['warning']}")
        rows.append(score(case, result["items"]))
        print(f"{mode} · 已评估 {number}/{len(scored)} 题", flush=True)
    names = [f"{metric}_at_{k}" for k in CUTOFFS
             for metric in ("hit_rate", "union_recall", "single_chunk_recall")]
    metrics = {name: mean(row[name] for row in rows) for name in [*names, "recall", "complete", "reciprocal_rank"]}
    report = dict(snapshot=snapshot, split=split, mode=mode, k=5, cutoffs=list(CUTOFFS), scored=len(rows),
                  excludedCount=len(selected)-len(rows), metrics=metrics, evidenceCounts=evidence_counts(rows, 5),
                  evidenceCountsAt50=evidence_counts(rows, 50))
    if split == "dev":
        report.update(cases=rows, unanswerable=[c["id"] for c in selected if not c["answerable"]])
    return report


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
        temp = Path(f.name)
    try:
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


async def run(args):
    snapshot, cases, digest = read_dataset()
    src = args.db.resolve()
    if args.mode == "validate":
        with closing(sqlite3.connect(src.as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("BEGIN")
            validate(db, snapshot, cases)
        print(f"Validated {len(cases)} questions at {snapshot['repo_sha']}", flush=True)
        return
    modes = ["bm25", "hybrid"] if args.mode == "compare" else [args.mode]
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(Path(tmp) / "corpus.db")
        try:
            retriever = HybridRetrieval(store, make_embedder(src.parent))
            with closing(sqlite3.connect((Path(tmp) / "corpus.db").as_uri(), uri=True)) as dest:
                copy_corpus(dest, src, snapshot["repo"], "hybrid" in modes)
            validate(store.db, snapshot, cases)
            if "hybrid" in modes and retriever.status(store.get(snapshot["repo"]))["state"] != "ready":
                raise ValueError("向量索引尚未就绪，请先完成构建。")
            results = [await evaluate(store, retriever, snapshot, cases, mode, args.split) for mode in modes]
        finally:
            store.close()
    report = (dict(snapshot=snapshot, split=args.split, k=5, results={r["mode"]: r for r in results})
              if args.mode == "compare" else results[0])
    report.update(scoringVersion=3, cutoffs=list(CUTOFFS), chunkerVersion=CHUNKER_VERSION, datasetSha256=digest, completedAt=datetime.now(timezone.utc).isoformat(),
                  embeddingModel=retriever.embedder.identity if "hybrid" in modes else None)
    output = args.output or src.parent / "evaluations" / f"loopforge-{args.split}-{args.mode}.json"
    write_report(output, report)
    print(json.dumps({r["mode"]: r["metrics"] for r in results}), flush=True)
    print(f"报告已保存：{output}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/repopilot.db"))
    parser.add_argument("--mode", choices=("validate", "bm25", "hybrid", "compare"), default="validate")
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (ValueError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"评测失败：{error}\n")


if __name__ == "__main__":
    main()

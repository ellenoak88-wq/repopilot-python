"""Validate the pinned LoopForge gold set or evaluate the production retriever."""
import argparse
import asyncio
import hashlib
import json
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from statistics import mean

from repopilot.embeddings import make_embedder
from repopilot.hybrid import HybridRetrieval
from repopilot.store import Store


HERE = Path(__file__).resolve().parent


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


def score(case, items):
    ranks = []
    for group in case["evidence_groups"]:
        first = next((rank for rank, item in enumerate(items, 1)
                      if any(item["path"] == ref["path"] and item["start"] <= ref["start"]
                             and item["end"] >= ref["end"] for ref in group["any_of"])), None)
        ranks.append(first)
    hits = [rank for rank in ranks if rank is not None]
    return dict(id=case["id"], ranks=ranks, recall=len(hits) / len(ranks), complete=len(hits) == len(ranks),
                reciprocal_rank=1 / min(hits) if hits else 0,
                results=[{k: item[k] for k in ("path", "start", "end")} for item in items])


async def evaluate(store, snapshot, cases, args):
    repo = store.get(snapshot["repo"])
    retriever = HybridRetrieval(store, make_embedder(args.db.parent))
    chunks = store.chunks(repo["id"])
    if args.mode == "hybrid" and retriever.status(repo, chunks)["state"] != "ready":
        raise ValueError("此快照的向量索引未就绪。请完成构建后评估 hybrid。")
    rows = []
    excluded = []
    for case in cases:
        if case["split"] != args.split:
            continue
        if not case["answerable"]:
            excluded.append(case["id"])
            continue
        result = await retriever.retrieve(repo, chunks, case["question"], limit=5, mode=args.mode)
        if result["actual"] != args.mode:
            raise ValueError(f"{case['id']} 检索降级：{result['warning']}")
        rows.append(score(case, result["items"]))
        print(f"{case['id']}  evidence ranks {rows[-1]['ranks']}", flush=True)
    metrics = {name: mean(row[name] for row in rows) for name in ("recall", "complete", "reciprocal_rank")}
    report = dict(snapshot=snapshot, split=args.split, mode=args.mode, k=5, scored=len(rows),
                  unanswerable=excluded, metrics=metrics, cases=rows)
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("data/repopilot.db"))
    parser.add_argument("--mode", choices=("validate", "bm25", "hybrid"), default="validate")
    parser.add_argument("--split", choices=("dev", "test"), default="dev")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    args.db = args.db.resolve()
    snapshot = json.loads((HERE / "loopforge.snapshot.json").read_text(encoding="utf-8"))
    cases = [json.loads(line) for line in (HERE / "loopforge.jsonl").read_text(encoding="utf-8").splitlines()]
    with tempfile.TemporaryDirectory() as tmp:
        with closing(sqlite3.connect(args.db.as_uri() + "?mode=ro", uri=True)) as source:
            source.execute("BEGIN")
            validate(source, snapshot, cases)
            print(f"Validated {len(cases)} questions at {snapshot['repo_sha']}", flush=True)
            if args.mode == "validate":
                return
            # A consistent private copy also isolates Store's schema initialization from the live application.
            db = Path(tmp) / "eval.db"
            with closing(sqlite3.connect(db)) as dest:
                source.backup(dest)
        store = Store(db)
        try:
            asyncio.run(evaluate(store, snapshot, cases, args))
        finally:
            store.close()


if __name__ == "__main__":
    main()

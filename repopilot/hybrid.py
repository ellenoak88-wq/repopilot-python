"""Persistent dense retrieval fused with BM25 using reciprocal rank fusion."""
import asyncio
import hashlib
import json
import math
import os
import sqlite3
import uuid
from contextlib import closing
from pathlib import Path

from .chunking import CHUNKER_VERSION, search_text
from .query_rewrite import rewrite_query
from .rerank import rerank
from .retrieval import search
from .store import now

MAX_VECTORS = 150_000


def chunk_key(chunk):
    return (chunk["path"], chunk["start"], chunk["end"])


def fingerprint(repo, chunks, model):
    value = [CHUNKER_VERSION, repo["sha"], model, [[*chunk_key(c), search_text(c)] for c in chunks]]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def normalize(vector, dimension=None):
    if not isinstance(vector, list) or not 1 <= len(vector) <= 4096 or (dimension and len(vector) != dimension):
        raise ValueError("向量维度无效。")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
        raise ValueError("向量包含无效数值。")
    norm = math.sqrt(sum(v * v for v in vector))
    if not math.isfinite(norm) or norm <= 0:
        raise ValueError("向量长度无效。")
    return [v / norm for v in vector]


def fuse(lexical, semantic, limit, k=60):
    merged = {}
    for label, ranked in (("bm25", lexical), ("vector", semantic)):
        seen = set()
        for rank, chunk in enumerate(ranked, 1):
            key = chunk_key(chunk)
            if key in seen:
                continue
            seen.add(key)
            item = merged.setdefault(key, {**chunk, "score": 0, "channels": [], "bm25Rank": None, "vectorRank": None})
            item["score"] += 1 / (k + rank)
            item["channels"].append(label)
            item[label + "Rank"] = rank
    return sorted(merged.values(), key=lambda c: (-c["score"], c["path"], c["start"]))[:limit]


def dense_scores(vectors, query, owners, allowed):
    import numpy as np
    scores = np.asarray(vectors, dtype=np.float32) @ np.asarray(query, dtype=np.float32)
    best = {}
    for owner, score in zip(owners, scores):
        if owner in allowed:
            best[owner] = max(best.get(owner, -1), float(score))
    return sorted(((i, s) for i, s in best.items() if s > 0), key=lambda pair: (-pair[1], pair[0]))


class HybridRetrieval:
    def __init__(self, store, embedder):
        self.store, self.embedder = store, embedder
        self.build_lock = asyncio.Lock()
        self.max_vectors = int(os.getenv("REPO_MAX_VECTOR_WINDOWS", MAX_VECTORS))
        if self.max_vectors < 1:
            raise ValueError("REPO_MAX_VECTOR_WINDOWS 必须为正整数。")
        self.db_file = store.db.execute("PRAGMA database_list").fetchone()[2]
        with store.db:
            store.db.execute("""CREATE TABLE IF NOT EXISTS vector_batches (
                repo TEXT NOT NULL, generation TEXT NOT NULL, seq INTEGER NOT NULL,
                owners BLOB NOT NULL, vectors BLOB NOT NULL,
                PRIMARY KEY(repo,generation,seq)) WITHOUT ROWID""")
            store.db.execute("""CREATE TABLE IF NOT EXISTS vector_indexes (
                repo TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, model TEXT NOT NULL,
                chunk_count INTEGER NOT NULL, vector_count INTEGER NOT NULL,
                built_at TEXT NOT NULL, payload TEXT NOT NULL)""")

    def status(self, repo, chunks=None):
        chunks = self.store.chunks(repo["id"]) if chunks is None else chunks
        row = self.store.db.execute("SELECT fingerprint,model,chunk_count,vector_count,built_at FROM vector_indexes WHERE repo=?", (repo["id"],)).fetchone()
        valid = bool(row and row["fingerprint"] == fingerprint(repo, chunks, self.embedder.identity))
        return dict(state="ready" if valid else "stale" if row else "missing", model=self.embedder.name,
                    chunkCount=len(chunks), windowLimit=self.max_vectors, indexedChunks=row["chunk_count"] if row else 0,
                    vectorCount=row["vector_count"] if row else 0, builtAt=row["built_at"] if row else None)

    async def build(self, repo, progress=lambda *_: None, *, force=False):
        async with self.build_lock:
            chunks = self.store.chunks(repo["id"])
            if not chunks:
                raise ValueError("当前仓库没有可建立向量索引的片段。")
            signature = fingerprint(repo, chunks, self.embedder.identity)
            if not force and self.status(repo, chunks)["state"] == "ready":
                return self.status(repo, chunks)
            counts = []
            for start in range(0, len(chunks), 128):
                progress(f"预检窗口：{start:,}/{len(chunks):,} 个片段", int(8 * start / len(chunks)))
                texts = [search_text(c) for c in chunks[start:start + 128]]
                batch_counts = await self.embedder.window_counts(texts)
                if len(batch_counts) != len(texts) or any(type(c) is not int or c < 1 for c in batch_counts):
                    raise ValueError("分词器返回的窗口数量无效。")
                counts.extend(batch_counts)
            total = sum(counts)
            if total > self.max_vectors:
                raise ValueError(f"预检需要 {total:,} 个向量窗口，上限 {self.max_vectors:,}。请缩小索引范围或调整 REPO_MAX_VECTOR_WINDOWS。")
            progress(f"预检通过：{total:,} 个窗口，上限 {self.max_vectors:,}，开始分批写入", 8)
            repo_id = repo["id"]
            generation = uuid.uuid4().hex
            row = self.store.db.execute("SELECT payload FROM vector_indexes WHERE repo=?", (repo_id,)).fetchone()
            active = json.loads(row[0]).get("generation", "") if row else ""
            # Recover abandoned staging batches after a process restart (single build worker).
            with self.store.db:
                self.store.db.execute("DELETE FROM vector_batches WHERE repo=? AND generation<>?", (repo_id, active))
            try:
                dimension = await self._write_batches(repo_id, generation, chunks, counts, total, progress)
                current = self.store.get(repo_id)
                if not current or fingerprint(current, self.store.chunks(repo_id), self.embedder.identity) != signature:
                    raise ValueError("仓库索引已变化，请重新建立向量索引。")
                payload = json.dumps(dict(generation=generation, dimension=dimension))
                with self.store.db:
                    self.store.db.execute("INSERT OR REPLACE INTO vector_indexes VALUES (?,?,?,?,?,?,?)",
                                          (repo_id, signature, self.embedder.identity, len(chunks), total, now(), payload))
                    self.store.db.execute("DELETE FROM vector_batches WHERE repo=? AND generation<>?", (repo_id, generation))
            except BaseException:
                with self.store.db:
                    self.store.db.execute("DELETE FROM vector_batches WHERE repo=? AND generation=?", (repo_id, generation))
                raise
            return self.status(repo, chunks)

    async def _write_batches(self, repo, generation, chunks, counts, total, progress):
        import numpy as np

        written = dimension = 0
        for start in range(0, len(chunks), 16):
            batch = chunks[start:start + 16]
            seen = [0] * len(batch)
            async for owners, vectors in self.embedder.document_batches([search_text(c) for c in batch]):
                if not owners or len(owners) != len(vectors):
                    raise ValueError("向量服务返回了不完整的片段结果。")
                for owner in owners:
                    if type(owner) is not int or not 0 <= owner < len(batch):
                        raise ValueError("向量片段编号无效。")
                    seen[owner] += 1
                values = np.asarray(vectors, dtype="<f4")
                if values.ndim != 2 or not 1 <= values.shape[1] <= 4096 or dimension not in (0, values.shape[1]):
                    raise ValueError("向量维度无效。")
                dimension = values.shape[1]
                norms = np.linalg.norm(values, axis=1, keepdims=True)
                if not np.isfinite(norms).all() or (norms <= 0).any():
                    raise ValueError("向量长度无效。")
                positions = np.asarray([start + owner for owner in owners], dtype="<i4")
                with self.store.db:
                    self.store.db.execute("INSERT INTO vector_batches VALUES (?,?,?,?,?)",
                                          (repo, generation, written, positions.tobytes(), (values / norms).astype("<f4", copy=False).tobytes()))
                written += len(owners)
                progress(f"已写入 {written:,}/{total:,} 个向量窗口", 8 + int(89 * written / total))
            if seen != counts[start:start + len(batch)]:
                raise ValueError("实际向量窗口数量与预检不符。")
        return dimension

    async def retrieve(self, repo, chunks, query, limit=6, mode="hybrid", path_filter="", previous_question=""):
        if mode not in ("hybrid", "bm25"):
            raise ValueError("检索方式必须为 hybrid 或 bm25。")
        scoped = [c for c in chunks if path_filter.lower() in c["path"].lower()]
        if mode == "bm25":
            items = [{**c, "channels": ["bm25"]} for c in search(scoped, query, limit)]
            return dict(items=items, requested=mode, actual=mode, warning="")

        rewrite = rewrite_query(query, previous_question)
        pool_size = max(30, limit * 3)
        lexical = search(scoped, rewrite.lexical, pool_size)
        candidates = [{**c, "channels": ["bm25"]} for c in lexical]
        actual, warning = "bm25", ""
        if scoped:
            state = self.status(repo, chunks)["state"]
            if state == "ready":
                try:
                    semantic = await self._vector_candidates(repo, chunks, rewrite.semantic, pool_size, path_filter)
                except Exception:
                    # Cancellation is BaseException and deliberately propagates.
                    warning = "本地向量检索暂不可用，已使用 BM25；可重试或重建向量索引。"
                else:
                    candidates = fuse(lexical, semantic, pool_size)
                    actual = "hybrid"
            else:
                warning = "向量索引尚未建立，已使用 BM25。" if state == "missing" else "向量索引已过期，已使用 BM25；请重建索引。"
        return dict(items=rerank(rewrite.semantic, candidates, limit), requested=mode, actual=actual,
                    warning=warning, candidateCount=len(candidates),
                    rewrite=dict(terms=list(rewrite.terms), contextual=rewrite.contextual))

    async def _vector_candidates(self, repo, chunks, query, limit, path_filter):
        vector = await self.embedder.query(query)
        allowed = {i for i, chunk in enumerate(chunks) if path_filter.lower() in chunk["path"].lower()}

        def read(db):
            import numpy as np

            row = db.execute("SELECT fingerprint,payload FROM vector_indexes WHERE repo=?", (repo["id"],)).fetchone()
            if not row or row[0] != fingerprint(repo, chunks, self.embedder.identity):
                raise ValueError("向量索引已变化。")
            index = json.loads(row[1])
            q = normalize(vector, index["dimension"])
            if "generation" not in index:
                return dense_scores(index["vectors"], q, index["owners"], allowed)
            best = np.zeros(len(chunks), dtype=np.float32)
            q = np.asarray(q, dtype=np.float32)
            for owners, block in db.execute("SELECT owners,vectors FROM vector_batches WHERE repo=? AND generation=? ORDER BY seq",
                                            (repo["id"], index["generation"])):
                matrix = np.frombuffer(block, dtype="<f4").reshape(-1, len(q))
                np.maximum.at(best, np.frombuffer(owners, dtype="<i4"), np.einsum("ij,j->i", matrix, q))
            return sorted(((i, float(best[i])) for i in allowed if best[i] > 0), key=lambda pair: (-pair[1], pair[0]))

        def read_snapshot():
            with closing(sqlite3.connect(Path(self.db_file).as_uri() + "?mode=ro", uri=True)) as db:
                db.execute("BEGIN")
                return read(db)

        ranked = await asyncio.to_thread(read_snapshot) if self.db_file else read(self.store.db)
        return [{**chunks[i], "score": score} for i, score in ranked[:limit]]

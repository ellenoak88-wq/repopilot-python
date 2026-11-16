"""Persistent dense retrieval fused with BM25 using reciprocal rank fusion."""
import asyncio
import hashlib
import json
import math

from .retrieval import search
from .store import now

MAX_VECTORS = 10000


def chunk_key(chunk):
    return (chunk["path"], chunk["start"], chunk["end"])


def fingerprint(repo, chunks, model):
    value = [repo["sha"], model, [{k: c[k] for k in ("path", "start", "end", "text")} for c in chunks]]
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
        with store.db:
            store.db.execute("""CREATE TABLE IF NOT EXISTS vector_indexes (
                repo TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, model TEXT NOT NULL,
                chunk_count INTEGER NOT NULL, vector_count INTEGER NOT NULL,
                built_at TEXT NOT NULL, payload TEXT NOT NULL)""")

    def status(self, repo, chunks=None):
        chunks = self.store.chunks(repo["id"]) if chunks is None else chunks
        row = self.store.db.execute("SELECT fingerprint,model,chunk_count,vector_count,built_at FROM vector_indexes WHERE repo=?", (repo["id"],)).fetchone()
        valid = bool(row and row["fingerprint"] == fingerprint(repo, chunks, self.embedder.identity))
        return dict(state="ready" if valid else "stale" if row else "missing", model=self.embedder.name,
                    chunkCount=len(chunks), indexedChunks=row["chunk_count"] if row else 0,
                    vectorCount=row["vector_count"] if row else 0, builtAt=row["built_at"] if row else None)

    async def build(self, repo, progress=lambda *_: None, *, force=False):
        async with self.build_lock:
            chunks = self.store.chunks(repo["id"])
            if not chunks:
                raise ValueError("当前仓库没有可建立向量索引的片段。")
            signature = fingerprint(repo, chunks, self.embedder.identity)
            if not force and self.status(repo, chunks)["state"] == "ready":
                return self.status(repo, chunks)
            owners, vectors, dimension = [], [], None
            progress("准备本地向量模型；首次使用需下载模型", 2)
            for start in range(0, len(chunks), 16):
                batch = chunks[start:start + 16]
                positions, values = await self.embedder.documents([c["text"] or " " for c in batch])
                if len(positions) != len(values) or set(positions) != set(range(len(batch))):
                    raise ValueError("向量服务返回了不完整的片段结果。")
                for owner, value in zip(positions, values):
                    if type(owner) is not int:
                        raise ValueError("向量片段编号无效。")
                    vec = normalize(value, dimension)
                    dimension = len(vec)
                    owners.append(start + owner)
                    vectors.append(vec)
                if len(vectors) > MAX_VECTORS:
                    raise ValueError("向量窗口超过 10,000 个，请减少索引文件后重试。")
                progress(f"已向量化 {min(start + 16, len(chunks))}/{len(chunks)} 个片段", min(95, int((start + len(batch)) / len(chunks) * 95)))
            current = self.store.get(repo["id"])
            if not current or fingerprint(current, self.store.chunks(repo["id"]), self.embedder.identity) != signature:
                raise ValueError("仓库索引已变化，请重新建立向量索引。")
            payload = json.dumps(dict(owners=owners, vectors=vectors, dimension=dimension))
            with self.store.db:
                self.store.db.execute("INSERT OR REPLACE INTO vector_indexes VALUES (?,?,?,?,?,?,?)",
                                      (repo["id"], signature, self.embedder.identity, len(chunks), len(vectors), now(), payload))
            return self.status(repo, chunks)

    async def retrieve(self, repo, chunks, query, limit=6, mode="hybrid", path_filter=""):
        if mode not in ("hybrid", "bm25"):
            raise ValueError("检索方式必须为 hybrid 或 bm25。")
        scoped = [c for c in chunks if path_filter.lower() in c["path"].lower()]
        lexical = search(scoped, query, max(30, limit * 3))
        fallback = dict(items=[{**c, "channels": ["bm25"]} for c in lexical[:limit]], requested=mode, actual="bm25", warning="")
        if mode == "bm25" or not scoped:
            return fallback
        state = self.status(repo, chunks)
        if state["state"] != "ready":
            fallback["warning"] = "向量索引尚未建立，已使用 BM25。" if state["state"] == "missing" else "向量索引已过期，已使用 BM25；请重建索引。"
            return fallback
        try:
            row = self.store.db.execute("SELECT payload FROM vector_indexes WHERE repo=?", (repo["id"],)).fetchone()
            index = json.loads(row["payload"])
            query_vector = normalize(await self.embedder.query(query), index["dimension"])
            allowed = {i for i, c in enumerate(chunks) if path_filter.lower() in c["path"].lower()}
            ranked = await asyncio.to_thread(dense_scores, index["vectors"], query_vector, index["owners"], allowed)
            semantic = [{**chunks[i], "score": score} for i, score in ranked[:max(30, limit * 3)]]
            return dict(items=fuse(lexical, semantic, limit), requested=mode, actual="hybrid", warning="")
        except Exception:
            # Cancellation is BaseException and deliberately propagates.
            fallback["warning"] = "本地向量检索暂不可用，已使用 BM25；可重试或重建向量索引。"
            return fallback

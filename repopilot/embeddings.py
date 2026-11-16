"""Lazy CPU embeddings; inference stays off the ASGI event loop."""
import asyncio
import os
import threading
from pathlib import Path

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


class LocalEmbedder:
    # Version includes the preprocessing contract and pinned runtime version.
    identity = "fastembed-0.9.1:" + MODEL + ":overflow-v1"
    name = "多语言 MiniLM · 本地 CPU"

    def __init__(self, cache_dir):
        self.cache_dir = str(Path(cache_dir))
        self.model = None
        self.lock = threading.Lock()

    def _load(self):
        if self.model is None:
            from fastembed import TextEmbedding
            self.model = TextEmbedding(model_name=MODEL, cache_dir=self.cache_dir, threads=2,
                                       providers=["CPUExecutionProvider"])
        return self.model

    def _documents(self, texts):
        with self.lock:
            model = self._load()
            # FastEmbed 0.9.1 exposes the underlying Hugging Face tokenizer here.
            # Overflow encodings retain the tail of long chunks instead of silently
            # embedding only the first 128 tokens. Results still cite original lines.
            tokenizer = model.model.tokenizer
            windows, owners = [], []
            for owner, text in enumerate(texts):
                encoded = tokenizer.encode(text)
                for window in [encoded, *encoded.overflowing]:
                    windows.append(tokenizer.decode(window.ids, skip_special_tokens=True) or " ")
                    owners.append(owner)
                    if len(windows) > 10000:
                        raise ValueError("向量窗口超过上限，请减少索引文件后重试。")
            vectors = [v.tolist() for v in model.embed(windows, batch_size=32)]
            return owners, vectors

    async def documents(self, texts):
        return await asyncio.to_thread(self._documents, texts)

    def _query(self, text):
        with self.lock:
            return next(self._load().query_embed(text)).tolist()

    async def query(self, text):
        return await asyncio.to_thread(self._query, text)


def make_embedder(data_dir):
    return LocalEmbedder(os.getenv("EMBEDDING_CACHE_DIR") or str(Path(data_dir) / "models"))

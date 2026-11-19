"""Lazy CPU embeddings; inference stays off the ASGI event loop."""
import asyncio
import os
import threading
from pathlib import Path

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
HF_REPO = "Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
REVISION = "faf4aa4225822f3bc6376869cb1164e8e3feedd0"
MODEL_FILES = ["config.json", "tokenizer_config.json", "tokenizer.json", "special_tokens_map.json", "model_optimized.onnx"]


class LocalEmbedder:
    # Version includes the preprocessing contract and pinned runtime version.
    identity = "fastembed-0.9.1:" + MODEL + ":" + REVISION + ":overflow-v1"
    name = "多语言 MiniLM · 本地 CPU"

    def __init__(self, cache_dir):
        self.cache_dir = str(Path(cache_dir))
        self.model = None
        self.lock = threading.Lock()

    def _load(self, *, allow_download=True):
        if self.model is None:
            from fastembed import TextEmbedding
            from huggingface_hub import snapshot_download
            from huggingface_hub.errors import LocalEntryNotFoundError
            try:
                directory = snapshot_download(HF_REPO, revision=REVISION, cache_dir=self.cache_dir, local_files_only=True, allow_patterns=MODEL_FILES)
            except LocalEntryNotFoundError:
                directory = None
            if not directory or not all((Path(directory) / name).is_file() for name in MODEL_FILES):
                if not allow_download:
                    raise ValueError("本地模型缓存缺失，请重建向量索引以下载模型。")
                directory = snapshot_download(HF_REPO, revision=REVISION, cache_dir=self.cache_dir, allow_patterns=MODEL_FILES)
            self.model = TextEmbedding(model_name=MODEL, cache_dir=self.cache_dir, threads=2,
                                       providers=["CPUExecutionProvider"], specific_model_path=directory, local_files_only=True)
        return self.model

    def _counts(self, texts):
        with self.lock:
            tokenizer = self._load().model.tokenizer
            return [1 + len(tokenizer.encode(text).overflowing) for text in texts]

    async def window_counts(self, texts):
        return await asyncio.to_thread(self._counts, texts)

    def _windows(self, texts):
        with self.lock:
            model = self._load()
            # FastEmbed 0.9.1 exposes the underlying Hugging Face tokenizer here.
            # Overflow encodings retain the tail of long chunks instead of silently
            # embedding only the first context window. Results still cite original lines.
            tokenizer = model.model.tokenizer
            windows, owners = [], []
            for owner, text in enumerate(texts):
                encoded = tokenizer.encode(text)
                for window in [encoded, *encoded.overflowing]:
                    windows.append(tokenizer.decode(window.ids, skip_special_tokens=True) or " ")
                    owners.append(owner)
            return owners, windows

    def _embed(self, windows):
        with self.lock:
            return [v.tolist() for v in self._load().embed(windows, batch_size=32)]

    async def document_batches(self, texts):
        owners, windows = await asyncio.to_thread(self._windows, texts)
        for start in range(0, len(windows), 128):
            vectors = await asyncio.to_thread(self._embed, windows[start:start + 128])
            yield owners[start:start + 128], vectors

    async def documents(self, texts):
        owners, vectors = [], []
        async for positions, batch in self.document_batches(texts):
            owners.extend(positions)
            vectors.extend(batch)
        return owners, vectors

    def _query(self, text):
        with self.lock:
            return next(self._load(allow_download=False).query_embed(text)).tolist()

    async def query(self, text):
        return await asyncio.to_thread(self._query, text)


def make_embedder(data_dir):
    return LocalEmbedder(os.getenv("EMBEDDING_CACHE_DIR") or str(Path(data_dir) / "models"))

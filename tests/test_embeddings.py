from types import SimpleNamespace

import numpy as np
import pytest

from repopilot.embeddings import LocalEmbedder, MODEL_FILES, REVISION


async def test_document_windows_keep_overflow_and_original_owners(tmp_path):
    class Tokenizer:
        def encode(self, text):
            return SimpleNamespace(ids=[1],overflowing=[SimpleNamespace(ids=[2])] if text=='long' else [])
        def decode(self, ids, **kwargs): return str(ids[0])
    seen=[]
    class Model:
        model=SimpleNamespace(tokenizer=Tokenizer())
        def embed(self, texts, **kwargs):
            seen.extend(texts)
            return [np.array([1.,0.]) for _ in texts]
    provider=LocalEmbedder(tmp_path)
    provider.model=Model()
    owners,vectors=await provider.documents(['long','short'])
    assert owners==[0,0,1] and len(vectors)==3 and seen==['1','2','1']


def test_complete_cache_uses_pinned_offline_model(tmp_path, monkeypatch):
    import fastembed
    import huggingface_hub
    for name in MODEL_FILES: (tmp_path/name).touch()
    calls=[]
    def snapshot(repo, **kwargs):
        calls.append(kwargs)
        assert kwargs['local_files_only'] and kwargs['revision']==REVISION
        return str(tmp_path)
    monkeypatch.setattr(huggingface_hub,'snapshot_download',snapshot)
    monkeypatch.setattr(fastembed,'TextEmbedding',lambda **kwargs:kwargs)
    model=LocalEmbedder(tmp_path)._load(allow_download=False)
    assert len(calls)==1 and model['local_files_only'] and model['specific_model_path']==str(tmp_path)


def test_query_does_not_download_missing_weights(tmp_path,monkeypatch):
    import huggingface_hub
    from huggingface_hub.errors import LocalEntryNotFoundError
    def missing(*args,**kwargs):
        assert kwargs['local_files_only'] is True
        raise LocalEntryNotFoundError('missing')
    monkeypatch.setattr(huggingface_hub,'snapshot_download',missing)
    with pytest.raises(ValueError,match='缓存缺失'):
        LocalEmbedder(tmp_path)._load(allow_download=False)

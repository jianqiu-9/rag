"""首次下载和离线运行的边界测试：只替换提供方，不接触网络或真实凭据。"""

import json
from types import SimpleNamespace

import pytest
from app.cli import download_model
from app.config import Settings
from app.embedding import Embedding
from app.errors import AppError


def test_download_pins_resolved_sha_and_writes_lock_only_after_success(tmp_path, monkeypatch):
    hub = pytest.importorskip("huggingface_hub")
    revision = "b" * 40
    calls = []
    monkeypatch.setattr(
        hub, "HfApi", lambda: SimpleNamespace(model_info=lambda *a, **k: SimpleNamespace(sha=revision))
    )
    monkeypatch.setattr(hub, "snapshot_download", lambda *args, **kwargs: calls.append(kwargs))
    settings = Settings(_env_file=None, model_cache_dir=tmp_path)
    download_model(settings, "main")
    assert calls[0]["revision"] == revision
    assert json.loads((tmp_path / "model-lock.json").read_text())["revision"] == revision


def test_permission_failure_does_not_create_success_lock(tmp_path, monkeypatch):
    hub = pytest.importorskip("huggingface_hub")
    from huggingface_hub.errors import HfHubHTTPError

    monkeypatch.setattr(
        hub, "HfApi", lambda: SimpleNamespace(model_info=lambda *a, **k: SimpleNamespace(sha="b" * 40))
    )

    def denied(*args, **kwargs):
        raise HfHubHTTPError("provider details must not reach user")

    monkeypatch.setattr(hub, "snapshot_download", denied)
    with pytest.raises(AppError) as exc:
        download_model(Settings(_env_file=None, model_cache_dir=tmp_path), "main")
    assert "provider details" not in exc.value.message
    assert not (tmp_path / "model-lock.json").exists()


def test_runtime_is_local_only_and_uses_separate_query_document_prompts(tmp_path, monkeypatch):
    transformers = pytest.importorskip("sentence_transformers")
    calls = []
    fake = SimpleNamespace(
        prompts={"query": "query: ", "document": "document: "},
        tokenizer=lambda text, **kwargs: {"input_ids": [1] * (len(text) + 2)},
        encode_query=lambda texts, **kwargs: [[2.0] + [0.0] * 767 for _ in texts],
        encode_document=lambda texts, **kwargs: [[2.0] + [0.0] * 767 for _ in texts],
    )

    def factory(*args, **kwargs):
        calls.append(kwargs)
        return fake

    monkeypatch.setattr(transformers, "SentenceTransformer", factory)
    embedding = Embedding(Settings(_env_file=None, model_cache_dir=tmp_path, embedding_revision="c" * 40))
    assert embedding.query("问题")[0] == 1.0
    assert embedding.documents(["原文"])[0][0] == 1.0
    assert embedding.count_tokens("问题", document=False) == len("query: 问题") + 2
    assert embedding.count_tokens("原文") == len("document: 原文") + 2
    assert len(calls) == 1
    assert calls[0]["local_files_only"] is True
    assert calls[0]["revision"] == "c" * 40

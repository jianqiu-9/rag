"""不加载真实权重也能验证模型边界；真实效果由独立验收集验证。"""

import json

import numpy as np
import pytest
from app.config import Settings
from app.embedding import Embedding
from app.errors import AppError
from app.parser import parse_document, split_to_token_limit


@pytest.mark.parametrize("vector", [[[0.0] * 768], [[1.0] * 767], [[float("nan")] * 768]])
def test_zero_wrong_dimension_and_nan_are_rejected(vector):
    with pytest.raises(AppError):
        Embedding.validate_vectors(vector, 1)


def test_vectors_are_normalized_before_pgvector():
    result = Embedding.validate_vectors([[2.0] + [0.0] * 767], 1)
    assert np.linalg.norm(result[0]) == pytest.approx(1)


def test_missing_revision_never_falls_back_to_network(tmp_path):
    model = Embedding(Settings(_env_file=None, model_cache_dir=tmp_path))
    with pytest.raises(AppError) as exc:
        _ = model.metadata
    assert exc.value.code == "MODEL_NOT_READY"
    (tmp_path / "model-lock.json").write_text(
        json.dumps({"model_id": "google/embeddinggemma-300m", "revision": "a" * 40})
    )
    assert model.metadata["revision"] == "a" * 40


def test_profile_changes_when_preprocessing_changes(tmp_path):
    first = Embedding(Settings(_env_file=None, embedding_revision="a" * 40, chunk_chars=700))
    second = Embedding(Settings(_env_file=None, embedding_revision="a" * 40, chunk_chars=600))
    assert first.profile != second.profile


def test_token_limit_splits_long_lines_without_silent_truncation():
    text = "长文本内容" * 100
    chunks = split_to_token_limit(parse_document(text), lambda value: len(value) * 5, limit=100)
    assert all(len(chunk.content) * 5 <= 100 for chunk in chunks)
    assert "".join(chunk.content for chunk in chunks) == text
    assert all(chunk.line_start == 1 and chunk.line_end == 1 for chunk in chunks)

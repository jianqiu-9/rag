"""EmbeddingGemma 本地适配器。模型重用、推理串行，避免 M1/8GB 上重复加载权重。"""

import hashlib
import json
import re
import threading

import numpy as np

from app.config import Settings
from app.errors import AppError


class Embedding:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._model = None
        self._lock = threading.RLock()

    @property
    def metadata(self) -> dict:
        revision = self.settings.embedding_revision
        lock_file = self.settings.model_cache_dir / "model-lock.json"
        if not revision and lock_file.exists():
            try:
                lock = json.loads(lock_file.read_text(encoding="utf-8"))
                if lock["model_id"] == self.settings.embedding_model:
                    revision = lock["revision"]
            except (ValueError, KeyError, OSError) as exc:
                raise AppError("MODEL_LOCK_INVALID", "模型锁文件无效，请重新下载模型", 503) from exc
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise AppError("MODEL_NOT_READY", "请先运行模型下载命令，锁定具体 revision 后再入库", 503, True)
        return {
            "model_id": self.settings.embedding_model,
            "revision": revision,
            "dimensions": 768,
            "normalization": "l2",
            "dtype": "float32",
            "encoding": "sentence-transformers-query-document-v1",
            "parser": "qa-line-v1",
            "chunk_chars": self.settings.chunk_chars,
            "chunk_overlap": self.settings.chunk_overlap,
        }

    @property
    def profile(self) -> str:
        return hashlib.sha256(json.dumps(self.metadata, sort_keys=True).encode()).hexdigest()

    def _load(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(
                    self.settings.embedding_model,
                    revision=self.metadata["revision"],
                    cache_folder=str(self.settings.model_cache_dir),
                    local_files_only=True,
                    device=self.settings.embedding_device,
                    model_kwargs={"torch_dtype": "float32"},
                )
                self._model.max_seq_length = 2048
            except AppError:
                raise
            except Exception as exc:
                # 不把 HF 请求地址、令牌或文件系统细节返回给前端。
                raise AppError(
                    "MODEL_NOT_READY", "本地模型未就绪，请安装 models 依赖并显式下载模型", 503, True
                ) from exc
        return self._model

    def count_tokens(self, text: str, document: bool = True) -> int:
        with self._lock:
            model = self._load()
            prefix = model.prompts.get("document" if document else "query", "")
            return len(model.tokenizer(prefix + text, truncation=False, add_special_tokens=True)["input_ids"])

    def context_tokens(self, text: str) -> int:
        # DeepSeek tokenizer 与 Gemma 不同：同时按 UTF-8 字节作保守预算上界。
        # 预算宁可少放一些证据，也不静默裁断原文或依赖远程自动截断。
        return max(self.count_tokens(text), len(text.encode("utf-8")))

    @staticmethod
    def validate_vectors(vectors, expected: int) -> list[list[float]]:
        array = np.asarray(vectors, dtype=np.float32)
        if array.shape != (expected, 768) or not np.isfinite(array).all():
            raise AppError("INVALID_EMBEDDING", "模型返回了无效维度或非有限向量", 503)
        norms = np.linalg.norm(array, axis=1, keepdims=True)
        if np.any(norms <= 0):
            raise AppError("INVALID_EMBEDDING", "模型返回了零向量", 503)
        return (array / norms).tolist()

    def documents(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            model = self._load()
            if any(self.count_tokens(t) > 2048 for t in texts):
                raise AppError("TOKEN_LIMIT", "文本块超过模型输入限制，请检查切分配置", 422)
            vectors = model.encode_document(
                texts,
                batch_size=self.settings.embedding_batch_size,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return self.validate_vectors(vectors, len(texts))

    def query(self, text: str) -> list[float]:
        with self._lock:
            model = self._load()
            if self.count_tokens(text, document=False) > 2048:
                raise AppError("QUERY_TOO_LONG", "问题超过模型输入上限，请缩短问题", 422)
            return self.validate_vectors(model.encode_query([text], normalize_embeddings=True), 1)[0]

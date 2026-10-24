"""原文件落盘及文档管理。文件先持久化，任务才允许进入 pending 队列。"""

import hashlib
import logging
import os
from pathlib import Path
from uuid import uuid4

from app.errors import AppError
from app.parser import normalize_filename

logger = logging.getLogger(__name__)


class FileStore:
    def __init__(self, data_dir: Path):
        self.root = data_dir / "uploads"
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        candidate = (self.root / name).resolve()
        if candidate.parent != self.root.resolve():
            raise AppError("INVALID_STORAGE_PATH", "原文件存储路径无效", 500)
        return candidate

    def write(self, name: str, content: bytes):
        temporary = self.path(name + ".tmp")
        try:
            with temporary.open("xb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path(name))
        finally:
            temporary.unlink(missing_ok=True)

    def read(self, name: str) -> str:
        try:
            return self.path(name).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError) as exc:
            raise AppError("SOURCE_UNAVAILABLE", "本地原文件不可读取，请重新导入", 503, True) from exc

    def remove(self, name: str):
        self.path(name).unlink(missing_ok=True)


class DocumentService:
    def __init__(self, settings, repository, store: FileStore):
        self.settings, self.repository, self.store = settings, repository, store

    def upload(self, filename: str, stream):
        filename = normalize_filename(filename)
        suffix = Path(filename).suffix.lower()
        if suffix not in {".md", ".txt"}:
            raise AppError("UNSUPPORTED_FILE", "仅支持 UTF-8 编码的 Markdown/TXT 文件", 415)
        maximum = self.settings.max_upload_mb * 1024 * 1024
        content = stream.read(maximum + 1)
        if len(content) > maximum:
            raise AppError("FILE_TOO_LARGE", f"单文件不能超过 {self.settings.max_upload_mb} MB", 413)
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise AppError("INVALID_ENCODING", "文件必须采用 UTF-8 编码", 422) from exc
        if not text.strip() or "\x00" in text:
            raise AppError("EMPTY_OR_BINARY_FILE", "文件为空或包含二进制内容", 422)
        document_id = str(uuid4())
        storage_path = document_id + suffix
        self.store.write(storage_path, content)
        try:
            row, duplicate = self.repository.add_upload(
                document_id, filename, hashlib.sha256(content).hexdigest(), storage_path
            )
        except Exception:
            self.store.remove(storage_path)
            raise
        if duplicate:
            self.store.remove(storage_path)
        logger.info("upload document_id=%s duplicate=%s", row["id"], duplicate)
        return row, duplicate

    def source(self, document_id: str):
        row = self.repository.source(document_id)
        content = self.store.read(row["storage_path"])
        # 读取文件时也可能发生版本切换，再查一次，避免返回已失效版本。
        self.repository.source(document_id)
        return {"document_id": document_id, "filename": row["filename"], "content": content}

    def collect_garbage(self):
        for row in self.repository.pending_gc():
            try:
                self.store.remove(row["storage_path"])
                self.repository.finish_gc(row["storage_path"])
            except OSError:
                logger.error("file_gc_failed")

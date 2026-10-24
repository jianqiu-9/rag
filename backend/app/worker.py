"""单 worker 执行持久化入库任务。等待用 Event，关闭服务时可立即唤醒。"""

import logging
import threading
import time

from app.audit import AuditStore
from app.db import WORKER_LOCK
from app.errors import AppError
from app.parser import parse_document, split_to_token_limit

logger = logging.getLogger(__name__)


class IngestionWorker:
    def __init__(self, settings, repository, embedding, documents):
        self.settings, self.repository, self.embedding, self.documents = (
            settings,
            repository,
            embedding,
            documents,
        )
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name="rag-ingestion", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)

    def process_once(self, leader=None) -> bool:
        task = self.repository.claim()
        if task is None:
            return False
        document_id = str(task["id"])
        attempt = task["attempt_count"]
        try:
            text = self.documents.store.read(task["storage_path"])
            chunks = parse_document(text, self.settings.chunk_chars, self.settings.chunk_overlap)
            chunks = split_to_token_limit(chunks, self.embedding.count_tokens)
            if not chunks:
                raise AppError("NO_CONTENT", "未解析到可索引内容", 422)
            vectors = []
            batch = self.settings.embedding_batch_size
            for offset in range(0, len(chunks), batch):
                if self.stop_event.is_set():
                    raise AppError("INTERRUPTED", "服务停止，请点击重试", 503, True)
                if not self.repository.is_processing(document_id):
                    return True
                vectors.extend(self.embedding.documents([c.content for c in chunks[offset : offset + batch]]))
            if leader is not None:
                leader.execute("SELECT 1")  # 会话锁丢失时不得提交此 worker 的结果。
            completed = self.repository.complete(
                document_id, chunks, vectors, self.embedding.profile, self.embedding.metadata, attempt=attempt
            )
            if completed:
                logger.info("ingestion_completed document_id=%s chunks=%d", document_id, len(chunks))
            else:
                logger.info("ingestion_discarded document_id=%s attempt=%d", document_id, attempt)
        except AppError as exc:
            self.repository.fail(document_id, exc.code, exc.message, attempt=attempt)
            logger.warning("ingestion_failed document_id=%s code=%s", document_id, exc.code)
        except Exception as exc:
            self.repository.fail(
                document_id, "INGESTION_FAILED", "入库失败，请检查本地模型与数据库后重试", attempt=attempt
            )
            logger.error("ingestion_failed document_id=%s type=%s", document_id, type(exc).__name__)
        return True

    def run(self):
        while not self.stop_event.is_set():
            try:
                with self.repository.db.connect(autocommit=True) as leader:
                    acquired = leader.execute(
                        "SELECT pg_try_advisory_lock(%s) AS acquired", (WORKER_LOCK,)
                    ).fetchone()["acquired"]
                    if not acquired:
                        self.stop_event.wait(2)
                        continue
                    self.repository.recover()
                    audit = AuditStore(self.repository.db, self.settings)
                    next_cleanup = 0.0
                    while not self.stop_event.is_set():
                        leader.execute("SELECT 1")
                        if time.monotonic() >= next_cleanup:
                            recovered = audit.recover_interrupted()
                            purged = audit.purge()
                            logger.info("audit_maintenance recovered=%d purged=%d", recovered, purged)
                            next_cleanup = time.monotonic() + self.settings.audit_cleanup_interval_seconds
                        self.documents.collect_garbage()
                        if not self.process_once(leader):
                            self.stop_event.wait(1)
            except Exception as exc:
                logger.error("worker_unavailable type=%s", type(exc).__name__)
                self.stop_event.wait(3)

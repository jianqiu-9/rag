"""持久化业务边界：所有版本切换、重试、删除都经过同一把短事务锁。"""

from uuid import uuid4

from psycopg.types.json import Jsonb

from app.db import Database
from app.errors import AppError
from app.schemas import Evidence

PUBLIC_FIELDS = "id, logical_document_id, filename, sha256, status, is_active, replaces_document_id, attempt_count, error_code, error_message, created_at, updated_at"


class Repository:
    def __init__(self, db: Database):
        self.db = db

    def add_upload(self, document_id: str, filename: str, sha256: str, storage_path: str):
        with self.db.transaction(mutation=True) as conn:
            duplicate = conn.execute(
                f"SELECT {PUBLIC_FIELDS} FROM documents WHERE sha256=%s AND (is_active OR status IN ('pending','processing')) LIMIT 1",
                (sha256,),
            ).fetchone()
            if duplicate:
                return duplicate, True
            logical = conn.execute(
                "SELECT id FROM logical_documents WHERE normalized_filename=%s", (filename,)
            ).fetchone()
            if logical is None:
                logical = {"id": uuid4()}
                conn.execute(
                    "INSERT INTO logical_documents(id, normalized_filename) VALUES (%s,%s)",
                    (logical["id"], filename),
                )
            logical_id = logical["id"]
            if conn.execute(
                "SELECT 1 FROM documents WHERE logical_document_id=%s AND status IN ('pending','processing')",
                (logical_id,),
            ).fetchone():
                raise AppError("UPLOAD_IN_PROGRESS", "同名文件正在处理，请等待完成后再上传", 409, True)
            active = conn.execute(
                "SELECT id FROM documents WHERE logical_document_id=%s AND is_active", (logical_id,)
            ).fetchone()
            # 清理此前失败的待处理版本，列表始终展示当前有效版及最新一次尝试。
            stale = conn.execute(
                "DELETE FROM documents WHERE logical_document_id=%s AND NOT is_active RETURNING storage_path",
                (logical_id,),
            ).fetchall()
            self._queue_gc(conn, stale)
            row = conn.execute(
                f"""INSERT INTO documents(id,logical_document_id,filename,sha256,storage_path,status,replaces_document_id)
                VALUES (%s,%s,%s,%s,%s,'pending',%s) RETURNING {PUBLIC_FIELDS}""",
                (document_id, logical_id, filename, sha256, storage_path, active["id"] if active else None),
            ).fetchone()
            return row, False

    def list_documents(self):
        with self.db.transaction() as conn:
            return conn.execute(f"""SELECT d.*, EXISTS(SELECT 1 FROM documents a WHERE a.logical_document_id=d.logical_document_id AND a.is_active) AS has_active_version
                FROM (SELECT DISTINCT ON (logical_document_id) {PUBLIC_FIELDS} FROM documents ORDER BY logical_document_id, created_at DESC) d
                ORDER BY d.created_at DESC""").fetchall()

    def get_document(self, document_id: str):
        with self.db.transaction() as conn:
            row = conn.execute(
                f"SELECT {PUBLIC_FIELDS} FROM documents WHERE id=%s", (document_id,)
            ).fetchone()
            if row is None:
                raise AppError("DOCUMENT_NOT_FOUND", "文档不存在或已被替换/删除", 404)
            return row

    def source(self, document_id: str):
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT id,filename,storage_path FROM documents WHERE id=%s AND is_active", (document_id,)
            ).fetchone()
            if row is None:
                raise AppError("SOURCE_CHANGED", "来源已删除或被替换，请重新查询", 409, True)
            return row

    def retry(self, document_id: str):
        with self.db.transaction(mutation=True) as conn:
            row = conn.execute("SELECT * FROM documents WHERE id=%s FOR UPDATE", (document_id,)).fetchone()
            if row is None:
                raise AppError("DOCUMENT_NOT_FOUND", "文档不存在", 404)
            if row["status"] != "failed":
                raise AppError("TASK_NOT_FAILED", "仅失败或中断的任务可重试", 409)
            if conn.execute(
                "SELECT 1 FROM documents WHERE logical_document_id=%s AND status IN ('pending','processing')",
                (row["logical_document_id"],),
            ).fetchone():
                raise AppError("UPLOAD_IN_PROGRESS", "已有同名任务正在处理", 409, True)
            conn.execute("DELETE FROM chunks WHERE document_id=%s", (document_id,))
            return conn.execute(
                f"UPDATE documents SET status='pending', error_code=NULL,error_message=NULL,updated_at=now() WHERE id=%s RETURNING {PUBLIC_FIELDS}",
                (document_id,),
            ).fetchone()

    def delete(self, document_id: str):
        with self.db.transaction(mutation=True) as conn:
            row = conn.execute(
                "SELECT logical_document_id FROM documents WHERE id=%s", (document_id,)
            ).fetchone()
            if row is None:
                raise AppError("DOCUMENT_NOT_FOUND", "文档不存在", 404)
            paths = conn.execute(
                "SELECT storage_path FROM documents WHERE logical_document_id=%s",
                (row["logical_document_id"],),
            ).fetchall()
            self._queue_gc(conn, paths)
            conn.execute("DELETE FROM logical_documents WHERE id=%s", (row["logical_document_id"],))

    @staticmethod
    def _queue_gc(conn, paths):
        for row in paths:
            conn.execute(
                "INSERT INTO file_gc(storage_path) VALUES (%s) ON CONFLICT DO NOTHING", (row["storage_path"],)
            )

    def recover(self):
        """只有拿到单 worker 会话锁后才恢复，不能把另一进程的活任务误判为中断。"""
        with self.db.transaction(mutation=True) as conn:
            conn.execute(
                "UPDATE documents SET status='failed',error_code='INTERRUPTED',error_message='服务中断，请点击重试',updated_at=now() WHERE status='processing'"
            )

    def claim(self):
        with self.db.transaction(mutation=True) as conn:
            return conn.execute("""UPDATE documents SET status='processing',attempt_count=attempt_count+1,updated_at=now()
                WHERE id=(SELECT id FROM documents WHERE status='pending' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1)
                RETURNING *""").fetchone()

    def is_processing(self, document_id: str) -> bool:
        with self.db.transaction() as conn:
            return (
                conn.execute(
                    "SELECT 1 FROM documents WHERE id=%s AND status='processing'", (document_id,)
                ).fetchone()
                is not None
            )

    def complete(
        self, document_id: str, chunks, vectors, profile: str, metadata: dict, attempt: int | None = None
    ):
        with self.db.transaction(mutation=True) as conn:
            document = conn.execute(
                "SELECT * FROM documents WHERE id=%s AND status='processing' FOR UPDATE", (document_id,)
            ).fetchone()
            if document is None or (attempt is not None and document["attempt_count"] != attempt):
                return False  # 删除可先于推理完成，绝不重建一个已删除的文档。
            self._assert_profile(conn, profile)
            conn.execute("DELETE FROM chunks WHERE document_id=%s", (document_id,))
            with conn.cursor() as cursor:
                cursor.executemany(
                    """INSERT INTO chunks(id,document_id,section,content,question,answer,line_start,line_end,embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::vector)""",
                    [
                        (
                            uuid4(),
                            document_id,
                            c.section,
                            c.content,
                            c.question,
                            c.answer,
                            c.line_start,
                            c.line_end,
                            str(v),
                        )
                        for c, v in zip(chunks, vectors, strict=True)
                    ],
                )
            obsolete = conn.execute(
                "DELETE FROM documents WHERE logical_document_id=%s AND id<>%s RETURNING storage_path",
                (document["logical_document_id"], document_id),
            ).fetchall()
            self._queue_gc(conn, obsolete)
            conn.execute(
                "UPDATE documents SET status='completed',is_active=true,embedding_profile=%s,embedding_config=%s,error_code=NULL,error_message=NULL,updated_at=now() WHERE id=%s",
                (profile, Jsonb(metadata), document_id),
            )
            return True

    def fail(self, document_id: str, code: str, message: str, attempt: int | None = None):
        with self.db.transaction(mutation=True) as conn:
            task = conn.execute(
                "SELECT attempt_count FROM documents WHERE id=%s AND status='processing' FOR UPDATE",
                (document_id,),
            ).fetchone()
            if task is None or (attempt is not None and task["attempt_count"] != attempt):
                return
            conn.execute("DELETE FROM chunks WHERE document_id=%s", (document_id,))
            conn.execute(
                "UPDATE documents SET status='failed',error_code=%s,error_message=%s,updated_at=now() WHERE id=%s AND status='processing'",
                (code, message, document_id),
            )

    @staticmethod
    def _assert_profile(conn, profile):
        if conn.execute(
            "SELECT 1 FROM documents WHERE is_active AND embedding_profile<>%s LIMIT 1", (profile,)
        ).fetchone():
            raise AppError(
                "INDEX_PROFILE_MISMATCH", "模型或预处理配置已改变，请备份并清空旧索引后重新导入", 409
            )

    def has_active_documents(self):
        with self.db.transaction() as conn:
            return conn.execute("SELECT 1 FROM documents WHERE is_active LIMIT 1").fetchone() is not None

    def search(self, vector: list[float], top_k: int, threshold: float, profile: str):
        with self.db.transaction() as conn:
            self._assert_profile(conn, profile)
            rows = conn.execute(
                """SELECT c.id::text AS chunk_id,d.id::text AS document_id,d.filename,c.section,c.content,c.question,c.answer,c.line_start,c.line_end,
                1-(c.embedding <=> %s::vector) AS score
                FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.is_active AND d.embedding_profile=%s
                AND 1-(c.embedding <=> %s::vector)>=%s ORDER BY c.embedding <=> %s::vector,c.id LIMIT %s""",
                (str(vector), profile, str(vector), threshold, str(vector), top_k),
            ).fetchall()
            return [Evidence(**r) for r in rows]

    def assert_sources_active(self, ids: list[str]):
        if not ids:
            return
        with self.db.transaction(mutation=True) as conn:
            count = conn.execute(
                "SELECT count(*) AS count FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id::text=ANY(%s) AND d.is_active",
                (list(set(ids)),),
            ).fetchone()["count"]
            if count != len(set(ids)):
                raise AppError("SOURCE_CHANGED", "回答期间来源已删除或被替换，请重新查询", 409, True)

    def log_query(self, request_id, ids, retrieval_ms, generation_ms, status, code, model):
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO query_logs(request_id,retrieved_chunk_ids,retrieval_ms,generation_ms,result_status,error_code,model_name) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (request_id, Jsonb(ids), retrieval_ms, generation_ms, status, code, model),
            )

    def pending_gc(self):
        with self.db.transaction() as conn:
            return conn.execute("SELECT storage_path FROM file_gc ORDER BY created_at LIMIT 100").fetchall()

    def finish_gc(self, path: str):
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM file_gc WHERE storage_path=%s", (path,))

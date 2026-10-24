"""问答审计存储：短事务写入、逐次调用快照、会话租约及可控留存。

完整业务内容只进入专用数据库表。文件日志只记请求 ID 和固定错误码；
写入失败必须中止远程调用或成功响应，避免出现“回答成功但无法追溯”。
"""

import hashlib
import json
import logging
import os
import re
from contextlib import contextmanager
from uuid import uuid4

from psycopg.conninfo import conninfo_to_dict
from psycopg.types.json import Jsonb

from app.errors import AppError

logger = logging.getLogger(__name__)


class AuditWriteError(AppError):
    def __init__(self, stage):
        super().__init__("AUDIT_UNAVAILABLE", "问答记录保存失败，请检查数据库后重试", 503, True)
        self.audit_stage = stage


class SecretRedactor:
    """保留普通业务文本，递归处理密钥字段、已知密钥和常见凭据格式。"""

    def __init__(self, secrets=()):
        self.secrets = sorted({v for v in secrets if v}, key=len, reverse=True)

    def clean(self, value):
        if isinstance(value, dict):
            return {
                k: "[REDACTED]"
                if re.search(
                    r"authorization|cookie|api[_-]?key|access[_-]?token|password|secret|^token$", str(k), re.I
                )
                else self.clean(v)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [self.clean(v) for v in value]
        if not isinstance(value, str):
            return value
        # messages.content 本身可能是 JSON 字符串，不能遗漏其内部的敏感字段。
        try:
            nested = json.loads(value)
        except (ValueError, TypeError):
            nested = None
        if isinstance(nested, (dict, list)):
            cleaned = self.clean(nested)
            return value if cleaned == nested else json.dumps(cleaned, ensure_ascii=False)
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r'(?i)Bearer\s+[^\s"\'<>]+', "Bearer [REDACTED]", value)
        value = re.sub(r"\bhf_[A-Za-z0-9]{10,}\b", "[REDACTED]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{10,}\b", "[REDACTED]", value)
        value = re.sub(r"([a-z][a-z0-9+.-]*://)[^\s/@]+@", r"\1[REDACTED]@", value, flags=re.I)
        return re.sub(
            r'(?i)([?&](?:token|api_key|key|password|secret|access_token)=)[^&\s"<>]+', r"\1[REDACTED]", value
        )


class AuditStore:
    def __init__(self, db, settings):
        self.db, self.settings = db, settings
        try:
            database_password = conninfo_to_dict(settings.database_url.get_secret_value()).get("password", "")
        except Exception:
            database_password = ""
        self.redactor = SecretRedactor(
            [settings.deepseek_api_key.get_secret_value(), os.getenv("HF_TOKEN", ""), database_password]
        )

    @contextmanager
    def write(self, stage):
        try:
            with self.db.transaction() as conn:
                yield conn
        except AuditWriteError:
            raise
        except Exception as exc:
            logger.error("audit_write_failed stage=%s type=%s", stage, type(exc).__name__)
            raise AuditWriteError(stage) from None

    def json(self, value):
        return Jsonb(self.redactor.clean(value))

    def begin(self, request_id, query, options):
        # 独立连接持有会话锁，不持有长事务；进程退出或连接丢失即释放租约。
        lease = None
        try:
            key = int.from_bytes(
                hashlib.blake2b(request_id.encode(), digest_size=8).digest(), "big", signed=True
            )
            lease = self.db.connect(autocommit=True)
            if not lease.execute("SELECT pg_try_advisory_lock(%s) AS ok", (key,)).fetchone()["ok"]:
                raise AuditWriteError("query_start")
            with self.write("query_start") as conn:
                conn.execute(
                    """INSERT INTO query_records
                    (request_id,user_query,request_options,status,lease_key,expires_at)
                    VALUES (%s,%s,%s,'running',%s,now()+%s*interval '1 day')""",
                    (
                        request_id,
                        self.redactor.clean(query),
                        self.json(options),
                        key,
                        self.settings.audit_retention_days,
                    ),
                )
            return QueryTrace(self, request_id, lease)
        except Exception:
            if lease is not None:
                lease.close()
            raise AuditWriteError("query_start") from None

    def start_call(self, trace, payload, evidence, attempt_no):
        call_id = str(uuid4())
        with self.write("call_start") as conn:
            updated = conn.execute(
                "UPDATE query_records SET model_called=true, current_stage='llm_transport' WHERE request_id=%s AND status='running' RETURNING request_id",
                (trace.request_id,),
            ).fetchone()
            if not updated:
                raise AuditWriteError("call_start")
            conn.execute(
                """INSERT INTO llm_calls
                (id,request_id,attempt_no,status,request_model,request_messages,request_parameters,evidence_snapshot,document_ids)
                VALUES (%s,%s,%s,'pending',%s,%s,%s,%s,%s)""",
                (
                    call_id,
                    trace.request_id,
                    attempt_no,
                    self.redactor.clean(payload.get("model")),
                    self.json(payload.get("messages", [])),
                    self.json({k: v for k, v in payload.items() if k != "messages"}),
                    self.json(evidence),
                    list({e["document_id"] for e in evidence}),
                ),
            )
        return call_id

    def capture_response(self, trace, response):
        # 即便响应不合法 JSON，也先保留原始 HTTP body，再尝试提取元数据。
        model = usage = assistant = None
        try:
            body = response.json()
            model = body.get("model")
            usage = body.get("usage")
            assistant = body.get("choices", [{}])[0].get("message", {}).get("content")
        except (ValueError, TypeError, AttributeError, IndexError):
            pass
        with self.write("response_save") as conn:
            row = conn.execute(
                """UPDATE llm_calls SET raw_response=%s,http_status=%s,
                response_model=%s,usage=%s,assistant_message=%s WHERE id=%s RETURNING id""",
                (
                    self.redactor.clean(response.text),
                    response.status_code,
                    self.redactor.clean(model) if isinstance(model, str) else None,
                    self.json(usage),
                    self.redactor.clean(assistant) if isinstance(assistant, str) else None,
                    trace.last_call_id,
                ),
            ).fetchone()
            if not row:
                raise AuditWriteError("response_save")

    def get_record(self, request_id):
        with self.db.transaction() as conn:
            conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = conn.execute("SELECT * FROM query_records WHERE request_id=%s", (request_id,)).fetchone()
            if row:
                row["calls"] = conn.execute(
                    "SELECT * FROM llm_calls WHERE request_id=%s ORDER BY attempt_no", (request_id,)
                ).fetchall()
            return row

    def recover_interrupted(self):
        count = 0
        with self.write("recovery") as conn:
            rows = conn.execute(
                "SELECT request_id,lease_key,current_stage FROM query_records WHERE status='running' FOR UPDATE SKIP LOCKED"
            ).fetchall()
            for row in rows:
                # 其他进程仍持有租约时绝不能把活跃请求误标记为中断。
                if not conn.execute(
                    "SELECT pg_try_advisory_xact_lock(%s) AS ok", (row["lease_key"],)
                ).fetchone()["ok"]:
                    continue
                conn.execute(
                    """UPDATE query_records SET status='interrupted',finished_at=clock_timestamp(),
                    error_stage=current_stage,error_code='INTERRUPTED',error_message='服务中断，请重新发起查询'
                    WHERE request_id=%s""",
                    (row["request_id"],),
                )
                conn.execute(
                    """UPDATE llm_calls SET status='interrupted',finished_at=clock_timestamp(),
                    duration_ms=extract(epoch FROM clock_timestamp()-started_at)*1000,
                    error_stage='llm_transport',error_code='INTERRUPTED',error_message='服务中断，远程结果未知'
                    WHERE request_id=%s AND status='pending'""",
                    (row["request_id"],),
                )
                count += 1
        return count

    def purge(self, *, request_id=None, document_id=None, before=None, after=None, dry_run=False):
        conditions, params = ["q.status <> 'running'"], []
        if request_id:
            conditions.append("q.request_id=%s")
            params.append(request_id)
        if document_id:
            conditions.append(
                "EXISTS (SELECT 1 FROM llm_calls c WHERE c.request_id=q.request_id AND c.document_ids @> %s::text[])"
            )
            params.append([document_id])
        if before:
            conditions.append("q.started_at < %s")
            params.append(before)
        if after:
            conditions.append("q.started_at >= %s")
            params.append(after)
        if not any((request_id, document_id, before, after)):
            conditions.append("q.expires_at <= now()")
        with self.write("purge") as conn:
            # 级联删除每次调用，不能只删证据而把同样的文本留在 messages 中。
            where = " AND ".join(conditions)
            if dry_run:
                return conn.execute(
                    "SELECT count(*) AS n FROM query_records q WHERE " + where, params
                ).fetchone()["n"]
            return conn.execute("DELETE FROM query_records q WHERE " + where, params).rowcount


class QueryTrace:
    def __init__(self, store, request_id, lease):
        self.store, self.request_id, self.lease = store, request_id, lease
        self.last_call_id = None
        self.stage = "planning"

    def set_stage(self, stage):
        self.stage = stage
        with self.store.write("query_stage") as conn:
            if (
                conn.execute(
                    "UPDATE query_records SET current_stage=%s WHERE request_id=%s AND status='running'",
                    (stage, self.request_id),
                ).rowcount
                != 1
            ):
                raise AuditWriteError("query_stage")

    def start_call(self, payload, evidence, attempt_no):
        try:
            self.lease.execute("SELECT 1")
        except Exception:
            raise AuditWriteError("call_start") from None
        self.stage = "llm_transport"
        snapshots = [e.model_dump() if hasattr(e, "model_dump") else e for e in evidence]
        self.last_call_id = self.store.start_call(self, payload, snapshots, attempt_no)

    def capture_response(self, response):
        self.store.capture_response(self, response)

    def finish_call(self, status, *, code=None, stage=None, message=None, pending_only=False):
        if self.last_call_id is None:
            return
        with self.store.write("call_finish") as conn:
            condition = " AND status='pending'" if pending_only else ""
            conn.execute(
                """UPDATE llm_calls SET status=%s,error_code=%s,error_stage=%s,error_message=%s,
                finished_at=clock_timestamp(),duration_ms=extract(epoch FROM clock_timestamp()-started_at)*1000
                WHERE id=%s"""
                + condition,
                (status, code, stage, self.store.redactor.clean(message), self.last_call_id),
            )

    def finish(self, status, response, *, no_call_reason=None, code=None, stage=None, message=None):
        with self.store.write("query_finish") as conn:
            if (
                conn.execute(
                    """UPDATE query_records SET status=%s,final_response=%s,no_call_reason=%s,
                error_code=%s,error_stage=%s,error_message=%s,finished_at=clock_timestamp()
                WHERE request_id=%s AND status='running' """,
                    (
                        status,
                        self.store.json(response),
                        no_call_reason,
                        code,
                        stage,
                        self.store.redactor.clean(message),
                        self.request_id,
                    ),
                ).rowcount
                != 1
            ):
                raise AuditWriteError("query_finish")

    def close(self):
        self.lease.close()

"""数据库连接及显式迁移。应用启动只检查健康，不偷偷创建或修改表结构。"""

from contextlib import contextmanager
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.config import Settings
from app.errors import AppError

MUTATION_LOCK = 7654101
WORKER_LOCK = 7654102


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings

    def connect(self, **kwargs):
        return psycopg.connect(
            self.settings.database_url.get_secret_value(), row_factory=dict_row, connect_timeout=5, **kwargs
        )

    @contextmanager
    def transaction(self, mutation: bool = False):
        with self.connect() as conn:
            if mutation:
                # 单用户 MVP 的写操作用短事务串行化；模型推理绝不放在此锁内。
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (MUTATION_LOCK,))
            yield conn

    def check(self):
        try:
            with self.transaction() as conn:
                migration = conn.execute(
                    "SELECT version FROM schema_migrations WHERE version='002_query_audit.sql'"
                ).fetchone()
                if migration is None:
                    raise AppError("DATABASE_UNAVAILABLE", "数据库迁移尚未完成", 503, True)
                conn.execute("SELECT 1 FROM chunks LIMIT 1")
        except (psycopg.Error, TypeError, KeyError) as exc:
            raise AppError("DATABASE_UNAVAILABLE", "数据库未就绪，请检查连接并运行迁移", 503, True) from exc

    def migrate(self):
        with self.transaction(mutation=True) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            for path in sorted((Path(__file__).parent / "migrations").glob("*.sql")):
                if conn.execute("SELECT 1 FROM schema_migrations WHERE version=%s", (path.name,)).fetchone():
                    continue
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (path.name,))

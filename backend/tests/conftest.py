"""数据库测试创建独立 schema，绝不清空用户已有表。默认不启动外部服务。"""

import os
import tempfile
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from app.config import Settings
from app.db import Database
from app.documents import DocumentService, FileStore
from app.repository import Repository
from app.worker import IngestionWorker
from psycopg import sql
from psycopg.conninfo import make_conninfo


class FakeEmbedding:
    """只验证数据库与业务流程，不用于宣称真实中文语义召回质量。"""

    profile = "fake-profile-v1"
    metadata = {"test": True, "revision": "fake"}

    def query(self, text):
        return [1.0] + [0.0] * 767

    def documents(self, texts):
        return [self.query(t) for t in texts]

    def count_tokens(self, text):
        return len(text)

    def context_tokens(self, text):
        return len(text)


@pytest.fixture(scope="session")
def postgres_url():
    if os.getenv("TEST_DATABASE_URL"):
        yield os.environ["TEST_DATABASE_URL"]
        return
    if os.getenv("RUN_POSTGRES_TESTS") != "1":
        pytest.skip("数据库测试需 TEST_DATABASE_URL 或 RUN_POSTGRES_TESTS=1 + pgserver")
    pgserver = pytest.importorskip("pgserver")
    with tempfile.TemporaryDirectory(prefix="ragpg-", dir="/tmp") as directory:
        server = pgserver.get_server(Path(directory), cleanup_mode="stop")
        try:
            yield server.get_uri()
        finally:
            server.cleanup()


@pytest.fixture
def system(postgres_url, tmp_path):
    schema = "rag_test_" + uuid4().hex
    with psycopg.connect(postgres_url, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
    settings = Settings(
        _env_file=None,
        database_url=make_conninfo(postgres_url, options=f"-csearch_path={schema},public"),
        data_dir=tmp_path / "data",
        model_cache_dir=tmp_path / "models",
        worker_enabled=False,
    )
    db = Database(settings)
    db.migrate()
    repo = Repository(db)
    documents = DocumentService(settings, repo, FileStore(settings.data_dir))
    embedding = FakeEmbedding()
    worker = IngestionWorker(settings, repo, embedding, documents)
    try:
        yield settings, repo, documents, worker, embedding
    finally:
        with psycopg.connect(postgres_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

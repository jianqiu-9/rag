"""浏览器测试专用服务：真实 PostgreSQL/pgvector，确定性假模型，不使用真实密钥。

禁止作为日常启动入口。服务仅监听本机，临时数据库随进程关闭清理。
"""

import tempfile
from pathlib import Path

import pgserver
import uvicorn
from app.config import Settings
from app.db import Database
from app.errors import AppError
from app.main import create_app
from app.repository import Repository
from app.schemas import Generation


class TestEmbedding:
    profile = "e2e-fake-profile"
    metadata = {"test_only": True}

    def query(self, text):
        return [1.0] + [0.0] * 767

    def documents(self, texts):
        return [self.query(text) for text in texts]

    def count_tokens(self, text):
        return len(text)

    def context_tokens(self, text):
        return len(text)


class TestLLM:
    def generate(self, query, evidence, is_list, limit, trace=None):
        if "服务故障" in query:
            raise AppError("LLM_TIMEOUT", "模型服务超时，请稍后重试", 504, True)
        if "不存在" in query:
            return Generation(status="insufficient")
        if is_list:
            return Generation(
                status="answered",
                questions=[
                    {"question": item.question, "answer": item.answer, "source_chunk_ids": [item.chunk_id]}
                    for item in evidence
                    if item.question
                ][:limit],
            )
        return Generation(
            status="answered",
            answer=evidence[0].answer or "原文证据。",
            source_chunk_ids=[evidence[0].chunk_id],
        )


def main():
    with tempfile.TemporaryDirectory(prefix="rag-e2e-", dir="/tmp") as directory:
        root = Path(directory)
        server = pgserver.get_server(root / "postgres", cleanup_mode="stop")
        try:
            settings = Settings(
                _env_file=None,
                database_url=server.get_uri(),
                data_dir=root / "data",
                model_cache_dir=root / "models",
                worker_enabled=True,
            )
            db = Database(settings)
            db.migrate()
            application = create_app(settings, Repository(db), TestEmbedding(), TestLLM())
            uvicorn.run(application, host="127.0.0.1", port=8123, log_level="warning")
        finally:
            server.cleanup()


if __name__ == "__main__":
    main()

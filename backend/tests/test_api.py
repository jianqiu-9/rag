"""用真实数据库和可控模型验证 HTTP 契约，不让模型偶然回答掩盖 API 缺陷。"""

import pytest
from app.errors import AppError
from app.main import create_app
from app.schemas import Generation
from fastapi.testclient import TestClient

pytestmark = pytest.mark.postgres


class CitingLLM:
    def generate(self, query, evidence, is_list, limit, trace=None):
        if is_list:
            return Generation(
                status="answered",
                questions=[
                    {"question": e.question, "answer": e.answer, "source_chunk_ids": [e.chunk_id]}
                    for e in evidence
                    if e.question
                ][:limit],
            )
        return Generation(status="answered", answer="检索增强生成。", source_chunk_ids=[evidence[0].chunk_id])


def test_upload_query_citation_delete_flow(system):
    settings, repo, docs, worker, embedding = system
    with TestClient(create_app(settings, repo, embedding, CitingLLM())) as client:
        assert client.get("/api/health").status_code == 200
        empty = client.post("/api/query", json={"query": "RAG是什么？"})
        assert empty.json()["status"] == "insufficient"
        response = client.post(
            "/api/documents", files={"files": ("面经.md", "Q: 什么是RAG？\nA: 检索增强生成。".encode())}
        )
        assert response.status_code == 202
        document_id = response.json()["results"][0]["document"]["id"]
        assert client.get(f"/api/documents/{document_id}").json()["status"] == "pending"
        worker.process_once()
        result = client.post("/api/query", json={"query": "解释RAG"}).json()
        assert result["status"] == "answered"
        assert result["sources"][0]["line_start"] == 1
        assert "storage_path" not in client.get("/api/documents").text
        assert "Q: 什么是RAG" in client.get(f"/api/documents/{document_id}/source").json()["content"]
        assert client.delete(f"/api/documents/{document_id}").status_code == 204
        assert client.post("/api/query", json={"query": "解释RAG"}).json()["status"] == "insufficient"


def test_llm_unavailable_is_not_insufficient(system):
    settings, repo, docs, worker, embedding = system
    from io import BytesIO

    docs.upload("资料.md", BytesIO("Q: 什么是RAG？\nA: 检索增强生成。".encode()))
    worker.process_once()

    class BrokenLLM:
        def generate(self, *args, **kwargs):
            raise AppError("LLM_TIMEOUT", "模型超时", 504, True)

    with TestClient(create_app(settings, repo, embedding, BrokenLLM())) as client:
        response = client.post("/api/query", json={"query": "解释RAG"})
        assert response.status_code == 504
        assert response.json()["status"] == "error"
        assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_invalid_input_and_partial_batch_are_explicit(system):
    settings, repo, _, _, embedding = system
    with TestClient(create_app(settings, repo, embedding, CitingLLM())) as client:
        assert client.post("/api/query", json={"query": "列出题目", "limit": 21}).status_code == 422
        assert client.get("/api/documents/not-a-uuid").status_code == 422
        assert client.post("/api/documents", files={"files": ("binary.pdf", b"pdf")}).status_code == 415
        response = client.post(
            "/api/documents", files=[("files", ("a.txt", "普通文本".encode())), ("files", ("b.pdf", b"pdf"))]
        )
        assert response.status_code == 207
        assert [r["status_code"] for r in response.json()["results"]] == [202, 415]


def test_delete_during_generation_returns_source_changed(system):
    from io import BytesIO

    settings, repo, docs, worker, embedding = system
    row, _ = docs.upload("资料.md", BytesIO("Q: 什么是RAG？\nA: 检索增强生成。".encode()))
    worker.process_once()

    class DeletingLLM:
        def generate(self, query, evidence, is_list, limit, trace=None):
            repo.delete(str(row["id"]))
            return Generation(
                status="answered", answer="检索增强生成。", source_chunk_ids=[evidence[0].chunk_id]
            )

    with TestClient(create_app(settings, repo, embedding, DeletingLLM())) as client:
        response = client.post("/api/query", json={"query": "解释RAG"})
        assert response.status_code == 409
        assert response.json()["error_code"] == "SOURCE_CHANGED"


def test_encoding_size_and_empty_file_are_rejected(system):
    settings, repo, _, _, embedding = system
    small = settings.model_copy(update={"max_upload_mb": 1})
    with TestClient(create_app(small, repo, embedding, CitingLLM())) as client:
        assert client.post("/api/documents", files={"files": ("a.txt", b"\xff\xfe")}).status_code == 422
        assert client.post("/api/documents", files={"files": ("a.txt", b" ")}).status_code == 422
        assert (
            client.post("/api/documents", files={"files": ("a.txt", b"a" * (1024 * 1024 + 1))}).status_code
            == 413
        )
        assert not repo.list_documents()

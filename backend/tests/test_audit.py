"""真实 PostgreSQL 上验证留存边界；远程调用只使用确定性 HTTP 替身。"""

import asyncio
import json
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from app.audit import AuditStore, AuditWriteError, SecretRedactor
from app.llm import DeepSeek
from app.main import create_app
from fastapi.testclient import TestClient


def test_redaction_keeps_business_text_but_removes_credentials():
    redactor = SecretRedactor(["sk-configured-secret"])
    value = {
        "messages": [{"content": json.dumps({"query": "如何使用RAG？", "echo": "sk-configured-secret"})}],
        "Authorization": "Bearer unrelated-secret",
        "link": "https://example.com/?token=private-value",
        "normal_link": "https://example.com/docs",
        "raw": "postgresql://user:password@localhost/db hf_abcdefghijklmnopqrst",
    }
    safe = redactor.clean(value)
    serialized = json.dumps(safe, ensure_ascii=False)
    assert "如何使用RAG" in serialized
    assert safe["normal_link"] == value["normal_link"]
    for secret in [
        "sk-configured-secret",
        "unrelated-secret",
        "private-value",
        "password@",
        "hf_abcdefghijklmnopqrst",
    ]:
        assert secret not in serialized


def seed(system):
    settings, repo, docs, worker, embedding = system
    row, _ = docs.upload("审计样例.md", BytesIO("Q: 什么是RAG？\nA: 检索增强生成。".encode()))
    worker.process_once()
    return str(row["id"])


def success_response(request, *, bad_citation=False):
    payload = json.loads(request.content)
    context = json.loads(payload["messages"][1]["content"])
    content = json.dumps(
        {
            "status": "answered",
            "answer": "检索增强生成。",
            "source_chunk_ids": ["invented" if bad_citation else context["evidence"][0]["chunk_id"]],
        }
    )
    return httpx.Response(
        200,
        json={
            "model": "provider-model-v1",
            "usage": {"prompt_tokens": 25, "completion_tokens": 10},
            "choices": [{"message": {"content": content}}],
        },
    )


def client_for(system, handler, **overrides):
    settings, repo, _, _, embedding = system
    settings = settings.model_copy(
        update={"deepseek_api_key": __import__("pydantic").SecretStr("sk-configured-secret"), **overrides}
    )
    return TestClient(
        create_app(settings, repo, embedding, DeepSeek(settings, transport=httpx.MockTransport(handler)))
    )


def records(system, request_id):
    settings, repo, *_ = system
    return AuditStore(repo.db, settings).get_record(request_id)


@pytest.mark.postgres
def test_empty_library_keeps_query_but_never_creates_model_call(system):
    def must_not_send(request):
        raise AssertionError("空库不能调用远程模型")

    with client_for(system, must_not_send) as client:
        response = client.post("/api/query", json={"query": "解释RAG"})
    data = records(system, response.json()["request_id"])
    assert data["status"] == "insufficient"
    assert data["user_query"] == "解释RAG"
    assert data["model_called"] is False
    assert data["no_call_reason"] == "empty_knowledge_base"
    assert data["calls"] == []
    assert data["final_response"] == response.json()


@pytest.mark.postgres
def test_success_keeps_actual_messages_raw_response_usage_and_final_answer(system):
    seed(system)
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return success_response(request)

    with client_for(system, handler) as client:
        response = client.post("/api/query", json={"query": "  解释RAG  "})
    assert response.status_code == 200
    data = records(system, response.json()["request_id"])
    call = data["calls"][0]
    assert data["user_query"] == "  解释RAG  "
    assert data["final_response"] == response.json()
    assert call["request_messages"] == sent[0]["messages"]
    assert call["request_parameters"] == {k: v for k, v in sent[0].items() if k != "messages"}
    assert call["status"] == "succeeded" and call["http_status"] == 200
    assert call["response_model"] == "provider-model-v1"
    assert call["usage"]["prompt_tokens"] == 25
    assert "检索增强生成" in call["raw_response"] or "\\u68c0" in call["raw_response"]
    assert call["assistant_message"]
    assert call["evidence_snapshot"][0]["content"]
    assert call["duration_ms"] >= 0 and call["finished_at"]


@pytest.mark.postgres
def test_retry_saves_failed_and_successful_attempts_separately(system):
    seed(system)
    attempts = []

    def handler(request):
        attempts.append(request)
        return (
            httpx.Response(503, text="provider is busy") if len(attempts) == 1 else success_response(request)
        )

    with client_for(system, handler) as client:
        response = client.post("/api/query", json={"query": "解释RAG"})
    calls = records(system, response.json()["request_id"])["calls"]
    assert [c["attempt_no"] for c in calls] == [1, 2]
    assert [c["status"] for c in calls] == ["error", "succeeded"]
    assert calls[0]["raw_response"] == "provider is busy"
    assert calls[0]["http_status"] == 503


@pytest.mark.postgres
@pytest.mark.parametrize(
    "mode,stage", [("bad_json", "response_parse"), ("bad_citation", "output_validation")]
)
def test_invalid_output_still_keeps_original_response(system, mode, stage):
    seed(system)

    def handler(request):
        return (
            httpx.Response(200, text="not JSON at all")
            if mode == "bad_json"
            else success_response(request, bad_citation=True)
        )

    with client_for(system, handler) as client:
        response = client.post("/api/query", json={"query": "解释RAG"})
    assert response.status_code == 502
    record = records(system, response.json()["request_id"])
    assert record["status"] == "error" and record["error_stage"] == stage
    call = record["calls"][0]
    assert call["raw_response"]
    assert call["http_status"] == 200 and call["status"] == "error"
    assert call["error_stage"] == stage


@pytest.mark.postgres
def test_timeout_keeps_attempt_with_no_fabricated_response(system):
    seed(system)

    async def slow(request):
        await asyncio.sleep(2)
        return success_response(request)

    with client_for(system, slow, llm_timeout_seconds=0.3) as client:
        response = client.post("/api/query", json={"query": "解释RAG"})
    assert response.status_code == 504
    data = records(system, response.json()["request_id"])
    call = data["calls"][0]
    assert call["raw_response"] is None and call["http_status"] is None
    assert call["status"] == "error" and call["error_code"] == "LLM_TIMEOUT"


@pytest.mark.postgres
def test_configuration_secrets_are_not_stored_in_echoed_response(system):
    seed(system)
    with client_for(system, lambda _: httpx.Response(401, text="Bearer sk-configured-secret")) as client:
        response = client.post("/api/query", json={"query": "解释RAG"})
    data = records(system, response.json()["request_id"])
    assert response.status_code == 503
    assert "sk-configured-secret" not in json.dumps(data, default=str)
    assert data["calls"][0]["raw_response"]


@pytest.mark.postgres
def test_attempt_write_failure_prevents_network_call(system, monkeypatch):
    seed(system)
    sent = []

    def fail(*args, **kwargs):
        raise AuditWriteError("call_start")

    monkeypatch.setattr(AuditStore, "start_call", fail)
    with client_for(system, lambda request: sent.append(request)) as client:
        response = client.post("/api/query", json={"query": "解释RAG"})
    assert response.status_code == 503
    assert response.json()["error_code"] == "AUDIT_UNAVAILABLE"
    assert sent == []
    assert records(system, response.json()["request_id"])["model_called"] is False


@pytest.mark.postgres
def test_deleted_documents_do_not_erase_evidence_but_explicit_purge_does(system):
    document_id = seed(system)
    settings, repo, *_ = system
    with client_for(system, success_response) as client:
        response = client.post("/api/query", json={"query": "解释RAG"})
        assert client.delete(f"/api/documents/{document_id}").status_code == 204
    request_id = response.json()["request_id"]
    assert records(system, request_id)["calls"][0]["evidence_snapshot"][0]["document_id"] == document_id
    store = AuditStore(repo.db, settings)
    assert store.purge(document_id=document_id, dry_run=True) == 1
    assert records(system, request_id)
    assert store.purge(document_id=document_id) == 1
    assert store.get_record(request_id) is None
    with repo.db.transaction() as conn:
        assert conn.execute("SELECT count(*) AS n FROM llm_calls").fetchone()["n"] == 0


@pytest.mark.postgres
def test_recovery_uses_session_lease_and_does_not_interrupt_live_requests(system):
    settings, repo, *_ = system
    store = AuditStore(repo.db, settings)
    request_id = str(uuid4())
    trace = store.begin(request_id, "正在执行的问题", {})
    trace.start_call({"model": "test", "messages": []}, [], 1)
    assert store.recover_interrupted() == 0
    assert store.get_record(request_id)["status"] == "running"
    trace.close()
    assert store.recover_interrupted() == 1
    record = store.get_record(request_id)
    assert record["status"] == "interrupted"
    assert record["calls"][0]["status"] == "interrupted"


@pytest.mark.postgres
def test_retention_cleanup_does_not_remove_running_records(system):
    settings, repo, *_ = system
    store = AuditStore(repo.db, settings)
    finished = store.begin(str(uuid4()), "结束问题", {})
    finished.finish("insufficient", {"status": "insufficient"}, no_call_reason="empty_knowledge_base")
    finished.close()
    running = store.begin(str(uuid4()), "执行问题", {})
    try:
        with repo.db.transaction() as conn:
            conn.execute("UPDATE query_records SET expires_at=now()-interval '1 day'")
        assert store.purge() == 1
        assert store.get_record(running.request_id)["status"] == "running"
    finally:
        running.close()


@pytest.mark.postgres
def test_response_save_failure_is_explicit_and_does_not_return_success(system, monkeypatch):
    seed(system)
    sent = []

    def handler(request):
        sent.append(request)
        return success_response(request)

    def fail(*args, **kwargs):
        raise AuditWriteError("response_save")

    monkeypatch.setattr(AuditStore, "capture_response", fail)
    with client_for(system, handler) as client:
        response = client.post("/api/query", json={"query": "解释RAG"})
    assert response.status_code == 503 and len(sent) == 1
    record = records(system, response.json()["request_id"])
    assert record["status"] == "error"
    assert record["error_stage"] == "response_save"
    assert record["calls"][0]["raw_response"] is None
    assert record["final_response"] == response.json()


@pytest.mark.postgres
def test_query_registration_failure_never_sends_remote_request(system, monkeypatch):
    seed(system)
    sent = []

    def fail(*args, **kwargs):
        raise AuditWriteError("query_start")

    monkeypatch.setattr(AuditStore, "begin", fail)
    with client_for(system, lambda request: sent.append(request)) as client:
        response = client.post("/api/query", json={"query": "解释RAG"})
    assert response.status_code == 503
    assert response.json()["error_code"] == "AUDIT_UNAVAILABLE"
    assert sent == []


@pytest.mark.postgres
def test_final_answer_write_failure_does_not_claim_answer_was_saved(system, monkeypatch):
    from app.audit import QueryTrace

    seed(system)
    original = QueryTrace.finish

    def fail_success(self, status, *args, **kwargs):
        if status == "answered":
            raise AuditWriteError("query_finish")
        return original(self, status, *args, **kwargs)

    monkeypatch.setattr(QueryTrace, "finish", fail_success)
    with client_for(system, success_response) as client:
        response = client.post("/api/query", json={"query": "解释RAG"})
    assert response.status_code == 503
    record = records(system, response.json()["request_id"])
    assert record["status"] == "error" and record["error_stage"] == "query_finish"
    # 模型本身已正常返回，保存最终业务回答失败不能抹去远程成功的事实。
    assert record["calls"][0]["status"] == "succeeded"
    assert record["final_response"] == response.json()


@pytest.mark.postgres
@pytest.mark.parametrize(
    "mode,reason", [("threshold", "no_candidates"), ("budget", "context_budget_exhausted")]
)
def test_filtered_evidence_does_not_create_phantom_calls(system, monkeypatch, mode, reason):
    seed(system)
    _, repo, _, _, embedding = system
    if mode == "threshold":
        monkeypatch.setattr(repo, "search", lambda *args: [])
    else:
        monkeypatch.setattr(embedding, "context_tokens", lambda _: 9000)

    def forbidden(request):
        raise AssertionError("证据不足时不能调用模型")

    with client_for(system, forbidden) as client:
        response = client.post("/api/query", json={"query": "解释RAG"})
    record = records(system, response.json()["request_id"])
    assert record["no_call_reason"] == reason
    assert record["model_called"] is False and record["calls"] == []


@pytest.mark.postgres
def test_cleanup_cli_can_inspect_and_purge_only_requested_record(system, monkeypatch, capsys):
    import sys

    from app import cli

    settings, repo, *_ = system
    store = AuditStore(repo.db, settings)
    ids = [str(uuid4()), str(uuid4())]
    for request_id in ids:
        trace = store.begin(request_id, "清理命令测试", {})
        trace.finish("insufficient", {"status": "insufficient"})
        trace.close()
    monkeypatch.setattr(cli, "Settings", lambda: settings)
    monkeypatch.setattr(sys, "argv", ["rag", "records", "--request-id", ids[0]])
    cli.main()
    assert json.loads(capsys.readouterr().out)["request_id"] == ids[0]
    monkeypatch.setattr(sys, "argv", ["rag", "purge-records", "--request-id", ids[0], "--dry-run"])
    cli.main()
    assert "将清理 1 条" in capsys.readouterr().out
    assert store.get_record(ids[0])
    monkeypatch.setattr(sys, "argv", ["rag", "purge-records", "--request-id", ids[0]])
    cli.main()
    assert "已清理 1 条" in capsys.readouterr().out
    assert store.get_record(ids[0]) is None and store.get_record(ids[1])


@pytest.mark.postgres
def test_time_range_purge_is_half_open_and_preserves_other_records(system):
    from datetime import UTC, datetime

    settings, repo, *_ = system
    store = AuditStore(repo.db, settings)
    ids = [str(uuid4()) for _ in range(3)]
    with repo.db.transaction() as conn:
        for index, request_id in enumerate(ids):
            trace = store.begin(request_id, "按时间清理测试", {})
            trace.finish("insufficient", {})
            trace.close()
            conn.execute(
                "UPDATE query_records SET started_at=%s WHERE request_id=%s",
                (datetime(2026, 10, 20 + index, tzinfo=UTC), request_id),
            )
    assert (
        store.purge(after=datetime(2026, 10, 20, tzinfo=UTC), before=datetime(2026, 10, 22, tzinfo=UTC)) == 2
    )
    assert store.get_record(ids[2])


@pytest.mark.postgres
def test_business_content_does_not_enter_application_file_log(system):

    seed(system)
    marker = "仅供审计表保存的提问_" + uuid4().hex
    with client_for(system, success_response) as client:
        assert client.post("/api/query", json={"query": marker}).status_code == 200
    # create_app 的 logger 可能沿用首个测试应用的 handler；逐个检查实际文件。
    import logging

    paths = [h.baseFilename for h in logging.getLogger("app").handlers if hasattr(h, "baseFilename")]
    from pathlib import Path

    assert paths
    for path in paths:
        assert marker not in Path(path).read_text()
        assert "sk-configured-secret" not in Path(path).read_text()


@pytest.mark.postgres
def test_retrieval_database_error_keeps_safe_original_error_contract(system, monkeypatch):
    import psycopg

    seed(system)
    _, repo, *_ = system

    def fail(*args):
        raise psycopg.OperationalError("private SQL and business parameters")

    monkeypatch.setattr(repo, "search", fail)
    with client_for(system, success_response) as client:
        response = client.post("/api/query", json={"query": "解释RAG"})
    assert response.status_code == 503
    assert response.json()["error_code"] == "DATABASE_UNAVAILABLE"
    assert "private" not in response.text
    record = records(system, response.json()["request_id"])
    assert record["error_stage"] == "retrieval"
    assert record["final_response"] == response.json()
    assert record["calls"] == []

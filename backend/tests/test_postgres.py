"""真实 PostgreSQL + pgvector 集成回归，覆盖版本切换与并发失效边界。"""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest
from app.errors import AppError
from app.parser import parse_document
from app.repository import Repository

pytestmark = pytest.mark.postgres


def upload(documents, filename="面经.md", text="Q: 什么是RAG？\nA: 检索增强生成。"):
    return documents.upload(filename, BytesIO(text.encode()))[0]


def test_replace_failure_preserves_old_then_retry_swaps_atomically(system):
    _, repo, docs, worker, embedding = system
    first = upload(docs)
    worker.process_once()
    old_hits = repo.search(embedding.query("RAG"), 8, 0.5, embedding.profile)
    assert len(old_hits) == 1
    second = upload(docs, text="Q: 什么是RAG？\nA: 新版本答案。")
    assert repo.get_document(str(first["id"]))["is_active"]
    task = repo.claim()
    repo.fail(str(task["id"]), "MODEL_NOT_READY", "下载未完成")
    assert repo.get_document(str(first["id"]))["is_active"]
    assert repo.list_documents()[0]["has_active_version"]
    repo.retry(str(second["id"]))
    worker.process_once()
    hits = repo.search(embedding.query("RAG"), 8, 0.5, embedding.profile)
    assert len(hits) == 1 and "新版本答案" in hits[0].content
    with pytest.raises(AppError):
        repo.assert_sources_active([old_hits[0].chunk_id])
    docs.collect_garbage()
    assert len(list(docs.store.root.iterdir())) == 1


def test_duplicate_does_not_write_twice_even_for_another_name(system):
    _, repo, docs, worker, _ = system
    first = upload(docs)
    second, duplicate = docs.upload("另一份.md", BytesIO("Q: 什么是RAG？\nA: 检索增强生成。".encode()))
    assert duplicate and first["id"] == second["id"]
    assert len(repo.list_documents()) == 1
    assert len(list(docs.store.root.iterdir())) == 1
    worker.process_once()


def test_delete_while_processing_prevents_late_activation(system):
    _, repo, docs, _, embedding = system
    row = upload(docs)
    task = repo.claim()
    repo.delete(str(row["id"]))
    chunks = parse_document("Q: 什么是RAG？\nA: 检索增强生成。")
    assert not repo.complete(
        str(task["id"]),
        chunks,
        embedding.documents([c.content for c in chunks]),
        embedding.profile,
        embedding.metadata,
    )
    assert not repo.has_active_documents()
    docs.collect_garbage()
    assert not list(docs.store.root.iterdir())


def test_restart_recovers_interrupted_and_new_connection_sees_index(system):
    _, repo, docs, worker, embedding = system
    row = upload(docs)
    repo.claim()
    repo.recover()
    assert repo.get_document(str(row["id"]))["error_code"] == "INTERRUPTED"
    repo.retry(str(row["id"]))
    worker.process_once()
    fresh = Repository(repo.db)
    assert fresh.search(embedding.query("RAG"), 8, 0.5, embedding.profile)
    assert fresh.get_document(str(row["id"]))["attempt_count"] == 2


def test_changed_profile_cannot_mix_vector_spaces(system):
    _, repo, docs, worker, embedding = system
    upload(docs)
    worker.process_once()
    with pytest.raises(AppError) as exc:
        repo.search(embedding.query("RAG"), 8, 0.5, "different-profile")
    assert exc.value.code == "INDEX_PROFILE_MISMATCH"


def test_concurrent_same_filename_only_one_task_survives(system):
    _, repo, docs, _, _ = system

    def send(index):
        try:
            return upload(docs, text=f"Q: 问题？\nA: 答案{index}")
        except AppError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, range(2)))
    assert sum(isinstance(r, dict) for r in results) == 1
    assert "UPLOAD_IN_PROGRESS" in results
    assert len(repo.list_documents()) == 1


def test_late_attempt_cannot_override_new_attempt(system):
    _, repo, docs, _, embedding = system
    row = upload(docs)
    first = repo.claim()
    repo.recover()
    repo.retry(str(row["id"]))
    second = repo.claim()
    chunks = parse_document("Q: 什么是RAG？\nA: 检索增强生成。")
    assert not repo.complete(
        str(row["id"]),
        chunks,
        embedding.documents([c.content for c in chunks]),
        embedding.profile,
        embedding.metadata,
        attempt=first["attempt_count"],
    )
    repo.fail(str(row["id"]), "LATE", "旧任务错误", attempt=first["attempt_count"])
    assert repo.get_document(str(row["id"]))["status"] == "processing"
    assert repo.complete(
        str(row["id"]),
        chunks,
        embedding.documents([c.content for c in chunks]),
        embedding.profile,
        embedding.metadata,
        attempt=second["attempt_count"],
    )


def test_invalid_vector_rolls_back_whole_replacement(system):
    import psycopg

    _, repo, docs, worker, embedding = system
    first = upload(docs)
    worker.process_once()
    second = upload(docs, text="Q: 什么是RAG？\nA: 待替换的新答案。")
    repo.claim()
    chunks = parse_document("Q: 什么是RAG？\nA: 待替换的新答案。")
    with pytest.raises(psycopg.Error):
        repo.complete(str(second["id"]), chunks, [[1.0] * 767], embedding.profile, embedding.metadata)
    assert repo.get_document(str(first["id"]))["is_active"]
    assert repo.get_document(str(second["id"]))["status"] == "processing"
    with repo.db.transaction() as conn:
        assert (
            conn.execute("SELECT count(*) AS n FROM chunks WHERE document_id=%s", (second["id"],)).fetchone()[
                "n"
            ]
            == 0
        )


def test_cosine_threshold_filters_unrelated_direction(system):
    _, repo, docs, worker, embedding = system
    upload(docs)
    worker.process_once()
    vector = [-1.0] + [0.0] * 767
    assert repo.search(vector, 8, 0.5, embedding.profile) == []

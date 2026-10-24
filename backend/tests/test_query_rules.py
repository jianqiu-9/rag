"""检索数量和生成引用是确定性规则，不依赖外部模型进行测试。"""

import pytest
from app.errors import AppError
from app.query import build_plan, validate_generation
from app.schemas import Evidence, Generation


def evidence():
    return Evidence(
        chunk_id="c1",
        document_id="d1",
        filename="资料.md",
        section="RAG",
        content="Q: 什么是RAG？\nA: 检索增强生成。",
        question="什么是RAG？",
        answer="检索增强生成。",
        line_start=1,
        line_end=2,
        score=0.8,
    )


def test_list_request_expands_candidates_and_explicit_limit_wins():
    plan = build_plan("列出10道题", top_k=8, limit=None)
    assert plan.limit == 10 and plan.candidate_k == 30 and plan.is_list
    assert build_plan("列出10道题", 8, 3).limit == 3
    assert build_plan("列出二十道题", 8, None).candidate_k == 50


def test_question_without_count_is_normal_qa():
    assert not build_plan("MCP与工具调用有什么区别？", 8, None).is_list
    assert build_plan("有哪些相关面试题？", 8, None).limit == 5
    with pytest.raises(AppError):
        build_plan("列出30道题", 8, None)


def test_unknown_or_missing_citation_is_rejected():
    for ids in (["invented"], []):
        result = Generation(status="answered", answer="检索增强生成。", source_chunk_ids=ids)
        with pytest.raises(AppError, match="引用"):
            validate_generation(result, [evidence()], build_plan("解释RAG", 8, None))


def test_unanswered_original_cannot_be_given_a_generated_answer():
    source = evidence().model_copy(update={"answer": None, "content": "Q: 什么是RAG？"})
    result = Generation(
        status="answered",
        questions=[{"question": "什么是RAG？", "answer": "模型自行补充的知识", "source_chunk_ids": ["c1"]}],
    )
    with pytest.raises(AppError):
        validate_generation(result, [source], build_plan("列出1道题", 8, None))


def test_list_deduplicates_original_questions_and_marks_missing_answers():
    result = Generation(
        status="answered",
        questions=[
            {"question": "什么是RAG？", "answer": "检索增强生成。", "source_chunk_ids": ["c1"]},
            {"question": "什么是RAG？", "answer": "检索增强生成。", "source_chunk_ids": ["c1"]},
        ],
    )
    valid = validate_generation(result, [evidence()], build_plan("列出2道题", 8, None))
    assert len(valid.questions) == 1
    assert valid.source_chunk_ids == ["c1"]

"""使用原文位置而非生成后的文本来验证引用，避免看似有来源却无法定位。"""

import pytest
from app.errors import AppError
from app.parser import normalize_filename, parse_document


def test_question_answer_keeps_original_line_numbers():
    text = "# RAG\n\n## 1. 什么是向量检索？\nA: 通过向量距离召回片段。\n\n## 2. 什么是 MCP？\nA: 工具连接协议。\n"
    chunks = parse_document(text, max_chars=700)
    assert len(chunks) == 2
    assert chunks[0].question == "什么是向量检索？"
    assert "通过向量距离" in chunks[0].answer
    assert chunks[0].line_start == 3
    assert chunks[0].line_end == 5
    assert text.splitlines()[chunks[0].line_start - 1] in chunks[0].content


def test_question_without_answer_is_not_filled():
    chunks = parse_document("Q: 如何设计 Agent？\n\nQ: 什么是工具调用？\nA: 调用外部函数。")
    assert chunks[0].question == "如何设计 Agent？"
    assert chunks[0].answer is None


def test_plain_text_is_split_without_losing_a_single_long_line():
    text = "普通资料" * 500
    chunks = parse_document(text, max_chars=600, overlap_chars=60)
    assert len(chunks) > 1
    assert all(len(c.content) <= 600 for c in chunks)
    assert all(c.line_start == c.line_end == 1 for c in chunks)
    assert all(c.question is None for c in chunks)


def test_names_are_nfc_and_ignore_client_directories():
    assert normalize_filename("C:\\fakepath\\面经.md") == "面经.md"
    assert normalize_filename("/notes/cafe\u0301.txt") == "café.txt"
    assert normalize_filename("A.md") != normalize_filename("a.md")
    with pytest.raises(AppError):
        normalize_filename("../")


def test_code_fence_does_not_create_fake_questions():
    chunks = parse_document("# 资料\n```python\nQ: 假题目？\n```\n普通资料。")
    assert all(c.question is None for c in chunks)

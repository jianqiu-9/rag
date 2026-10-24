"""轻量 MD/TXT 解析器：优先问答边界，其次按字符窗口切分，始终保留原文行号。"""

import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass, replace

from app.errors import AppError

QUESTION = re.compile(r"^(?:#{1,6}\s*)?(?:(?:Q|问|问题)\s*[:：]\s*|(?:\d+[.、)）]\s*))(.+)$", re.I)
HEADING = re.compile(r"^#{1,6}\s+(.+)$")
ANSWER = re.compile(r"^(?:A|答|答案)\s*[:：]\s*", re.I)


@dataclass(frozen=True)
class ParsedChunk:
    content: str
    section: str
    question: str | None
    answer: str | None
    line_start: int
    line_end: int


def normalize_filename(filename: str) -> str:
    """浏览器可能传 Windows 假路径；禁止让上传文件名决定服务器目录。"""
    name = unicodedata.normalize("NFC", filename.replace("\\", "/").split("/")[-1])
    if not name or name in {".", ".."} or len(name) > 255 or any(ord(c) < 32 for c in name):
        raise AppError("INVALID_FILENAME", "文件名无效", 422)
    return name


def clean_question(text: str) -> str:
    text = re.sub(r"^#{1,6}\s*", "", text.strip())
    match = QUESTION.match(text)
    return (match[1] if match else text).strip()


def question_key(text: str) -> str:
    """只做 PRD 规定的精确规范化，不用模型猜测两道题是否语义相同。"""
    return "".join(
        c for c in clean_question(text) if not c.isspace() and not unicodedata.category(c).startswith("P")
    )


def _windows(content: str, first_line: int, max_chars: int, overlap: int):
    # 按偏移映射回原始行号；超长单行也可切分，引用时高亮同一原文行。
    starts = [0] + [m.end() for m in re.finditer("\n", content)]
    offset = 0
    while offset < len(content):
        end = min(len(content), offset + max_chars)
        piece = content[offset:end]
        if piece.strip():
            yield (
                piece,
                first_line + bisect_right(starts, offset) - 1,
                first_line + bisect_right(starts, end - 1) - 1,
            )
        if end == len(content):
            break
        offset = end - overlap


def parse_document(text: str, max_chars: int = 700, overlap_chars: int = 80) -> list[ParsedChunk]:
    if max_chars <= overlap_chars or overlap_chars < 0:
        raise ValueError("切分长度必须大于重叠长度")
    lines = text.splitlines(keepends=True)
    boundaries: list[tuple[int, str | None, str]] = []
    section = ""
    fence = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        fence_match = re.match(r"^(`{3,}|~{3,})", stripped)
        if fence_match:
            marker = fence_match[1][0]
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence is not None:
            continue
        heading = HEADING.match(stripped)
        matched = QUESTION.match(stripped)
        # 标题中的问句可直接作为题目；普通标题仅更新章节。
        question = (
            clean_question(stripped) if matched or (heading and stripped.endswith(("?", "？"))) else None
        )
        if question:
            boundaries.append((i, question, section))
        elif heading:
            section = heading[1]
            boundaries.append((i, None, section))
    if not boundaries or boundaries[0][0] != 0:
        boundaries.insert(0, (0, None, ""))
    chunks = []
    for index, (start, question, section) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(lines)
        block = "".join(lines[start:end])
        # 只有标题的片段不入库；其章节信息已经保留在后续题目中。
        if not question and all(not line.strip() or HEADING.match(line.strip()) for line in lines[start:end]):
            continue
        for piece, line_start, line_end in _windows(block, start + 1, max_chars, overlap_chars):
            answer = None
            if question:
                answer_text = (
                    "".join(piece.splitlines(keepends=True)[1:]) if line_start == start + 1 else piece
                )
                answer_text = ANSWER.sub("", answer_text.strip())
                answer = answer_text or None
            chunks.append(ParsedChunk(piece, section, question, answer, line_start, line_end))
    return chunks


def split_to_token_limit(chunks: list[ParsedChunk], count_tokens, limit: int = 2048) -> list[ParsedChunk]:
    """字符长度不等于 token 数；在模型真实 tokenizer 下再次切分，绝不依赖自动截断。"""
    result = []
    for chunk in chunks:
        if count_tokens(chunk.content) <= limit:
            result.append(chunk)
            continue
        if len(chunk.content) < 2:
            raise AppError("TOKEN_LIMIT", "单个字符及模型任务提示已超过输入限制", 422)
        pieces = []
        width = max(1, len(chunk.content) // 2)
        for content, start, end in _windows(chunk.content, chunk.line_start, width, 0):
            pieces.append(
                replace(
                    chunk,
                    content=content,
                    line_start=start,
                    line_end=end,
                    answer=(content if chunk.answer else None),
                )
            )
        result.extend(split_to_token_limit(pieces, count_tokens, limit))
    return result

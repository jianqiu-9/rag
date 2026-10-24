"""API 与模型输出契约。来源字段只由后端数据库提供，不接受 LLM 编造。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryRequest(StrictModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(None, ge=1, le=50)
    limit: int | None = Field(None, ge=1, le=20)


class Evidence(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    section: str
    content: str
    question: str | None = None
    answer: str | None = None
    line_start: int
    line_end: int
    score: float


class QuestionResult(StrictModel):
    question: str = Field(min_length=1)
    answer: str | None = None
    source_chunk_ids: list[str] = Field(default_factory=list)
    note: str | None = None


class Generation(StrictModel):
    status: Literal["answered", "insufficient"]
    answer: str = ""
    source_chunk_ids: list[str] = Field(default_factory=list)
    questions: list[QuestionResult] = Field(default_factory=list)


class Source(BaseModel):
    chunk_id: str
    document_id: str
    filename: str
    section: str
    line_start: int
    line_end: int
    excerpt: str


class QueryResponse(Generation):
    request_id: str
    sources: list[Source] = Field(default_factory=list)
    requested_count: int | None = None
    actual_count: int = 0
    notice: str | None = None

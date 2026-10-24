"""检索问答用例：数量解析、上下文预算、结果校验与来源二次有效性检查。"""

import json
import logging
import re
import time
from dataclasses import dataclass

import psycopg

from app.audit import AuditStore, AuditWriteError
from app.errors import AppError
from app.parser import question_key
from app.schemas import Evidence, Generation, QueryRequest, QueryResponse, Source

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QueryPlan:
    is_list: bool
    limit: int
    candidate_k: int


def _chinese_number(value: str) -> int:
    digits = {
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
        "零": 0,
    }
    if value.isdigit():
        return int(value)
    if "十" in value:
        before, after = value.split("十", 1)
        return (digits.get(before, 1) * 10) + digits.get(after, 0)
    return digits.get(value, 0)


def build_plan(query: str, top_k: int, limit: int | None) -> QueryPlan:
    count = re.search(r"([0-9]+|[一二两三四五六七八九十零]+)\s*(?:道|个)\s*(?:面试)?(?:题|问题)", query)
    is_list = (
        limit is not None
        or bool(count)
        or bool(
            re.search(r"(?:列出|列举|有哪些|几道|多少道).*(?:题|问题)|(?:题|问题).*(?:有哪些|列表)", query)
        )
    )
    number = limit if limit is not None else _chinese_number(count[1]) if count else 5
    if not 1 <= number <= 20:
        raise AppError("INVALID_LIMIT", "列题数量必须在 1–20 之间", 422)
    return QueryPlan(is_list, number, min(50, max(top_k, 3 * number)) if is_list else top_k)


def _invalid(message="模型引用无效，请重新查询"):
    return AppError("INVALID_GENERATION", message, 502, True)


def validate_generation(result: Generation, evidence: list[Evidence], plan: QueryPlan) -> Generation:
    """引用存在不代表语义必然正确，但这些确定性边界必须由服务端保证。"""
    sources = {e.chunk_id: e for e in evidence}
    if result.status == "insufficient":
        return Generation(status="insufficient", answer="本地知识库暂无足够相关资料")

    def check_ids(ids):
        if not ids or any(i not in sources for i in ids):
            raise _invalid()

    if not plan.is_list:
        check_ids(result.source_chunk_ids)
        if not result.answer.strip() or result.questions:
            raise _invalid("模型回答结构无效，请重新查询")
        cited = [sources[i] for i in result.source_chunk_ids]
        if all(e.question and not e.answer for e in cited):
            return Generation(status="insufficient", answer="原文未提供答案，本地知识库暂无足够相关资料")
        return result.model_copy(update={"source_chunk_ids": list(dict.fromkeys(result.source_chunk_ids))})

    if not result.questions:
        raise _invalid("模型未返回有效原题，请重新查询")
    merged = {}
    for item in result.questions:
        check_ids(item.source_chunk_ids)
        key = question_key(item.question)
        cited = [sources[i] for i in item.source_chunk_ids]
        if not key or not any(e.question and question_key(e.question) == key for e in cited):
            raise _invalid("模型题目与引用原题不符，请重新查询")
        if item.answer and not any(e.answer or e.question is None for e in cited):
            raise _invalid("原文未提供答案，禁止模型补写")
        normalized = item.model_copy(update={"note": None if item.answer else "原文未提供答案"})
        if key in merged:
            previous = merged[key]
            previous.source_chunk_ids = list(dict.fromkeys(previous.source_chunk_ids + item.source_chunk_ids))
        else:
            merged[key] = normalized
    questions = list(merged.values())[: plan.limit]
    ids = list(dict.fromkeys(i for q in questions for i in q.source_chunk_ids))
    return Generation(
        status="answered",
        answer=f"本次检索找到 {len(questions)} 道有来源的面试题。",
        questions=questions,
        source_chunk_ids=ids,
    )


class QueryService:
    def __init__(self, settings, repository, embedding, llm):
        self.settings, self.repository, self.embedding, self.llm = settings, repository, embedding, llm

    def execute(self, request: QueryRequest, request_id: str) -> QueryResponse:
        start = time.perf_counter()
        trace = None
        no_call_reason = None
        generation_ms = 0.0
        retrieval_ms = 0.0
        retrieved_ids = []
        state = "error"
        error_code = None
        try:
            trace = AuditStore(self.repository.db, self.settings).begin(
                request_id, request.query, request.model_dump(exclude={"query"})
            )
            text = request.query.strip()
            if not text:
                raise AppError("EMPTY_QUERY", "请输入问题", 422)
            plan = build_plan(text, request.top_k or self.settings.top_k, request.limit)
            candidates = []
            trace.set_stage("retrieval")
            has_documents = self.repository.has_active_documents()
            if has_documents:
                candidates = self.repository.search(
                    self.embedding.query(text),
                    plan.candidate_k,
                    self.settings.similarity_threshold,
                    self.embedding.profile,
                )
            retrieved_ids = [e.chunk_id for e in candidates]
            # 预算基于实际发送的 JSON，而不是只计算 content；整块保留，超预算则跳过。
            trace.set_stage("context_prepare")
            evidence = []
            budget = (
                self.settings.context_token_budget - self.embedding.context_tokens(text) - 1000
                if candidates
                else 0
            )
            for candidate in candidates:
                cost = self.embedding.context_tokens(json.dumps(candidate.model_dump(), ensure_ascii=False))
                if cost <= budget:
                    evidence.append(candidate)
                    budget -= cost
            retrieval_ms = (time.perf_counter() - start) * 1000
            if not evidence:
                no_call_reason = (
                    "empty_knowledge_base"
                    if not has_documents
                    else "no_candidates"
                    if not candidates
                    else "context_budget_exhausted"
                )
                result = Generation(status="insufficient", answer="本地知识库暂无足够相关资料")
            else:
                generated_at = time.perf_counter()
                try:
                    trace.set_stage("llm_prepare")
                    raw = self.llm.generate(text, evidence, plan.is_list, plan.limit, trace=trace)
                    trace.set_stage("output_validation")
                    result = validate_generation(raw, evidence, plan)
                finally:
                    generation_ms = (time.perf_counter() - generated_at) * 1000
                trace.set_stage("source_validation")
                self.repository.assert_sources_active(result.source_chunk_ids)
            state = result.status
            by_id = {e.chunk_id: e for e in evidence}
            sources = [
                Source(
                    **{
                        k: getattr(by_id[i], k)
                        for k in ("chunk_id", "document_id", "filename", "section", "line_start", "line_end")
                    },
                    excerpt=by_id[i].content,
                )
                for i in result.source_chunk_ids
            ]
            notice = None
            if plan.is_list and len(result.questions) < plan.limit:
                notice = f"请求 {plan.limit} 道，本次检索仅找到 {len(result.questions)} 道；不代表知识库全部题目数量。"
            response = QueryResponse(
                **result.model_dump(),
                request_id=request_id,
                sources=sources,
                requested_count=plan.limit if plan.is_list else None,
                actual_count=len(result.questions),
                notice=notice,
            )
            if not trace.last_call_id and no_call_reason is None:
                no_call_reason = "adapter_no_remote_call"
            trace.finish(state, response.model_dump(mode="json"), no_call_reason=no_call_reason)
            return response
        except Exception as exc:
            state = "error"
            error = (
                exc
                if isinstance(exc, AppError)
                else AppError("DATABASE_UNAVAILABLE", "数据库暂不可用，请检查连接后重试", 503, True)
                if isinstance(exc, psycopg.Error)
                else AppError("INTERNAL_ERROR", "服务处理失败，请根据请求 ID 查看日志", 500, True)
            )
            error_code = error.code
            if trace:
                stage = error.audit_stage if isinstance(error, AuditWriteError) else trace.stage
                try:
                    # 输出/来源校验失败属于本次模型输出失败，原始响应仍完整保留。
                    trace.finish_call(
                        "error",
                        code=error.code,
                        stage=stage,
                        message=error.message,
                        pending_only=stage not in {"output_validation", "source_validation"},
                    )
                    trace.finish(
                        "error",
                        {
                            "status": "error",
                            "request_id": request_id,
                            "error_code": error.code,
                            "message": error.message,
                            "retryable": error.retryable,
                        },
                        code=error.code,
                        stage=stage,
                        message=error.message,
                        no_call_reason=(
                            "audit_unavailable"
                            if isinstance(error, AuditWriteError)
                            else "llm_configuration"
                            if error.code == "LLM_CONFIGURATION"
                            else "failed_before_call"
                        )
                        if not trace.last_call_id
                        else None,
                    )
                except AuditWriteError:
                    # 原始异常不写文件日志；无法保存错误本身时仍须明确向调用方报告审计失败。
                    logger.error("audit_finalize_failed request_id=%s", request_id)
                    raise AuditWriteError("query_finish") from None
            raise error from None
        finally:
            if trace:
                trace.close()
            if not retrieval_ms:
                retrieval_ms = (time.perf_counter() - start) * 1000
            logger.info(
                "query request_id=%s result=%s error_code=%s retrieved_ids=%s retrieval_ms=%.1f generation_ms=%.1f",
                request_id,
                state,
                error_code,
                retrieved_ids,
                retrieval_ms,
                generation_ms,
            )
            try:
                self.repository.log_query(
                    request_id,
                    retrieved_ids,
                    retrieval_ms,
                    generation_ms,
                    state,
                    error_code,
                    self.settings.deepseek_model,
                )
            except Exception:
                # 日志存储故障不覆盖本次查询的原始错误，也不把原始 SQL/参数写入日志。
                logger.error("query_log_failed request_id=%s", request_id)

"""FastAPI 装配入口。路由层不包含模型提示词或数据库版本切换逻辑。"""

import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from typing import Annotated
from uuid import UUID, uuid4

import psycopg
from fastapi import FastAPI, File, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import ROOT, Settings
from app.db import Database
from app.documents import DocumentService, FileStore
from app.embedding import Embedding
from app.errors import AppError
from app.llm import DeepSeek
from app.query import QueryService
from app.repository import Repository
from app.schemas import QueryRequest, QueryResponse
from app.worker import IngestionWorker


def configure_logging(settings):
    directory = settings.data_dir / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("app")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(
            directory / "rag.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)


def create_app(settings=None, repository=None, embedding=None, llm=None):
    settings = settings or Settings()
    configure_logging(settings)
    repository = repository or Repository(Database(settings))
    embedding = embedding or Embedding(settings)
    documents = DocumentService(settings, repository, FileStore(settings.data_dir))
    queries = QueryService(settings, repository, embedding, llm or DeepSeek(settings))
    worker = IngestionWorker(settings, repository, embedding, documents)

    @asynccontextmanager
    async def lifespan(app):
        if settings.worker_enabled:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="Agent 面经知识库", version="0.1.0", lifespan=lifespan)
    app.state.worker = worker
    app.state.repository = repository
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def error_response(request, error):
        return JSONResponse(
            status_code=error.status,
            content={
                "status": "error",
                "request_id": request.state.request_id,
                "error_code": error.code,
                "message": error.message,
                "retryable": error.retryable,
            },
        )

    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response(
            request, AppError("INVALID_REQUEST", "请求参数无效，请检查数量、文件或文档 ID", 422)
        )

    @app.exception_handler(psycopg.Error)
    async def database_error(request: Request, exc: psycopg.Error):
        return error_response(
            request, AppError("DATABASE_UNAVAILABLE", "数据库暂不可用，请检查连接后重试", 503, True)
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception):
        logging.getLogger(__name__).error(
            "unhandled request_id=%s type=%s", request.state.request_id, type(exc).__name__
        )
        return error_response(
            request, AppError("INTERNAL_ERROR", "服务处理失败，请根据请求 ID 查看日志", 500, True)
        )

    @app.get("/api/health")
    def health():
        repository.db.check()
        return {
            "status": "ok",
            "embedding_model": settings.embedding_model,
            "llm_model": settings.deepseek_model,
            "model_cached": (settings.model_cache_dir / "model-lock.json").exists(),
        }

    @app.post("/api/documents")
    def upload(request: Request, response: Response, files: Annotated[list[UploadFile], File()]):
        if len(files) > 20:
            raise AppError("TOO_MANY_FILES", "一次最多上传 20 个文件", 422)
        results = []
        for file in files:
            try:
                row, duplicate = documents.upload(file.filename or "", file.file)
                results.append(
                    {
                        "document_id": str(row["id"]),
                        "logical_document_id": str(row["logical_document_id"]),
                        "document": jsonable_encoder(row),
                        "duplicate": duplicate,
                        "status_code": 200 if duplicate else 202,
                    }
                )
            except AppError as exc:
                results.append(
                    {
                        "filename": file.filename,
                        "error_code": exc.code,
                        "message": exc.message,
                        "retryable": exc.retryable,
                        "status_code": exc.status,
                    }
                )
            finally:
                file.file.close()
        response.status_code = results[0]["status_code"] if len(results) == 1 else 207
        return {"request_id": request.state.request_id, "results": results}

    @app.get("/api/documents")
    def list_documents():
        return {"documents": repository.list_documents()}

    @app.get("/api/documents/{document_id}")
    def get_document(document_id: UUID):
        return repository.get_document(str(document_id))

    @app.post("/api/documents/{document_id}/retry", status_code=202)
    def retry_document(document_id: UUID):
        return repository.retry(str(document_id))

    @app.get("/api/documents/{document_id}/source")
    def get_source(document_id: UUID):
        return documents.source(str(document_id))

    @app.delete("/api/documents/{document_id}", status_code=204)
    def delete_document(document_id: UUID):
        repository.delete(str(document_id))
        documents.collect_garbage()
        return Response(status_code=204)

    @app.post("/api/query", response_model=QueryResponse)
    def query(request: Request, body: QueryRequest):
        return queries.execute(body, request.state.request_id)

    # React 构建产物存在时，由 FastAPI 同源托管；开发时使用 Vite 的 /api 代理。
    frontend = ROOT / "frontend/dist"
    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()

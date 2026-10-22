from __future__ import annotations

import hmac
import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import router
from .config import Settings
from .db import Database
from .errors import AppError
from .pipeline import PipelineRunner
from .qa_agent import AgenticQAService
from .storage import LocalObjectStore


LOGGER = logging.getLogger("evidencegraph")


@dataclass
class AppContainer:
    settings: Settings
    db: Database
    storage: LocalObjectStore
    runner: PipelineRunner
    qa: QAService


def create_app(settings: Settings | None = None) -> FastAPI:
    runtime = settings or Settings()
    runtime.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(runtime.database_path)
    storage = LocalObjectStore(runtime.storage_root)
    runner = PipelineRunner(runtime, db, storage)
    qa = AgenticQAService(runtime, db)
    container = AppContainer(runtime, db, storage, runner, qa)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        LOGGER.info("EvidenceGraph backend ready at %s:%s", runtime.host, runtime.port)
        yield
        runner.shutdown()

    app = FastAPI(
        title=runtime.app_name,
        version="1.0.0",
        description=(
            "Independent GraphRAG backend for document ingestion, "
            "knowledge graph indexing and evidence-grounded question answering."
        ),
        lifespan=lifespan,
    )
    app.state.container = container
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://localhost:8010",
            "http://127.0.0.1:8010",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(router)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex[:20]}"
        request.state.request_id = request_id
        if runtime.auth_mode == "bearer" and request.url.path.startswith("/api/"):
            authorization = request.headers.get("Authorization", "")
            expected = f"Bearer {runtime.api_auth_token}"
            if not runtime.api_auth_token or not hmac.compare_digest(
                authorization, expected
            ):
                return JSONResponse(
                    status_code=401,
                    content={
                        "type": "https://api.example.com/problems/auth-required",
                        "title": "Authentication Required",
                        "status": 401,
                        "code": "AUTH_REQUIRED",
                        "detail": "a valid bearer token is required",
                        "instance": request.url.path,
                        "request_id": request_id,
                        "retryable": False,
                        "details": None,
                    },
                    headers={"X-Request-Id": request_id},
                )
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        request_id = getattr(request.state, "request_id", f"req_{uuid.uuid4().hex[:20]}")
        return JSONResponse(
            status_code=exc.status,
            content={
                "type": f"https://api.example.com/problems/{exc.code.lower()}",
                "title": exc.code.replace("_", " ").title(),
                "status": exc.status,
                "code": exc.code,
                "detail": exc.detail,
                "instance": request.url.path,
                "request_id": request_id,
                "retryable": exc.retryable,
                "details": exc.details,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", f"req_{uuid.uuid4().hex[:20]}")
        return JSONResponse(
            status_code=422,
            content={
                "type": "https://api.example.com/problems/validation-error",
                "title": "Validation Error",
                "status": 422,
                "code": "VALIDATION_ERROR",
                "detail": "request payload is invalid",
                "instance": request.url.path,
                "request_id": request_id,
                "retryable": False,
                "details": {"errors": exc.errors()},
            },
        )

    @app.get("/health/live", tags=["health"])
    async def live():
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def ready():
        with db.connect() as conn:
            conn.execute("SELECT 1").fetchone()
        return {
            "status": "ready",
            "database": "ok",
            "storage": "ok",
            "pipeline_mode": runtime.pipeline_mode,
        }

    return app


app = create_app()


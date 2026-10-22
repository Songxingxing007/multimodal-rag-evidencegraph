from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pypdf import PdfReader

from .db import new_id, utc_now
from .errors import AppError
from .qa import QAError
from .schemas import QARequest, ReindexRequest, UploadCompleteRequest, UploadCreateRequest


router = APIRouter()


GRAPH_V1_EXTENSIONS = {".pdf"}
PRECISE_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".jp2",
    ".webp",
    ".gif",
    ".bmp",
    ".docx",
    ".doc",
    ".pptx",
    ".ppt",
    ".xlsx",
    ".xls",
    ".html",
}
MIME_TYPES = {
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".png": {"image/png", "application/octet-stream"},
    ".jpg": {"image/jpeg", "application/octet-stream"},
    ".jpeg": {"image/jpeg", "application/octet-stream"},
    ".jp2": {"image/jp2", "image/jpeg2000", "application/octet-stream"},
    ".webp": {"image/webp", "application/octet-stream"},
    ".gif": {"image/gif", "application/octet-stream"},
    ".bmp": {"image/bmp", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
    },
    ".doc": {"application/msword", "application/octet-stream"},
    ".pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/octet-stream",
    },
    ".ppt": {"application/vnd.ms-powerpoint", "application/octet-stream"},
    ".xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/octet-stream",
    },
    ".xls": {"application/vnd.ms-excel", "application/octet-stream"},
    ".html": {"text/html", "application/xhtml+xml", "application/octet-stream"},
}


def _container(request: Request) -> Any:
    return request.app.state.container


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", new_id("req"))


def _ok(request: Request, data: Any, *, meta: Any = None, status: int = 200) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"data": data, "request_id": _request_id(request), "meta": meta},
    )


def _decode_graph_artifact(container: Any, document_id: str, artifact_type: str) -> Any:
    document = container.db.get_document(document_id)
    if not document:
        raise AppError(404, "RESOURCE_NOT_FOUND", "document not found")
    snapshot_id = document.get("active_graph_snapshot_id")
    if not snapshot_id:
        raise AppError(
            409,
            "GRAPH_NOT_READY",
            "graph is not ready",
            retryable=True,
            details={"status": document["status"]},
        )
    snapshot = container.db.get_snapshot(snapshot_id)
    artifacts = [
        item
        for item in container.db.list_artifacts(document_id)
        if item["job_id"] == snapshot["job_id"]
        and item["artifact_type"] == artifact_type
    ]
    if not artifacts:
        raise AppError(500, "GRAPH_ARTIFACT_MISSING", f"missing {artifact_type}")
    return json.loads(Path(artifacts[0]["path"]).read_text(encoding="utf-8"))


def _document_view(container: Any, document: dict[str, Any]) -> dict[str, Any]:
    counts = {"pages": 0, "nodes": 0, "edges": 0}
    snapshot = None
    if document.get("active_graph_snapshot_id"):
        snapshot = container.db.get_snapshot(document["active_graph_snapshot_id"])
        if snapshot:
            counts["nodes"] = snapshot["node_count"]
            counts["edges"] = snapshot["edge_count"]
            counts["pages"] = snapshot["manifest"].get("counts", {}).get("pages", 0)
    return {
        "document_id": document["document_id"],
        "filename": document["filename"],
        "media_type": document["media_type"],
        "size_bytes": document["size_bytes"],
        "sha256": document["sha256"],
        "indexing_profile": document["indexing_profile"],
        "indexing": document["indexing"],
        "status": document["status"],
        "active_job_id": document.get("active_job_id"),
        "active_graph_snapshot_id": document.get("active_graph_snapshot_id"),
        "counts": counts,
        "capabilities": {
            "queryable": document["status"] == "indexed",
            "downloadable": True,
            "reindexable": document["status"] not in {"deleting", "deleted"},
        },
        "warnings": document.get("warnings", []),
        "created_at": document["created_at"],
        "updated_at": document["updated_at"],
        "links": {
            "job": (
                f"/api/v1/jobs/{document['active_job_id']}"
                if document.get("active_job_id")
                else None
            ),
            "graph": f"/api/v1/documents/{document['document_id']}/graph/manifest",
            "extractions": f"/api/v1/documents/{document['document_id']}/extractions",
            "artifacts": f"/api/v1/documents/{document['document_id']}/artifacts",
        },
    }


def _job_view(container: Any, job: dict[str, Any]) -> dict[str, Any]:
    events = container.db.list_events(job["job_id"])
    stage_rows: dict[str, dict[str, Any]] = {}
    for event in events:
        stage = event["stage"]
        row = stage_rows.setdefault(
            stage,
            {
                "name": stage,
                "status": "pending",
                "started_at": event["created_at"],
                "finished_at": None,
                "progress": event["progress"],
            },
        )
        row["progress"] = max(row["progress"], event["progress"])
        if event["event_type"] == "completed":
            row["status"] = "succeeded"
            row["finished_at"] = event["created_at"]
        elif event["event_type"] == "failed":
            row["status"] = "failed"
            row["finished_at"] = event["created_at"]
        else:
            row["status"] = "running"
    if job["status"] == "succeeded" and stage_rows:
        for row in stage_rows.values():
            if row["status"] == "running":
                row["status"] = "succeeded"
                row["finished_at"] = job.get("finished_at")
    ordered = sorted(
        stage_rows.values(), key=lambda item: item["started_at"] or ""
    )
    return {
        "job_id": job["job_id"],
        "document_id": job["document_id"],
        "status": job["status"],
        "stage": job["stage"],
        "progress": job["progress"],
        "attempt": job["attempt"],
        "created_at": job["created_at"],
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "stages": ordered,
        "warnings": job.get("warnings", []),
        "error": job.get("error"),
        "links": {
            "events": f"/api/v1/jobs/{job['job_id']}/events",
            "document": f"/api/v1/documents/{job['document_id']}",
        },
    }


@router.get("/api/v1/health/summary")
async def health_summary(request: Request) -> JSONResponse:
    container = _container(request)
    return _ok(
        request,
        {
            "status": "ok",
            "app_env": container.settings.app_env,
            "pipeline_mode": container.settings.pipeline_mode,
            "extractor_mode": container.settings.extractor_mode,
            "qa_mode": container.settings.qa_mode,
            "database": "ok",
            "storage": "ok",
            "capabilities": {
                "graph_v1_formats": sorted(GRAPH_V1_EXTENSIONS),
                "precise_formats": sorted(PRECISE_EXTENSIONS),
                "embedding_required": False,
            },
        },
    )


@router.post("/api/v1/uploads")
async def create_upload(request: Request, body: UploadCreateRequest) -> JSONResponse:
    container = _container(request)
    filename = body.filename.strip()
    if Path(filename).name != filename:
        raise AppError(400, "INVALID_FILENAME", "filename must be a basename")
    extension = Path(filename).suffix.lower()
    if extension not in PRECISE_EXTENSIONS:
        raise AppError(
            415,
            "FORMAT_NOT_SUPPORTED",
            f"extension {extension or '<none>'} is not supported",
            details={"extension": extension},
        )
    if body.indexing.profile == "graph_v1" and extension not in GRAPH_V1_EXTENSIONS:
        raise AppError(
            422,
            "FORMAT_GRAPH_NOT_READY",
            "this format can be parsed but Graph V1 is not enabled",
            details={"extension": extension, "profile": "graph_v1"},
        )
    if container.settings.pipeline_mode == "local" and extension not in {".pdf"}:
        raise AppError(
            422,
            "FORMAT_GRAPH_NOT_READY",
            "local P0 test pipeline supports PDF only",
            details={"extension": extension},
        )
    if extension in MIME_TYPES and body.media_type not in MIME_TYPES[extension]:
        raise AppError(
            415,
            "MIME_EXTENSION_MISMATCH",
            "declared media type does not match the extension",
            details={"extension": extension, "media_type": body.media_type},
        )
    limit = container.settings.max_upload_mb * 1024 * 1024
    if body.size_bytes > limit:
        raise AppError(
            413,
            "FILE_TOO_LARGE",
            f"file exceeds {container.settings.max_upload_mb} MB",
        )
    upload_id = new_id("upl")
    path = container.storage.upload_path(upload_id, filename)
    created = datetime.now(timezone.utc)
    record = container.db.create_upload(
        {
            "upload_id": upload_id,
            "filename": filename,
            "media_type": body.media_type,
            "size_bytes": body.size_bytes,
            "declared_sha256": body.sha256.lower() if body.sha256 else None,
            "actual_sha256": None,
            "object_path": str(path),
            "indexing": body.indexing.model_dump(),
            "state": "uploading",
            "created_at": created.isoformat().replace("+00:00", "Z"),
            "expires_at": (created + timedelta(minutes=30))
            .isoformat()
            .replace("+00:00", "Z"),
        }
    )
    return _ok(
        request,
        {
            "upload_id": upload_id,
            "expires_at": record["expires_at"],
            "upload": {
                "method": "PUT",
                "url": f"/api/v1/uploads/{upload_id}/content",
                "headers": {
                    "Content-Type": body.media_type,
                    "Content-Length": str(body.size_bytes),
                },
                "mode": "local",
            },
        },
        status=201,
    )


@router.put("/api/v1/uploads/{upload_id}/content")
async def upload_content(upload_id: str, request: Request) -> JSONResponse:
    container = _container(request)
    upload = container.db.get_upload(upload_id)
    if not upload:
        raise AppError(404, "RESOURCE_NOT_FOUND", "upload session not found")
    if upload["state"] != "uploading":
        raise AppError(409, "UPLOAD_STATE_INVALID", "upload session is not writable")
    path = Path(upload["object_path"])
    temp = path.with_suffix(path.suffix + ".part")
    size = 0
    limit = container.settings.max_upload_mb * 1024 * 1024
    try:
        with temp.open("wb") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                if size > limit:
                    raise AppError(413, "FILE_TOO_LARGE", "upload stream exceeded the limit")
                handle.write(chunk)
        if size != upload["size_bytes"]:
            raise AppError(
                422,
                "CHECKSUM_MISMATCH",
                "uploaded size does not match the declared size",
                retryable=True,
            )
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
    actual_sha = container.storage.sha256(path)
    declared_sha = upload.get("declared_sha256")
    if declared_sha and declared_sha != actual_sha:
        raise AppError(
            422,
            "CHECKSUM_MISMATCH",
            "SHA-256 does not match the declared digest",
            retryable=True,
        )
    container.db.update_upload(
        upload_id, actual_sha256=actual_sha, state="uploaded"
    )
    return Response(status_code=204)


@router.post("/api/v1/uploads/{upload_id}/complete")
async def complete_upload(
    upload_id: str, request: Request, body: UploadCompleteRequest
) -> JSONResponse:
    container = _container(request)
    upload = container.db.get_upload(upload_id)
    if not upload:
        raise AppError(404, "RESOURCE_NOT_FOUND", "upload session not found")
    expires = datetime.fromisoformat(upload["expires_at"].replace("Z", "+00:00"))
    if datetime.now(timezone.utc) > expires:
        raise AppError(409, "UPLOAD_EXPIRED", "upload session expired", retryable=True)
    if upload["state"] not in {"uploaded", "completed"}:
        raise AppError(409, "UPLOAD_STATE_INVALID", "file has not been uploaded")
    path = Path(upload["object_path"])
    if not path.exists():
        raise AppError(409, "UPLOAD_STATE_INVALID", "uploaded object is missing")
    sha256 = container.storage.sha256(path)
    if body.sha256 and body.sha256.lower() != sha256:
        raise AppError(422, "CHECKSUM_MISMATCH", "complete SHA-256 does not match")
    if body.expected_size_bytes and body.expected_size_bytes != path.stat().st_size:
        raise AppError(422, "CHECKSUM_MISMATCH", "complete size does not match")

    filename = upload["filename"]
    extension = Path(filename).suffix.lower()
    if extension == ".pdf":
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                raise AppError(415, "MIME_EXTENSION_MISMATCH", "file is not a valid PDF")
        try:
            pages = len(PdfReader(str(path)).pages)
        except Exception as exc:
            raise AppError(422, "FILE_CORRUPTED", f"unable to read PDF: {exc}") from exc
        if pages > container.settings.max_pages:
            raise AppError(
                422,
                "PAGE_LIMIT_EXCEEDED",
                f"document has {pages} pages; limit is {container.settings.max_pages}",
            )

    idempotency_key = request.headers.get("Idempotency-Key")
    if idempotency_key:
        existing_job = container.db.get_job_by_idempotency(idempotency_key)
        if existing_job:
            document = container.db.get_document(existing_job["document_id"])
            return _ok(
                request,
                {
                    "document_id": document["document_id"],
                    "job_id": existing_job["job_id"],
                    "status": existing_job["status"],
                    "deduplicated": True,
                    "links": {
                        "document": f"/api/v1/documents/{document['document_id']}",
                        "job": f"/api/v1/jobs/{existing_job['job_id']}",
                        "events": f"/api/v1/jobs/{existing_job['job_id']}/events",
                    },
                },
            )

    if not body.force_reindex:
        existing = container.db.find_document_by_sha(
            sha256, upload["indexing"]["profile"]
        )
        if existing:
            return _ok(
                request,
                {
                    "document_id": existing["document_id"],
                    "job_id": existing.get("active_job_id"),
                    "status": existing["status"],
                    "deduplicated": True,
                    "links": {
                        "document": f"/api/v1/documents/{existing['document_id']}",
                        "job": (
                            f"/api/v1/jobs/{existing['active_job_id']}"
                            if existing.get("active_job_id")
                            else None
                        ),
                    },
                },
            )

    document_id = new_id("doc")
    source_path = container.storage.document_path(document_id, filename)
    container.storage.copy(path, source_path)
    now = utc_now()
    document = container.db.create_document(
        {
            "document_id": document_id,
            "filename": filename,
            "media_type": upload["media_type"],
            "size_bytes": path.stat().st_size,
            "sha256": sha256,
            "object_path": str(source_path),
            "indexing_profile": upload["indexing"]["profile"],
            "indexing": upload["indexing"],
            "status": "queued",
            "active_job_id": None,
            "active_graph_snapshot_id": None,
            "warnings": [],
            "created_at": now,
            "updated_at": now,
        }
    )
    job_id = new_id("job")
    container.db.create_job(
        {
            "job_id": job_id,
            "document_id": document_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "attempt": 1,
            "idempotency_key": idempotency_key,
            "run_id": None,
            "warnings": [],
            "created_at": utc_now(),
        }
    )
    container.db.update_document(
        document_id, active_job_id=job_id, updated_at=utc_now()
    )
    container.db.update_upload(upload_id, state="completed")
    container.db.add_event(job_id, "queued", "queued", 0, {"message": "排队中"})
    container.runner.submit(job_id)
    return _ok(
        request,
        {
            "document_id": document["document_id"],
            "job_id": job_id,
            "status": "queued",
            "deduplicated": False,
            "links": {
                "document": f"/api/v1/documents/{document_id}",
                "job": f"/api/v1/jobs/{job_id}",
                "events": f"/api/v1/jobs/{job_id}/events",
            },
        },
        status=202,
    )


@router.get("/api/v1/documents")
async def list_documents(request: Request) -> JSONResponse:
    container = _container(request)
    limit = max(1, min(int(request.query_params.get("limit", "20")), 100))
    cursor = request.query_params.get("cursor")
    status = request.query_params.get("status")
    records = container.db.list_documents(limit + 10, cursor)
    if status:
        records = [item for item in records if item["status"] == status]
    page = records[:limit]
    next_cursor = page[-1]["created_at"] if len(records) > limit and page else None
    return _ok(
        request,
        [_document_view(container, item) for item in page],
        meta={"next_cursor": next_cursor, "has_more": bool(next_cursor)},
    )


@router.get("/api/v1/documents/{document_id}")
async def get_document(document_id: str, request: Request) -> JSONResponse:
    container = _container(request)
    document = container.db.get_document(document_id)
    if not document:
        raise AppError(404, "RESOURCE_NOT_FOUND", "document not found")
    return _ok(request, _document_view(container, document))


@router.post("/api/v1/documents/{document_id}/reindex")
async def reindex_document(
    document_id: str, request: Request, body: ReindexRequest
) -> JSONResponse:
    container = _container(request)
    document = container.db.get_document(document_id)
    if not document:
        raise AppError(404, "RESOURCE_NOT_FOUND", "document not found")
    active_job = (
        container.db.get_job(document["active_job_id"])
        if document.get("active_job_id")
        else None
    )
    if active_job and active_job["status"] in {"queued", "running"}:
        raise AppError(
            409,
            "JOB_ALREADY_ACTIVE",
            "document already has an active job",
            details={"active_job_id": active_job["job_id"]},
        )
    job_id = new_id("job")
    container.db.create_job(
        {
            "job_id": job_id,
            "document_id": document_id,
            "status": "queued",
            "stage": "queued",
            "progress": 0,
            "attempt": 1,
            "idempotency_key": request.headers.get("Idempotency-Key"),
            "warnings": [],
            "created_at": utc_now(),
        }
    )
    container.db.update_document(
        document_id,
        status="queued",
        active_job_id=job_id,
        indexing_json=json.dumps(
            body.indexing.model_dump(), ensure_ascii=False
        ),
        updated_at=utc_now(),
    )
    container.db.add_event(job_id, "queued", "queued", 0, {"message": "重新索引入队"})
    container.runner.submit(job_id)
    return _ok(
        request,
        {
            "job_id": job_id,
            "document_id": document_id,
            "status": "queued",
            "activate_on_success": body.activate_on_success,
        },
        status=202,
    )


@router.delete("/api/v1/documents/{document_id}")
async def delete_document(document_id: str, request: Request) -> JSONResponse:
    container = _container(request)
    document = container.db.get_document(document_id)
    if not document:
        raise AppError(404, "RESOURCE_NOT_FOUND", "document not found")
    container.db.update_document(
        document_id, status="deleted", updated_at=utc_now()
    )
    return _ok(
        request,
        {"document_id": document_id, "status": "deleted"},
        status=202,
    )


@router.get("/api/v1/jobs/{job_id}")
async def get_job(job_id: str, request: Request) -> JSONResponse:
    container = _container(request)
    job = container.db.get_job(job_id)
    if not job:
        raise AppError(404, "RESOURCE_NOT_FOUND", "job not found")
    return _ok(request, _job_view(container, job))


@router.get("/api/v1/jobs/{job_id}/events")
async def stream_job_events(job_id: str, request: Request) -> StreamingResponse:
    container = _container(request)
    if not container.db.get_job(job_id):
        raise AppError(404, "RESOURCE_NOT_FOUND", "job not found")
    try:
        after = int(request.headers.get("Last-Event-ID", "0"))
    except ValueError:
        after = 0

    async def generator():
        nonlocal after
        last_keepalive = asyncio.get_running_loop().time()
        while True:
            events = container.db.list_events(job_id, after_seq=after)
            for event in events:
                after = event["seq"]
                payload = {
                    "job_id": job_id,
                    "stage": event["stage"],
                    "status": event["event_type"],
                    "progress": event["progress"],
                    **event["payload"],
                }
                yield (
                    f"event: {event['event_type']}\n"
                    f"id: {event['seq']}\n"
                    f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                )
            job = container.db.get_job(job_id)
            if not job or job["status"] in {"succeeded", "failed", "cancelled"}:
                break
            now = asyncio.get_running_loop().time()
            if now - last_keepalive > 15:
                yield ": keepalive\n\n"
                last_keepalive = now
            await asyncio.sleep(0.5)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/v1/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request) -> JSONResponse:
    container = _container(request)
    job = container.db.get_job(job_id)
    if not job:
        raise AppError(404, "RESOURCE_NOT_FOUND", "job not found")
    if job["status"] not in {"queued", "running"}:
        raise AppError(409, "JOB_NOT_CANCELLABLE", "job has already finished")
    container.db.update_job(
        job_id, status="cancelled", stage="cancelled", finished_at=utc_now()
    )
    container.db.add_event(
        job_id, "cancelled", job["stage"], job["progress"], {"message": "任务已取消"}
    )
    return _ok(
        request,
        {
            "job_id": job_id,
            "cancel_requested": True,
            "remote_cancel_supported": False,
        },
        status=202,
    )


@router.get("/api/v1/documents/{document_id}/artifacts")
async def list_artifacts(document_id: str, request: Request) -> JSONResponse:
    container = _container(request)
    if not container.db.get_document(document_id):
        raise AppError(404, "RESOURCE_NOT_FOUND", "document not found")
    items = []
    for artifact in container.db.list_artifacts(document_id):
        items.append(
            {
                "artifact_id": artifact["artifact_id"],
                "artifact_type": artifact["artifact_type"],
                "media_type": artifact["media_type"],
                "size_bytes": artifact["size_bytes"],
                "sha256": artifact["sha256"],
                "schema_version": artifact["schema_version"],
                "created_at": artifact["created_at"],
                "download_url": f"/api/v1/artifacts/{artifact['artifact_id']}/download",
            }
        )
    return _ok(request, items, meta={"next_cursor": None, "has_more": False})


@router.get("/api/v1/artifacts/{artifact_id}/download")
async def download_artifact(artifact_id: str, request: Request) -> FileResponse:
    container = _container(request)
    artifact = container.db.get_artifact(artifact_id)
    if not artifact:
        raise AppError(404, "RESOURCE_NOT_FOUND", "artifact not found")
    path = Path(artifact["path"])
    if not path.is_file():
        raise AppError(404, "RESOURCE_NOT_FOUND", "artifact file is missing")
    return FileResponse(
        path,
        media_type=artifact["media_type"],
        filename=path.name,
    )


@router.get("/api/v1/documents/{document_id}/extractions")
async def list_extractions(document_id: str, request: Request) -> JSONResponse:
    container = _container(request)
    document = container.db.get_document(document_id)
    if not document:
        raise AppError(404, "RESOURCE_NOT_FOUND", "document not found")
    if not document.get("active_graph_snapshot_id"):
        raise AppError(409, "GRAPH_NOT_READY", "graph is not ready", retryable=True)
    snapshot = container.db.get_snapshot(document["active_graph_snapshot_id"])
    artifacts = [
        item
        for item in container.db.list_artifacts(document_id)
        if item["job_id"] == snapshot["job_id"]
        and item["artifact_type"] == "extraction_jsonl"
    ]
    if not artifacts:
        return _ok(request, [], meta={"next_cursor": None, "has_more": False})
    results: list[dict[str, Any]] = []
    class_filter = request.query_params.get("extraction_class")
    page_filter = request.query_params.get("page_idx")
    query = normalize_query(request.query_params.get("q", ""))
    for line in Path(artifacts[0]["path"]).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        page_record = json.loads(line)
        for item in page_record.get("extractions", []):
            if class_filter and item["extraction_class"] != class_filter:
                continue
            if page_filter is not None and str(item["page_idx"]) != page_filter:
                continue
            if query and query not in normalize_query(
                f"{item['extraction_text']} {json.dumps(item['attributes'], ensure_ascii=False)}"
            ):
                continue
            results.append(item)
    return _ok(request, results, meta={"next_cursor": None, "has_more": False})


def normalize_query(value: str) -> str:
    return " ".join(value.casefold().split())


@router.get("/api/v1/documents/{document_id}/graph/manifest")
async def graph_manifest(document_id: str, request: Request) -> JSONResponse:
    container = _container(request)
    document = container.db.get_document(document_id)
    if not document:
        raise AppError(404, "RESOURCE_NOT_FOUND", "document not found")
    if not document.get("active_graph_snapshot_id"):
        raise AppError(
            409,
            "GRAPH_NOT_READY",
            "graph is not ready",
            retryable=True,
            details={"status": document["status"]},
        )
    snapshot = container.db.get_snapshot(document["active_graph_snapshot_id"])
    return _ok(request, snapshot["manifest"])


@router.get("/api/v1/documents/{document_id}/graph/nodes")
async def graph_nodes(document_id: str, request: Request) -> JSONResponse:
    container = _container(request)
    nodes = _decode_graph_artifact(container, document_id, "graph_nodes")
    type_filter = request.query_params.get("type")
    page_filter = request.query_params.get("page_idx")
    q = normalize_query(request.query_params.get("q", ""))
    if type_filter:
        nodes = [item for item in nodes if item["type"] == type_filter]
    if page_filter is not None:
        nodes = [item for item in nodes if str(item["page_idx"]) == page_filter]
    if q:
        nodes = [
            item
            for item in nodes
            if q in normalize_query(f"{item['name']} {item.get('evidence', '')}")
        ]
    limit = max(1, min(int(request.query_params.get("limit", "50")), 200))
    offset = max(0, int(request.query_params.get("cursor", "0") or 0))
    page = nodes[offset : offset + limit]
    next_cursor = str(offset + limit) if offset + limit < len(nodes) else None
    return _ok(
        request,
        page,
        meta={"next_cursor": next_cursor, "has_more": next_cursor is not None},
    )


@router.get("/api/v1/documents/{document_id}/graph/edges")
async def graph_edges(document_id: str, request: Request) -> JSONResponse:
    container = _container(request)
    edges = _decode_graph_artifact(container, document_id, "graph_edges")
    type_filter = request.query_params.get("type")
    source = request.query_params.get("source_id")
    target = request.query_params.get("target_id")
    if type_filter:
        edges = [item for item in edges if item["type"] == type_filter]
    if source:
        edges = [item for item in edges if item["source_id"] == source]
    if target:
        edges = [item for item in edges if item["target_id"] == target]
    limit = max(1, min(int(request.query_params.get("limit", "100")), 500))
    offset = max(0, int(request.query_params.get("cursor", "0") or 0))
    page = edges[offset : offset + limit]
    next_cursor = str(offset + limit) if offset + limit < len(edges) else None
    return _ok(
        request,
        page,
        meta={"next_cursor": next_cursor, "has_more": next_cursor is not None},
    )


@router.get("/api/v1/graph/nodes/{node_id}/neighbors")
async def graph_neighbors(node_id: str, request: Request) -> JSONResponse:
    container = _container(request)
    document_id = request.query_params.get("document_id")
    if not document_id:
        raise AppError(400, "DOCUMENT_SCOPE_REQUIRED", "document_id query parameter is required")
    graphs = [container.qa.loader.load(document_id)]
    from .qa import KGIndex

    index = KGIndex(graphs)
    depth = max(1, min(int(request.query_params.get("depth", "1")), 2))
    return _ok(request, index.neighbors(document_id, node_id, depth))


@router.post("/api/v1/qa/answers")
async def qa_answers(body: QARequest, request: Request) -> JSONResponse:
    container = _container(request)
    try:
        result = container.qa.answer(
            body,
            request_id=new_id("qa"),
            conversation_id=body.conversation_id or new_id("conv"),
        )
    except QAError as exc:
        status = 404
        if exc.code == "GRAPH_NOT_READY":
            status = 409
        elif exc.code.startswith("DEEPSEEK") or exc.code.startswith("LANGCHAIN"):
            status = 502
        raise AppError(
            status,
            exc.code,
            str(exc),
            retryable=exc.retryable,
        ) from exc
    return JSONResponse(status_code=200, content=result)


from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_lock = threading.Lock()
        self._initialized = False

    def connect(self) -> sqlite3.Connection:
        if not self._initialized:
            with self._init_lock:
                if not self._initialized:
                    self._initialize()
                    self._initialized = True
        conn = sqlite3.connect(
            self.path,
            timeout=30,
            check_same_thread=False,
            factory=ClosingConnection,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _initialize(self) -> None:
        with self.connect_initial() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS uploads (
                    upload_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    declared_sha256 TEXT,
                    actual_sha256 TEXT,
                    object_path TEXT NOT NULL,
                    indexing_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    object_path TEXT NOT NULL,
                    indexing_profile TEXT NOT NULL,
                    indexing_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    active_job_id TEXT,
                    active_graph_snapshot_id TEXT,
                    warning_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    idempotency_key TEXT,
                    parser_task_id TEXT,
                    run_id TEXT,
                    error_json TEXT,
                    warnings_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    FOREIGN KEY(document_id) REFERENCES documents(document_id)
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_idempotency
                ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL;

                CREATE TABLE IF NOT EXISTS job_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL,
                    path TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS graph_snapshots (
                    snapshot_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    node_count INTEGER NOT NULL,
                    edge_count INTEGER NOT NULL,
                    manifest_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    activated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS qa_requests (
                    request_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'local',
                    conversation_id TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer_status TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def connect_initial(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path,
            timeout=30,
            check_same_thread=False,
            factory=ClosingConnection,
        )
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row else None

    def create_upload(self, record: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO uploads (
                    upload_id, filename, media_type, size_bytes, declared_sha256,
                    actual_sha256, object_path, indexing_json, state, created_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["upload_id"],
                    record["filename"],
                    record["media_type"],
                    record["size_bytes"],
                    record.get("declared_sha256"),
                    record.get("actual_sha256"),
                    record["object_path"],
                    json.dumps(record["indexing"], ensure_ascii=False),
                    record["state"],
                    record["created_at"],
                    record["expires_at"],
                ),
            )
        return self.get_upload(record["upload_id"])

    def get_upload(self, upload_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM uploads WHERE upload_id = ?", (upload_id,)
            ).fetchone()
        result = self._dict(row)
        if result:
            result["indexing"] = json.loads(result.pop("indexing_json"))
        return result

    def update_upload(self, upload_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {"actual_sha256", "state"}
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"invalid upload fields: {invalid}")
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE uploads SET {assignments} WHERE upload_id = ?",
                (*fields.values(), upload_id),
            )

    def create_document(self, record: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO documents (
                    document_id, filename, media_type, size_bytes, sha256, object_path,
                    indexing_profile, indexing_json, status, active_job_id,
                    active_graph_snapshot_id, warning_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["document_id"],
                    record["filename"],
                    record["media_type"],
                    record["size_bytes"],
                    record["sha256"],
                    record["object_path"],
                    record["indexing_profile"],
                    json.dumps(record["indexing"], ensure_ascii=False),
                    record["status"],
                    record.get("active_job_id"),
                    record.get("active_graph_snapshot_id"),
                    json.dumps(record.get("warnings", []), ensure_ascii=False),
                    record["created_at"],
                    record["updated_at"],
                ),
            )
        return self.get_document(record["document_id"])

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        return self._decode_document(row)

    def find_document_by_sha(
        self, sha256: str, indexing_profile: str
    ) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM documents
                WHERE sha256 = ? AND indexing_profile = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (sha256, indexing_profile),
            ).fetchone()
        return self._decode_document(row)

    def list_documents(self, limit: int, cursor: str | None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM documents"
        params: list[Any] = []
        if cursor:
            sql += " WHERE created_at < ?"
            params.append(cursor)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit + 1)
        with self.connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._decode_document(row) for row in rows]

    def update_document(self, document_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "status",
            "active_job_id",
            "active_graph_snapshot_id",
            "warning_json",
            "indexing_json",
            "updated_at",
        }
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"invalid document fields: {invalid}")
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE documents SET {assignments} WHERE document_id = ?",
                (*fields.values(), document_id),
            )

    def create_job(self, record: dict[str, Any]) -> dict[str, Any]:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, document_id, status, stage, progress, attempt,
                    idempotency_key, parser_task_id, run_id, error_json,
                    warnings_json, created_at, started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["job_id"],
                    record["document_id"],
                    record["status"],
                    record["stage"],
                    record["progress"],
                    record.get("attempt", 1),
                    record.get("idempotency_key"),
                    record.get("parser_task_id"),
                    record.get("run_id"),
                    json.dumps(record.get("error"), ensure_ascii=False)
                    if record.get("error")
                    else None,
                    json.dumps(record.get("warnings", []), ensure_ascii=False),
                    record["created_at"],
                    record.get("started_at"),
                    record.get("finished_at"),
                ),
            )
        return self.get_job(record["job_id"])

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return self._decode_job(row)

    def get_job_by_idempotency(self, key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE idempotency_key = ?", (key,)
            ).fetchone()
        return self._decode_job(row)

    def update_job(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "status",
            "stage",
            "progress",
            "attempt",
            "parser_task_id",
            "run_id",
            "error_json",
            "warnings_json",
            "started_at",
            "finished_at",
        }
        invalid = set(fields) - allowed
        if invalid:
            raise ValueError(f"invalid job fields: {invalid}")
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id = ?",
                (*fields.values(), job_id),
            )

    def add_event(
        self,
        job_id: str,
        event_type: str,
        stage: str,
        progress: int,
        payload: dict[str, Any],
    ) -> None:
        with self.connect() as conn:
            seq = conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM job_events WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO job_events (
                    job_id, seq, event_type, stage, progress, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    seq,
                    event_type,
                    stage,
                    progress,
                    json.dumps(payload, ensure_ascii=False),
                    utc_now(),
                ),
            )

    def list_events(self, job_id: str, after_seq: int = 0) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM job_events
                WHERE job_id = ? AND seq > ? ORDER BY seq ASC
                """,
                (job_id, after_seq),
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            result.append(item)
        return result

    def add_artifact(self, record: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO artifacts (
                    artifact_id, document_id, job_id, artifact_type, path,
                    media_type, size_bytes, sha256, schema_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["artifact_id"],
                    record["document_id"],
                    record["job_id"],
                    record["artifact_type"],
                    record["path"],
                    record["media_type"],
                    record["size_bytes"],
                    record["sha256"],
                    record["schema_version"],
                    record["created_at"],
                ),
            )

    def list_artifacts(self, document_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE document_id = ? ORDER BY created_at DESC",
                (document_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        return self._dict(row)

    def create_snapshot(self, record: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO graph_snapshots (
                    snapshot_id, document_id, job_id, version, node_count,
                    edge_count, manifest_json, created_at, activated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["snapshot_id"],
                    record["document_id"],
                    record["job_id"],
                    record["version"],
                    record["node_count"],
                    record["edge_count"],
                    json.dumps(record["manifest"], ensure_ascii=False),
                    record["created_at"],
                    record.get("activated_at"),
                ),
            )

    def get_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM graph_snapshots WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
        result = self._dict(row)
        if result:
            result["manifest"] = json.loads(result.pop("manifest_json"))
        return result

    def next_snapshot_version(self, document_id: str) -> int:
        with self.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM graph_snapshots WHERE document_id = ?",
                    (document_id,),
                ).fetchone()[0]
            ) + 1

    def activate_snapshot(self, document_id: str, snapshot_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE graph_snapshots SET activated_at = ? WHERE snapshot_id = ?",
                (utc_now(), snapshot_id),
            )
            conn.execute(
                """
                UPDATE documents
                SET active_graph_snapshot_id = ?, status = 'indexed', updated_at = ?
                WHERE document_id = ?
                """,
                (snapshot_id, utc_now(), document_id),
            )

    def save_qa(self, record: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO qa_requests (
                    request_id, tenant_id, conversation_id, scope_json, question,
                    answer_status, answer, response_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["request_id"],
                    record.get("tenant_id", "local"),
                    record["conversation_id"],
                    json.dumps(record["scope"], ensure_ascii=False),
                    record["question"],
                    record["answer_status"],
                    record["answer"],
                    json.dumps(record["response"], ensure_ascii=False),
                    record["created_at"],
                ),
            )

    def _decode_document(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        result = self._dict(row)
        if result:
            result["indexing"] = json.loads(result.pop("indexing_json"))
            result["warnings"] = json.loads(result.pop("warning_json"))
        return result

    def _decode_job(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        result = self._dict(row)
        if result:
            error_raw = result.pop("error_json")
            warnings_raw = result.pop("warnings_json")
            result["error"] = json.loads(error_raw) if error_raw else None
            result["warnings"] = json.loads(warnings_raw)
        return result


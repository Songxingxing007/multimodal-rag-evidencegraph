from __future__ import annotations

import hashlib
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path):
    settings = Settings(
        app_env="test",
        auth_mode="disabled",
        data_dir=tmp_path / "data",
        pipeline_mode="local",
        extractor_mode="heuristic",
        qa_mode="deterministic",
        max_upload_mb=20,
        max_pages=20,
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "report.pdf"
    pdf = canvas.Canvas(str(path), pagesize=(595, 842))
    pdf.setFont("Helvetica", 16)
    pdf.drawString(72, 770, "Acme Annual Report")
    pdf.setFont("Helvetica", 11)
    pdf.drawString(72, 730, "Acme Data Center is located in Shanghai.")
    pdf.drawString(72, 706, "Annual rainfall is 775 mm.")
    pdf.drawString(72, 682, "The runoff ratio is 0.26.")
    pdf.showPage()
    pdf.save()
    return path


@pytest.fixture()
def indexed_document(client, sample_pdf) -> dict:
    payload = sample_pdf.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    create = client.post(
        "/api/v1/uploads",
        json={
            "filename": sample_pdf.name,
            "media_type": "application/pdf",
            "size_bytes": len(payload),
            "sha256": digest,
            "indexing": {"profile": "graph_v1", "parser": "local"},
        },
    )
    assert create.status_code == 201, create.text
    upload = create.json()["data"]
    put = client.put(
        upload["upload"]["url"],
        content=payload,
        headers={"Content-Type": "application/pdf"},
    )
    assert put.status_code == 204, put.text
    complete = client.post(
        f"/api/v1/uploads/{upload['upload_id']}/complete",
        json={"sha256": digest, "expected_size_bytes": len(payload)},
    )
    assert complete.status_code == 202, complete.text
    created = complete.json()["data"]
    for _ in range(120):
        job = client.get(f"/api/v1/jobs/{created['job_id']}")
        assert job.status_code == 200, job.text
        job_data = job.json()["data"]
        if job_data["status"] == "succeeded":
            return created
        if job_data["status"] == "failed":
            raise AssertionError(job.text)
        time.sleep(0.05)
    raise AssertionError("pipeline did not finish")


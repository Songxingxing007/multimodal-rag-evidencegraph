from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import time
from pathlib import Path

import httpx
from reportlab.pdfgen import canvas


SAMPLE_LINES = [
    "Acme Data Center is located in Shanghai.",
    "Annual rainfall is 775 mm.",
    "The runoff ratio is 0.26.",
]


def make_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=(595, 842))
    pdf.setFont("Helvetica", 16)
    pdf.drawString(72, 770, "Acme Annual Report")
    pdf.setFont("Helvetica", 11)
    y = 730
    for line in SAMPLE_LINES:
        pdf.drawString(72, y, line)
        y -= 24
    pdf.showPage()
    pdf.save()


def wait_for_job(client: httpx.Client, job_id: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/jobs/{job_id}")
        response.raise_for_status()
        job = response.json()["data"]
        if job["status"] in {"succeeded", "failed", "cancelled"}:
            return job
        time.sleep(0.25)
    raise TimeoutError(f"job {job_id} did not finish within {timeout}s")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="evidencegraph-smoke-") as temp:
        pdf_path = Path(temp) / "smoke_sample.pdf"
        make_pdf(pdf_path)
        payload = pdf_path.read_bytes()
        sha256 = hashlib.sha256(payload).hexdigest()
        with httpx.Client(base_url=args.base_url, timeout=60.0) as client:
            health = client.get("/health/ready")
            health.raise_for_status()
            create = client.post(
                "/api/v1/uploads",
                json={
                    "filename": pdf_path.name,
                    "media_type": "application/pdf",
                    "size_bytes": len(payload),
                    "sha256": sha256,
                    "indexing": {"profile": "graph_v1", "parser": "local"},
                },
            )
            create.raise_for_status()
            upload = create.json()["data"]
            put = client.put(
                upload["upload"]["url"],
                content=payload,
                headers={"Content-Type": "application/pdf"},
            )
            put.raise_for_status()
            complete = client.post(
                f"/api/v1/uploads/{upload['upload_id']}/complete",
                json={"sha256": sha256, "expected_size_bytes": len(payload)},
            )
            complete.raise_for_status()
            created = complete.json()["data"]
            job = wait_for_job(client, created["job_id"])

            document = client.get(f"/api/v1/documents/{created['document_id']}")
            document.raise_for_status()
            graph = client.get(
                f"/api/v1/documents/{created['document_id']}/graph/manifest"
            )
            graph.raise_for_status()
            extractions = client.get(
                f"/api/v1/documents/{created['document_id']}/extractions"
            )
            extractions.raise_for_status()
            qa = client.post(
                "/api/v1/qa/answers",
                json={
                    "question": "What is annual rainfall?",
                    "document_ids": [created["document_id"]],
                    "options": {"include_trace": True, "strict_citations": True},
                },
            )
            qa.raise_for_status()
            qa_data = qa.json()["data"]
            artifacts = client.get(
                f"/api/v1/documents/{created['document_id']}/artifacts"
            )
            artifacts.raise_for_status()

            report = {
                "health": health.json(),
                "job": job,
                "document": document.json()["data"],
                "graph_manifest": graph.json()["data"],
                "extractions": extractions.json()["data"],
                "qa": qa_data,
                "artifact_count": len(artifacts.json()["data"]),
            }
            out_dir = Path(__file__).resolve().parents[1] / "data"
            out_dir.mkdir(parents=True, exist_ok=True)
            report_path = out_dir / "smoke-test-report.json"
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(
                json.dumps(
                    {
                        "job_status": job["status"],
                        "document_status": report["document"]["status"],
                        "nodes": report["graph_manifest"]["counts"]["nodes"],
                        "edges": report["graph_manifest"]["counts"]["edges"],
                        "pages": report["graph_manifest"]["counts"]["pages"],
                        "extractions": len(report["extractions"]),
                        "qa_status": qa_data["answer_status"],
                        "citations": len(qa_data["citations"]),
                        "artifact_count": report["artifact_count"],
                        "report_path": str(report_path),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


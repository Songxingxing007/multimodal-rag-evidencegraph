from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health_and_openapi(client):
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"
    summary = client.get("/api/v1/health/summary")
    assert summary.status_code == 200
    assert summary.json()["data"]["capabilities"]["embedding_required"] is False
    schema = client.get("/openapi.json")
    assert schema.status_code == 200
    paths = schema.json()["paths"]
    assert "/api/v1/uploads" in paths
    assert "/api/v1/qa/answers" in paths


def test_document_pipeline_and_grounded_qa(client, indexed_document):
    document_id = indexed_document["document_id"]
    document = client.get(f"/api/v1/documents/{document_id}")
    assert document.status_code == 200
    data = document.json()["data"]
    assert data["status"] == "indexed"
    assert data["counts"]["pages"] == 1
    assert data["counts"]["nodes"] >= 2
    assert data["counts"]["edges"] >= 1

    nodes = client.get(f"/api/v1/documents/{document_id}/graph/nodes")
    assert nodes.status_code == 200
    assert any(node["name"] == "Acme Data Center" for node in nodes.json()["data"])

    edges = client.get(f"/api/v1/documents/{document_id}/graph/edges")
    assert edges.status_code == 200
    assert any(edge["type"] == "located_in" for edge in edges.json()["data"])

    extractions = client.get(f"/api/v1/documents/{document_id}/extractions")
    assert extractions.status_code == 200
    assert any(item["extraction_text"] == "775" for item in extractions.json()["data"])

    qa = client.post(
        "/api/v1/qa/answers",
        json={
            "question": "What is annual rainfall?",
            "document_ids": [document_id],
        },
    )
    assert qa.status_code == 200, qa.text
    answer = qa.json()["data"]
    assert answer["answer_status"] == "answered"
    assert answer["grounded"] is True
    assert "775" in answer["answer"]
    assert answer["citations"]
    citation = answer["citations"][0]
    assert citation["document_id"] == document_id
    assert citation["page_idx"] == 0
    assert citation["char_interval"]["end_pos"] > citation["char_interval"]["start_pos"]

    runoff = client.post(
        "/api/v1/qa/answers",
        json={
            "question": "What is runoff ratio?",
            "document_ids": [document_id],
        },
    )
    assert runoff.status_code == 200, runoff.text
    runoff_answer = runoff.json()["data"]
    assert "0.26" in runoff_answer["answer"]
    assert runoff_answer["answer"] != answer["answer"]

    unrelated = client.post(
        "/api/v1/qa/answers",
        json={
            "question": "Write a short poem about the ocean.",
            "document_ids": [document_id],
        },
    )
    assert unrelated.status_code == 200, unrelated.text
    unrelated_answer = unrelated.json()["data"]
    assert unrelated_answer["answer_status"] == "failed"
    assert unrelated_answer["answer_mode"] == "general"
    assert unrelated_answer["finish_reason"] == "model_not_configured"
    assert unrelated_answer["citations"] == []

    missing_evidence = client.post(
        "/api/v1/qa/answers",
        json={
            "question": "According to this document, what is the volcano alert threshold?",
            "document_ids": [document_id],
        },
    )
    assert missing_evidence.status_code == 200, missing_evidence.text
    missing_answer = missing_evidence.json()["data"]
    assert missing_answer["answer_status"] == "insufficient_evidence"
    assert missing_answer["answer_mode"] == "knowledge"
    assert missing_answer["citations"] == []


def test_artifact_and_job_resources(client, indexed_document):
    document_id = indexed_document["document_id"]
    artifacts = client.get(f"/api/v1/documents/{document_id}/artifacts")
    assert artifacts.status_code == 200
    items = artifacts.json()["data"]
    assert len(items) >= 8
    types = {item["artifact_type"] for item in items}
    assert {
        "mineru_content_list",
        "mineru_layout",
        "graph_ready_bundle",
        "extraction_jsonl",
        "graph_nodes",
        "graph_edges",
        "graph_provenance",
        "graph_manifest",
    } <= types
    download = client.get(items[0]["download_url"])
    assert download.status_code == 200
    job = client.get(f"/api/v1/jobs/{indexed_document['job_id']}")
    assert job.status_code == 200
    assert job.json()["data"]["progress"] == 100
    events = client.get(
        f"/api/v1/jobs/{indexed_document['job_id']}/events"
    )
    assert events.status_code == 200
    assert "event: completed" in events.text


def test_unsupported_format_and_not_ready_qa(client):
    unsupported = client.post(
        "/api/v1/uploads",
        json={
            "filename": "book.epub",
            "media_type": "application/epub+zip",
            "size_bytes": 100,
            "indexing": {"profile": "parse_only", "parser": "local"},
        },
    )
    assert unsupported.status_code == 415
    assert unsupported.json()["code"] == "FORMAT_NOT_SUPPORTED"

    qa = client.post(
        "/api/v1/qa/answers",
        json={"question": "test", "document_ids": ["doc_missing"]},
    )
    assert qa.status_code == 404
    assert qa.json()["code"] == "DOCUMENT_NOT_FOUND"


def test_bearer_authentication(tmp_path):
    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            auth_mode="bearer",
            api_auth_token="test-token",
            pipeline_mode="local",
            extractor_mode="heuristic",
            qa_mode="deterministic",
        )
    )
    with TestClient(app) as protected:
        assert protected.get("/health/ready").status_code == 200
        unauthorized = protected.get("/api/v1/health/summary")
        assert unauthorized.status_code == 401
        authorized = protected.get(
            "/api/v1/health/summary",
            headers={"Authorization": "Bearer test-token"},
        )
        assert authorized.status_code == 200


def test_general_question_uses_general_model_without_citations(client, indexed_document):
    class FakeModel:
        def invoke(self, messages):
            system = str(getattr(messages[0], "content", ""))
            if "请求路由器" in system:
                content = '{"route":"general","reason":"问题与文档无关"}'
            else:
                content = "海洋诗歌是一个通用创作请求，不需要知识库证据。"
            return SimpleNamespace(
                content=content,
                usage_metadata={
                    "input_tokens": 10,
                    "output_tokens": 8,
                    "total_tokens": 18,
                },
            )

    service = client.app.state.container.qa
    service.settings.qa_mode = "auto"
    service.settings.deepseek_api_key = "test-key"
    service._model_factory = FakeModel
    response = client.post(
        "/api/v1/qa/answers",
        json={
            "question": "Write a short poem about the ocean.",
            "document_ids": [indexed_document["document_id"]],
        },
    )
    assert response.status_code == 200, response.text
    answer = response.json()["data"]
    assert answer["answer_status"] == "answered"
    assert answer["answer_mode"] == "general"
    assert answer["grounded"] is False
    assert answer["citations"] == []
    assert "通用创作" in answer["answer"]


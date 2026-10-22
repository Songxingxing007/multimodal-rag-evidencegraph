# EvidenceGraph

EvidenceGraph is a document-to-knowledge-graph and evidence-grounded RAG application.

The current V1 flow is:

```text
PDF upload
  → MinerU-compatible document parsing
  → LangExtract entity/relation extraction
  → graph snapshot publication
  → LangChain tool-calling agent
  → cited answer or explicit insufficient-evidence response
```

## Repository layout

```text
backend/   FastAPI service, indexing pipeline, graph storage and QA orchestration
frontend/  React, Vite and Tailwind knowledge workspace
docs/      Architecture, API and input/output specifications
```

## Quick start

### Backend

```powershell
cd backend
uv sync --group dev
Copy-Item .env.example .env
```

Configure MinerU and DeepSeek credentials in `backend/.env`, then start:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

API documentation is available at `http://127.0.0.1:8010/docs`.

Without external model credentials, the backend runs in a deterministic fallback
mode for knowledge-base questions.

### Frontend

```powershell
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

Open `http://127.0.0.1:8443/`.

## QA behavior

The backend routes questions into four modes:

- `knowledge`: evidence is required and every factual claim must be cited.
- `general`: the general model answers without knowledge-base citations.
- `mixed`: document facts are cited and general explanations are explicitly labeled.
- `insufficient_evidence`: document-related questions without sufficient evidence are rejected.

## Tests

```powershell
cd backend
.\.venv\Scripts\python.exe -m pytest -q

cd ..\frontend
pnpm exec tsc --noEmit
pnpm build
pnpm test:integration
```

## Security

- Secrets are loaded from environment files that are excluded from Git.
- Uploaded documents, parsed artifacts, databases and model outputs are not committed.
- Public deployments should use a secret manager, object storage, authentication and tenant isolation.

## License

Project-owned code is provided for evaluation and development. Third-party
dependencies retain their respective licenses.

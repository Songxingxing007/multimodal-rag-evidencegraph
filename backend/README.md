# EvidenceGraph Backend

Independent GraphRAG backend implementing the contracts in
`docs/Backend-Service-Architecture-V1.0.md`.

## Structure

```text
backend/
├── app/
│   ├── api.py        HTTP resources
│   ├── config.py     backend/.env settings
│   ├── db.py         SQLite metadata and job events
│   ├── main.py       FastAPI application factory
│   ├── pipeline.py   parse -> extract -> graph -> publish
│   ├── qa.py         KG index and grounded QA
│   ├── schemas.py    request contracts
│   └── storage.py    local object storage
├── tests/
├── scripts/
├── .env
└── .venv
```

## Run

```powershell
cd backend
uv sync --group dev
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

OpenAPI: `http://127.0.0.1:8010/docs`

## Test

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe scripts\smoke_test.py
```

Default configuration is intentionally offline:

- `PIPELINE_MODE=local`
- `EXTRACTOR_MODE=heuristic`
- `QA_MODE=auto`（有 DeepSeek Key 时使用 LangChain Agent；无 Key 时安全降级）

For production integrations, set the MinerU and DeepSeek values in
`backend/.env`. Secrets are ignored by the root `.gitignore`.

## QA routing

`POST /api/v1/qa/answers` now returns `answer_mode`:

- `knowledge`: LangChain Agent selected KG tools and the answer has citations.
- `general`: unrelated question answered directly by the general model, with no citations.
- `mixed`: document facts are cited; general explanation is explicitly labeled.
- `insufficient_evidence`: a document-related question has no usable evidence.

When `DEEPSEEK_API_KEY` is configured, `QA_MODE=auto` enables the router and
tool-calling agent. Without a key, knowledge questions use a deterministic
keyword fallback, while unrelated questions report `model_not_configured`.


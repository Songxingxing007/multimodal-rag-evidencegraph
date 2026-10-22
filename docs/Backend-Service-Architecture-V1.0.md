# 多模态 RAG 后端服务架构与 API 规范 V1.0

> **文档定位**：面向工程落地的后端架构和 HTTP API 契约。
>
> **事实基线**：
>
> - MinerU 实测：[MinerU-输入输出规范v1.0.md](MinerU-输入输出规范v1.0.md)
> - LangExtract 实测：[LangExtract-输入输出与模型接入规范v1.0.md](LangExtract-输入输出与模型接入规范v1.0.md)
> - Agentic-RAG 实测：[AgenticRAG-MVP-V1.0.md](AgenticRAG-MVP-V1.0.md)
> - Bridge 实测：[BridgePipeline_v1.0.md](BridgePipeline_v1.0.md)
>
> **版本边界**：本文设计的是 V1 服务契约。当前本地 MVP 是“单进程、文件目录、内存索引”的验证实现，不等于生产部署。文中会明确标注哪些字段已验证、哪些是服务化时必须补齐的契约。
>
> **核心约束**：当前问答链路不使用 Embedding，不建向量库；召回依赖知识图谱、实体/关系邻接和页文本关键词检索。文档中的“多模态”表示支持多类型文档输入，不代表接入视觉语言模型或图片向量检索。

## 目录

- [0. 结论与设计原则](#0-结论与设计原则)
- [1. 总体系统架构](#1-总体系统架构)
- [2. 输入格式与准入策略](#2-输入格式与准入策略)
- [3. Indexing Pipeline](#3-indexing-pipeline)
- [4. Agentic-RAG 查询链路](#4-agentic-rag-查询链路)
- [5. 数据平面与持久化模型](#5-数据平面与持久化模型)
- [6. 组件间接口规范](#6-组件间接口规范)
- [7. HTTP API 统一约定](#7-http-api-统一约定)
- [8. 上传与文档 API](#8-上传与文档-api)
- [9. 任务与产物 API](#9-任务与产物-api)
- [10. 抽取与知识图谱 API](#10-抽取与知识图谱-api)
- [11. 问答 API](#11-问答-api)
- [12. 最终问答返回格式](#12-最终问答返回格式)
- [13. 错误码与状态映射](#13-错误码与状态映射)
- [14. 安全、隔离与非功能要求](#14-安全隔离与非功能要求)
- [15. 部署演进与验收](#15-部署演进与验收)
- [附录 A：MVP 产物到服务资源的映射](#附录-a-mvp-产物到服务资源的映射)
- [附录 B：关键参数速查](#附录-b关键参数速查)

---

## 0. 结论与设计原则

### 0.1 推荐的技术方案

| 层 | V1 推荐实现 | 责任 |
| --- | --- | --- |
| API 网关 | FastAPI + Uvicorn，Nginx/Ingress 终止 TLS | 鉴权、限流、上传签名、资源 API、错误封装 |
| 控制面数据库 | PostgreSQL | 租户、文档、任务、事件、产物索引、图谱快照版本 |
| 队列 | Redis + RQ/Celery/Arq 任一实现 | 异步解析、抽取、图谱构建、重索引 |
| 对象存储 | S3 / MinIO / 兼容 OSS | 原始文件、MinerU ZIP、抽取 JSONL、图谱快照、问答审计 |
| 解析 Worker | 独立运行环境，调用 MinerU 精准 API | 文件校验、云端任务、全量产物下载与归一化 |
| 抽取 Worker | 独立运行环境，调用 Bridge Pipeline + LangExtract + DeepSeek | 页切块、实体/关系抽取、证据对齐 |
| 图谱 Worker | 与抽取 Worker 可同进程但独立任务 | 实体合并、边校验、快照构建与原子发布 |
| 问答服务 | 独立运行环境，LangChain + MCP + DeepSeek | Agent 工具调用、匿名化、引用后处理 |
| 图检索 | V1 内存 `KGIndex`，按 active snapshot 懒加载 | 实体、邻接、页文本和精确证据查询 |
| 向量库 | **V1 不引入** | 当前链路不依赖 Embedding |

### 0.2 六条架构原则

1. **原始文件只上传一次**：后续所有解析、抽取和重试消费对象存储中的同一份原始文件。
2. **任务异步化**：上传接口只负责接收和排队，MinerU 解析和 LLM 抽取均不在 HTTP 请求线程内执行。
3. **产物不可变、指针可切换**：每次处理生成新的 `run_id` 和不可变快照；只有验收通过后才更新文档的 active graph。
4. **不直接消费 `full.md` 建图**：权威输入是 MinerU 精准模式的 `content_list.json` 和 `layout.json`。
5. **所有结论必须可溯源**：实体、关系、问答引用都应能定位到 `document_id → page_idx → char_interval → block_id → bbox`。
6. **密钥只在服务端**：MinerU Token、DeepSeek Key 和数据库凭证不得进入浏览器、URL、日志、响应或图谱数据。

### 0.3 当前能力与生产补充

| 能力 | 当前 MVP | V1 服务必须补齐 |
| --- | --- | --- |
| 存储 | 本地 `output/<run_id>/` | 对象存储 + 元数据数据库 |
| 任务 | 同步进程中调用或命令行运行 | 持久化任务、重试、取消、幂等 |
| 图谱 | 单文档内存索引 | 多租户、多文档 scope、快照版本 |
| 引用 | 回答文本内联 `[page X, span a-b]` | 结构化 `citations[]`，保留原字段兼容 |
| 检索 | 实体/邻居/页关键词 | 增加 scope、重排、权限过滤和命中诊断 |
| 文件类型 | Bridge 对 PDF 做过端到端验证 | 其他格式增加统一 Normalizer 和格式回归测试 |
| 并发 | 单机单文档 | 队列限流、上游 429 退避、任务租约 |
| 安全 | 本地 `.env` | KMS/Secret Manager、租户隔离、审计、病毒扫描 |

---

## 1. 总体系统架构

### 1.1 逻辑架构

```text
┌──────────────────────────────── Web / OpenAPI Client ────────────────────────────────┐
│  上传  │  查看解析进度 / 抽取结果 / 图谱  │  提问  │  下载受控产物  │  管理            │
└───────────────────────────────────┬───────────────────────────────────────────────────┘
                                    │ HTTPS / Bearer / Idempotency-Key / trace_id
                                    ▼
┌──────────────────────────────── API Gateway / FastAPI ───────────────────────────────┐
│  AuthN/AuthZ  │  Upload Session  │  Document API  │  Job API  │  Graph API  │ QA API   │
│  Schema Validation  │  Tenant Guard  │  Rate Limit  │  Problem Details  │  Audit      │
└───────────────┬───────────────────────┬─────────────────────────┬────────────────────┘
                │                       │                         │
                ▼                       ▼                         ▼
        ┌──────────────┐        ┌───────────────┐         ┌────────────────┐
        │ Object Store │        │ PostgreSQL    │         │ Redis Queue    │
        │ raw/artifacts│        │ metadata/state│         │ jobs/leases    │
        └──────┬───────┘        └───────┬───────┘         └───────┬────────┘
               │                        │                         │
               └───────────┬────────────┴─────────────┬───────────┘
                           ▼                          ▼
             ┌─────────────────────────┐  ┌────────────────────────────┐
             │ Parse Worker            │  │ QA Worker / Service         │
             │ MinerU precise adapter  │  │ LangChain + MCP + DeepSeek  │
             │ download + normalize    │  │ KGIndex + citations         │
             └────────────┬────────────┘  └──────────────┬─────────────┘
                          ▼                              │
             ┌─────────────────────────┐                  │
             │ Extract + Graph Worker  │                  │
             │ Bridge + LangExtract    │                  │
             │ nodes/edges/provenance  │──────────────────┘
             └─────────────────────────┘   active snapshot
```

### 1.2 服务边界

| 服务 | 对外暴露 | 内部依赖 | 不应承担 |
| --- | --- | --- | --- |
| `api-service` | 是 | PostgreSQL、Redis、对象存储 | 长时间 MinerU 轮询、LLM 抽取 |
| `parse-worker` | 否 | MinerU 精准 API、对象存储 | 图谱合并、问答 |
| `extract-graph-worker` | 否 | DeepSeek、LangExtract、对象存储 | 浏览器交互 |
| `qa-service` | 经 API 网关暴露 | active graph snapshot、DeepSeek、MCP 工具 | 文档解析 |
| `scheduler` | 否 | PostgreSQL、Redis | 持有租户业务状态 |

### 1.3 一次 Indexing 的时序

```text
Client       API          Store/DB/Queue       MinerU       Extract/Graph       QA
  │            │                │                 │                │             │
  │ 创建上传会话│───────────────>│                 │                │             │
  │ PUT 文件   │───────────────>│                 │                │             │
  │ complete   │──校验 SHA/MIME─>│                 │                │             │
  │<──202 job──│──创建 job ────>│                 │                │             │
  │            │                │──parse task────>│                │             │
  │            │                │<──status/files──│                │             │
  │            │                │──graph-ready bundle─────────────>│             │
  │            │                │<──raw/nodes/edges/provenance────│             │
  │            │                │──atomic active snapshot──────────────────────>│
  │ poll job   │────────────────│                 │                │             │
  │<──indexed──│                │                 │                │             │
  │ ask        │────────────────────────────────────────────────────────────────>│
  │<──answer + citations─────────────────────────────────────────────────────────│
```

### 1.4 为什么不能把 MVP 直接嵌进 API 进程

当前组件分别使用独立 `.venv`，且 MinerU 轮询、LangExtract 调用和 Agent 工具循环都可能持续数十秒。把它们放进 HTTP 进程会造成：

- 请求超时和 Web Worker 被长时间占用；
- 依赖冲突，无法独立升级 MinerU、LangExtract、LangChain；
- 进程重启丢失任务状态；
- 无法针对上游 429、超时和部分失败做可靠重试；
- 同一文档重复上传时可能并发写坏图谱。

V1 必须用“API 控制面 + 异步 Worker + 持久化状态”的方式解耦。本地开发可以使用 subprocess 或同机 Worker，但契约不能改变。

---

## 2. 输入格式与准入策略

### 2.1 格式矩阵

下表的“上传支持”基于 MinerU 云端能力；“Graph V1”只代表当前 Bridge 链路是否已实测。

| 扩展名 | 云端精准 API | Agent 轻量 API | 上传到本服务 | 当前 Graph V1 | 处理方式 |
| --- | --- | --- | --- | --- | --- |
| `.pdf` | 支持 | 支持 | 支持 | **支持，已实测** | MinerU precise → content/layout → Bridge |
| `.png` `.jpg` `.jpeg` `.jp2` `.webp` `.gif` `.bmp` | 支持 | 支持 | 支持 | 需补归一化验证 | OCR/版面解析后转统一 Block |
| `.docx` | 支持 | 支持 | 支持 | 需补归一化验证 | Office 原生解析 → 统一 Block |
| `.pptx` | 支持 | 支持 | 支持 | 需补归一化验证 | 逐页/逐形状解析 → 统一 Block |
| `.xlsx` | 支持 | 支持 | 支持 | 需补归一化验证 | Sheet/单元格 → 表格 Block |
| `.doc` `.ppt` `.xls` | 支持 | 不支持 | 支持，路由 precise | 需补归一化验证 | 精准 API 原生解析 |
| `.html` | 支持，需 `MinerU-HTML` | 不支持 | 支持，路由 precise | 需补归一化验证 | HTML 正文/表格 → Block |
| `.mhtml` `.mht` `.csv` `.tsv` `.rtf` `.odt` `.ods` `.odp` `.epub` `.ofd` | 当前云端不支持 | 不支持 | 默认拒绝 | 不可用 | 后续接自部署 MinerU 或专用适配器 |

### 2.2 两类索引档位

上传请求必须选择 `indexing.profile`：

| profile | 含义 | 当前准入 |
| --- | --- | --- |
| `graph_v1` | 解析完成后进入 LangExtract 和知识图谱构建 | V1 准入仅 `.pdf`；其他格式待 Normalizer 回归通过后开放 |
| `parse_only` | 只做 MinerU 解析并保留产物，不进入图谱 | 精准 API 支持的格式可开放 |

这样可以支持“多格式上传”，同时避免把尚未验证的 Office/图片格式静默送入图谱。对未具备 Graph V1 质量的格式，服务端返回可识别的 `FORMAT_GRAPH_NOT_READY`，文档保留为 `parse_only` 或直接阻止，由部署配置决定。

### 2.3 大小与页数

| 路径 | 大小上限 | 页数上限 | Token |
| --- | --- | --- | --- |
| MinerU precise | 200 MB | 200 页 | 必需，仅服务端保存 |
| MinerU Agent | 10 MB | 20 页 | 不需要 |
| 本服务 `graph_v1` | 200 MB | 200 页 | 由服务端 precise Token 提供 |

> 当前 Bridge 需要 bbox 和结构化内容块，因此 **Agent 轻量 API 不能作为 Graph V1 主路径**。Agent 模式只适合 `parse_only`、快速预览或降级。

### 2.4 文件安全校验

接收文件必须同时执行：

1. 文件名长度 ≤ 180，只保留 basename，拒绝路径分隔符和 NUL；
2. 扩展名白名单、声明 MIME 与实际文件头同时匹配；
3. 文件大小和页数在进入 MinerU 前预检；
4. 计算 SHA-256，支持租户内去重；
5. 可选 ClamAV 或企业安全扫描；
6. 远程 URL 导入默认关闭，开启时必须做 SSRF 防护；
7. PDF 宏、嵌入式附件、压缩炸弹和超长 OCR 任务需有资源限额。

---

## 3. Indexing Pipeline

### 3.1 总体流程

```text
receive
  → validate
  → persist raw file
  → MinerU submit
  → MinerU poll
  → download ZIP
  → artifact validation
  → normalize to GraphReadyBundle
  → page chunking
  → LangExtract entity/relation extraction
  → merge entities + validate edges
  → provenance assembly
  → graph snapshot validation
  → atomic publish
  → index ready
```

### 3.2 Stage 定义

| stage | 输入 | 输出 | 失败处理 |
| --- | --- | --- | --- |
| `receive` | 上传完成信号 | 文件记录 | 校验失败直接 4xx |
| `validate` | 原始文件 | MIME、SHA、页数 | 不进入队列 |
| `mineru_submit` | 文件和方法名 | `batch_id/task_id` | 可重试；先查幂等记录 |
| `mineru_poll` | 上游任务 ID | 完成状态和下载地址 | 指数退避；超时转失败 |
| `mineru_download` | ZIP URL | 原始 ZIP | 校验大小、成员和路径 |
| `normalize` | `content_list.json`、`layout.json` | `GraphReadyBundle` | 不完整时阻断索引 |
| `extract` | 页文本、块映射 | LangExtract JSONL | 按页重试，保留部分失败 |
| `graph_build` | JSONL、页映射 | nodes/edges/provenance/stats | 端点校验、去重、确定性 ID |
| `publish` | 图谱文件 | immutable snapshot + active pointer | 单事务切换 active |
| `index_ready` | active snapshot | 可查询状态 | 重建 KGIndex |

### 3.3 进入抽取前的强校验

`parse-worker` 只生成符合以下条件的 `GraphReadyBundle` 才能交给 Bridge：

- `page_idx` 为非负整数；
- 每页文本非空；
- `bbox` 明确为 0–1000 坐标；
- 页面尺寸 `width/height > 0`；
- 块的 `char_start/char_end` 能覆盖其实际文本；
- 表格以 `table_body` 或结构化单元格保存；
- `content_list.json` 和 `layout.json` 都属于同一个 `run_id`；
- MinerU 响应中的 `data_id` 与内部 `document_id` 一致或可映射。

当前实测中 `full.md` 可能遗漏被分类为 `header` 的整页内容，因此：

> `full.md` 可以保存、预览和作为 Markdown 导出，但**不得**作为 Graph V1 的唯一输入。

### 3.4 分块规范

V1 保持与 Bridge 一致的页级基础切块：

```text
document_id = <internal_document_id>
page_id     = <internal_document_id>:p<page_idx>
```

- 页文本按 MinerU 阅读顺序拼接；
- 页内 LangExtract 偏移为 0 基、闭开区间；
- 长页超过 `max_char_buffer` 时由 LangExtract 内部再切块，结果仍归并到页；
- 块与页文本之间保存 `char_start/char_end` 映射，用于反查 bbox；
- 后续若加入语义分块，必须保留 `parent_page_id` 和 `parent_char_interval`。

### 3.5 图谱构建与发布

实体 ID 使用确定性规则：

```text
node_id = <entity_type>:<normalized_name>
edge_id = rel:<source_id>:<normalized_relation_type>:<target_id>
```

发布前必须检查：

- 所有边的 source/target 都存在；
- 实体名和证据不为空；
- `page_idx` 存在且不越界；
- `char_interval` 可在页文本中定位；
- 同一实体/边按规则去重，并保留别名和证据次数；
- `stats.json` 记录丢弃原因和计数；
- 新快照校验通过后，才更新 `documents.active_graph_snapshot_id`。

### 3.6 重试与幂等

- Job、stage 和上游任务 ID 必须持久化；
- 上游提交成功但客户端超时时，先用 `Idempotency-Key` 查已有任务，不能盲目重复提交；
- MinerU 429 使用 5、10、20、40、60 秒封顶退避；
- MinerU 超时只标记本地任务失败，记录远端任务 ID，支持人工接管；
- 抽取按 `page_id + profile_version + model_id` 缓存；
- 同一源文件、同一 profile 的并发重复索引请求返回已有 job 或 409；
- Worker 使用租约和心跳，租约过期可被安全重领；
- 产物路径不可变，禁止覆盖已完成 run。

---

## 4. Agentic-RAG 查询链路

### 4.1 查询流程

```text
question + document scope
  → auth/scope check
  → load active graph snapshot
  → search_entities
  → get_neighbors（按需）
  → search_pages（数值、定义、原文细节）
  → get_evidence（精确切片）
  → answer generation
  → strict citation validation
  → response envelope + audit
```

### 4.2 Agent 工具契约

| 工具 | 参数 | 关键约束 | 返回 |
| --- | --- | --- | --- |
| `search_entities` | `query`, `limit` | 只搜索授权文档 scope | 实体 ID、名称、类型、页码、区间 |
| `get_neighbors` | `entity_id`, `depth` | 深度默认 1，最大 2 | 中心节点、邻接节点、边、证据 |
| `search_pages` | `query`, `top_k` | 仅 active snapshot 页文本 | 页命中、snippet、字符区间 |
| `get_evidence` | `document_id`, `page_idx`, `start`, `end` | 区间必须有效且有权限 | 精确文本与溯源 |

当前 MVP 将工具注册在进程内 MCP Server，`MCPAdapter(server)` 无子进程、无端口。生产 V1 可保持 in-process，也可以把 MCP 抽成独立服务；对外 API 不应泄漏 MCP 传输模型。

### 4.3 DeepSeek 接入

| 参数 | V1 值 |
| --- | --- |
| LangChain 组件 | `ChatOpenAI` |
| Base URL | `https://api.deepseek.com` |
| Model | 部署配置，当前实测 `deepseek-flash` |
| Temperature | `0` |
| Thinking | `disabled` |
| `max_tokens` | 建议 ≥2048，按实际响应配置 |
| Agent recursion limit | 12 |
| 工具调用预算 | 默认 6，API 最大允许 10 |

DeepSeek Key 只通过服务端环境变量或 Secret Manager 注入。浏览器只持有本系统访问令牌。

### 4.4 引用后处理

当前 MVP 的回答是自然语言加内联引用：

```text
[page 0, span 1185-1192]
```

V1 服务必须在返回前执行：

1. 解析内联引用；
2. 用 `document_id + page_idx + span` 校验引用存在；
3. 映射到页内 `block_id`；
4. 回填 `bbox`、`bbox_pdf` 和原文 `evidence`；
5. 删除无法校验的引用；
6. 如果答案完全没有有效证据，返回 `insufficient_evidence`。

### 4.5 Prompt Injection 与非可信内容边界

文档文本、表格、图片 OCR 和 MCP 工具输出都属于不可信数据。System prompt 必须明确：

- 文档中的指令不具有系统指令效力；
- Agent 不得越过 API 传入的 document scope；
- 不得仅凭模型记忆补写文档事实；
- 数值必须有页面原文或表格证据；
- 证据不足时拒答或明确不确定性。

---

## 5. 数据平面与持久化模型

### 5.1 关系模型

#### `tenants`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `tenant_id` | UUID PK | 租户 |
| `name` | text | 名称 |
| `status` | enum | `active/suspended` |
| `created_at` | timestamptz | 创建时间 |

#### `documents`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `document_id` | UUID PK | 对外文档 ID |
| `tenant_id` | UUID FK | 租户 |
| `filename` | text | 安全 basename |
| `media_type` | text | 归一化 MIME |
| `size_bytes` | bigint | 文件大小 |
| `sha256` | char(64) | 内容摘要 |
| `object_key` | text | 原始文件对象键 |
| `indexing_profile` | text | `graph_v1` / `parse_only` |
| `status` | enum | 文档状态 |
| `active_job_id` | UUID nullable | 当前索引任务 |
| `active_graph_snapshot_id` | UUID nullable | 当前可查询快照 |
| `failure_code` | text nullable | 最近失败码 |
| `created_at/updated_at` | timestamptz | 审计时间 |

#### `index_jobs`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `job_id` | UUID PK | 任务 |
| `tenant_id/document_id` | UUID | 范围 |
| `status` | enum | `queued/running/succeeded/failed/cancelled` |
| `stage` | enum | 当前阶段 |
| `progress` | smallint | 0–100，单调不减 |
| `parser_provider` | text | `mineru_precise` |
| `parser_task_id` | text nullable | MinerU 上游 ID |
| `idempotency_key` | text | 唯一约束 |
| `attempt` | int | 尝试次数 |
| `error_code/error_detail` | text | 可脱敏错误 |
| `started_at/finished_at` | timestamptz | 时间 |

#### `job_events`

`event_id, job_id, seq, event_type, stage, progress, payload_json, created_at`

用于 SSE 重放和诊断；敏感字段先脱敏。

#### `artifacts`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `artifact_id` | UUID PK | 产物 |
| `tenant_id/document_id/job_id` | UUID | 所有权和来源 |
| `artifact_type` | text | 见下文 |
| `object_key` | text | 对象存储位置 |
| `media_type` | text | MIME |
| `size_bytes` | bigint | 大小 |
| `sha256` | char(64) | 完整性 |
| `schema_version` | text | 数据契约版本 |
| `created_at` | timestamptz | 创建时间 |

#### `graph_snapshots`

`snapshot_id, document_id, job_id, version, node_count, edge_count, status, manifest_object_key, created_at, activated_at`

#### `qa_requests`

`request_id, tenant_id, conversation_id, scope_json, question, answer_status, answer, citations_json, tool_trace_json, usage_json, latency_ms, created_at`

### 5.2 文档状态

| 状态 | 含义 | 可执行动作 |
| --- | --- | --- |
| `uploading` | 上传会话进行中 | PUT content |
| `validating` | 正在校验文件 | 取消 |
| `queued` | 等待 Worker | 取消 |
| `parsing` | MinerU 解析中 | 取消、查看进度 |
| `extracting` | LLM 抽取中 | 取消 |
| `graph_building` | 图谱构建中 | 取消 |
| `indexed` | active graph 可用 | 查询、重索引 |
| `parse_ready` | 只有解析产物，未建图 | 下载、补建图 |
| `failed` | 失败 | 重试、查看错误 |
| `deleting` | 删除中 | 无 |
| `deleted` | 已逻辑删除 | 由保留策略物理清理 |

### 5.3 任务状态机

```text
queued ──> running ──> succeeded
   │          │
   └──────────┴──────> failed
   │          └──────> cancelled
```

仅允许以下转换：

- `queued → running`
- `queued/running → cancelled`
- `running → succeeded/failed`
- `failed → queued` 通过新建 retry job 实现

禁止原地修改已完成 job 的结果。

### 5.4 对象键规范

```text
tenants/{tenant_id}/documents/{document_id}/
  source/{sha256}.{ext}
  jobs/{job_id}/mineru/source.zip
  jobs/{job_id}/mineru/{content_list,layout,model}.json
  jobs/{job_id}/mineru/images/{image_id}.jpg
  jobs/{job_id}/extract/raw_result.jsonl
  jobs/{job_id}/graph/nodes.json
  jobs/{job_id}/graph/edges.json
  jobs/{job_id}/graph/provenance.jsonl
  jobs/{job_id}/graph/stats.json
  jobs/{job_id}/manifest.json
```

所有下载必须经授权接口或短时签名 URL，不能直接暴露对象存储桶。

---

## 6. 组件间接口规范

### 6.1 `GraphReadyBundle`

解析 Worker 交给抽取 Worker 的归一化中间契约：

```json
{
  "schema_version": "graph-ready-bundle/1.0",
  "document_id": "01JXX...",
  "run_id": "20261022T120000Z_report",
  "source": {
    "filename": "report.pdf",
    "media_type": "application/pdf",
    "sha256": "64-hex"
  },
  "parser": {
    "provider": "mineru",
    "mode": "precise",
    "model_version": "pipeline",
    "parser_version": "3.4.4"
  },
  "coordinate_system": "bbox_0_1000_by_page",
  "pages": [
    {
      "page_idx": 0,
      "width": 595,
      "height": 841,
      "text": "page text in reading order",
      "blocks": [
        {
          "block_id": "01JXX:p0:b0",
          "type": "text",
          "text": "block text",
          "char_start": 0,
          "char_end": 120,
          "bbox": [100, 101, 447, 116],
          "bbox_pdf": [59.5, 84.95, 265.97, 97.56],
          "table_html": null
        }
      ]
    }
  ],
  "artifacts": {
    "mineru_content_list": "artifact-id",
    "mineru_layout": "artifact-id",
    "mineru_model": "artifact-id",
    "full_markdown": "artifact-id"
  }
}
```

约束：

- `text`、`blocks[].text` 的字符编码统一为 UTF-8；
- `char_start/char_end` 相对该页 `text`；
- `bbox` 为 0–1000 归一化；
- `bbox_pdf` 由 `bbox / 1000 × page_size` 生成；
- `block_id` 在一次 document snapshot 内稳定；
- `full_markdown` 只用于预览，不是权威抽取源。

### 6.2 LangExtract 输入与输出

每页生成一个 Document：

```json
{
  "text": "<page text>",
  "document_id": "<document_id>:p<page_idx>",
  "additional_context": {
    "page_idx": 0,
    "document_id": "<document_id>"
  }
}
```

抽取输出使用 LangExtract `AnnotatedDocument` / JSONL：

```json
{
  "document_id": "01JXX...:p0",
  "text": "page text",
  "extractions": [
    {
      "extraction_class": "entity",
      "extraction_text": "Pine Creek",
      "char_interval": {"start_pos": 1185, "end_pos": 1195},
      "alignment_status": "match_exact",
      "extraction_index": 1,
      "group_index": 0,
      "description": null,
      "attributes": {"entity_type": "location"}
    }
  ]
}
```

DeepSeek 接入固定要求：

- 显式 OpenAI provider；
- 关闭 thinking；
- `response_format=json_object`；
- 使用 `UnicodeTokenizer`；
- `max_output_tokens` 足够大；
- prompt 中避免把内部字段名误当输出键。

### 6.3 Graph Snapshot Manifest

```json
{
  "schema_version": "kg-snapshot/1.0",
  "snapshot_id": "01JGRAPH...",
  "document_id": "01JXX...",
  "job_id": "01JJOB...",
  "parser": {"provider": "mineru", "version": "3.4.4"},
  "extractor": {"model": "deepseek-flash", "profile": "enterprise_kg_v1"},
  "counts": {"nodes": 51, "edges": 12, "provenance": 63},
  "files": {
    "nodes": "tenants/.../nodes.json",
    "edges": "tenants/.../edges.json",
    "provenance": "tenants/.../provenance.jsonl",
    "stats": "tenants/.../stats.json"
  },
  "validated": true,
  "created_at": "2026-10-22T12:00:00Z"
}
```

### 6.4 图谱对象

`nodes.json[]` 必填字段：

`id, type, name, attributes, document_id, page_idx, char_interval, evidence`

`edges.json[]` 必填字段：

`id, type, source_id, target_id, document_id, page_idx, evidence, evidence_count, attributes`

精确数值提取还应保留：

`bbox, bbox_pdf, block_id, value, unit, raw_text`

其中 `value/unit/raw_text` 不是当前 MVP 已有字段，属于 V1 的数值节点扩展；兼容旧图谱时可从 `name/evidence/attributes` 推导。

---

## 7. HTTP API 统一约定

### 7.1 Base URL 与版本

```text
https://<host>/api/v1
```

- 版本号在路径中；
- 破坏性变更升 `/v2`；
- 新增可选字段不升主版本；
- OpenAPI 3.1 是机器可读契约。

### 7.2 请求头

| Header | 必需 | 说明 |
| --- | --- | --- |
| `Authorization: Bearer <access_token>` | API 请求必需 | 本系统 OAuth2/OIDC/JWT |
| `X-Request-Id` | 可选 | 客户端链路 ID；服务端缺失时生成 |
| `X-Tenant-Id` | 条件 | 多租户网关注入，普通客户端不可伪造 |
| `Idempotency-Key` | 创建型 POST 建议/必需 | 24 小时幂等窗口 |
| `Content-Type` | 按接口 | JSON 或文件 MIME |
| `Accept` | 可选 | `application/json` 或 `text/event-stream` |

### 7.3 成功信封

单资源：

```json
{
  "data": {},
  "request_id": "req_01J...",
  "meta": null
}
```

列表：

```json
{
  "data": [],
  "request_id": "req_01J...",
  "meta": {
    "next_cursor": "opaque-cursor",
    "has_more": true
  }
}
```

### 7.4 错误信封

采用 RFC 9457 Problem Details 风格：

```json
{
  "type": "https://api.example.com/problems/format-not-supported",
  "title": "Unsupported document format",
  "status": 415,
  "code": "FORMAT_NOT_SUPPORTED",
  "detail": "The file extension .epub is not supported by MinerU precise API.",
  "instance": "/api/v1/uploads",
  "request_id": "req_01J...",
  "retryable": false,
  "details": {
    "field": "file",
    "extension": ".epub"
  }
}
```

### 7.5 时间、ID 与分页

- 时间统一 RFC 3339 UTC，如 `2026-10-22T12:00:00Z`；
- 内部 ID 使用 UUIDv7 或 ULID，便于排序；
- 游标分页，不暴露 offset；
- 数值区间统一闭开 `[start,end)`；
- 页码统一 0 基；
- bbox 数组统一 `[x0,y0,x1,y1]`，并显式声明坐标系。

---

## 8. 上传与文档 API

### 8.1 `POST /uploads`：创建上传会话

请求：

```json
{
  "filename": "annual_report.pdf",
  "media_type": "application/pdf",
  "size_bytes": 34981,
  "sha256": "64-hex-digest",
  "indexing": {
    "profile": "graph_v1",
    "parser": "mineru_precise",
    "language": "ch",
    "ocr": "auto",
    "enable_table": true,
    "enable_formula": true,
    "page_range": null,
    "extraction_profile": "enterprise_kg_v1"
  }
}
```

字段：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `filename` | string | 是 | 1–180 字符，含扩展名 |
| `media_type` | string | 是 | 建议实测确认，不用浏览器猜测作为唯一依据 |
| `size_bytes` | int64 | 是 | 必须大于 0 |
| `sha256` | string | 条件 | 客户端可先计算；未提供时 complete 阶段计算 |
| `indexing.profile` | enum | 是 | `graph_v1/parse_only` |
| `indexing.parser` | enum | 是 | V1 `mineru_precise`；预览可另加 `mineru_agent` |
| `language` | string | 否 | 默认 `ch`，仅 PDF |
| `ocr` | enum | 否 | `auto/true/false` |
| `enable_table` | bool | 否 | 默认 `true` |
| `enable_formula` | bool | 否 | 默认 `true` |
| `page_range` | string/null | 否 | precise 使用 `1,3-5`；由服务端按上游格式转换 |
| `extraction_profile` | string | `graph_v1` 必填 | 抽取规则版本 |

响应 `201`：

```json
{
  "data": {
    "upload_id": "upl_01J...",
    "expires_at": "2026-10-22T12:30:00Z",
    "upload": {
      "method": "PUT",
      "url": "https://storage.example.com/presigned/...",
      "headers": {
        "Content-Type": "application/pdf",
        "Content-Length": "34981"
      },
      "mode": "presigned"
    }
  },
  "request_id": "req_01J...",
  "meta": null
}
```

部署选择：

- 生产默认 `presigned`，文件不经过 API 进程；
- 本地开发可返回 `/api/v1/uploads/{upload_id}/content`，由 API 流式写入；
- 两种模式的 `complete` 接口相同。

### 8.2 `PUT /uploads/{upload_id}/content`

用于非 presigned 部署。请求体是原始字节，不包 multipart。服务端必须：

- 校验上传会话未过期；
- 流式写临时对象，限制最大字节数；
- 计算 SHA-256；
- 不信任覆盖写入；
- 成功返回 `204 No Content`。

presigned 模式直接 PUT 到对象存储，不经过本 API。

### 8.3 `POST /uploads/{upload_id}/complete`

请求：

```json
{
  "sha256": "64-hex-digest",
  "expected_size_bytes": 34981
}
```

服务端验证对象、MIME、大小、SHA 和格式后创建文档与 job。

响应 `202`：

```json
{
  "data": {
    "document_id": "doc_01J...",
    "job_id": "job_01J...",
    "status": "queued",
    "deduplicated": false,
    "links": {
      "document": "/api/v1/documents/doc_01J...",
      "job": "/api/v1/jobs/job_01J...",
      "events": "/api/v1/jobs/job_01J.../events"
    }
  },
  "request_id": "req_01J...",
  "meta": null
}
```

同一租户内同一 SHA、同一 profile 的重复 complete：

- 默认返回已有文档与 job，`deduplicated=true`，HTTP 200；
- `force_reindex=true` 时创建新 job，不覆盖旧快照。

### 8.4 `GET /documents`

Query：

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `status` | string | 可重复 |
| `media_type` | string | MIME prefix |
| `created_from/created_to` | datetime | 时间范围 |
| `q` | string | 文件名搜索 |
| `limit` | int | 1–100，默认 20 |
| `cursor` | string | 游标 |

响应项：

```json
{
  "document_id": "doc_01J...",
  "filename": "annual_report.pdf",
  "media_type": "application/pdf",
  "size_bytes": 34981,
  "status": "indexed",
  "indexing_profile": "graph_v1",
  "active_job_id": "job_01J...",
  "active_graph_snapshot_id": "gs_01J...",
  "counts": {"nodes": 51, "edges": 12, "pages": 2},
  "created_at": "2026-10-22T12:00:00Z",
  "updated_at": "2026-10-22T12:00:10Z"
}
```

### 8.5 `GET /documents/{document_id}`

返回文档详情，并包含：

- `source`：原始文件元数据；
- `active_job`：当前任务；
- `active_graph_snapshot`：当前可用图谱；
- `capabilities`：`{queryable, downloadable, reindexable}`；
- `counts`：页数、节点数、边数、抽取条数；
- `warnings`：例如 `full_markdown_incomplete`；
- `links`：job、graph、extractions、artifacts。

### 8.6 `POST /documents/{document_id}/reindex`

请求：

```json
{
  "indexing": {
    "profile": "graph_v1",
    "extraction_profile": "enterprise_kg_v1_v2",
    "parser_options": {
      "language": "ch",
      "enable_table": true,
      "enable_formula": true
    }
  },
  "activate_on_success": true
}
```

返回新 `job_id`。原 snapshot 在成功后仍可保留用于回滚。

### 8.7 `DELETE /documents/{document_id}`

逻辑删除并创建清理任务。若文档正在索引：

- 设置 `delete_after_current=true`；
- 尽量取消远端任务；
- 禁止再创建新查询 scope。

响应 `202`。生产环境按保留策略物理删除对象和运行日志。

---

## 9. 任务与产物 API

### 9.1 `GET /jobs/{job_id}`

响应：

```json
{
  "data": {
    "job_id": "job_01J...",
    "document_id": "doc_01J...",
    "status": "running",
    "stage": "extracting",
    "progress": 68,
    "attempt": 1,
    "created_at": "2026-10-22T12:00:00Z",
    "started_at": "2026-10-22T12:00:02Z",
    "finished_at": null,
    "stages": [
      {"name": "validate", "status": "succeeded", "started_at": "...", "finished_at": "..."},
      {"name": "mineru_poll", "status": "succeeded", "started_at": "...", "finished_at": "..."},
      {"name": "extract", "status": "running", "started_at": "...", "finished_at": null}
    ],
    "warnings": [],
    "error": null,
    "links": {
      "events": "/api/v1/jobs/job_01J.../events",
      "document": "/api/v1/documents/doc_01J..."
    }
  },
  "request_id": "req_01J...",
  "meta": null
}
```

`progress` 只用于展示；真实完成状态以 `status` 和 active snapshot 为准。

### 9.2 `GET /jobs/{job_id}/events`

`Accept: text/event-stream`。事件：

```text
event: stage
id: 42
data: {"job_id":"job_01J...","stage":"mineru_poll","status":"succeeded","progress":45,"at":"..."}

event: warning
data: {"code":"FULL_MARKDOWN_INCOMPLETE","message":"full.md is shorter than content_list.md"}

event: completed
data: {"job_id":"job_01J...","status":"succeeded","graph_snapshot_id":"gs_01J..."}

event: failed
data: {"job_id":"job_01J...","code":"EXTRACTION_FAILED","retryable":true}
```

支持 `Last-Event-ID` 重放；连接定期发送 heartbeat comment。

### 9.3 `POST /jobs/{job_id}/cancel`

仅 `queued` 或 `running` 可取消。响应 `202`，返回 `cancel_requested=true`。远端 MinerU 能取消时同步取消，不能取消时标注 `remote_cancel_supported=false`。

### 9.4 `GET /documents/{document_id}/artifacts`

返回产物索引，不直接返回大文件：

```json
{
  "data": [
    {
      "artifact_id": "art_01J...",
      "artifact_type": "mineru_content_list",
      "media_type": "application/json",
      "size_bytes": 5460,
      "sha256": "...",
      "schema_version": "mineru-3.4.4-content-list",
      "download_url": "/api/v1/artifacts/art_01J.../download"
    }
  ],
  "request_id": "req_01J...",
  "meta": {"next_cursor": null, "has_more": false}
}
```

允许的 `artifact_type`：

`source_file, mineru_zip, mineru_full_md, mineru_content_list, mineru_content_list_v2, mineru_layout, mineru_model, mineru_image, extraction_jsonl, graph_nodes, graph_edges, graph_provenance, graph_stats, run_manifest, qa_audit`

### 9.5 `GET /artifacts/{artifact_id}/download`

授权后返回 `302` 到短时签名 URL，或 `200` 流式下载。禁止仅凭可猜测对象键下载。

---

## 10. 抽取与知识图谱 API

### 10.1 `GET /documents/{document_id}/extractions`

Query：

| 参数 | 说明 |
| --- | --- |
| `page_idx` | 精确页 |
| `extraction_class` | `entity/relation/...` |
| `alignment_status` | 对齐状态 |
| `q` | 在 `extraction_text/evidence` 中搜索 |
| `limit/cursor` | 游标分页 |

返回 LangExtract 兼容字段：

```json
{
  "document_id": "doc_01J...",
  "page_idx": 0,
  "extraction_class": "entity",
  "extraction_text": "Pine Creek",
  "char_interval": {"start_pos": 1185, "end_pos": 1195},
  "alignment_status": "match_exact",
  "extraction_index": 1,
  "group_index": 0,
  "description": null,
  "attributes": {"entity_type": "location"},
  "block_id": "doc_01J...:p0:b12",
  "bbox": [320, 500, 410, 520],
  "bbox_coordinate_system": "0_1000_by_page"
}
```

> `block_id/bbox` 是服务端根据 char interval 回填的增强字段，不改变 LangExtract 原始输出。

### 10.2 `GET /documents/{document_id}/graph/manifest`

返回 active snapshot 的 manifest。查询未完成时：

```json
{
  "type": "https://api.example.com/problems/graph-not-ready",
  "title": "Graph is not ready",
  "status": 409,
  "code": "GRAPH_NOT_READY",
  "detail": "The document is currently parsing.",
  "instance": "/api/v1/documents/doc_01J.../graph/manifest",
  "request_id": "req_01J...",
  "retryable": true,
  "details": {"status": "parsing", "active_job_id": "job_01J..."}
}
```

### 10.3 `GET /documents/{document_id}/graph/nodes`

Query：

`type, q, page_idx, min_degree, limit, cursor, snapshot_id?`

默认只查询该文档 active snapshot。服务端必须按租户和文档 scope 过滤。

```json
{
  "data": [
    {
      "id": "location:pine creek",
      "type": "location",
      "name": "Pine Creek",
      "aliases": [],
      "attributes": {},
      "document_id": "doc_01J...",
      "page_idx": 0,
      "block_id": "doc_01J...:p0:b12",
      "char_interval": {"start_pos": 1185, "end_pos": 1195},
      "bbox": [228, 68, 769, 95],
      "bbox_pdf": [135.66, 57.2, 457.56, 79.9],
      "evidence": "Pine Creek",
      "degree": 3
    }
  ],
  "request_id": "req_01J...",
  "meta": {"next_cursor": null, "has_more": false}
}
```

### 10.4 `GET /documents/{document_id}/graph/edges`

Query：

`type, source_id, target_id, page_idx, limit, cursor, snapshot_id?`

响应字段严格对应 `edges.json`：

`id, type, source_id, target_id, attributes, document_id, page_idx, char_interval?, bbox?, evidence, evidence_count`

### 10.5 `GET /graph/nodes/{node_id}/neighbors`

Query：

`depth` 1–2，默认 1；`document_ids` 必须包含或能反查权限范围。

响应：

```json
{
  "center": {"id": "location:pine creek", "type": "location", "name": "Pine Creek"},
  "depth": 1,
  "nodes": [],
  "edges": []
}
```

同一实体可能在多文档出现。V1 要么先做跨文档实体合并，要么明确返回 `occurrences[]`；禁止仅按名称把不同租户的节点合并。

### 10.6 多文档查询 scope

问答与图谱查询允许：

```json
{
  "document_ids": ["doc_A", "doc_B"]
}
```

规则：

- 最多默认 10 个文档；
- 所有文档必须 `indexed` 且属于同一租户；
- 多文档图谱以 `(document_id,node_id)` 为 occurrence，跨文档合并另建 canonical entity；
- V1 若尚未实现跨文档实体消解，则按文档分别检索、合并证据后回答。

---

## 11. 问答 API

### 11.1 `POST /qa/answers`

同步模式适用于目标 SLA 30–60 秒且前置代理允许长请求的场景。

请求：

```json
{
  "question": "Pine Ck 的年降雨量和径流比分别是多少？",
  "document_ids": ["doc_01J..."],
  "conversation_id": null,
  "options": {
    "answer_style": "standard",
    "answer_language": "zh-CN",
    "max_tool_calls": 6,
    "top_k_pages": 3,
    "include_trace": true,
    "include_evidence": true,
    "strict_citations": true
  }
}
```

字段：

| 字段 | 必填 | 约束 |
| --- | --- | --- |
| `question` | 是 | 1–4000 字符，去除首尾空白后非空 |
| `document_ids` | 是 | 1–10，全部可读且 `indexed` |
| `conversation_id` | 否 | 服务端生成或校验归属 |
| `answer_style` | 否 | `concise/standard/detailed` |
| `answer_language` | 否 | BCP 47，默认继承请求语言 |
| `max_tool_calls` | 否 | 1–10，默认 6 |
| `top_k_pages` | 否 | 1–10，默认 3 |
| `include_trace` | 否 | 生产默认受角色控制 |
| `include_evidence` | 否 | 默认 true |
| `strict_citations` | 否 | 默认 true |

同步响应 `200` 见第 12 节。若请求处理超过部署限制，返回 `202` + `qa_job_id` 的异步模式，或由服务端始终使用异步。

### 11.2 `POST /qa/answers:stream`

`Content-Type: text/event-stream`，事件顺序：

```text
event: accepted
data: {"request_id":"qa_01J...","conversation_id":"conv_01J..."}

event: step
data: {"tool":"search_entities","status":"completed","latency_ms":38}

event: token
data: {"text":"根据"}

event: citation
data: {"citation_id":"cit_1","document_id":"doc_01J...","page_idx":0}

event: completed
data: {"answer_status":"answered","finish_reason":"stop","usage":{"total_tokens":812}}
```

客户断开连接不得自动取消长任务，除非明确调用取消接口或 `cancel_on_disconnect=true`。

### 11.3 异步问答模式

当 LLM P99 超过网关超时时间时，建议：

1. `POST /qa/jobs` 创建问答任务；
2. `GET /qa/jobs/{id}` 查询；
3. `GET /qa/jobs/{id}/events` 订阅；
4. 完成后取同一结构化回答。

V1 可先保留同步接口，V1.1 再增加异步，不影响返回对象。

### 11.4 拒答

证据不足属于正常业务结果，不是 HTTP 错误：

```json
{
  "answer_status": "insufficient_evidence",
  "answer": "当前资料不足以回答该问题。",
  "grounded": false,
  "citations": []
}
```

仅当请求、权限或系统失败时才使用 4xx/5xx。

---

## 12. 最终问答返回格式

### 12.1 完整响应

```json
{
  "data": {
    "request_id": "qa_01J...",
    "conversation_id": "conv_01J...",
    "message_id": "msg_01J...",
    "answer_status": "answered",
    "answer": "Pine Ck 的年降雨量为 775 mm，年径流量为 202 mm，径流比为 0.26。",
    "grounded": true,
    "citations": [
      {
        "citation_id": "cit_1",
        "document_id": "doc_01J...",
        "filename": "annual_report.pdf",
        "page_idx": 0,
        "page_number": 1,
        "block_id": "doc_01J...:p0:b12",
        "char_interval": {"start_pos": 1185, "end_pos": 1193},
        "bbox": [320, 500, 410, 520],
        "bbox_pdf": [190.4, 420.5, 243.95, 437.3],
        "evidence": "Pine Ck",
        "source_kind": "graph_node",
        "source_id": "location:pine creek"
      }
    ],
    "used_documents": [
      {"document_id": "doc_01J...", "filename": "annual_report.pdf", "snapshot_id": "gs_01J..."}
    ],
    "tool_trace": [
      {
        "step": 1,
        "tool": "search_entities",
        "args": {"query": "Pine Ck", "limit": 5},
        "status": "completed",
        "hits": 1,
        "latency_ms": 12
      },
      {
        "step": 2,
        "tool": "search_pages",
        "args": {"query": "annual rainfall runoff ratio", "top_k": 3},
        "status": "completed",
        "hits": 2,
        "latency_ms": 8
      }
    ],
    "retrieval": {
      "strategy": "kg_mcp_keyword",
      "entities_hit": 1,
      "pages_hit": 2,
      "evidence_blocks": 2,
      "graph_snapshot_ids": ["gs_01J..."]
    },
    "usage": {
      "model": "deepseek-flash",
      "prompt_tokens": 684,
      "completion_tokens": 128,
      "total_tokens": 812,
      "tool_calls": 2
    },
    "timings": {
      "retrieval_ms": 20,
      "generation_ms": 2380,
      "total_ms": 2410
    },
    "warnings": [],
    "finish_reason": "stop"
  },
  "request_id": "req_01J...",
  "meta": null
}
```

### 12.2 必填字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `answer_status` | enum | 是 | `answered/insufficient_evidence/failed` |
| `answer` | string | 是 | 面向用户的最终回答 |
| `grounded` | bool | 是 | 是否至少有一条有效引用；拒答为 false |
| `citations` | array | 是 | 可为空；每项必须通过服务端校验 |
| `used_documents` | array | 是 | 实际使用的文档和图谱快照 |
| `tool_trace` | array | 是 | 只读审计轨迹；用户无权查看时可返回裁剪版 |
| `retrieval` | object | 是 | 检索策略和命中统计 |
| `usage` | object | 是 | token 与工具调用统计 |
| `timings` | object | 是 | 性能分解 |
| `warnings` | array | 是 | 如图谱噪声、部分内容缺失 |
| `conversation_id/message_id` | string | 是 | 会话与消息标识 |

### 12.3 `citations[]` 字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `citation_id` | string | 是 | 回答内唯一 |
| `document_id` | string | 是 | 文档 |
| `filename` | string | 是 | 原文件名 |
| `page_idx` | int | 是 | 0 基页号 |
| `page_number` | int | 是 | 面向用户 1 基页号 |
| `block_id` | string/null | 否 | 精确内容块 |
| `char_interval` | object/null | 否 | 页内闭开区间 |
| `bbox` | int[4]/null | 否 | 0–1000 归一化坐标 |
| `bbox_pdf` | number[4]/null | 否 | PDF 点坐标 |
| `evidence` | string | 是 | 可验证的原文切片 |
| `source_kind` | enum | 是 | `graph_node/graph_edge/page_text/table` |
| `source_id` | string/null | 否 | 节点或边 ID |

### 12.4 与 MVP `answers.json` 的兼容

当前 MVP：

```json
{
  "document_id": "20261017T180436Z_sample_report",
  "results": [
    {
      "question": "...",
      "answer": "... [page 0, span 1185-1192]",
      "tool_calls": [{"tool": "search_entities", "args": {}}],
      "elapsed_sec": 3.83
    }
  ]
}
```

服务化适配规则：

| MVP | V1 API |
| --- | --- |
| `results[].question` | 请求 `question` + 响应请求 ID |
| `results[].answer` | `data.answer` |
| 内联 `[page X, span a-b]` | 解析为 `citations[]`，同时可保留原文本 |
| `tool_calls[]` | 增强为 `tool_trace[]`，补状态、命中数和耗时 |
| `elapsed_sec` | 改为 `timings.total_ms` |
| `document_id` | `used_documents[].document_id` 与 query scope |

**迁移门槛**：在结构化引用未实现前，不能声称服务端“保证每句话都有可点击证据”。V1 发布前必须加入引用解析、存在性校验和 block 回填测试。

### 12.5 表格数值回答建议

回答表格数值时，`citations[].source_kind` 应为 `table`，并建议附带：

```json
{
  "table": {
    "rows": 5,
    "cols": 5,
    "row_header": "Pine Ck",
    "column_header": "Runoff ratio",
    "raw_value": "0.26",
    "value": 0.26,
    "unit": null
  }
}
```

该对象是可选扩展，不得替代原始 evidence 和 bbox。

---

## 13. 错误码与状态映射

### 13.1 HTTP 状态

| HTTP | 场景 |
| --- | --- |
| 400 | JSON/字段格式错误 |
| 401 | 缺少或无效访问令牌 |
| 403 | 无文档/租户权限 |
| 404 | 资源不存在或无权感知（可故意返回 404） |
| 409 | 状态冲突、Graph 未就绪、重复 active job |
| 413 | 文件/请求体超过上限 |
| 415 | 扩展名或 MIME 不支持 |
| 422 | 页数、参数组合或文件内容校验失败 |
| 429 | 客户端限流或上游限流 |
| 500 | 未分类内部错误 |
| 502 | MinerU/DeepSeek 上游响应异常 |
| 503 | 队列、数据库或依赖暂不可用 |
| 504 | 上游超时 |

### 13.2 业务错误码

| code | HTTP | retryable | 说明 |
| --- | --- | --- | --- |
| `AUTH_REQUIRED` | 401 | 否 | 缺少令牌 |
| `TOKEN_INVALID` | 401 | 否 | 令牌无效/过期 |
| `DOCUMENT_FORBIDDEN` | 403 | 否 | 文档越权 |
| `RESOURCE_NOT_FOUND` | 404 | 否 | 资源不存在 |
| `FORMAT_NOT_SUPPORTED` | 415 | 否 | 格式不在白名单 |
| `MIME_EXTENSION_MISMATCH` | 415 | 否 | 文件头与扩展名不符 |
| `FILE_TOO_LARGE` | 413 | 否 | 超过模式上限 |
| `PAGE_LIMIT_EXCEEDED` | 422 | 否 | 超过 200 页 |
| `FILE_CORRUPTED` | 422 | 否 | 无法解析 |
| `FORMAT_GRAPH_NOT_READY` | 422 | 否 | 格式可解析但暂无 Graph 适配器 |
| `UPLOAD_EXPIRED` | 409 | 是 | 重新创建会话 |
| `CHECKSUM_MISMATCH` | 422 | 是 | 重传文件 |
| `GRAPH_NOT_READY` | 409 | 是 | 文档尚未 indexed |
| `JOB_ALREADY_ACTIVE` | 409 | 否 | 已有运行中 job |
| `JOB_NOT_CANCELLABLE` | 409 | 否 | 已结束 |
| `MINERU_RATE_LIMITED` | 429 | 是 | 上游限流 |
| `MINERU_TASK_FAILED` | 502 | 视上游 | 返回脱敏后的上游错误 |
| `MINERU_TIMEOUT` | 504 | 是 | 轮询超时 |
| `MINERU_ARTIFACT_INVALID` | 502 | 是 | ZIP/JSON 缺失或损坏 |
| `EXTRACTION_FAILED` | 502 | 是 | LLM 抽取失败 |
| `MODEL_RATE_LIMITED` | 429 | 是 | DeepSeek 限流 |
| `INSUFFICIENT_EVIDENCE` | 200 | 否 | 正常拒答，不是错误码 |
| `CITATION_VALIDATION_FAILED` | 500 | 是 | 服务端后处理异常 |
| `INTERNAL_ERROR` | 500 | 视情况 | 不暴露堆栈和密钥 |

### 13.3 重试原则

- 4xx 默认不自动重试，除上传过期、限流和 checksum 重传；
- 429、502、503、504 由 Worker 有界重试；
- MinerU 远端失败不自动超过 3 次；
- LLM 输出解析失败可重试 1 次，并降低或调整输出格式；
- 重试必须复用已完成 stage 的产物；
- 错误详情不得包含 Token、原始 prompt 或完整模型响应。

---

## 14. 安全、隔离与非功能要求

### 14.1 身份与租户

- 所有业务 API 需要有效访问令牌；
- 每次数据库查询必须带 `tenant_id`；
- 对象键包含 tenant 前缀，但仍以数据库授权为准；
- MCP 工具入参中的 `document_id` 必须再次校验用户 scope；
- 管理员操作单独 RBAC，不能复用普通问答端点。

### 14.2 密钥与日志

| 密钥 | 保存位置 | 禁止出现的位置 |
| --- | --- | --- |
| MinerU Token | Secret Manager/环境变量 | 浏览器、API 响应、job events |
| DeepSeek Key | Secret Manager/环境变量 | prompt、日志、错误详情 |
| 数据库凭证 | Secret Manager | 配置文件提交 |
| 下载签名 | 短时生成 | 长期日志 |

日志统一脱敏为 `sk-****xxxx` 或完全哈希。禁止记录完整文件正文。

### 14.3 组件环境隔离

当前本机以独立 `.venv` 验证：

- `mineru-mvp/.venv`：HTTP 客户端与文档预检；
- `langextract/.venv`：LangExtract；
- `bridge-pipeline/.venv`：Bridge 与 DeepSeek 适配；
- `GrapRAG/.venv`：LangChain、MCP、Agent；
- API 服务自己的虚拟环境或容器。

生产转换为独立容器镜像，每个 Worker 只安装其必要依赖，不跨组件复用同一 Python 环境。

### 14.4 上传和文件处理

- 临时文件权限最小化；
- ZIP 解压前防目录穿越、符号链接和压缩炸弹；
- 解析文件进入隔离目录；
- 禁止执行文档中的宏或脚本；
- 不直接渲染未清洗的 HTML；
- 对图表、表格和 OCR 内容按不可信输入处理。

### 14.5 容量与性能基线

| 指标 | V1 目标 |
| --- | --- |
| 创建上传会话 API | P95 < 300 ms |
| 文档/job 查询 API | P95 < 200 ms |
| 图谱分页 API | P95 < 500 ms |
| QA 首工具调用 | P50 < 2 s；受 DeepSeek 网络影响 |
| QA 完整回答 | P95 ≤ 30 s（2 页测试基线约 2–4 s/问） |
| 单文档 Graph 节点 | MVP 基线 51，需设软上限与告警 |
| 工具调用 | 默认 ≤6，硬上限 10 |

### 14.6 可观测性

所有日志、指标和 trace 必须带：

`request_id, tenant_id, document_id, job_id, run_id, stage, attempt`

关键指标：

- 上传失败率、MIME 拒绝率；
- MinerU job 时长、429、失败码分布；
- LangExtract 对齐状态分布和抽取条数；
- 图谱节点/边/丢弃原因；
- QA tool-call 数、拒答率、引用校验率、token 和耗时；
- queue depth、worker heartbeat、对象存储错误。

### 14.7 审计与保留

- 上传、下载、重索引、删除和管理员查询写入审计日志；
- QA 请求保存问题、scope、答案摘要、工具轨迹和引用，保留期可配置；
- 原始文件和解析产物按租户策略清理；
- 删除必须传播到对象存储、缓存和索引。

---

## 15. 部署演进与验收

### 15.1 P0：单机工程化 MVP

- FastAPI；
- SQLite 或 PostgreSQL；
- 本地文件目录或 MinIO；
- 进程内后台队列或 Redis/RQ；
- `parse-worker`、`extract-worker`、`qa-service` 分进程运行；
- 每个进程使用独立虚拟环境；
- 单节点、单租户可选，但接口仍保留 `tenant_id`。

### 15.2 P1：可部署服务

- PostgreSQL；
- Redis + 独立 Worker；
- S3/MinIO；
- API 多副本；
- 对象存储直传；
- SSE 任务进度；
- 任务租约、重试、取消；
- 结构化引用和 QA 审计。

### 15.3 P2：规模化

- 图谱存储迁移到 Neo4j 或图数据库（可选）；
- 增加跨文档实体消解；
- 增加混合检索：关键词 + 可选向量；
- 增加图谱多版本对比和回滚；
- 将 MCP 工具服务独立部署；
- 引入模型网关、配额和多供应商路由。

> 向量能力是可选演进，不是 V1 上线前置条件。

### 15.4 验收清单

#### 上传与解析

- [ ] PDF 走 precise，生成 `content_list.json` 和 `layout.json`；
- [ ] 文件大小、页数、MIME、扩展名和 SHA 校验生效；
- [ ] `full.md` 缺失内容不影响 GraphReadyBundle；
- [ ] `page_idx` 0 基、bbox 坐标系统和 `page_size` 有测试；
- [ ] Agent/precise 路由和 `parse_only/graph_v1` 策略正确。

#### 抽取与图谱

- [ ] DeepSeek 显式 OpenAI provider、thinking disabled、JSON object；
- [ ] 中文使用 UnicodeTokenizer；
- [ ] 每个节点能回查页内 char interval 和 bbox；
- [ ] 悬空边、无依据实体被记录并过滤；
- [ ] snapshot 校验通过后才切换 active pointer；
- [ ] 重复运行生成新 snapshot，不覆盖旧结果。

#### 问答

- [ ] 只能查询授权且 indexed 的文档；
- [ ] 有答案的问题返回至少一条结构化引用；
- [ ] 无证据问题返回 `insufficient_evidence`；
- [ ] 工具调用不超过请求预算；
- [ ] 回答、引用、evidence 三者一致；
- [ ] Token 和敏感配置不出现在响应、日志、SSE。

#### 运维

- [ ] `/health/live` 与 `/health/ready`；
- [ ] 任务可重试、可取消、可重放事件；
- [ ] 上游 429 指数退避；
- [ ] 失败码、request_id、job_id 可关联；
- [ ] 多租户隔离和对象存储签名下载通过安全测试；
- [ ] 备份、保留和删除流程可执行。

---

## 附录 A：MVP 产物到服务资源的映射

| 现有本地产物 | V1 资源/产物类型 | 用途 |
| --- | --- | --- |
| 上传原始文件 | `source_file` | 重解析、审计、下载 |
| MinerU `source.zip` | `mineru_zip` | 原始解析包 |
| `full.md` / `content_list.md` | `mineru_full_md` | 预览与可读导出 |
| `*_content_list.json` | `mineru_content_list` | 权威块、页文本、bbox |
| `*_content_list_v2.json` | `mineru_content_list_v2` | 兼容/备用 |
| `layout.json` | `mineru_layout` | page_size 与坐标换算 |
| `*_model.json` | `mineru_model` | 模型检测结果 |
| `images/*` | `mineru_image` | 图像预览/后续多模态扩展 |
| `raw_result.jsonl` | `extraction_jsonl` | LangExtract 原始输出 |
| `graph/nodes.json` | `graph_nodes` | 图节点 |
| `graph/edges.json` | `graph_edges` | 图边 |
| `graph/provenance.jsonl` | `graph_provenance` | 证据审计 |
| `graph/stats.json` | `graph_stats` | 质量统计 |
| `answers.json` | `qa_audit` | 问答与工具轨迹审计 |
| `meta.json/response.json/summary.json` | `run_manifest` 或内部诊断 | 运行诊断，不直接作为公共业务 API |

### 公共 API 不应直接暴露的 MVP 文件

以下文件可以保存，但不应让前端/业务服务直接读取：

- `.env`、任何密钥文件；
- `response.json` 中的原始上游响应；
- `meta.json` 中的本机路径和内部 token 脱敏字段；
- `*_origin.pdf` 的内部副本路径；
- Worker 内部日志和 exception trace。

---

## 附录 B：关键参数速查

### B.1 MinerU

| 参数 | 规范 |
| --- | --- |
| Graph 主路径 | `precise` |
| `model_version` | `pipeline` 或 `vlm`，以质量回归选择 |
| `language` | 默认 `ch`，PDF 生效 |
| `enable_table` | 默认 true |
| `enable_formula` | 默认 true |
| `is_ocr` | 扫描件启用；MVP 默认 false |
| 大小/页数 | 200 MB / 200 页 |
| 轮询 | 5 s，总超时 300 s，429 指数退避 |
| 权威输入 | `content_list.json` + `layout.json` |
| 坐标 | content_list 0–1000；layout 为 PDF 点 |

### B.2 LangExtract / DeepSeek

| 参数 | 规范 |
| --- | --- |
| Provider | 显式 `openai` |
| Model | `deepseek-flash` 或部署指定 |
| Thinking | disabled |
| Response format | `json_object` |
| Schema constraints | false；DeepSeek 不支持 `json_schema` |
| Tokenizer | `UnicodeTokenizer` |
| `max_output_tokens` | 8192 基线 |
| `max_char_buffer` | 1000 基线 |
| 输出 | `AnnotatedDocument` / JSONL |
| 必须保留 | `extraction_class, extraction_text, char_interval, alignment_status, attributes` |

### B.3 LangChain Agent / MCP

| 参数 | 规范 |
| --- | --- |
| Model wrapper | LangChain `ChatOpenAI` |
| Base URL | `https://api.deepseek.com` |
| Temperature | 0 |
| Recursion limit | 12 |
| Tool budget | 默认 6，最大 10 |
| 工具 | `search_entities/get_neighbors/search_pages/get_evidence` |
| 引用 | V1 必须结构化并通过存在性校验 |
| Scope | 每次工具调用强制租户和文档过滤 |

### B.4 API

| 项 | 规范 |
| --- | --- |
| Base | `/api/v1` |
| 分页 | cursor |
| 页号 | 0 基 |
| 字符区间 | 闭开 `[start,end)` |
| bbox | `[x0,y0,x1,y1]`，坐标系统显式字段 |
| 创建幂等 | `Idempotency-Key` |
| 任务进度 | `GET /jobs/{id}` 或 SSE |
| 图谱未就绪 | `409 GRAPH_NOT_READY` |
| 证据不足 | `200 insufficient_evidence` |
| 产物下载 | 授权 + 短时签名 URL |

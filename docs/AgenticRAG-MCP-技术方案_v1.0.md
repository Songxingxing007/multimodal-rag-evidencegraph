# Agentic-RAG + LangChain MCP 技术构建方案 v1.0

> **文档性质**：规划文档，不包含实现代码。基于
> [BridgePipeline_v1.0.md](BridgePipeline_v1.0.md) 的图谱产物，
> 以及 LangChain 官方文档（`create_agent`、`MCPAdapter`、MCP 多服务器配置）。
>
> **版本基线**：`langchain 1.4.6`、`langgraph 1.2.16`、`mcp 2.3.0`、`fastmcp 4.1.1`。
> **注意**：`langchain.mcp` 目前是 **Beta**，导入会触发 `LangChainBetaWarning`，API 可能变化。
>
> **官方依据**：
> - https://docs.langchain.com/oss/python/langchain/agents
> - https://docs.langchain.com/oss/python/langchain/mcp

## 目录

- [1. 目标与边界](#1-目标与边界)
- [2. 总体架构](#2-总体架构)
- [3. 数据层设计](#3-数据层设计)
- [4. MCP Server 设计](#4-mcp-server-设计)
- [5. Agent 设计](#5-agent-设计)
- [6. Agentic 工作流](#6-agentic-工作流)
- [7. 接口契约](#7-接口契约)
- [8. 部署与运行](#8-部署与运行)
- [9. 测试与验收](#9-测试与验收)
- [10. 分阶段实施计划](#10-分阶段实施计划)
- [11. 风险与对策](#11-风险与对策)
- [12. 假设与默认值](#12-假设与默认值)

---

## 1. 目标与边界

### 1.1 目标

在 MinerU（文档解析）与 LangExtract（实体/关系抽取）产出的知识图谱之上，
构建一个 **Agentic-RAG** 问答层：Agent 自主决定检索哪个工具、检索几次，
并基于图谱证据 + 原文页码/坐标给出**可溯源**的回答。

### 1.2 输入

- `bridge-pipeline/output/<run>/graph/nodes.json`
- `graph/edges.json`
- `graph/provenance.jsonl`
- `graph/stats.json`
- MinerU `content_list.json`（页级原文，用于文本检索与证据切片）

### 1.3 输出

- 自然语言回答
- 结构化引用：`document_id` + `page_idx` + `char_interval` + `bbox`
- 检索轨迹（调用了哪些工具、命中哪些节点/页面）

### 1.4 边
界

- v1 不做模型微调；
- v1 不引入向量数据库（先用 SQLite FTS5）；
- v1 为单文档/小规模多文档，图谱可全量载入内存；
- 图谱 Schema 沿用 Bridge v1.0 的 `entity` / `relation`。

---

## 2. 总体架构

```
┌──────────────────────────────────────────────────────────────┐
│ 用户界面 / API                                                │
│  web-app（现有上传页） + 新增 /api/ask                        │
└───────────────────────────┬──────────────────────────────────┘
                            ▼
┌──────────────────────────────────────────────────────────────┐
│ LangChain Agent（create_agent）                               │
│  ChatOpenAI(base_url=DeepSeek, model=deepseek-flash)          │
│  system_prompt: 先检索、后回答、必须引用证据                   │
└───────┬───────────────────────┬───────────────────┬──────────┘
        │ MCP stdio             │ MCP stdio         │ MCP stdio
        ▼                       ▼                   ▼
┌───────────────┐     ┌──────────────────┐   ┌──────────────────┐
│ graph-server  │     │ docs-server      │   │ ingest-server    │
│ nodes/edges   │     │ 页文本 + 证据切片 │   │ MinerU + Bridge  │
└───────┬───────┘     └────────┬─────────┘   └────────┬─────────┘
        │                      │                      │
        └──────────────┬───────┴──────────────────────┘
                       ▼
             ┌─────────────────────┐
             │ SQLite 索引          │
             │ entities / edges    │
             │ pages / pages_fts   │
             │ provenance          │
             └─────────────────────┘
                       ▲
                       │ 离线写入
             ┌─────────────────────┐
             │ MinerU + Bridge      │
             │ （现有 Pipeline）     │
             └─────────────────────┘
```

**关键点**：Agent 不直接读文件，只通过 MCP 工具访问数据；
这样检索能力可独立演进（SQLite → 向量库 → Neo4j），Agent 无需改动。

---

## 3. 数据层设计

### 3.1 为什么用 SQLite

| 方案 | 优点 | 缺点 |
| --- | --- | --- |
| **SQLite + FTS5（推荐 v1）** | 零服务、stdlib、支持全文检索 | 无向量语义检索 |
| NetworkX 内存图 | 图算法方便 | 不持久、重启丢失 |
| Neo4j | 生产级图查询 | 需部署服务，当前不可用 |

SQLite 既能存图（entities/edges 表），又能存文本与 FTS 索引，**一个文件即完整索引**。

### 3.2 表结构

```sql
CREATE TABLE entities (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  name TEXT NOT NULL,
  attributes TEXT,
  document_id TEXT,
  page_idx INTEGER,
  bbox TEXT,
  bbox_pdf TEXT,
  char_start INTEGER,
  char_end INTEGER,
  evidence TEXT
);

CREATE TABLE edges (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  evidence TEXT,
  evidence_count INTEGER DEFAULT 1,
  document_id TEXT,
  page_idx INTEGER
);

CREATE TABLE pages (
  document_id TEXT,
  page_idx INTEGER,
  text TEXT,
  page_size TEXT,
  PRIMARY KEY (document_id, page_idx)
);

CREATE VIRTUAL TABLE pages_fts USING fts5(
  text, document_id UNINDEXED, page_idx UNINDEXED
);

CREATE TABLE provenance (
  kind TEXT,
  id TEXT,
  document_id TEXT,
  page_idx INTEGER,
  char_start INTEGER,
  char_end INTEGER,
  bbox TEXT,
  evidence TEXT
);
```

### 3.3 索引构建流程

```
Bridge 产物 + MinerU content_list.json
        ▼
index_builder.py
  ├─ 读 nodes/edges/provenance
  ├─ 重建页文本并写入 pages
  ├─ 写入 entities / edges
  └─ 构建 pages_fts
        ▼
agentic-rag/index/rag_index.db
```

> 页文本重建规则必须与 Bridge v1.0 一致：按 `page_idx` 拼接
> `text/header/footer/equation` 与 `table_body`，保证 `char_interval` 可直接切片。

---

## 4. MCP Server 设计

推荐三个本地 stdio 服务器，各自一个 Python 脚本：

```
agentic-rag/mcp_servers/
├─ graph_server.py
├─ docs_server.py
└─ ingest_server.py
```

### 4.1 graph-server

**职责**：实体检索、邻域展开、路径查询。

| 工具 | 输入 | 输出 | 用途 |
| --- | --- | --- | --- |
| `search_entities` | `query`, `type?`, `limit=10` | 实体列表（含 id/name/type/page） | 按名称/别名找节点 |
| `get_entity` | `entity_id` | 实体详情 + 入边/出边 | 查看某实体 |
| `get_neighbors` | `entity_id`, `depth=1` | 子图（nodes+edges） | 关系扩展 |
| `find_path` | `source_id`, `target_id`, `max_depth=3` | 节点/边路径 | 多跳问答 |
| `query_edges` | `type?`, `page_idx?`, `limit=20` | 边列表 | 按关系类型检索 |

每个返回项必须带：`document_id`、`page_idx`、`char_interval`、`bbox`。

### 4.2 docs-server

**职责**：页级全文检索与证据切片。

| 工具 | 输入 | 输出 | 用途 |
| --- | --- | --- | --- |
| `search_pages` | `query`, `top_k=5` | 命中页 + 高亮片段 | FTS5 检索 |
| `get_page` | `document_id`, `page_idx` | 页全文 + page_size | 读取整页 |
| `get_evidence` | `document_id`, `page_idx`, `start`, `end` | 精确文本切片 + bbox | 引用核验 |

### 4.3 ingest-server（管理面，可选暴露给 Agent）

| 工具 | 输入 | 输出 | 用途 |
| --- | --- | --- | --- |
| `list_documents` | — | 文档列表与状态 | 多文档管理 |
| `ingest_pdf` | `path` | `job_id` | 触发 MinerU + Bridge |
| `get_ingest_status` | `job_id` | 状态/产物路径 | 轮询 |

> 建议：**v1 不把 ingest 暴露给问答 Agent**，避免 Agent 误触发长任务；
> 通过 Web 上传页或管理员 CLI 触发更安全。

---

## 5. Agent 设计

### 5.1 模型

```python
from langchain_openai import ChatOpenAI

model = ChatOpenAI(
    model="deepseek-flash",
    api_key=os.environ["DEEPSEEK_API_KEY"],
    base_url="https://api.deepseek.com",
    temperature=0,
    extra_body={"thinking": {"type": "disabled"}},
)
```

> 需额外安装 `langchain-openai`（当前 `langchain-mcp/.venv` 尚未安装）。
> 关闭 thinking 的原因与 Bridge 一致：思考会耗尽输出 token 预算。

### 5.2 工具装配

```python
from pathlib import Path
from langchain.agents import create_agent
from langchain.mcp import MCPAdapter

async def build_agent():
    async with MCPAdapter(Path("mcp_servers/graph_server.py")) as graph:
        graph_tools = await graph.list_tools()
    async with MCPAdapter(Path("mcp_servers/docs_server.py")) as docs:
        docs_tools = await docs.list_tools()

    return create_agent(
        model=model,
        tools=[*graph_tools, *docs_tools],
        system_prompt=SYSTEM_PROMPT,
    )
```

多服务器也可用一个 `MCPConfig` 字典（`{"mcpServers": {...}}`）交给单个 `MCPAdapter`。

### 5.3 System Prompt 约束

```
你是基于知识图谱与原文的问答助手。
规则：
1. 回答前必须先调用检索工具；不要凭记忆作答。
2. 优先用 graph-server 定位实体与关系，再用 docs-server 核对原文。
3. 每个结论必须附引用：document_id / page_idx / char_interval。
4. 找不到证据时明确回答"当前资料不足以回答"，不得编造。
5. 数值必须与原文完全一致，不做单位换算除非明确说明。
6. 单次问答工具调用不超过 6 次。
```

### 5.4 工具输出格式

```json
{
  "results": [
    {
      "id": "location:pine creek",
      "name": "Pine Creek",
      "type": "location",
      "page_idx": 0,
      "char_interval": {"start_pos": 1185, "end_pos": 1192},
      "bbox": [320, 500, 410, 520]
    }
  ],
  "count": 1
}
```

---

## 6. Agentic 工作流

### 6.1 单 Agent + 工具（v1 推荐）

`create_agent` 自带「模型 → 工具 → 模型」循环，适合 v1：

```
用户问题
   → Agent 判断：实体？关系？数值？总结？
   → 调用 search_entities / search_pages
   → 视情况调用 get_neighbors / find_path / get_evidence
   → 生成带引用的回答
```

### 6.2 LangGraph 状态机（v2）

```
[plan] → [retrieve] → [verify] ──不足──> [expand_query] ──┐
                          │                               │
                          └──足够──> [answer] → [cite_check] → END
```

| 节点 | 职责 |
| --- | --- |
| `plan` | 拆解问题，决定调用哪些工具 |
| `retrieve` | 执行 MCP 工具，收集证据 |
| `verify` | 检查证据是否覆盖问题（实体/数值/关系） |
| `expand_query` | 改写查询（同义词、别名、页码）重试，最多 2 轮 |
| `answer` | 生成回答，强制附引用 |
| `cite_check` | 校验每个结论都有引用，否则回退 |

### 6.3 防幻觉策略

1. 工具优先：没有工具结果不得作答；
2. 引用强制：答案中的每个事实绑定 `evidence`；
3. 证据切片校验：`get_evidence` 返回的文本必须包含引用片段；
4. 低置信度兜底：无命中时返回"资料不足"；
5. 工具预算：最多 6 次调用，防止循环。

---

## 7. 接口契约

### 7.1 MCP 工具命名

```
graph.search_entities
graph.get_entity
graph.get_neighbors
graph.find_path
graph.query_edges
docs.search_pages
docs.get_page
docs.get_evidence
admin.list_documents
admin.ingest_pdf
admin.get_ingest_status
```

### 7.2 溯源字段（所有检索结果必带）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `document_id` | string | MinerU 运行目录名 |
| `page_idx` | int | 0 基页号 |
| `char_interval` | object | 页内闭开区间 |
| `bbox` | int[4] | 0–1000 归一化 |
| `evidence` | string | 原文片段 |

### 7.3 Web 集成

现有 `web-app` 新增：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/ask` | `{question, document_id?}` → Agent 回答 + 引用 |
| GET | `/api/documents` | 已索引文档列表 |

回答结构：

```json
{
  "answer": "……",
  "citations": [
    {"document_id": "...", "page_idx": 0, "char_interval": {}, "evidence": "..."}
  ],
  "tool_trace": [
    {"tool": "search_entities", "args": {"query": "Pine Creek"}, "hits": 1}
  ]
}
```

---

## 8. 部署与运行

### 8.1 目录结构

```
agentic-rag/
├─ .venv/
├─ .env
├─ index_builder.py
├─ mcp_servers/
│  ├─ graph_server.py
│  ├─ docs_server.py
│  └─ ingest_server.py
├─ agent_app.py
├─ run_agent.py
├─ index/rag_index.db
└─ AGENTS.md
```

### 8.2 依赖

```
langchain[mcp]
langchain-openai
```

### 8.3 启动顺序

1. `index_builder.py` 构建 SQLite 索引；
2. `graph_server.py` / `docs_server.py` 以 stdio 被 Agent 拉起（无需常驻端口）；
3. `run_agent.py` 或 Web `/api/ask` 启动 Agent。

> MCP 传输选择：本地用 **stdio**（零端口、进程隔离）；
> 多用户/远程部署时改为 **streamable HTTP**，只需换 `MCPAdapter` 的 target。

---

## 9. 测试与验收

### 9.1 分层测试

| 层 | 测试 | 通过标准 |
| --- | --- | --- |
| 索引 | 构建 SQLite | entities/edges 数量与 nodes/edges.json 一致 |
| FTS | `search_pages("Pine Creek")` | 命中 page 0/1，片段含关键词 |
| graph MCP | `search_entities("Pine Creek")` | 返回节点，含 page/bbox |
| graph MCP | `get_neighbors(id)` | 返回入边/出边 |
| docs MCP | `get_evidence(...)` | 切片等于原文 |
| MCP 装配 | `MCPAdapter.list_tools()` | 工具数与预期一致 |
| Agent | 3 个示例问题 | 均带引用，无编造 |

### 9.2 示例验收问题

1. "Pine Creek 的年降雨量是多少？" → 命中 `1472`，引用 page 0。
2. "哪些指标与 Glendhu 2 有关？" → 邻居展开，引用对应页。
3. "总结第 2 页的结论。" → `search_pages` + `get_page`，引用 page 1。
4. "文档里有没有提到火星？" → 明确回答"资料不足"。

### 9.3 质量指标

| 指标 | 目标 |
| --- | --- |
| 引用覆盖率 | 100% |
| 工具调用数 | ≤ 6 / 问 |
| 端到端延迟 | < 20 s |
| 无证据问题 | 100% 明确拒答 |

---

## 10. 分阶段实施计划

| 阶段 | 内容 | 产出 |
| --- | --- | --- |
| P1 | SQLite 索引 + graph/docs MCP Server | 可用工具 + 单元测试 |
| P2 | `create_agent` + DeepSeek + MCP 工具 | CLI 问答可用 |
| P3 | Web `/api/ask` + 引用展示 | 页面问答 |
| P4 | LangGraph 状态机 + 引用校验 | 可控 Agentic 流程 |
| P5 | 向量检索 / Neo4j / 多文档 | 生产化 |

---

## 11. 风险与对策

| 风险 | 影响 | 对策 |
| --- | --- | --- |
| `langchain.mcp` 处于 Beta | API 可能变更 | 锁定 `langchain==1.4.6`，封装 Adapter 层 |
| DeepSeek 思考模式耗尽 token | 空回答 | `extra_body={"thinking":{"type":"disabled"}}` |
| 图谱噪声（标题/题注） | 检索命中低价值节点 | 索引时过滤 `^\d+\.`、`^Table \d` |
| char_interval 与页文本不一致 | 引用错位 | 索引重建规则与 Bridge 完全一致 |
| 工具过多导致 Agent 迷路 | 延迟/循环 | 工具预算 + system prompt 限制 |
| 多文档 ID 冲突 | 引用混淆 | `document_id` 使用 MinerU run 名 |

---

## 12. 假设与默认值

- 模型：DeepSeek `deepseek-flash`，关闭思考模式；
- 传输：本地 stdio；远程再切 streamable HTTP；
- 存储：SQLite + FTS5，单文件 `index/rag_index.db`；
- 图谱：沿用 Bridge v1.0 的 nodes/edges schema，不新增字段；
- 范围：v1 先支持单文档，多文档通过 `document_id` 过滤扩展；
- 依赖：新增 `langchain-openai`，其余复用 `langchain[mcp]`。

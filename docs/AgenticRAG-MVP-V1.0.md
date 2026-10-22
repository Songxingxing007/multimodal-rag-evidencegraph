# Agentic-RAG MVP 规范 V1.0（LangChain Agent + MCP）

> **文档性质**：基于本地真实执行的 `agentic_rag_mvp.py` 与产出的 `answers.json` 编写，
> 并对照 [BridgePipeline_v1.0.md](BridgePipeline_v1.0.md) 与
> [MinerU-输入输出规范v1.0.md](MinerU-输入输出规范v1.0.md)。
>
> **实测日期**：2026-10-19
> **输入文档**：MinerU run `20261017T180436Z_sample_report`（2 页）
> **图谱**：Bridge run `20261018T180151Z`（51 节点 / 12 边）
> **模型**：DeepSeek `deepseek-flash`（LangChain ChatOpenAI，关闭思考模式）
> **环境**：`GrapRAG/.venv`（Python 3.12.14）

## 目录

- [0. 基线结论](#0-基线结论)
- [1. 执行思路与脚本位置](#1-执行思路与脚本位置)
- [2. 接入 LangChain Agent 后的输入与输出](#2-接入-langchain-agent-后的输入与输出)
- [3. 与 MinerU 对接的关键参数与核心技术架构](#3-与-mineru-对接的关键参数与核心技术架构)
- [4. 最终问答数据返回格式规范](#4-最终问答数据返回格式规范)
- [5. 运行与测试](#5-运行与测试)
- [6. 已知限制](#6-已知限制)
- [附录 A：实测记录](#附录-a实测记录)
- [附录 B：术语表](#附录-b术语表)

---

## 0. 基线结论

### 0.1 本次实测结果

| 项 | 值 |
| --- | --- |
| 文档 | `20261017T180436Z_sample_report`（2 页） |
| 图谱 | 51 节点 / 12 边 |
| MCP 工具 | 4 个 |
| 测试问题 | 3 个 |
| 有引用作答 | 2 个 |
| 正确拒答 | 1 个 |
| 总耗时 | 8.32 s（3.83 + 2.41 + 2.08） |

### 0.2 五条必须记住的结论

1. **不使用 Embedding，也不做 RAG 分块**：检索完全基于知识图谱 + 页文本关键词。
2. **MCP 使用进程内模式**：`MCPAdapter(MCPServer实例)`，无子进程、无端口。
3. **DeepSeek 通过 LangChain 标准组件接入**：`ChatOpenAI(base_url=...)`。
4. **必须关闭思考模式**：`extra_body={"thinking":{"type":"disabled"}}`。
5. **当前引用是文本内联格式**（`[page 0, span a-b]`），尚未结构化；结构化方案见 §4.4。

---

## 1. 执行思路与脚本位置

### 1.1 整体流程

```
MinerU 精准模式产物
  content_list.json（页文本来源）
Bridge 产物
  nodes.json / edges.json / provenance.jsonl
        │
        ▼
① KGIndex：内存索引
   - nodes by id
   - name/alias → node id
   - adjacency（入边/出边）
   - pages（page_idx → text）
        │
        ▼
② MCPServer("kg-rag") 注册 4 个工具
        │
        ▼
③ MCPAdapter(server).list_tools()
        │
        ▼
④ create_agent(model=ChatOpenAI(DeepSeek), tools=..., system_prompt=...)
        │
        ▼
⑤ Agent 循环：模型 → 工具 → 模型（recursion_limit=12）
        │
        ▼
⑥ answers.json（答案 + 工具轨迹 + 耗时）
```

### 1.2 逐步说明

| 步骤 | 动作 | 实现要点 |
| --- | --- | --- |
| ① | 加载图谱 | 从 `BRIDGE_RUN_DIR/graph/` 读 nodes/edges |
| ② | 重建页文本 | 从 MinerU `content_list.json` 按 `page_idx` 拼接，规则与 Bridge 一致 |
| ③ | 建索引 | `KGIndex`：实体名/别名索引、邻接表、页文本列表 |
| ④ | 注册 MCP 工具 | `@server.tool()` 装饰 4 个函数，返回 JSON 字符串 |
| ⑤ | 装配 Agent | `MCPAdapter(server)` → `list_tools()` → `create_agent` |
| ⑥ | 问答 | `agent.ainvoke({"messages":[...]}, config={"recursion_limit":12})` |
| ⑦ | 提取结果 | 取最后一条消息为回答；遍历 `tool_calls` 记录轨迹 |
| ⑧ | 落盘 | `answers.json` |

### 1.3 实际脚本位置

根目录：`GrapRAG/`

| 文件 | 作用 |
| --- | --- |
| `agentic_rag_mvp.py` | **单文件 MVP**：索引、MCP Server、Agent、测试 |
| `.env` | DeepSeek 配置与数据路径 |
| `uv.toml` | 清华 TUNA 镜像 |
| `requirements.txt` | `langchain[mcp]`、`langchain-openai` |
| `.venv/` | Python 3.12.14 |
| `output/<UTC时间戳>/answers.json` | 问答结果 |

### 1.4 关键代码位置

| 内容 | 行号 |
| --- | --- |
| `SYSTEM_PROMPT` | 258 |
| `TESTS`（3 个测试问题） | 267 |
| 4 个 `@server.tool()` | 296 / 303 / 308 / 313 |
| `MCPAdapter(server)` | 323 |
| `max_tokens=2048` | 339 |
| `create_agent` | 342 |
| `recursion_limit=12` | 356 |

---

## 2. 接入 LangChain Agent 后的输入与输出

### 2.1 相对 Bridge v1.0 新增了什么

| 层 | Bridge v1.0 | 加入 Agent 后 |
| --- | --- | --- |
| 数据 | nodes/edges/provenance/stats | 同上（只读，不修改） |
| 检索 | 无 | KGIndex + 4 个 MCP 工具 |
| 交互 | 无 | 自然语言问题 → 工具调用 → 回答 |
| 输出 | 图谱 JSON | 图谱 JSON **+ answers.json** |

### 2.2 Agent 的输入契约

| 输入 | 来源 | 必需 | 说明 |
| --- | --- | --- | --- |
| `nodes.json` | Bridge `graph/` | **是** | 实体索引 |
| `edges.json` | Bridge `graph/` | **是** | 邻接表 |
| `content_list.json` | MinerU run | **是** | 页文本与字符切片 |
| `provenance.jsonl` | Bridge `graph/` | 否 | MVP 未直接使用，可留作审计 |
| `document_id` | `nodes[0].document_id` | 自动 | 用于定位 MinerU run |

### 2.3 字段映射（Bridge → Agent）

| Bridge 字段 | KGIndex 字段 | MCP 工具输出 |
| --- | --- | --- |
| `id` | `nodes[id]` | `id` |
| `name` | 名称索引 | `name` |
| `aliases` | 别名索引 | 用于匹配 |
| `type` | 节点类型 | `type` |
| `page_idx` | 溯源 | `page_idx` |
| `char_interval` | 溯源 | `char_interval` |
| `bbox` | 溯源 | `bbox` |
| `source_id` / `target_id` | 邻接表 | `edges[]` |

### 2.4 Agent 的输出契约（MVP 实际）

| 输出 | 位置 | 说明 |
| --- | --- | --- |
| 自然语言答案 | `results[].answer` | 含内联引用 `[page X, span a-b]` |
| 工具轨迹 | `results[].tool_calls` | 每次调用的工具名与参数 |
| 耗时 | `results[].elapsed_sec` | 单问耗时 |
| 文档 ID | 顶层 `document_id` | 来源文档 |

---

## 3. 与 MinerU 对接的关键参数与核心技术架构

### 3.1 MinerU 侧关键参数（本 MVP 实际使用）

| 参数 / 字段 | 值 | 用途 |
| --- | --- | --- |
| 解析模式 | **precise** | 只有精准模式有 JSON 与 bbox |
| `content_list.json` | 17 块 / 2 页 | 页文本重建 |
| `page_idx` | 0 / 1 | 页级索引 |
| `bbox` | 0–1000 归一化 | 节点溯源 |
| `layout.json` 的 `page_size` | `[595, 841]` | 如需 PDF 点坐标换算 |
| 限制 | 200 MB / 200 页 | 上游约束 |

**不使用的输入**：

- `full.md`：可能因 header 分类丢页；
- Agent 轻量模式产物：无 JSON、无 bbox；
- `images/`：本 MVP 不做视觉检索。

### 3.2 Agentic-RAG 核心技术架构

```
┌────────────────────┐
│ MinerU 精准解析     │  content_list.json（页文本/页码/bbox）
└─────────┬──────────┘
          ▼
┌────────────────────┐
│ Bridge Pipeline     │  nodes.json / edges.json / provenance
└─────────┬──────────┘
          ▼
┌────────────────────┐
│ KGIndex（内存）      │  实体索引 + 邻接表 + 页文本
└─────────┬──────────┘
          ▼
┌────────────────────┐
│ MCP Server          │  4 个工具（stdio/in-process）
└─────────┬──────────┘
          ▼
┌────────────────────┐
│ LangChain MCPAdapter│  工具发现与调用
└─────────┬──────────┘
          ▼
┌────────────────────┐
│ create_agent        │  DeepSeek + system prompt + 工具预算
└─────────┬──────────┘
          ▼
      answers.json
```

### 3.3 各层关键参数

| 层 | 参数 | 实测值 |
| --- | --- | --- |
| MinerU | mode | `precise` |
| MinerU | model_version | `pipeline`（产物中 `_version_name=3.4.4`） |
| Bridge | 切块 | 按 `page_idx` |
| Bridge | 节点 ID | `type:normalized_name` |
| KGIndex | 页文本 | 按块顺序拼接，`\n\n` 分隔 |
| MCP | 传输 | in-process（`MCPAdapter(server)`） |
| Agent | provider | `ChatOpenAI` → DeepSeek |
| Agent | model | `deepseek-flash` |
| Agent | `temperature` | `0` |
| Agent | `max_tokens` | `2048` |
| Agent | thinking | `disabled` |
| Agent | `recursion_limit` | `12` |
| Agent | 工具预算 | system prompt 限制 6 次 |

### 3.4 MCP 工具契约

| 工具 | 签名 | 返回 |
| --- | --- | --- |
| `search_entities` | `(query: str, limit: int = 5)` | 实体列表（id/name/type/page/char_interval/bbox） |
| `get_neighbors` | `(entity_id: str, depth: int = 1)` | 中心实体 + 邻接节点 + 边 |
| `search_pages` | `(query: str, top_k: int = 3)` | 页命中 + snippet + 字符区间 |
| `get_evidence` | `(document_id: str, page_idx: int, start: int, end: int)` | 精确文本切片 |

工具统一返回 JSON 字符串；`MCPAdapter` 会将其包装为内容块
（如 `[{"type":"text","text":"{...}"}]`）。

---

## 4. 最终问答数据返回格式规范

### 4.1 `answers.json` 顶层结构

```json
{
  "document_id": "20261017T180436Z_sample_report",
  "results": [ ... ]
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `document_id` | string | 是 | MinerU 运行目录名 |
| `results` | array | 是 | 每个问题一条 |

### 4.2 `results[]` 字段

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `question` | string | 是 | 用户问题 |
| `answer` | string | 是 | 最终回答（含内联引用） |
| `tool_calls` | array | 是 | 工具调用轨迹，可为空 |
| `elapsed_sec` | number | 是 | 单问耗时（秒） |

### 4.3 `tool_calls[]` 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `tool` | string | 工具名 |
| `args` | object | 模型传入的参数 |

### 4.4 答案中的引用格式（当前实现）

回答文本内联引用：

```
[page 0, span 1185-1192]
```

- `page`：`page_idx`（0 基）
- `span`：页内字符闭开区间 `[start, end)`

这是**文本格式**，便于人读；程序解析需正则提取。

### 4.5 完整真实样例

```json
{
  "document_id": "20261017T180436Z_sample_report",
  "results": [
    {
      "question": "Pine Ck 的年降雨量和径流比分别是多少？",
      "answer": "根据报告中的流域指标表，Pine Ck 的数据如下：\n\n- **年降雨量：775 mm**\n- **年径流量：202 mm**\n- **径流比：0.26**（全表最低）\n\n来源：Pine Ck 所在表格行 [page 0, span 1185-1192]；正文亦指出“the lowest is 0.26 at Pine Creek” [page 0, span 318-328]。\n\n注：知识图谱中 `location:pine ck` 实体没有关联边，因此上述数值均取自页面原文表格。",
      "tool_calls": [
        {"tool": "search_entities", "args": {"query": "Pine Ck"}},
        {"tool": "search_pages", "args": {"query": "Pine Ck annual precipitation runoff ratio"}},
        {"tool": "get_neighbors", "args": {"entity_id": "location:pine ck"}},
        {"tool": "search_pages", "args": {"query": "Pine Ck mean annual rainfall runoff ratio table", "top_k": 3}},
        {"tool": "get_evidence", "args": {"document_id": "20261017T180436Z_sample_report", "page_idx": 0, "start": 0, "end": 1400}}
      ],
      "elapsed_sec": 3.83
    }
  ]
}
```

### 4.6 结构化引用扩展（建议 v1.1）

当前版本未实现结构化引用。建议后续在 `results[]` 中新增：

```json
{
  "citations": [
    {
      "document_id": "20261017T180436Z_sample_report",
      "page_idx": 0,
      "char_interval": {"start_pos": 1185, "end_pos": 1192},
      "bbox": [320, 500, 410, 520],
      "evidence": "Pine Ck"
    }
  ],
  "tool_trace": [
    {"tool": "search_entities", "args": {}, "hits": 1, "latency_ms": 12}
  ],
  "confidence": 0.92
}
```

> 该扩展需要在 Agent 后处理阶段把内联引用解析回图谱节点，
> 并与 `provenance.jsonl` 对齐。

### 4.7 拒答格式

无证据时 `answer` 必须包含明确措辞，且 `tool_calls` 保留检索记录：

```json
{
  "question": "文档里有没有提到火星？",
  "answer": "当前资料不足以回答该问题。……",
  "tool_calls": [
    {"tool": "search_entities", "args": {"query": "火星"}},
    {"tool": "search_pages", "args": {"query": "火星"}}
  ],
  "elapsed_sec": 2.08
}
```

---

## 5. 运行与测试

### 5.1 运行命令

```powershell
cd GrapRAG
$env:PYTHONUTF8 = "1"

# 只验证 MCP 工具装载
.\.venv\Scripts\python.exe agentic_rag_mvp.py --list-tools

# 运行内置三问
.\.venv\Scripts\python.exe agentic_rag_mvp.py

# 自定义问题
.\.venv\Scripts\python.exe agentic_rag_mvp.py --question "Traralgon Ck 的面积是多少？"
```

### 5.2 实测结果

| # | 问题 | 结果 | 工具数 | 耗时 |
| --- | --- | --- | --- | --- |
| 1 | Pine Ck 的年降雨量和径流比 | 775 mm / 202 mm / 0.26，带引用 | 5 | 3.83 s |
| 2 | Glendhu 2 的径流比 | 0.64，带引用 | 3 | 2.41 s |
| 3 | 文档里有没有提到火星 | **正确拒答**（两次检索 0 命中） | 2 | 2.08 s |

### 5.3 验收标准

- `--list-tools` 输出 4 个工具；
- 有答案的问题必须含引用；
- 无证据问题必须拒答；
- `answers.json` 可被 `json.loads` 解析；
- 单问工具调用 ≤ 6 次。

---

## 6. 已知限制

| 限制 | 影响 | 后续方案 |
| --- | --- | --- |
| 引用是文本格式 | 程序无法直接结构化消费 | v1.1 增加 `citations[]` |
| 内存索引 | 重启需重建 | 迁移 SQLite FTS5 |
| 关键词检索 | 无同义/语义召回 | 后续可加向量检索 |
| 图谱噪声 | 标题/题注进入图谱 | 索引时过滤 |
| `langchain.mcp` Beta | API 可能变化 | 锁定 `langchain==1.4.6` |
| 单文档 | 多文档需切换 | 按 `document_id` 扩展 |

---

## 附录 A：实测记录

### A.1 环境

| 项 | 值 |
| --- | --- |
| OS | Windows |
| Python | 3.12.14（GrapRAG/.venv） |
| langchain | 1.4.6 |
| langgraph | 1.2.16 |
| mcp | 2.3.0 |
| langchain-openai | 已安装（ChatOpenAI） |

### A.2 产物

| 文件 | 字节 | 说明 |
| --- | --- | --- |
| `answers.json` | 3,280 | 3 个问答结果 |

目录：`GrapRAG/output/20261019T091316Z/`

---

## 附录 B：术语表

| 术语 | 含义 |
| --- | --- |
| **Agentic-RAG** | Agent 自主决定检索动作的 RAG |
| **KGIndex** | 本 MVP 的内存知识图谱索引 |
| **MCPServer** | mcp 2.x 中的服务器类（原 FastMCP） |
| **MCPAdapter** | LangChain 的 MCP 客户端适配器 |
| **create_agent** | LangChain 1.x 的 Agent 工厂 |
| **tool_calls** | Agent 调用工具的轨迹 |
| **内联引用** | 答案中的 `[page X, span a-b]` 文本 |

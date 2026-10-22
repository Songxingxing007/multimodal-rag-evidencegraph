# Bridge Pipeline 规范 v1.0（MinerU → LangExtract → 知识图谱）

> **文档性质**：本版基于**本地真实执行的 Bridge Pipeline 与产出的文件**编写，并逐项对照
> [MinerU-输入输出规范v1.0.md](MinerU-输入输出规范v1.0.md) 与
> [LangExtract-输入输出与模型接入规范v1.0.md](LangExtract-输入输出与模型接入规范v1.0.md)。
> 凡官方说明与实测冲突处，**一律以实测为准**。
>
> **实测日期**：2026-10-19
> **输入**：MinerU 精准模式运行目录 `20261017T180436Z_sample_report`（2 页）
> **模型**：DeepSeek `deepseek-flash`（OpenAI 兼容接口，关闭思考模式）
> **环境**：`bridge-pipeline/.venv`（Python 3.12.14）

## 目录

- [0. 基线结论](#0-基线结论)
- [1. Bridge Pipeline 执行思路与脚本位置](#1-bridge-pipeline-执行思路与脚本位置)
- [2. 输入契约：MinerU](#2-输入契约mineru)
- [3. 调用契约：LangExtract](#3-调用契约langextract)
- [4. 接口对接规范](#4-接口对接规范)
- [5. Bridge 输入与输出参数规范](#5-bridge-输入与输出参数规范)
- [6. 实际产物 vs 官方文档：冲突与裁决](#6-实际产物-vs-官方文档冲突与裁决)
- [7. 已知限制与调优建议](#7-已知限制与调优建议)
- [附录 A：实测记录](#附录-a实测记录)
- [附录 B：术语表](#附录-b术语表)

---

## 0. 基线结论

### 0.1 一次运行的实测结果

| 项 | 值 |
| --- | --- |
| 输入页数 | 2（page_idx 0/1） |
| 页文本长度 | 1,560 / 591 字符 |
| 耗时 | 4.91 s |
| 原始实体 | 55 |
| 合并后节点 | 51 |
| 原始关系 | 21 |
| 最终边 | 12 |
| 丢弃无依据抽取 | 6 |
| 丢弃悬空关系 | 3 |
| 合并重复实体 | 4 |

### 0.2 五条必须记住的结论

1. **输入只能用 MinerU 精准模式的 `content_list.json`**；`full.md` 会因 header 分类丢页，Agent 模式没有 JSON 与 bbox。
2. **页级切块**是默认单位；每页一段文本，LangExtract 的 `char_interval` 是**页内偏移**。
3. **坐标要换算**：`content_list` 的 bbox 是 0–1000 归一化，必须按 `page_size/1000` 转成 PDF 点。
4. **DeepSeek 必须关闭思考模式**，否则 8192 token 预算会被推理耗尽（`finish_reason=length`）。
5. **图谱输出是纯 JSON**（nodes/edges/provenance/stats），结构可迁移到 NetworkX / Neo4j。

---

## 1. Bridge Pipeline 执行思路与脚本位置

### 1.1 整体架构

```
MinerU 精准模式运行目录
   │  content_list.json（权威块）+ layout.json（page_size）
   ▼
① 读块并按 page_idx 分组
② 每页拼接文本，记录每块的 char_start / char_end
③ 每页生成一个 LangExtract Document（document_id = <run>:p<page_idx>）
④ LangExtract + DeepSeek 抽取 entity / relation
⑤ 实体归一与合并、关系端点校验、边去重
⑥ 回填 page_idx / bbox / char_interval 溯源
   ▼
graph/nodes.json · edges.json · provenance.jsonl · stats.json
```

### 1.2 逐步说明

| 步骤 | 动作 | 实现要点 |
| --- | --- | --- |
| ① | 定位输入 | 优先取非 `v2` 的 `*_content_list.json`；同时读取 `layout.json` |
| ② | 页级切块 | 按 `page_idx` 聚合；`text/header/footer/equation` 取 `text`，`table` 取 `table_body` + 题注 |
| ③ | 构造文档 | `document_id = "<mineru_run_name>:p<page_idx>"`，便于反解页号 |
| ④ | 调用 LLM | `DeepSeekLanguageModel`（OpenAI provider 子类，注入 `thinking=disabled`） |
| ⑤ | 后处理 | 实体 ID = `type:normalized_name`；边 ID = `rel:src:type:tgt` |
| ⑥ | 落盘 | `raw_result.jsonl` + 四个图谱文件 |

### 1.3 实际脚本位置

根目录：`bridge-pipeline/`

| 文件 | 作用 |
| --- | --- |
| `bridge_pipeline.py` | **主脚本**：切块、调用、后处理、落盘 |
| `.env` | DeepSeek 配置与 MinerU 输入路径 |
| `uv.toml` | 清华 TUNA 镜像 |
| `AGENTS.md` | 子环境隔离规则 |
| `README.md` | 运行说明 |
| `.venv/` | 独立 Python 3.12.14 环境 |
| `output/<run_id>/` | 运行产物 |

### 1.4 复跑命令

```powershell
cd bridge-pipeline
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe bridge_pipeline.py

# 指定其它 MinerU 运行目录
.\.venv\Scripts\python.exe bridge_pipeline.py --mineru-run ..\mineru-mvp\output\<run_id>
```

---

## 2. 输入契约：MinerU

### 2.1 可接受与不可接受的输入

| 输入 | 是否接受 | 原因 |
| --- | --- | --- |
| 精准模式 `*_content_list.json` | **是（权威）** | 含 `page_idx`、`bbox`、块类型 |
| 精准模式 `layout.json` | **是（必需）** | 提供 `page_size`，用于坐标换算 |
| 精准模式 `full.md` | 否 | 渲染器会隐藏 `header` 页，可能缺页 |
| 精准模式 `*_content_list_v2.json` | 否（备用） | 结构为二维数组、无 `page_idx`，仅当 v1 缺失时降级 |
| Agent 模式 `full.md` | 否 | 无 JSON、无 bbox、无页码 |

### 2.2 本桥接实际消费的 MinerU 参数

| 参数 | 实测值 | 用途 |
| --- | --- | --- |
| `content_list` 块数 | 17 | 分组依据 |
| `page_idx` | 0 / 1 | 页级切块 |
| `bbox` | 0–1000 归一化 | 换算为 PDF 点 |
| `layout.pdf_info[].page_size` | `[595, 841]` | 坐标换算基准 |
| 页文本长度 | 1,560 / 591 | 决定 LangExtract 内部子块 |

---

## 3. 调用契约：LangExtract

### 3.1 模型与请求参数

| 参数 | 实测值 | 说明 |
| --- | --- | --- |
| `provider` | `openai`（通过 `DeepSeekLanguageModel` 子类） | `deepseek-*` 默认会被 Ollama 规则命中 |
| `model_id` | `deepseek-flash` | 可用还有 `deepseek-v4-pro` |
| `base_url` | `https://api.deepseek.com` | OpenAI 兼容端点 |
| `thinking` | `disabled` | 由子类写入 `extra_body` |
| `reasoning_effort` | `low` | 关闭思考后仅作兼容保留 |
| `max_output_tokens` | `8192` | 映射为 `max_tokens` |
| `response_format` | `{"type":"json_object"}` | 不使用 `json_schema` |
| `tokenizer` | `UnicodeTokenizer` | 中文/英文均可精确对齐 |
| `max_workers` | 2 | 两页并行 |
| `max_char_buffer` | 默认 1000 | 长页会内部再切块，最终偏移仍归并到页 |

### 3.2 为什么需要子类

LangExtract 的 `OpenAILanguageModel._build_chat_completions_params` **不透传 `extra_body`**，
而 DeepSeek 关闭思考模式必须在 `extra_body` 中传：

```python
extra_body = {"thinking": {"type": "disabled"}}
```

因此 `DeepSeekLanguageModel` 覆写该方法注入该字段；否则 8192 token 也会被推理耗尽。

### 3.3 抽取类别

| 类别 | 作用 | attributes |
| --- | --- | --- |
| `entity` | 实体 | `entity_type`（person/organization/location/equipment/metric/concept/other） |
| `relation` | 关系 | `source`、`target`、`relation_type` |

---

## 4. 接口对接规范

### 4.1 字段映射

| MinerU 字段 | Bridge 中间字段 | LangExtract 输出 | 图谱字段 |
| --- | --- | --- | --- |
| `page_idx` | `page_idx` | `document_id` 后缀 `:p{n}` | `page_idx` |
| `bbox`（0–1000） | `blocks[].bbox` | — | `bbox` |
| `page_size` | `blocks[].bbox_pdf` | — | `bbox_pdf` |
| 块文本 | 页文本 + `char_start/char_end` | `char_interval`（页内） | 反查块 bbox |
| — | — | `extraction_class` | `type` |
| — | — | `extraction_text` | `name` / `evidence` |
| — | — | `attributes` | `attributes` |

### 4.2 坐标换算

```
bbox_pdf = bbox_1000 / 1000 * page_size
```

实测样例：`[100,101,447,116]` × `[595,841]/1000` → `[59.5, 84.95, 265.97, 97.56]`，
与 `layout.json` 中同块 `[60,85,266,98]` 吻合（±1 pt 取整误差）。

### 4.3 溯源链路

```
LangExtract char_interval（页内偏移）
   → 在 page.blocks 中查找包含该区间的块
   → 取块的 bbox / bbox_pdf
   → 写入节点
```

找不到包含块时，退化为"最大重叠块"；仍找不到则 `bbox = null`。

### 4.4 页文档标识

`document_id = "<mineru_run_name>:p<page_idx>"`，例如
`20261017T180436Z_sample_report:p0`。

后处理通过 `rsplit(":p", 1)` 反解页号，避免额外传参。

---

## 5. Bridge 输入与输出参数规范

### 5.1 输入参数（`.env` / CLI）

| 参数 | 来源 | 默认 | 说明 |
| --- | --- | --- | --- |
| `MINERU_RUN_DIR` | `.env` | `..\mineru-mvp\output\20261017T180436Z_sample_report` | MinerU 运行目录 |
| `--mineru-run` | CLI | 覆盖上项 | 显式指定 |
| `OUTPUT_DIR` | `.env` | `output` | 产物根目录 |
| `--output-dir` | CLI | 覆盖上项 | 显式指定 |
| `DEEPSEEK_API_KEY` | `.env` | — | 必需 |
| `DEEPSEEK_BASE_URL` | `.env` | `https://api.deepseek.com` | 兼容端点 |
| `DEEPSEEK_MODEL` | `.env` | `deepseek-flash` | 模型 |
| `DEEPSEEK_THINKING` | `.env` | `disabled` | 关键：必须关闭 |
| `DEEPSEEK_REASONING_EFFORT` | `.env` | `low` | 兼容保留 |
| `MAX_OUTPUT_TOKENS` | `.env` | `8192` | 输出预算 |

### 5.2 节点字段（`nodes.json`）

| 字段 | 类型 | 示例 | 说明 |
| --- | --- | --- | --- |
| `id` | string | `location:pine creek` | `type:normalized_name` |
| `type` | string | `location` | 实体类型 |
| `name` | string | `Pine Creek` | 原始名称 |
| `attributes` | object | `{}` | 除 `entity_type` 外的属性 |
| `document_id` | string | `20261017T180436Z_sample_report` | 来源文档 |
| `page_idx` | int | `0` | 页号（0 基） |
| `bbox` | int[4] | `[228,68,769,95]` | 0–1000 归一化 |
| `bbox_pdf` | float[4] | `[135.66,57.2,457.56,79.9]` | PDF 点 |
| `char_interval` | object | `{"start_pos":22,"end_pos":28}` | 页内闭开区间 |
| `evidence` | string | `Pine Creek` | 原文证据 |
| `aliases` | array | `[]` | 合并时的别名 |

### 5.3 边字段（`edges.json`）

| 字段 | 类型 | 示例 | 说明 |
| --- | --- | --- | --- |
| `id` | string | `rel:metric:0.26:located_at:location:pine creek` | 确定性 ID |
| `type` | string | `located_at` | 关系类型 |
| `source_id` | string | `metric:0.26` | 指向节点 |
| `target_id` | string | `location:pine creek` | 指向节点 |
| `evidence` | string | `the lowest is 0.26 at Pine Creek` | 原文证据 |
| `evidence_count` | int | `1` | 同边出现次数 |
| `attributes` | object | `{}` | 扩展属性 |
| `document_id` | string | — | 来源文档 |
| `page_idx` | int | `0` | 页号 |

### 5.4 其它产物

| 文件 | 内容 |
| --- | --- |
| `provenance.jsonl` | 每行一条节点或边的溯源记录（63 行 = 51 节点 + 12 边） |
| `stats.json` | 原始/合并计数、丢弃原因、逐页统计 |
| `raw_result.jsonl` | LangExtract 原始 `AnnotatedDocument`（每页一行） |

---

## 6. 实际产物 vs 官方文档：冲突与裁决

### 6.1 冲突总表

| # | 官方文档说法 | 本地实测 | 裁决 |
| --- | --- | --- | --- |
| 1 | MinerU ZIP 含 `*_middle.json` | 实际是 `layout.json` | **以实测为准**：读 `layout.json` |
| 2 | MinerU `full.md` 是解析结果 | 2 页文档中第 2 页被标为 header，`full.md` 缺页 | **以实测为准**：不消费 `full.md` |
| 3 | LangExtract README 推荐 `.env` | 库内无 `load_dotenv`，仅写 `.env` 不生效 | **以实测为准**：脚本显式加载 |
| 4 | LangExtract 按 `model_id` 路由 | `^deepseek` 命中 Ollama | **以实测为准**：子类 + 显式 OpenAI |
| 5 | OpenAI schema 使用 `json_schema` | DeepSeek 只支持 `json_object` | **以实测为准**：不用 schema 约束 |
| 6 | 官方未提示思考模式预算 | 8192 token 仍被推理耗尽 | **以实测为准**：关闭 thinking |
| 7 | 官方未定义 Bridge 产物 | 实际 5 个文件（1 raw + 4 graph） | **以实测为准**：见 §5 |
| 8 | LangExtract JSONL 8 个抽取字段 | 实测一致 | **一致** |
| 9 | MinerU `page_idx` 从 0 开始 | 实测 0/1 | **一致** |

### 6.2 文件数量与类型（以本地为准）

运行目录 `output/20261018T180151Z/` 实际包含：

| 文件 | 字节 | 类型 |
| --- | --- | --- |
| `raw_result.jsonl` | 23,518 | JSON Lines |
| `graph/nodes.json` | 24,271 | JSON 数组 |
| `graph/edges.json` | 4,191 | JSON 数组 |
| `graph/provenance.jsonl` | 10,839 | JSON Lines |
| `graph/stats.json` | 384 | JSON |

**没有任何官方文档描述过这套 Bridge 产物**，因此上述 5 个文件即为规范。

---

## 7. 已知限制与调优建议

| 现象 | 实测数据 | 建议 |
| --- | --- | --- |
| 节点含标题/题注噪声 | 51 节点中出现 `1. Scope and Method`、`Table 1. …` 等 | 在 prompt 中排除标题/题注，或后处理过滤 `^\d+\.` / `^Table \d` |
| 无依据抽取被丢弃 | 6 条 | 检查 few-shot 示例与原文一致性 |
| 关系端点缺失 | 3 条 | 让模型先抽实体再抽关系，或做二次补全 |
| 实体重复 | 4 条被合并 | 正常，`probable_duplicates` 已记录 |
| 示例对齐告警 | 5 条 `MATCH_FUZZY` | 英文示例的标点导致，属告警不影响结果 |
| `use_schema_constraints` 告警 | 1 条 | 使用预建 `model` 时的无害提示 |

---

## 附录 A：实测记录

### A.1 环境

| 项 | 值 |
| --- | --- |
| OS | Windows（PowerShell） |
| Bridge venv | Python 3.12.14 |
| LangExtract | 1.7.1（editable） |
| OpenAI SDK | 3.35.1 |
| 镜像 | 清华 TUNA |

### A.2 运行记录

| # | 配置 | 结果 |
| --- | --- | --- |
| 1 | thinking 默认开启，8192 tokens | 失败：`finish_reason=length`，无 content |
| 2 | 子类注入 `thinking=disabled` | **成功：51 节点 / 12 边 / 4.91 s** |

### A.3 安全

- Key 仅存在于 `bridge-pipeline/.env`（已 gitignore）。
- 产物中未出现完整 Key。

---

## 附录 B：术语表

| 术语 | 含义 |
| --- | --- |
| **Bridge Pipeline** | MinerU 与 LangExtract 之间的编排层 |
| **页级切块** | 按 `page_idx` 把内容块聚合成一页文本 |
| **bbox（0–1000）** | MinerU `content_list` 的归一化坐标 |
| **bbox_pdf** | 按 `page_size/1000` 换算后的 PDF 点坐标 |
| **char_interval** | LangExtract 返回的页内字符区间 |
| **确定性 ID** | `type:normalized_name`，保证重复运行结果稳定 |
| **悬空关系** | source/target 不在节点集合中的关系，默认丢弃 |

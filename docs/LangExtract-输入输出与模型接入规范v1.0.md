# LangExtract 输入输出与模型接入规范 v1.0（实测校准版）

> **文档性质**：本版基于**本地真实执行的 DeepSeek MVP 与产出的文件**编写，并逐项与 LangExtract 官方文档/README 比对。
> 凡官方说明与实测行为冲突处，**一律以实测为准**，冲突记录见第 2 节。
>
> **实测日期**：2026-10-18
> **实测模型**：DeepSeek `deepseek-flash`（经 OpenAI 兼容接口接入）
> **环境隔离**：`langextract/.venv`（uv 管理，Python 3.12.14，172 个包）
> **上一版**：[LangExtract-输入输出与模型接入规范.md](LangExtract-输入输出与模型接入规范.md)（实测前版本）

## 目录

- [0. 版本口径与关键结论](#0-版本口径与关键结论)
- [1. 完整 Pipeline 执行思路与脚本位置](#1-完整-pipeline-执行思路与脚本位置)
- [2. 实际行为 vs 官方文档：冲突清单与裁决](#2-实际行为-vs-官方文档冲突清单与裁决)
- [3. Pipeline 关键参数规范](#3-pipeline-关键参数规范)
- [4. 本地实际关键文件与字段](#4-本地实际关键文件与字段)
- [附录 A：实测记录](#附录-a实测记录)
- [附录 B：术语表](#附录-b术语表)

---

## 0. 版本口径与关键结论

### 0.1 实测基线

| 项 | 值 |
| --- | --- |
| LangExtract | 1.7.1（editable 安装） |
| Python | 3.12.14（`.venv`） |
| LLM | `deepseek-flash` |
| Base URL | `https://api.deepseek.com` |
| Provider | 显式 `openai` |
| 输入 | 自建中文设备巡检文本，167 字符 |
| 结果 | 11 条抽取，**11 条 match_exact** |
| 耗时 | 7.38 s |

### 0.2 六条必须记住的结论

1. **`deepseek-*` 会被 Ollama 规则抢走**，必须显式传 `provider="openai"`。
2. **DeepSeek 不支持 `json_schema`**，必须关闭 schema 约束，走 `response_format={"type":"json_object"}`。
3. **LangExtract 不会自动加载 `.env`**；脚本必须显式 `load_dotenv()` 或使用 shell 环境变量。
4. **中文必须用 `UnicodeTokenizer`**；默认 `RegexTokenizer` 会把整段中文当成一个 WORD token，导致子串无法对齐。
5. **DeepSeek 思考模式会先消耗 token 预算**；`max_output_tokens` 设 2048 时会返回空 content（`finish_reason=length`），实测 8192 可用。
6. **prompt 中不要出现 `extraction_text` 等内部字段名**；模型会把它们当成输出键照抄，导致每条结果重复一遍。

---

## 1. 完整 Pipeline 执行思路与脚本位置

### 1.1 整体架构

```
本地纯文本
   │
   ├─ ① 加载 .env（显式 load_dotenv）
   ├─ ② 构造 few-shot examples（ExampleData + Extraction）
   ├─ ③ 构造 ModelConfig(provider="openai")
   │        model_id      = deepseek-flash
   │        base_url      = https://api.deepseek.com
   │        provider_kwargs = api_key / reasoning_effort / max_output_tokens
   ├─ ④ lx.extract(...)
   │        分词（UnicodeTokenizer）→ 分块（max_char_buffer=1000）
   │        构造 prompt：description + Examples(Q/A 的 JSON) + Q: 文本 + A:
   ├─ ⑤ Provider 调用
   │        OpenAI SDK → chat.completions.create
   │        response_format = {"type":"json_object"}
   ├─ ⑥ Resolver 解析
   │        解析 JSON 信封 → 提取各类别 → 对齐回原文 char_interval
   ├─ ⑦ 生成 AnnotatedDocument
   └─ ⑧ 落盘 JSONL（每行一篇文档）
```

### 1.2 逐步说明

| 步骤 | 动作 | 实现要点 |
| --- | --- | --- |
| ① | 加载配置 | `load_dotenv(HERE/".env", override=True)`；库本身不会加载 |
| ② | 准备示例 | 至少一条 `ExampleData`；`extraction_text` 必须逐字来自示例正文 |
| ③ | 指定模型 | `lx.factory.ModelConfig(provider="openai", model_id="deepseek-flash")` |
| ④ | 调用入口 | `lx.extract(..., use_schema_constraints=False, tokenizer=UnicodeTokenizer())` |
| ⑤ | 请求模型 | OpenAI 兼容客户端，`base_url` 指向 DeepSeek |
| ⑥ | 解析与对齐 | 解析 JSON 信封；逐条计算字符区间与对齐状态 |
| ⑦ | 结果对象 | `AnnotatedDocument`，含 `document_id / text / extractions` |
| ⑧ | 落盘 | `lx.io.save_annotated_documents` → `result.jsonl` |

### 1.3 实际测试脚本位置

根目录：`langextract/`

| 文件 | 作用 |
| --- | --- |
| `mvp_deepseek/run_mvp.py` | **MVP 主脚本**：配置、示例、抽取、落盘、报告 |
| `mvp_deepseek/.env` | DeepSeek Key 与模型配置（已 gitignore） |
| `mvp_deepseek/README.md` | 接入要点与运行方式 |
| `mvp_deepseek/output/<UTC时间戳>/result.jsonl` | 抽取结果 |
| `mvp_deepseek/output/<UTC时间戳>/summary.json` | 运行摘要 |
| `uv.toml` | 项目级镜像配置（清华 TUNA） |
| `AGENTS.md` | 本目录环境隔离与启动规则 |
| `.venv/` | Python 3.12.14，172 个包 |

### 1.4 复跑命令

```powershell
cd langextract
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python.exe mvp_deepseek\run_mvp.py
```

---

## 2. 实际行为 vs 官方文档：冲突清单与裁决

### 2.1 冲突总表

| # | 官方文档/README 说法 | 本地实测 | 裁决 |
| --- | --- | --- | --- |
| 1 | "Option 2: `.env` File (Recommended)"，把 Key 写进 `.env` 即可 | 库内**没有任何 `load_dotenv` 调用**；只写 `.env` 不生效 | **以实测为准**：脚本显式 `load_dotenv()`，或改用 shell 环境变量 |
| 2 | OpenAI provider 由 `^gpt-*`、`^o[1-9]` 路由；`model_id` 足以选择模型 | `^deepseek` 命中 **Ollama** 规则，`deepseek-flash` 会被路由到 Ollama | **以实测为准**：必须显式 `provider="openai"` |
| 3 | `use_schema_constraints` 默认 `True`，OpenAI 支持结构化输出 | LangExtract 的 OpenAI schema 发出 `response_format={"type":"json_schema"}`；DeepSeek 只支持 `json_object` | **以实测为准**：DeepSeek 场景必须设 `False` |
| 4 | 默认分词器为 `RegexTokenizer` | 中文连续片段被当成一个 WORD token（如「号机组累计运行」），子串无法对齐 | **以实测为准**：中文改用 `UnicodeTokenizer` |
| 5 | `pyproject.toml` 提供 `dev` extras | `pytype` 在 PyPI 全部版本均无 Windows wheel，`.[dev]` 无法安装 | **以实测为准**：dev 工具按清单安装，跳过 pytype |
| 6 | 官方未提示思考模式的 token 预算 | `max_output_tokens=2048` → `finish_reason=length`，content 为空 | **以实测为准**：设为 8192 |
| 7 | 官方未提示 prompt 字段名会被模型复用 | prompt 里写 "extraction_text" → 模型额外输出 `extraction_text` 键，结果 22 条（应为 11 条） | **以实测为准**：用描述性措辞 + 明确 JSON 示例 |
| 8 | JSONL 输出 8 个抽取字段 | 实测字段与官方一致（见 §4.2） | **一致**，无冲突 |
| 9 | `max_char_buffer` 默认 1000 | 167 字符文本单块处理 | **一致**，无冲突 |
| 10 | 无需 Embedding 模型 | 实测仅调用文本 LLM，未使用任何向量能力 | **一致**，无冲突 |

### 2.2 冲突的工程含义

前三项属于"官方说明成立，但**换到 DeepSeek 这条路径后不再成立**"。它们不是文档错误，而是：

- 路由规则是**全局正则**，Ollama 的 `^deepseek` 优先级与 OpenAI 相同（都为 10），谁先注册谁命中；
- schema 能力由 **provider 实现决定**，不是所有 OpenAI 兼容服务都支持 `json_schema`；
- `.env` 只是 README 的**使用建议**，库本身不负责加载。

因此接入任何第三方 OpenAI 兼容服务时，必须同时确认：**provider 显式指定、schema 能力、JSON 模式、分词器**四件事。

---

## 3. Pipeline 关键参数规范

### 3.1 三层参数模型

```
① 模型选择层   model > config > model_id + api_key
② 抽取控制层   prompt_description / examples / use_schema_constraints / tokenizer / max_char_buffer
③ Provider 层  api_key / base_url / reasoning_effort / max_output_tokens
```

### 3.2 入口参数（`lx.extract`）

| 参数 | 实测取值 | 说明 |
| --- | --- | --- |
| `text_or_documents` | 167 字符中文文本 | 纯文本；PDF 需先解析 |
| `prompt_description` | 中文抽取指令 + JSON 示例 | 必须包含 "json" 字样（DeepSeek JSON Output 要求） |
| `examples` | 1 条 `ExampleData`，6 个 `Extraction` | 无 `output_schema` 时必填 |
| `config` | `ModelConfig(...)` | 优先于 `model_id` |
| `tokenizer` | `UnicodeTokenizer()` | 中文对齐必需 |
| `use_schema_constraints` | `False` | 避免 `json_schema` |
| `max_char_buffer` | 默认 1000 | 未显式设置 |
| `batch_length` / `max_workers` | 默认 10 / 10 | 单块，无并发 |
| `extraction_passes` | 默认 1 | 单轮 |
| `show_progress` | `False` | 便于脚本日志 |
| `prompt_validation_level` | 默认 `WARNING` | 修正示例后无告警 |

### 3.3 模型配置参数（`ModelConfig`）

| 参数 | 实测值 | 说明 |
| --- | --- | --- |
| `model_id` | `deepseek-flash` | 当前可用还有 `deepseek-v4-pro` |
| `provider` | `"openai"` | **必须显式**，否则被 Ollama 规则命中 |
| `provider_kwargs.api_key` | DeepSeek Key | 日志中脱敏为 `sk-****30c6` |
| `provider_kwargs.base_url` | `https://api.deepseek.com` | OpenAI 兼容端点 |
| `provider_kwargs.reasoning_effort` | `low` | 透传为请求参数 |
| `provider_kwargs.max_output_tokens` | `8192` | 映射为 `max_tokens` |

### 3.4 实际发出的请求参数

| 字段 | 实测值 | 来源 |
| --- | --- | --- |
| `model` | `deepseek-flash` | `model_id` |
| `messages` | system + user 两条 | `You are a helpful assistant that responds in JSON format.` + prompt |
| `response_format` | `{"type":"json_object"}` | `use_schema_constraints=False` |
| `max_tokens` | `8192` | `max_output_tokens` |
| `reasoning_effort` | `low` | provider kwargs |
| `temperature` | 未发送 | 思考模式不支持该参数 |

> 注意：LangExtract 的 OpenAI provider **不透传 `extra_body`**，因此无法通过它传
> `{"thinking":{"type":"disabled"}}`；当前 DeepSeek 思考模式为默认开启。

### 3.5 中文对齐参数

| 项 | 默认 | 实测建议 |
| --- | --- | --- |
| Tokenizer | `RegexTokenizer` | `UnicodeTokenizer` |
| 原因 | 连续中文被合并为一个 WORD | 按字/字素切分，子串可精确对齐 |
| 代价 | 更快 | 略慢，但 167 字符可忽略 |

### 3.6 输出字段参数

| 项 | 值 |
| --- | --- |
| `format_type` | JSON（默认） |
| `fence_output` | 未设置；OpenAI JSON 模式默认无围栏 |
| 属性后缀 | `_attributes` |
| 信封键 | `extractions` |

---

## 4. 本地实际关键文件与字段

> 本节以本地实际产出的**数量与类型**为准。

### 4.1 运行目录实际产物（2 个文件）

目录：`langextract/mvp_deepseek/output/20261018T131825Z/`

| # | 文件 | 字节 | 类型 | 说明 |
| --- | --- | --- | --- | --- |
| 1 | `result.jsonl` | 3,336 | JSON Lines | 1 行，1 篇文档，11 条抽取 |
| 2 | `summary.json` | 245 | JSON | 运行摘要 |

**没有**官方示例中可能出现的额外产物（如单独的 HTML、CSV、SQLite）；LangExtract 只负责抽取结果。

### 4.2 `result.jsonl` 字段规范

顶层键（实测）：`extractions`、`text`、`document_id`。

| 字段 | 类型 | 实测值 | 说明 |
| --- | --- | --- | --- |
| `document_id` | string | `doc_6c3800fd` | 自动生成 |
| `text` | string | 167 字符 | 原文 |
| `extractions` | array | 11 项 | 抽取结果 |

抽取项字段（实测 8 个）：

| 字段 | 类型 | 实测值 | 说明 |
| --- | --- | --- | --- |
| `extraction_class` | string | `equipment` | 类别 |
| `extraction_text` | string | `1号冷水机组` | 抽取文本 |
| `char_interval` | object | `{"start_pos":22,"end_pos":28}` | 闭开区间 |
| `alignment_status` | string | `match_exact` | 11 条全为精确匹配 |
| `extraction_index` | int | `1` | 序号 |
| `group_index` | int | `0` | 分组 |
| `description` | null | `null` | 未使用 |
| `attributes` | object | `{"role":"main"}` | 类别属性 |

**未出现**的字段：`token_interval`（私有字段，不落盘）。

单条真实样例：

```json
{"extraction_class":"equipment","extraction_text":"1号冷水机组","char_interval":{"start_pos":22,"end_pos":28},"alignment_status":"match_exact","extraction_index":1,"group_index":0,"description":null,"attributes":{"role":"main"}}
```

### 4.3 `summary.json` 字段

| 字段 | 实测值 |
| --- | --- |
| `model` | `deepseek-flash` |
| `base_url` | `https://api.deepseek.com` |
| `elapsed_sec` | `7.38` |
| `extractions` | `11` |
| `grounded` | `11` |
| `by_class` | `{"equipment":3,"issue":1,"metric":6,"recommendation":1}` |

### 4.4 实际抽取结果总览

| 类别 | 数量 | 示例 |
| --- | --- | --- |
| `equipment` | 3 | `1号冷水机组`、`2号冷水机组`、`3号备用机组` |
| `metric` | 6 | `4,120`、`7.2`、`18,640`、`3,850`、`7.8`、`17,920` |
| `issue` | 1 | `冷凝器压差偏高` |
| `recommendation` | 1 | `在 4 月 15 日前完成清洗` |

### 4.5 环境产物（非运行产物）

| 路径 | 说明 |
| --- | --- |
| `.venv/` | Python 3.12.14，172 个包 |
| `uv.toml` | 清华 TUNA 镜像 |
| `AGENTS.md` | 环境隔离规则 |
| `mvp_deepseek/.env` | DeepSeek Key（gitignore） |

---

## 附录 A：实测记录

### A.1 环境

| 项 | 值 |
| --- | --- |
| OS | Windows（PowerShell） |
| 全局 Python | 3.14.5（**不用于本项目**） |
| 子虚拟环境 | `.venv`，Python 3.12.14 |
| 包数量 | 172 |
| 镜像 | 清华 TUNA（`uv.toml`） |

### A.2 运行记录

| # | 阶段 | 结果 | 耗时 |
| --- | --- | --- | --- |
| 1 | 首次运行（2048 tokens，RegexTokenizer，prompt 含 extraction_text） | 失败：`finish_reason=length`，无 content | — |
| 2 | 修正 token 预算与分词器后 | 22 条（11 条真实 + 11 条 `extraction_text` 重复），全部 grounded | 19.23 s |
| 3 | 修正 prompt 措辞后（最终） | **11 条，全部 match_exact** | **7.38 s** |

### A.3 安全校验

| 检查项 | 结果 |
| --- | --- |
| 产物中出现完整 Key | 0 次 |
| 日志中的 Key | `sk-****30c6` |
| `.env` 是否在仓库内 | 是（已被 `mvp_deepseek/.gitignore` 排除） |
| 全局 Python 是否导入 langextract | 否 |

---

## 附录 B：术语表

| 术语 | 含义 |
| --- | --- |
| **provider** | 决定用哪套 SDK 调用模型；DeepSeek 必须显式 `openai` |
| **ModelConfig** | 模型配置对象，优先于 `model_id` |
| **use_schema_constraints** | 是否由示例推导 provider 侧 schema；DeepSeek 必须关闭 |
| **response_format** | `json_object`（DeepSeek 支持）/ `json_schema`（DeepSeek 不支持） |
| **UnicodeTokenizer** | 按 Unicode 字素切分的分词器，适合中文 |
| **char_interval** | 抽取结果在原文中的闭开区间 `[start_pos, end_pos)` |
| **alignment_status** | `match_exact` / `match_greater` / `match_lesser` / `match_fuzzy` |
| **grounded** | `char_interval` 非空，表示可在原文中定位 |
| **AnnotatedDocument** | 一篇文档的抽取结果容器 |

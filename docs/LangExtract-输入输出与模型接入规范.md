# LangExtract 输入输出与模型接入规范

> **文档性质**：本文档描述 LangExtract **源码的实际行为**，用于团队内部把 LangExtract 作为数据抽取后端时的接口约定，**不是上游官方的兼容性承诺**。
>
> **锚定版本**：`langextract 1.7.1`（commit `f781da9`），`requires-python >= 3.10`。
> 上游版本升级后，本规范中的默认值、字段名与错误类型可能变化，需以源码复核为准。

## 目录

- [0. 阅读约定](#0-阅读约定)
- [1. Pipeline 输入规范](#1-pipeline-输入规范)
- [2. 文本模型接入规范](#2-文本模型接入规范)
- [3. Pipeline 输出数据格式规范](#3-pipeline-输出数据格式规范)
- [4. 多模态 RAG 对接边界](#4-多模态-rag-对接边界)
- [附录 A：源码锚点索引](#附录-a源码锚点索引)
- [附录 B：术语表](#附录-b术语表)

---

## 0. 阅读约定

本文档中的规范性用语：

| 用语 | 含义 |
| --- | --- |
| **必须** | 违反会导致异常或结果不可用 |
| **应当** | 强建议，违反会有明确的质量损失 |
| **可以** | 可选能力，有默认值 |
| **不支持** | 源码中不存在该能力，不要依赖，也不要尝试绕过 |

所有字段、默认值、错误类型均以源码为准；每节结论的源码位置见[附录 A](#附录-a源码锚点索引)。

LangExtract 的对外入口只有两个：

- `lx.extract(...)`：抽取主流程
- `lx.visualize(...)`：结果可视化

其余能力（`lx.io`、`lx.data`、`lx.factory`、`lx.schema`、`lx.providers` 等）通过惰性属性访问暴露。

---

## 1. Pipeline 输入规范

### 1.1 三档参数优先级

当同时传入多个模型指定方式时，生效顺序为：

```
model（预建的模型实例） > config（ModelConfig） > model_id + api_key + language_model_type
```

- 传 `model` 时，`temperature`、`use_schema_constraints` 等参数**会被忽略**并触发 `UserWarning`。
- 传 `config` 时，`model_id`、`api_key`、`language_model_type` 均被忽略。
- `language_model_type` 已标记废弃，将在 v2.0.0 移除。

### 1.2 `lx.extract()` 参数规范

| 参数 | 类型 | 必填 | 默认值 | 约束与说明 |
| --- | --- | --- | --- | --- |
| `text_or_documents` | `str \| Iterable[Document]` | **是** | — | 位置参数。字符串按纯文本处理；`http(s)://` 开头仅在 `fetch_urls=True` 时才下载 |
| `prompt_description` | `str \| None` | 否 | `None` | 抽取指令，进入 Prompt 的说明段 |
| `examples` | `Sequence[ExampleData] \| None` | 条件必填 | `None` | 无 `output_schema` 且模型无预置 schema 时**必须**提供，否则 `ValueError` |
| `output_schema` | `JsonSchema \| None` | 否 | `None` | 关键字参数。仅 Gemini / OpenAI 支持；与 `fence_output=True`、YAML 格式互斥 |
| `model_id` | `str` | 否 | `"gemini-3.5-flash"` | 按正则匹配 Provider，见 §2.2 |
| `model` | `BaseLanguageModel \| None` | 否 | `None` | 预建模型实例，优先级最高 |
| `config` | `factory.ModelConfig \| None` | 否 | `None` | 显式指定 Provider 与 `provider_kwargs` |
| `api_key` | `str \| None` | 否 | `None` | 未传时按 §2.3 从环境变量解析 |
| `language_model_type` | `Type \| None` | 否 | `None` | **已废弃**，会触发 `FutureWarning` |
| `format_type` | `FormatType \| None` | 否 | `None`（等价 JSON） | 仅支持 `JSON` / `YAML` |
| `temperature` | `float \| None` | 否 | `None`（用模型默认） | 显式值优先于 `language_model_params` 与 `config.provider_kwargs` 中的同名项 |
| `fence_output` | `bool \| None` | 否 | `None`（按 schema 推导） | `True` 表示期望模型用代码围栏包裹输出；与 `output_schema` 互斥。启用 schema 约束时默认为无围栏 |
| `use_schema_constraints` | `bool` | 否 | `True` | 由 `examples` 生成约束 schema |
| `max_char_buffer` | `int` | 否 | `1000` | 单次推理的**字符**上限，决定分块粒度。越小则调用次数越多、成本越高 |
| `batch_length` | `int` | 否 | `10` | 每批处理的分块数 |
| `max_workers` | `int` | 否 | `10` | 并发 worker 数，受 `min(batch_length, max_workers)` 限制 |
| `extraction_passes` | `int` | 否 | `1` | 大于 1 时多轮抽取并合并（重叠时先到者胜），**成倍增加 token 成本** |
| `context_window_chars` | `int \| None` | 否 | `None` | 把上一分块的尾部字符作为当前分块上下文，缓解跨块指代 |
| `additional_context` | `str \| None` | 否 | `None` | 追加到 Prompt 的补充上下文 |
| `resolver_params` | `dict \| None` | 否 | `None` | 解析与对齐调参，见 §1.6 |
| `language_model_params` | `dict \| None` | 否 | `None` | Provider 专属构造参数，如 `max_output_tokens`、`top_p`、`max_retries` |
| `model_url` | `str \| None` | 否 | `None` | 自建或私有端点；仅当所选 Provider 接受该参数时透传 |
| `fetch_urls` | `bool` | 否 | `False` | **关键字参数**。开启后 http(s) 字符串会被 `requests.get` 下载，存在 SSRF 风险 |
| `prompt_validation_level` | `PromptValidationLevel` | 否 | `WARNING` | `OFF` / `WARNING` / `ERROR` 三档 |
| `prompt_validation_strict` | `bool` | 否 | `False` | 仅当 level 为 `ERROR` 时，对非精确匹配也抛错 |
| `show_progress` | `bool` | 否 | `True` | 进度条开关 |
| `debug` | `bool` | 否 | `False` | 开启后对整个 `langextract` 命名空间记录调试日志，**进程内一旦开启不可关闭** |
| `tokenizer` | `Tokenizer \| None` | 否 | `None`（`RegexTokenizer`） | 用于分块与对齐的分词器 |

> 说明：`lx.extract` 的 `max_char_buffer` 默认是 `1000`；底层 `Annotator.annotate_text()` / `annotate_documents()` 自身默认是 `200`。直接调用 Annotator 时行为不同，不要混用两套默认值。

### 1.3 输入载体类型规范

`text_or_documents` 只接受以下四类载体。

#### 1.3.1 纯文本字符串

- 类型：`str`；**必须**是已完成解析的纯文本。
- 返回值：单个 `AnnotatedDocument`。
- 字符串若形如 URL，仅在 `fetch_urls=True` 时才会被下载，否则**按字面文本处理**（不会报错，会把 URL 当成正文抽取）。

#### 1.3.2 `Iterable[Document]`

`lx.data.Document` 的字段规范：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `text` | `str` | **是** | 文档正文 |
| `document_id` | `str \| None` | 否 | 未提供时按 `doc_<8位十六进制>` 自动生成 |
| `additional_context` | `str \| None` | 否 | 该文档专属补充上下文；`extract(additional_context=...)` 只填充未设置该字段的文档 |

```python
import langextract as lx

documents = [
    lx.data.Document(text="Patient has hypertension.", document_id="note-001"),
    lx.data.Document(text="Patient denies diabetes."),
]

results = lx.extract(
    text_or_documents=documents,
    prompt_description="Extract medical conditions.",
    examples=examples,
    model_id="gemini-3.5-flash",
)  # -> list[AnnotatedDocument]
```

#### 1.3.3 CSV 数据集

`lx.io.Dataset` 是**唯一**支持的文件输入类型。

| 约束 | 规定 |
| --- | --- |
| 文件后缀 | **必须**为 `.csv`；其他后缀抛 `NotImplementedError: Unsupported file type: <path>` |
| 必需列 | `text_key`（正文列）与 `id_key`（文档 ID 列），两列**必须**同时存在 |
| 编码 | UTF-8 |
| 分隔符 | `load(delimiter=",")` 可自定义，默认逗号 |
| 文件不存在 | `IOError` |
| 空文件或缺列 | `InvalidDatasetError` |

```python
from langextract import io

dataset = io.Dataset(
    input_path="notes.csv",
    id_key="doc_id",
    text_key="content",
)
documents = list(dataset.load(delimiter=","))  # Iterator[Document]
```

#### 1.3.4 HTTP(S) URL

| 约束 | 规定 |
| --- | --- |
| 生效条件 | **必须**同时满足：`fetch_urls=True` 且字符串通过 `io.is_url()` 校验 |
| URL 形态 | 仅 `http` / `https`，且 host 为合法 IP、`localhost` 或**含点号的域名**；含空格或换行的字符串不算 URL |
| Content-Type 白名单 | `text/*`、`application/json`、`application/xml` |
| 白名单外 | **仅打印 warning，仍会继续下载**，随后按编码顺序解码 |
| 解码顺序 | `utf-8` → `latin-1` → `ascii` → `utf-16`，全部失败则抛 `ValueError: Could not decode content from <url> as text` |
| 网络失败 | `requests.RequestException` |
| 安全 | 无任何净化：内网元数据地址、回环地址、重定向、DNS rebinding 均不设防。仅当 URL 来自可信来源且进程运行在沙箱中才可开启 |

### 1.4 示例数据契约（Few-shot）

`examples` 是驱动抽取质量的核心，字段规范：

| 对象 | 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- | --- |
| `ExampleData` | `text` | `str` | **是** | 示例正文 |
| `ExampleData` | `extractions` | `list[Extraction]` | **是** | 该示例的期望抽取结果 |
| `Extraction` | `extraction_class` | `str` | **是** | 抽取类别名，将成为输出信封的 key |
| `Extraction` | `extraction_text` | `str` | **是** | 应当与示例 `text` **逐字一致**，且按出现顺序排列 |
| `Extraction` | `attributes` | `dict[str, str \| list[str]]` | 否 | 附加属性，会以 `<class>_attributes` 形式进入输出信封 |

Prompt 对齐校验（`prompt_validation_level`）三档行为：

- `OFF`：跳过校验。
- `WARNING`（默认）：发现示例未逐字对齐时记录告警但继续执行。
- `ERROR`：抛 `PromptAlignmentError`；配合 `prompt_validation_strict=True` 时，模糊匹配（`match_fuzzy`、`match_lesser`）也一并抛错。

### 1.5 明确不支持的输入

以下输入**不支持**，源码中不存在任何解析路径，不要依赖也不要尝试绕过：

| 输入类型 | 实际行为 |
| --- | --- |
| PDF（本地文件） | 无解析器；无任何 PDF 依赖 |
| DOCX / PPTX / XLSX | 无解析器 |
| PDF / DOCX 等二进制文件的 URL | 会下载，但因 Content-Type 不在白名单（仅告警）且内容为二进制，解码失败抛 `ValueError` |
| 图片（PNG / JPG 等） | 无多模态输入通道，`Document` 数据结构中**不存在**任何图像或文件字段 |
| 音频 / 视频 | 同上 |
| 非 `.csv` 的表格文件 | `NotImplementedError: Unsupported file type` |
| HTML 网页正文 | 无正文抽取（readability）能力，只能按原始文本处理 |

> 结论：**任何非纯文本输入都必须在进入 LangExtract 之前由你自己完成解析与文本化。**

### 1.6 输入校验与失败模式

| 触发条件 | 结果 |
| --- | --- |
| `examples` 为空**且**无 `output_schema` 且模型无预置 schema | `ValueError: Examples are required for reliable extraction...` |
| `output_schema` 与 `fence_output=True` 同时出现 | `InferenceConfigError`（`output_schema_fence_error`） |
| `output_schema` 配 YAML 格式或非默认 wrapper 与后缀 | `InferenceConfigError` |
| `output_schema` 信封结构不合法（顶层非 object、`required` 缺 `extractions`、item 使用保留字段名等） | `InferenceConfigError` |
| 无 API key 且未提供本地模型 | `ValueError`（由 Provider 抛出） |
| `batch_length < max_workers` | `UserWarning`，实际并发降为 `batch_length` |
| `resolver_params` 含未知键 | `TypeError: Unknown key in resolver_params; check spelling: ...` |
| 分块级解析失败 | 默认丢弃该分块并告警；`resolver_params={'suppress_parse_errors': False}` 时抛 `ResolverParsingError` |
| `fetch_urls=True` 且下载失败 | `requests.RequestException` |

**`resolver_params` 可用键与默认值**：

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `suppress_parse_errors` | `True`（在 `extract()` 中） | 抑制分块级解析错误 |
| `extraction_index_suffix` | `None` | 指定用哪个属性作为抽取排序键 |
| `enable_fuzzy_alignment` | `True` | 模糊对齐开关 |
| `fuzzy_alignment_threshold` | `0.75` | 模糊对齐阈值 |
| `fuzzy_alignment_algorithm` | `"lcs"` | `"legacy"` 已废弃 |
| `fuzzy_alignment_min_density` | `1/3` | 最小匹配密度 |
| `exact_alignment_algorithm` | `"dp"` | `"difflib"` 为旧行为 |
| `accept_match_lesser` | `True` | 接受部分匹配的结果 |

---

## 2. 文本模型接入规范

### 2.1 模型类型要求（核心结论）

**LangExtract 必须接入 text-to-text 文本大模型。接口契约是"字符串进、字符串出"，不存在多模态输入通道。**

抽象基类的唯一抽象方法是：

```python
def infer(
    self, batch_prompts: Sequence[str], **kwargs
) -> Iterator[Sequence[types.ScoredOutput]]:
```

- **入参**：`Sequence[str]`，即一批已拼装好的完整 Prompt 字符串。
- **出参**：每轮 `yield` 一个 `Sequence[ScoredOutput]`，按分数降序排列，通常只用第一个。
- 因为入参是纯字符串，**图片、音频、文件引用无法通过该接口传递**。即使底层模型（如 Gemini）本身是多模态的，LangExtract 也只发送文本。

`ScoredOutput` 字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `score` | `float \| None` | 置信分；多数内置 Provider 直接填 `1.0` |
| `output` | `str \| None` | 模型返回的原始文本（即 §3.2 的原始信封） |

> 证据：默认 Provider 的调用点为 `generate_content(model=..., contents=prompt, config=...)`，`contents` 就是一个字符串，没有任何 Part、图片或文件上传逻辑。

### 2.2 内置 Provider 与 `model_id` 匹配规则

路由规则：按注册正则逐个匹配 `model_id`（不区分大小写），**第一个匹配者胜出**；同优先级下按注册顺序。

| Provider | 匹配正则（`model_id` 前缀） | 默认模型 | 额外依赖 |
| --- | --- | --- | --- |
| Gemini | `^gemini` | `gemini-3.5-flash` | 无（核心依赖） |
| OpenAI | `^gpt-3.5`、`^gpt-4`、`^gpt4.`、`^gpt-5`、`^gpt5.`、`^o[1-9]` | 由 `model_id` 指定 | `pip install langextract[openai]` |
| Ollama | `^gemma`、`^llama`、`^mistral`、`^mixtral`、`^phi`、`^qwen`、`^deepseek`、`^command-r`、`^starcoder`、`^codellama`、`^codegemma`、`^tinyllama`、`^wizardcoder`、`^gpt-oss`，以及 `meta-llama/Llama*`、`google/gemma*`、`mistralai/*`、`microsoft/phi*`、`Qwen/*`、`deepseek-ai/*`、`bigcode/starcoder*`、`codellama/*`、`TinyLlama/*`、`WizardLM/*` | 由 `model_id` 指定 | 本地 Ollama 服务 |

内置 Provider 注册优先级均为 `10`。

**显式指定 Provider**（当多个 Provider 匹配同一 `model_id` 时使用）：

```python
from langextract import factory

config = factory.ModelConfig(
    model_id="gemma2:2b",
    provider="OllamaLanguageModel",   # 可用全类名，或 "ollama" 之类的部分匹配
    provider_kwargs={"model_url": "http://localhost:11434"},
)
```

### 2.3 凭证与环境变量规范

| 环境变量 | 用途 | 解析规则 |
| --- | --- | --- |
| `GEMINI_API_KEY` | Gemini | `model_id` 匹配 `gemini` 时按 `GEMINI_API_KEY` → `LANGEXTRACT_API_KEY` 顺序取第一个非空值 |
| `OPENAI_API_KEY` | OpenAI | `model_id` 匹配 `gpt` 或 `^o[1-9]`（推理模型）时按 `OPENAI_API_KEY` → `LANGEXTRACT_API_KEY` 顺序取第一个非空值 |
| `LANGEXTRACT_API_KEY` | 通用兜底 | 只在上面两个专用变量均未命中时生效 |
| `OLLAMA_BASE_URL` | Ollama 服务地址 | 默认 `http://localhost:11434`；仅当 `model_id` 含 `ollama` 时自动注入 |
| `LANGEXTRACT_DISABLE_PLUGINS` | 插件加载开关 | 取 `1` / `true` / `yes` 时禁用第三方 Provider 插件发现 |

规则细节：

- 显式传入 `api_key=` 时**不读环境变量**（`vertexai=True` 时同样跳过）。
- 同时命中多个 key 时发出 `UserWarning`，列出所有命中项并**使用第一个**。

### 2.4 自定义 Provider 接入契约

外部 Provider 走 Python entry point 自动发现，无需改动 LangExtract 代码。

**步骤 1 — 包结构**

```
langextract-myprovider/
├── pyproject.toml
└── langextract_myprovider/
    ├── __init__.py
    ├── provider.py
    └── schema.py        # 可选，需要 output_schema 支持时才要
```

**步骤 2 — 声明 entry point**（`pyproject.toml`）

```toml
[project.entry-points."langextract.providers"]
myprovider = "langextract_myprovider:MyProviderLanguageModel"
```

**步骤 3 — 实现 Provider 类**

```python
import os

from langextract.core import base_model, types
from langextract.providers import router


@router.register(r"^mymodel", r"^custom", priority=10)
class MyProviderLanguageModel(base_model.BaseLanguageModel):
    def __init__(
        self,
        model_id: str = "mymodel-base",
        api_key: str | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.model_id = model_id
        self.api_key = api_key or os.environ.get("MYPROVIDER_API_KEY")
        self.client = MyProviderClient(api_key=self.api_key)

    def infer(self, batch_prompts, **kwargs):
        merged = self.merge_kwargs(kwargs)
        for prompt in batch_prompts:
            text = self.client.generate(prompt, **merged)
            yield [types.ScoredOutput(score=1.0, output=text)]
```

**必备要素清单**

| 项 | 要求 |
| --- | --- |
| 基类 | **必须**继承 `langextract.core.base_model.BaseLanguageModel` |
| `__init__` | 接收 `model_id`、`api_key`、`**kwargs`；需要时调用 `super().__init__(**kwargs)` 以保留 schema 与围栏配置 |
| `infer` | **必须**实现，签名 `(self, batch_prompts: Sequence[str], **kwargs)`，产出 `ScoredOutput` |
| 注册方式 | 二选一：模块级 `@router.register(模式..., priority=10)`，或定义类方法 `get_model_patterns()` 返回模式列表（插件加载器会读取它，默认优先级 `20`） |
| 输出清洗 | **应当**把 SDK 响应类型限制在 Provider 内部；无法使用时抛 `InferenceRuntimeError` |
| 异常约定 | `InferenceRuntimeError` **必须**在重试与兜底异常处理**之前**抛出，否则诊断信息会被吞掉 |

**可选：结构化输出（schema）支持**

实现 `get_schema_class()` 返回自定义 schema 类（继承 `langextract.core.schema.BaseSchema`），并实现：

| 方法 | 作用 |
| --- | --- |
| `from_examples(examples)` | 由 few-shot 示例推导约束 |
| `from_schema_dict(output_schema)` | 由用户传入的 `output_schema` 构建（不支持则抛 `NotImplementedError`，框架会转成 `InferenceConfigError`） |
| `to_provider_config()` | 输出 Provider 侧的约束参数（如 `response_mime_type`） |
| `requires_raw_output` | 为 `True` 时框架默认关闭围栏输出 |
| `sync_with_provider_kwargs(kwargs)` | 与用户传入参数对齐 |
| `output_schema_reserved_provider_kwargs()` | 声明被 schema 占用的参数名，冲突时报错 |

未实现 schema 支持的 Provider 仍可用于普通抽取（依赖 few-shot 示例），但**不能**使用 `output_schema`。

**插件加载机制**：惰性加载，首次需要模型解析时触发；可手动调用 `lx.providers.load_plugins_once()`；插件加载失败只记录 warning，不中断主流程。

### 2.5 结构化输出约束矩阵

| `output_schema` | `use_schema_constraints` | `fence_output` | `format_type` | 结果 |
| --- | --- | --- | --- | --- |
| `None` | `True`（默认） | `None` | JSON | 可用。Gemini / OpenAI 由示例推导约束且默认**无围栏裸 JSON** |
| `None` | `True` | `None` | YAML | 可用，但无约束能力（`FormatModeSchema` 仅传递格式） |
| `None` | `False` | `None` | JSON / YAML | 可用。纯 Prompt 模式，靠模型自觉，解析失败风险高；默认期望带围栏 |
| `None` | 任意 | `True` / `False` | JSON / YAML | 可用。显式控制围栏 |
| 提供 | 任意（被覆盖） | `None` 或 `False` | JSON | **推荐路径**。Provider 侧约束解码，输出无围栏裸 JSON |
| 提供 | 任意 | `True` | 任意 | 报错 `InferenceConfigError` |
| 提供 | 任意 | 任意 | YAML | 报错 `InferenceConfigError` |

围栏默认值的推导规则：`requires_fence_output = not schema.requires_raw_output`；Gemini 与 OpenAI 的 schema 类均返回 `requires_raw_output=True`，即**启用 schema 约束时默认不加围栏**；完全没有 schema 时默认期望带围栏。

`output_schema` 被强制锁定的三项（不可通过 `resolver_params` 覆盖）：`use_wrapper=True`、`wrapper_key="extractions"`、`attribute_suffix="_attributes"`。

### 2.6 关于 Embedding 模型的明确结论

**不需要接入任何 Embedding 模型，源码中也不存在向量化能力。**

| 检查项 | 结论 |
| --- | --- |
| 依赖清单 | 无 `torch`、`sentence-transformers`、`tiktoken`、`faiss`、`chromadb` 等任何嵌入或向量库 |
| 数值计算 | `numpy` 仅作为 `pandas` 的间接依赖出现，包内无直接使用 |
| 结构化输出实现 | 依赖**约束解码**（如 Gemini 的 `response_mime_type="application/json"` 加 JSON Schema）与 few-shot 示例，而非向量相似度 |
| 检索与召回 | 无任何 embedding 或相似度检索代码；长文召回靠 `extraction_passes` 多轮抽取 |

因此接入成本仅为：一个能输出 JSON 的文本 LLM（云端或本地 Ollama 皆可）加对应凭证。

---

## 3. Pipeline 输出数据格式规范

### 3.1 两层输出总览

LangExtract 的输出分两层，**不要混淆**：

| 层 | 产出方到消费方 | 形态 |
| --- | --- | --- |
| **层 A：模型原始输出信封** | Provider 到 Resolver | LLM 返回的 JSON / YAML 文本 |
| **层 B：对外对象与 JSONL** | Resolver 到调用方 | `AnnotatedDocument` 对象，或落盘的 JSONL |

### 3.2 层 A：模型原始输出信封

**标准形态**（JSON。**启用 schema 约束时默认是无围栏裸 JSON**，未启用 schema 时默认期望代码围栏）：

```json
{
  "extractions": [
    {
      "condition": "hypertension",
      "condition_attributes": { "status": "present" }
    },
    {
      "condition": "diabetes",
      "condition_attributes": { "status": "absent" }
    }
  ]
}
```

**YAML 变体**（`format_type=FormatType.YAML`）：

```yaml
extractions:
  - condition: hypertension
    condition_attributes:
      status: present
```

**结构规则**

| 项 | 规定 |
| --- | --- |
| 顶层容器键 | `extractions`（保留常量 `EXTRACTIONS_KEY`） |
| 数组元素 | 每个元素是一个对象，key 为 `extraction_class`，value 为该类的**原文片段字符串** |
| 属性对象 | 以 `<extraction_class>_attributes` 为 key，value 为对象；无属性时该键可省略 |
| 围栏 | `requires_fence_output = not schema.requires_raw_output`；Gemini / OpenAI 启用 schema 约束时默认为无围栏裸 JSON，无 schema 时默认期望围栏 |
| 顶层列表容错 | 默认允许模型直接返回 `[{...}, {...}]`（`allow_top_level_list=True`）；严格模式下必须带 wrapper |
| 解析失败 | 抛 `FormatParseError`；在 `extract()` 中默认被抑制为告警并丢弃该分块 |

**常见错误形态（必须避免）**

```json
{
  "extractions": [
    {
      "extraction_class": "condition",
      "extraction_text": "hypertension",
      "attributes": { "status": "present" }
    }
  ]
}
```

三个问题：使用了内部字段名 `extraction_class`、`extraction_text`、`attributes`（Resolver 不认这三个 key），且缺少 `<class>_attributes` 后缀形式。使用 `output_schema` 时，这类键会被直接拒绝。

另外两条约束：

- `extraction_class` **不得**以 `_attributes` 结尾（该后缀被保留）。
- 被 `<think>...</think>` 包裹的思考段会在解析前被剥离。

### 3.3 层 B：对外对象

#### `AnnotatedDocument`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `document_id` | `str` | 未设置时自动生成 `doc_<8位十六进制>` |
| `text` | `str \| None` | 原文全文 |
| `extractions` | `list[Extraction] \| None` | 抽取结果 |

#### `Extraction`

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `extraction_class` | `str` | 抽取类别 |
| `extraction_text` | `str` | 抽取到的原文片段 |
| `char_interval` | `CharInterval \| None` | **闭开区间** `[start_pos, end_pos)`；`None` 表示该结果**无法在原文中定位**（可能是模型从示例里抄来的） |
| `alignment_status` | `AlignmentStatus \| None` | 对齐状态枚举，见下 |
| `extraction_index` | `int \| None` | 在抽取列表中的序号 |
| `group_index` | `int \| None` | 所属分组序号 |
| `description` | `str \| None` | 可选描述 |
| `attributes` | `dict[str, str \| list[str]] \| None` | 属性字典 |
| `token_interval` | `TokenInterval \| None` | 属性存在，但**不参与序列化** |

`AlignmentStatus` 取值：

| 值 | 含义 |
| --- | --- |
| `match_exact` | 在原文中精确命中 |
| `match_greater` | 匹配区间大于抽取文本 |
| `match_lesser` | 匹配区间小于抽取文本 |
| `match_fuzzy` | 模糊匹配命中 |

> 完整溯源用法：过滤 `char_interval is not None` 即得"有原文依据"的结果集，例如 `[e for e in result.extractions if e.char_interval]`。

### 3.4 JSONL 落盘规范

写入函数：`lx.io.save_annotated_documents(documents, output_dir=".", output_name="data.jsonl", show_progress=True)`

| 项 | 规定 |
| --- | --- |
| 编码 | UTF-8，`ensure_ascii=False`（中文原样输出，不转义） |
| 粒度 | **每行一个** `AnnotatedDocument` 对应一个 JSON 对象（JSON Lines） |
| 顶层键 | `document_id`、`text`、`extractions` |
| 抽取项键 | `extraction_class`、`extraction_text`、`char_interval`、`alignment_status`、`extraction_index`、`group_index`、`description`、`attributes` |
| 排除字段 | `token_interval` 及所有下划线开头的私有字段**不落盘** |
| 空数据集 | 抛 `InvalidDatasetError` |
| `output_dir` 默认 | `test_output`（自动创建） |
| 安全提示 | `output_name` **不做路径净化**；托管服务中调用前应自行拒绝 `..` 与绝对路径 |

**示例行**（对应"患者有高血压、否认糖尿病"）：

```json
{"document_id": "doc_3f9a1c07", "extractions": [{"extraction_class": "condition", "extraction_text": "hypertension", "char_interval": {"start_pos": 12, "end_pos": 24}, "alignment_status": "match_exact", "extraction_index": 1, "group_index": 0, "description": null, "attributes": {"status": "present"}}, {"extraction_class": "condition", "extraction_text": "diabetes", "char_interval": {"start_pos": 36, "end_pos": 44}, "alignment_status": "match_exact", "extraction_index": 2, "group_index": 0, "description": null, "attributes": {"status": "absent"}}], "text": "Patient has hypertension and denies diabetes."}
```

> 该行的偏移可直接核验：`"Patient has hypertension and denies diabetes."` 中 `hypertension` 位于 `[12, 24)`，`diabetes` 位于 `[36, 44)`，符合闭开区间语义。

回读函数：`lx.io.load_annotated_documents_jsonl(path)` 返回 `Iterator[AnnotatedDocument]`，会还原 `char_interval`、`alignment_status`、`token_interval` 的对象类型。

> JSON 对象键序不具语义；下游解析**必须**按键名取值，不要依赖顺序。

### 3.5 返回值规范

| 输入类型 | 返回值 |
| --- | --- |
| `str`（含 URL） | 单个 `AnnotatedDocument` |
| `Iterable[Document]` | `list[AnnotatedDocument]` |

### 3.6 可视化产物

调用形式：`lx.visualize(data_source, *, animation_speed=1.0, show_legend=True, gif_optimized=True)`

| 项 | 规定 |
| --- | --- |
| 入参 | `AnnotatedDocument` 或 JSONL 文件路径 |
| 出参 | 有 IPython 时返回 `IPython.display.HTML`，否则返回 HTML 字符串 |
| 产物 | 自包含的交互式 HTML（内联样式与脚本），可在原文上动画高亮标注 |
| **重要限制** | 从 JSONL 读取时**只使用第一条文档**（`documents[0]`）；文件不存在抛 `FileNotFoundError`，无文档抛 `ValueError` |
| 定位 | **仅供人工核验**，不是数据交换格式，下游不要解析 HTML |

### 3.7 不输出清单

LangExtract 的输出中**不存在**以下内容：

- 向量或 embedding 表示
- 任何索引结构（倒排、ANN、向量索引）
- 图结构：无节点表、无边表、无三元组
- 实体消解或共指合并结果（相同实体在不同分块、不同文档中不会自动统一）
- 文档解析产物（PDF 版面、表格结构等）

关系类信息只能以"某个 `extraction_class` 加 `attributes` 中的 `source` / `target` 字段"的形式存在，例如：

```python
lx.data.Extraction(
    extraction_class="relationship",
    extraction_text="Juliet is the sun",
    attributes={"type": "metaphor", "source": "Romeo", "target": "Juliet"},
)
```

这是文本字段，**不是**图结构，需要下游自行建图。

---

## 4. 多模态 RAG 对接边界

### 4.1 能力矩阵

| RAG 环节 | LangExtract 支持 | 证据与补齐建议 |
| --- | --- | --- |
| 原始文档解析（PDF / DOCX / 图片） | 不支持 | 无解析器、无相关依赖；需自建解析层（如 `pypdf`、`python-docx`、`unstructured`，扫描件需 OCR 或视觉模型） |
| 文本分块 | 支持 | `ChunkIterator` 按 `max_char_buffer` 字符切分，优先句子边界与换行，非语义分块 |
| 结构化抽取 | 支持 | 核心能力，few-shot 加约束解码 |
| 原文溯源（grounding） | 支持 | `char_interval` 闭开区间加 `alignment_status` |
| 多模态理解（图片 / 音频） | 不支持 | `infer()` 只接受 `Sequence[str]`；即使接 Gemini 也只发文本 |
| Embedding | 不支持 | 无任何嵌入模型依赖 |
| 索引与向量检索 | 不支持 | 无索引结构，无检索接口 |
| 知识图谱构建 | 不支持 | 无节点与边的模型 |
| 实体消解与跨文档归一 | 不支持 | 无共指合并；跨分块的同名实体不会自动统一 |
| 结果落盘与人工核验 | 支持 | JSONL 加交互式 HTML |

### 4.2 建议分层架构

```
[1] 解析层（自建）
    PDF / DOCX / PPTX / 图片  ->  纯文本（附版面与页码 metadata）
                |
                v
[2] LangExtract 抽取层   <-- 唯一由 LangExtract 承担的环节
    分块 -> 文本 LLM 抽取 -> 原文对齐 -> AnnotatedDocument / JSONL
                |
                v
[3] 向量与检索层（自建）
    抽取结果 + char_interval 作为 metadata -> embedding -> 索引
                |
                v
[4] 图谱层（自建）
    实体归一 -> 节点与边 -> 图库；关系取自 relationship 类的 attributes
```

### 4.3 两个关键接缝

**接缝一：`char_interval` 作为 RAG 溯源 metadata。**

抽取结果的 `{start_pos, end_pos}` 与 `alignment_status` 是 LangExtract 最有价值的输出：把它与 `document_id` 一起写入 chunk 或向量库的 metadata，即可在问答引用时精确高亮回原文位置。只用 `extraction_text` 会丢失这层能力。

**接缝二：JSONL 作为系统间中间契约。**

抽取层与向量层之间**应当**以 §3.4 的 JSONL 为契约交换：

```python
import langextract as lx

result = lx.extract(
    text_or_documents=text,
    prompt_description=prompt,
    examples=examples,
    model_id="gemini-3.5-flash",
)
lx.io.save_annotated_documents(
    [result], output_dir="out", output_name="extractions.jsonl"
)

# 下游：回读并送入自建的向量化与入库流程
for doc in lx.io.load_annotated_documents_jsonl("out/extractions.jsonl"):
    for ext in doc.extractions or []:
        if ext.char_interval is None:
            continue          # 无原文依据，丢弃
        build_chunk(doc.document_id, doc.text, ext)
```

### 4.4 风险提示

- **不要用 `fetch_urls` 抓 PDF 链接**：Content-Type 白名单外只告警不拦截，二进制内容会尝试文本解码，结果要么抛 `ValueError`，要么得到乱码并污染上下文。PDF 必须先在解析层文本化。
- **不要假设存在跨文档实体合并**：不同分块或不同文档中的同一实体不会被自动归一，需要自建消解步骤。
- **不要假设 `extractions` 全部有原文依据**：模型可能从 few-shot 示例中抄内容，这类结果 `char_interval` 为 `None`，入库前必须过滤。
- **成本随配置增长**：`extraction_passes > 1` 与更小的 `max_char_buffer` 都会成倍增加 API 调用量；`max_workers` 与 `batch_length` 只提升速度，不增加成本。

---

## 附录 A：源码锚点索引

以下路径均相对于克隆仓库根目录。

| 结论 | 源码位置 |
| --- | --- |
| `lx.extract()` 签名、参数默认值、优先级链 | `langextract/langextract/extraction.py:45` |
| 参数校验与失败模式（examples、schema、fence、batch_length） | `langextract/langextract/extraction.py:150-230` |
| `Document` / `Extraction` / `AnnotatedDocument` / `ExampleData` 字段与 `doc_<8hex>` 生成 | `langextract/langextract/core/data.py` |
| `AlignmentStatus` 枚举、`CharInterval` 闭开区间 | `langextract/langextract/core/data.py` |
| CSV 加载、`.csv` 唯一支持、`NotImplementedError` | `langextract/langextract/io.py:50`、`:67`、`:82` |
| JSONL 落盘与回读、`ensure_ascii=False`、空数据集报错 | `langextract/langextract/io.py:85`、`:144` |
| URL 校验与下载、Content-Type 白名单、编码回退顺序 | `langextract/langextract/io.py:226`、`:265` |
| 分块策略（字符窗口加句子边界） | `langextract/langextract/chunking.py:343` |
| Annotator 内部默认 `max_char_buffer=200`、多轮合并 | `langextract/langextract/annotation.py:209`、`:534` |
| `BaseLanguageModel.infer` 的 `Sequence[str]` 契约、`infer_batch` | `langextract/langextract/core/base_model.py:171`、`:186` |
| `ScoredOutput` 字段 | `langextract/langextract/core/types.py` |
| Provider 匹配正则与优先级 | `langextract/langextract/providers/patterns.py` |
| 默认模型 `gemini-3.5-flash`、纯文本 `contents=prompt` 调用 | `langextract/langextract/providers/gemini.py:42`、`:449` |
| 环境变量解析、多 key 警告、Ollama base_url | `langextract/langextract/factory.py` |
| Provider 路由（首个匹配胜出）与注册装饰器 | `langextract/langextract/providers/router.py:108`、`:139` |
| 插件发现与 `get_model_patterns()` | `langextract/langextract/providers/__init__.py` |
| 原始信封构造（`<class>` 加 `<class>_attributes`） | `langextract/langextract/core/format_handler.py:134` |
| 原始输出解析（wrapper、顶层列表、strict 规则） | `langextract/langextract/core/format_handler.py:169` |
| 围栏默认值推导 | `langextract/langextract/core/base_model.py:136`、`langextract/langextract/providers/schemas/gemini.py:87` |
| `output_schema` 信封校验与保留键限制 | `langextract/langextract/core/output_schema.py` |
| schema 基类方法（`from_examples`、`to_provider_config`、`requires_raw_output`） | `langextract/langextract/core/schema.py:39` |
| 异常类型清单 | `langextract/langextract/core/exceptions.py` |
| 依赖清单（无 embedding、vector、graph 依赖） | `langextract/pyproject.toml` |
| Provider 接入契约与插件清单 | `langextract/langextract/providers/README.md` |
| 输出信封与 `output_schema` 官方示例 | `langextract/docs/examples/output_schema.md` |

## 附录 B：术语表

| 术语 | 含义 |
| --- | --- |
| **extraction_class** | 抽取类别名，同时作为输出信封的 key 与 `<class>_attributes` 的前缀 |
| **grounding / alignment** | 把抽取文本对齐回原文区间，产出 `char_interval` 与 `alignment_status` |
| **fence** | 模型输出外层的 `json` / `yaml` 代码围栏 |
| **wrapper** | 输出信封的顶层容器对象，键名为 `extractions` |
| **schema constraints** | 由 few-shot 示例或 `output_schema` 推导出的结构化约束，交给 Provider 做约束解码 |
| **chunk** | 由 `max_char_buffer` 切分出的文本分块，是推理与对齐的最小单位 |
| **AnnotatedDocument** | 一篇文档的抽取结果容器，JSONL 落盘的单位 |
| **text-to-text** | 模型接口只接受字符串、只返回字符串，无图像或音频输入通道 |

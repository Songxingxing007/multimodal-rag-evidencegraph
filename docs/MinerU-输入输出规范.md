# MinerU 输入输出规范（Agent 轻量解析 API 方案）

> **文档性质**：本文档基于 MinerU 官方文档与**一次真实调用实测**整理，用于团队把 MinerU 作为文档解析层接入多模态 RAG 链路时的接口约定。
>
> **来源**：
> - 云端 API 文档：`https://mineru.net/apiManage/docs`（抓取日期 2026-10-15）
> - 开源项目文档站：`https://opendatalab.github.io/MinerU/`（抓取日期 2026-10-15）
> - 实测记录：2026-10-15 调用 Agent 轻量 API 解析官方 demo 文件，详见[附录 A](#附录-a来源与实测记录)
>
> **锚定方案**：本文档以**云端 Agent 轻量解析 API** 为主线（免 Token），精准解析 API 与自部署 4.x 作为对照。

## 目录

- [1. 支持的原始输入文件格式](#1-支持的原始输入文件格式)
- [2. Agent 轻量 API 的输出格式与文件字段说明](#2-agent-轻量-api-的输出格式与文件字段说明)
- [3. 解析后的布局信息梳理（面向精准数值提取）](#3-解析后的布局信息梳理面向精准数值提取)
- [4. 执行 MVP 的必要与必须字段](#4-执行-mvp-的必要与必须字段)
- [附录 A：来源与实测记录](#附录-a来源与实测记录)
- [附录 B：术语表](#附录-b术语表)

---

## 1. 支持的原始输入文件格式

### 1.1 三条路径的支持范围（总览）

| 文件类型 | Agent 轻量 API（本文主线） | 精准解析 API | 自部署 4.x |
| --- | --- | --- | --- |
| `.pdf` | 支持 | 支持 | 支持 |
| 图片 `.png` / `.jpg` / `.jpeg` / `.jp2` / `.webp` / `.gif` / `.bmp` | 支持 | 支持 | 支持 |
| `.docx` | 支持 | 支持 | 支持 |
| `.doc` | 不支持 | 支持 | 支持 |
| `.pptx` | 支持 | 支持 | 支持 |
| `.ppt` | 不支持 | 支持 | 支持 |
| `.xlsx` | 支持 | 支持 | 支持 |
| `.xls` | 不支持 | 支持 | 支持 |
| `.html` | 不支持 | 支持（需 `model_version=MinerU-HTML`） | 支持（static HTML） |
| `.mhtml` / `.mht` | 不支持 | 不支持 | 支持 |
| `.csv` / `.tsv` | 不支持 | 不支持 | 支持 |
| `.rtf` | 不支持 | 不支持 | 支持 |
| `.odt` / `.ods` / `.odp` | 不支持 | 不支持 | 支持 |
| `.epub` | 不支持 | 不支持 | 支持 |
| `.ofd` | 不支持 | 不支持 | 支持 |

### 1.2 Agent 轻量 API 的输入限制（主线）

| 限制项 | 值 |
| --- | --- |
| 文件大小上限 | **10 MB** |
| 文件页数上限 | **20 页** |
| 批量 | 不支持，单文件；每次请求只能上传一个文件 |
| 鉴权 | **无需 Token**，按 IP 限频；超出返回 HTTP 429 |
| 不支持的类型 | `.doc`、`.ppt`、`.xls`、`.html` |
| 解析引擎差异 | PDF / 图片走 pipeline 轻量模型；Word / PPT / Excel 走 Office 原生 API 解析 |

> ⚠️ 对长文档、扫描件、图表密集的文档，Agent 轻量 API 的 20 页上限会直接失败（错误码 `-30003`）。此时必须改用精准解析 API 或自部署。

### 1.3 对照路径的输入限制

| 限制项 | 精准解析 API | 自部署 4.x |
| --- | --- | --- |
| 大小上限 | 200 MB | 无（受本机资源限制） |
| 页数上限 | 200 页 | PDF **默认仅前 10 页**，需 `--pages all` 全量 |
| 批量 | 支持（URL 批量 ≤200；本地文件上传单次 ≤50 个链接） | 目录批处理支持 |
| 鉴权 | **需要 Token**（`Authorization: Bearer <token>`） | 无 |
| 网络限制 | github、aws 等国外 URL 会请求超时 | 不涉及 |

---

## 2. Agent 轻量 API 的输出格式与文件字段说明

### 2.1 调用时序

```
提交任务 -> 轮询查询 -> 取 markdown_url -> 下载 full.md
```

| 步骤 | 方法 | 地址 |
| --- | --- | --- |
| 提交（URL 模式） | `POST` | `https://mineru.net/api/v1/agent/parse/url` |
| 提交（本地上传模式） | `POST` | `https://mineru.net/api/v1/agent/parse/file` |
| 上传（仅文件模式） | `PUT` | 上一步返回的 `data.file_url` |
| 查询结果 | `GET` | `https://mineru.net/api/v1/agent/parse/{task_id}` |

### 2.2 提交响应字段（`/parse/url`）

| 字段 | 类型 | 示例 | 说明 |
| --- | --- | --- | --- |
| `code` | int | `0` | 接口状态码，成功为 `0` |
| `msg` | string | `ok` | 接口处理信息 |
| `trace_id` | string | `c876cd60b202f2396de1f9e39a1b0172` | 请求 ID |
| `data.task_id` | string | `a90e6ab6-44f3-4554-b459-b62fe4c6b43605` | 解析任务 ID，用于查询结果 |

`/parse/file` 额外返回 `data.file_url`：OSS 签名上传地址，客户端用 `PUT` 上传文件。

### 2.3 查询响应字段（`/parse/{task_id}`）

| 字段 | 类型 | 取值 | 说明 |
| --- | --- | --- | --- |
| `code` | int | `0` | 成功为 `0` |
| `msg` | string | `ok` | 处理信息 |
| `trace_id` | string | — | 请求 ID |
| `data.task_id` | string | — | 与提交时一致 |
| `data.state` | string | `waiting-file` / `uploading` / `pending` / `running` / `done` / `failed` | 任务状态 |
| `data.markdown_url` | string | `https://cdn-mineru.../full.md` | **产物下载链接，仅 `state=done` 时有效** |
| `data.err_msg` | string | — | 错误信息，仅 `state=failed` 时有效 |
| `data.err_code` | int | `-30003` | 错误码，仅 `state=failed` 时有效 |

### 2.4 最终生成文件：只有一个

**Agent 轻量 API 的最终产物是唯一一个 Markdown 文件**，通过 `data.markdown_url` 下载：

| 产物 | 数量 | 形态 | 说明 |
| --- | --- | --- | --- |
| `full.md` | **1 个** | UTF-8 纯文本（Markdown） | 解析后的全文，文件名固定为 `full.md` |

**不产出**的内容（与精准解析 API 的关键差异）：

- 没有 ZIP 压缩包
- 没有 `content_list.json`、`middle_json.json`、`model.json`
- 没有 `images/` 目录 —— **图片与图表不会被下载**，Markdown 中仅留占位标记
- 不能通过 `extra_formats` 导出 docx / html / latex

### 2.5 `full.md` 的内部结构规范（实测）

对官方 demo 论文（`example.pdf`）实测得到的产物结构如下：

| 语义元素 | Markdown 形态 | 本次实测数量 |
| --- | --- | --- |
| 文档标题 | `# 标题` | 1 |
| 章节标题 | `## 1. Introduction` 形式的二级标题 | 17 |
| 三级及以下标题 | 未出现 | 0 |
| 正文段落 | 空行分隔的纯文本（无缩进、无硬换行） | 每段一行 |
| 表格 | **单行内联 HTML**：`<table><tr><td>…</td></tr></table>` | 5（开闭标签平衡） |
| 表格题注 | 表格后的空行加 `Table N …` 独立段落 | 1（另 4 张表无独立题注行） |
| 行间公式 | `$$ … $$` 块，可含 `\tag{N}` 编号 | 7 个块（`$$` 定界符出现 14 次） |
| 行内公式 | `$ … $`，保留 LaTeX 原样（如 `\mathrm`） | 128 处定界符 |
| 图片 | `<!-- image-->` HTML 注释占位（**不含图片本体**） | 8 |
| 图片题注 | `Fig. N. …` 独立段落 | 5（Fig.1 至 Fig.5） |
| 参考文献 | 普通段落列表，无 Markdown 列表标记 | 全文末尾 |
| 页码与坐标 | **不存在**（全文无 `page N` 之类标记） | 0 |

**结构总览**：50,727 字符 / 257 行。

#### 2.5.1 图片占位的两个重要细节（实测）

8 个图片占位符，只有 5 条题注，原因是**同一张多子图会连续出现多个占位符**：

| 占位符所在行 | 后续内容 |
| --- | --- |
| 第 23 行 | `Fig. 1.` 题注 |
| 第 48、50 行 | 两个连续占位符，之后是 `Fig. 2.` 题注（多子图） |
| 第 129 行 | `Fig. 3.` 题注 |
| 第 140 行 | `Fig. 4.` 题注 |
| 第 174、176、178 行 | 三个连续占位符，之后是 `Fig. 5.` 题注（多子图） |

结论：**不能假设"一个占位符对应一条题注"**。解析时应把"连续占位符 + 其后的 `Fig. N` 段落"视为一个整体图表单元，否则会错配图表编号。

#### 2.5.2 各元素的真实样例

文档标题与章节标题：

```markdown
# The response of flow duration curves to afforestation

## Abstract

## 1. Introduction
```

行内公式（`$...$`）与行间公式（`$$...$$`，含编号）：

```markdown
The t-value was calculated as $F ^ { 0 . 5 }$, and compared with the critical value ...

$$
F = \frac { \mathrm { [ ( S S E _ { s } - S S E _ { c } ) / ( d f _ { c } - d f _ { s } ) ] } } { \mathrm { S S E _ { c } / d f _ { c } } }\tag{7}
$$
```

表格为**单行 HTML**（下方为真实片段，已截断）：

```html
<table><tr><td></td><td>Area (ha)</td><td>Species</td><td>% Area planted</td><td>Mean annual rainfall (mm)</td>...</tr><tr><td>Traralgon Ck (Vic)</td><td>8700</td><td>Eucalypt</td><td>~70</td><td>1472</td>...</tr>...</table>

Table 1 Site characteristics for all catchments
```

图片占位与题注（图片本体不提供）：

```markdown
<!-- image-->  
Fig. 1. Annual flow duration curves of daily flows from Pine Creek, Australia, 1989–2000.
```

### 2.6 与精准解析 API 的输出对照

| 维度 | Agent 轻量 API | 精准解析 API |
| --- | --- | --- |
| 交付形态 | 单个 `full.md` 的 CDN 链接 | **ZIP 压缩包**（`data.full_zip_url`） |
| ZIP 内文件 | 无 | `full.md`、`*_content_list.json`、`*_middle.json`、`*_model.json`、`images/` |
| 版面与坐标 | 无 | 有（在 JSON 内） |
| 图片资源 | 无 | 有（`images/` 目录） |
| 额外导出 | 不支持 | `extra_formats` 可加 `docx` / `html` / `latex`（对 HTML 源文件无效） |

> HTML 源文件在精准解析 API 下的产物略有不同：`full.md` 加 `main.html`（提取后正文 HTML）。

---

## 3. 解析后的布局信息梳理（面向精准数值提取）

### 3.1 首要结论

**Agent 轻量 API 不提供任何版面布局信息。**

实测产物中不存在 `page_idx`、`bbox`（坐标框）、块类型（block type）、字号、栏数等任何版面字段 —— 全文只是一份线性 Markdown。这意味着：

- 无法知道某个数值出现在第几页、哪个坐标区域；
- 无法做"按表格单元格定位"的精确取值；
- 无法回溯高亮到原文位置。

### 3.2 Agent 方案下可用的"弱结构信号"

虽然没有坐标，Markdown 本身仍带有可用于定位的**文本级结构信号**：

| 信号 | 形态 | 可用于什么 |
| --- | --- | --- |
| 标题层级 | `#` 与 `##` | 按章节切分文档，把数值限定在章节范围内 |
| HTML 表格 | `<table><tr><td>` | **表格内的数值可直接按行列解析**，这是 Agent 方案下最可靠的数值来源 |
| 表格题注 | `Table N …` 段落（**可能缺失**） | 把表格与其语义标签关联；缺失时只能用表格前后的段落推断 |
| 行间公式 | `$$ … $$`，含 `\tag{N}` | 公式编号可用于交叉引用；公式内的数值可解析 |
| 行内公式 | `$ … $` | 正文中的变量与常量符号 |
| 图片占位与题注 | `<!-- image-->`（可连续多个）加后续 `Fig. N …` | **只能拿到图表编号与题注文字**，图表内的数值曲线无法获取 |
| 段落边界 | 空行分隔 | 按段落粒度切分，作为 chunk 的天然边界 |

### 3.3 能力边界（重要）

| 你的需求 | Agent API 是否可满足 | 说明 |
| --- | --- | --- |
| 抽取正文中的数值（含单位、百分数、区间） | 可满足 | 文本正则加 LLM 抽取均可 |
| 抽取表格内的数值 | 可满足（较可靠） | 表格为结构化 HTML，可直接解析成二维数组 |
| 抽取**图表（折线/柱状/散点）中的数值** | **不可满足** | 只有题注文字，图片本体不返回 |
| 抽取公式中的系数 | 部分可满足 | 公式为 LaTeX 源码，可解析，但复杂公式需专门处理 |
| 按页定位与页码溯源 | 不可满足 | 无页码信息 |
| 按坐标高亮回原文 | 不可满足 | 无 bbox |
| 保留图片用于多模态问答 | 不可满足 | 图片不下载 |

### 3.4 需要真布局信息时的升级路径

当"精准提取数值"依赖于版面（图表数值、跨页表格、双栏排版）时，必须改用**精准解析 API**，从 ZIP 中取结构化 JSON：

| 文件 | 提供的布局信息 |
| --- | --- |
| `*_content_list.json` | 按阅读顺序排列的内容块列表；每块含块类型与所在页索引 |
| `*_middle.json` | 中间处理结果：页级结构、块级边界框、说明文字（caption / footnote） |
| `*_model.json` | 模型推理原始结果 |
| `images/` | 抽出的图片资源（图表数值可交由视觉模型解读） |

已知的布局字段语义（以官方 output_files 说明为准，**建议接入前用 1 份样例实测核对**）：

| 字段 | 含义 |
| --- | --- |
| `page_idx` | 页索引，**从 0 开始**（注意与 CLI 的 1 基页码不同） |
| `bbox` | 边界框，归一化坐标 `[x0, y0, x1, y1]`；源坐标缺失时该键省略 |
| block `type` | 块类型，如 `text`、`paragraph_title`、`table`、`image`、`equation`、`chart`、`list`、`index` |
| `captions` / `footnotes` | 说明文字数组，每项形如 `{bbox?: [...], content: "..."}` |
| `image_source` | 该块的图片来源路径（在 ZIP 的 `images/` 下） |
| 跨页标记 | `continues_prev`（该块是上一页内容的续接）、表格跨页单元格合并提示 |

### 3.5 推荐的混合策略

```
常规文档（文本 + 表格为主）
    -> Agent 轻量 API -> full.md -> 章节切分 + HTML 表格解析 -> 数值抽取

图表密集 / 需要精确版面的文档
    -> 精准解析 API -> ZIP -> content_list.json + middle_json.json + images/
         -> 用 bbox / page_idx 定位，用 images/ 交给视觉模型读取图表数值
```

两者可以并存：先用 Agent API 快速跑通，遇到图表类数值再对同一份文档走精准解析 API 兜底。

---

## 4. 执行 MVP 的必要与必须字段

### 4.1 需要配置的外部项：无

| 项目 | 是否需要 | 说明 |
| --- | --- | --- |
| API Key / Token | **不需要** | Agent 轻量 API 免登录，靠 IP 限频 |
| `Authorization` 请求头 | 不需要 | 官方明确"无需 Authorization 请求头" |
| 配置文件 / `.env` | 不需要 | 无需任何本地配置文件 |
| 本地模型权重 | 不需要 | 解析在云端完成 |
| 必需的请求头 | 需要 `Content-Type: application/json` | **不支持** `multipart/form-data` |

> 唯一的"外部约束"是**网络与限频**：同一 IP 每分钟请求数有限，超出返回 HTTP 429，需要退避重试。

### 4.2 提交请求字段（`POST /api/v1/agent/parse/url`）

| 字段 | 类型 | 必填性 | 默认值 | 说明 |
| --- | --- | --- | --- | --- |
| `url` | string | **URL 模式必填** | — | 远程文件 URL；支持 PDF、图片、Doc/Docx、PPT/PPTx、Xlsx；**不支持 HTML** |
| `file_name` | string | 可选 | 从 URL 自动解析 | 文件名（含扩展名），用于判断文件类型；不确定类型时**建议显式提供** |
| `language` | string | 可选 | `ch` | 解析语言，影响 OCR 效果；**仅对 PDF 生效** |
| `enable_table` | bool | 可选 | `true` | 是否开启表格识别；**仅对 PDF 生效** |
| `is_ocr` | bool | 可选 | `false` | 是否开启 OCR；**仅对 PDF 生效** |
| `enable_formula` | bool | 可选 | `true` | 是否开启公式识别；**仅对 PDF 生效** |
| `page_range` | string | 可选 | — | 页码范围；**仅对 PDF 生效**；支持 `1-10` 或 `5`，**不支持逗号分隔的复杂格式** |

**文件上传模式**（`POST /api/v1/agent/parse/file`）的必填字段为 `file_name`，其余相同；返回的 `file_url` 用 `PUT` 上传，之后无需再调提交接口。

### 4.3 查询请求字段（`GET /api/v1/agent/parse/{task_id}`）

| 字段 | 位置 | 必填性 | 说明 |
| --- | --- | --- | --- |
| `task_id` | URL 路径 | **必填** | 提交响应返回的 `data.task_id` |

### 4.4 MVP 的"必须 vs 建议"清单

| 级别 | 项 | 理由 |
| --- | --- | --- |
| **必须** | `Content-Type: application/json` | 不支持 multipart |
| **必须** | `url`（或 `file_name`） | 无此字段无法提交 |
| **必须** | 轮询 `task_id` 直到 `state=done` | 异步接口，不轮询拿不到结果 |
| **建议** | 显式传 `file_name` | 避免后端误判类型 |
| **建议** | 传 `page_range` 控制页数 | 规避 20 页上限导致的 `-30003` |
| **建议** | 限制文件不超过 10 MB | 超出触发 `-30001` |
| **建议** | 轮询间隔不少于 3 秒并设总超时 | 避免触发 IP 限频（HTTP 429） |
| 可选 | `language` | 中文与英文混合文档用默认 `ch` 即可 |
| 可选 | `is_ocr=true` | 仅扫描件需要；会显著变慢 |

### 4.5 最小可运行示例

PowerShell（URL 模式）：

```powershell
$body = @{ url = 'https://cdn-mineru.openxlab.org.cn/demo/example.pdf'; file_name = 'example.pdf' } | ConvertTo-Json
$r = Invoke-RestMethod -Uri 'https://mineru.net/api/v1/agent/parse/url' -Method Post -ContentType 'application/json' -Body $body
$taskId = $r.data.task_id

do {
  Start-Sleep -Seconds 5
  $q = Invoke-RestMethod -Uri "https://mineru.net/api/v1/agent/parse/$taskId"
} while ($q.data.state -in @('waiting-file','uploading','pending','running'))

$md = (Invoke-WebRequest -Uri $q.data.markdown_url -UseBasicParsing).Content
$md | Set-Content -Encoding UTF8 .\output.md
```

Python（URL 模式）：

```python
import requests, time

BASE = "https://mineru.net/api/v1/agent"

resp = requests.post(
    f"{BASE}/parse/url",
    json={
        "url": "https://cdn-mineru.openxlab.org.cn/demo/example.pdf",
        "file_name": "example.pdf",
        "language": "ch",
        "enable_table": True,
        "enable_formula": True,
        "is_ocr": False,
    },
    timeout=30,
)
resp.raise_for_status()
task_id = resp.json()["data"]["task_id"]

while True:
    time.sleep(5)
    data = requests.get(f"{BASE}/parse/{task_id}", timeout=30).json()["data"]
    if data["state"] == "done":
        markdown = requests.get(data["markdown_url"], timeout=60).text
        break
    if data["state"] == "failed":
        raise RuntimeError(f"{data.get('err_code')}: {data.get('err_msg')}")
```

### 4.6 错误码与处置

| 错误码 | 含义 | 处置 |
| --- | --- | --- |
| `-30001` | 文件大小超出轻量接口限制（10 MB） | 拆分文件，或改用精准解析 API / 自部署 |
| `-30002` | 轻量接口不支持该文件类型 | 只上传 PDF / 图片 / Docx / PPTx / Xlsx |
| `-30003` | 文件页数超出轻量接口限制（20 页） | 用 `page_range` 分批，或改用精准解析 API |
| `-30004` | 请求参数错误 | 检查必填参数是否缺失、`Content-Type` 是否正确 |
| HTTP 429 | 触发 IP 限频 | 指数退避重试，降低提交频率 |

---

## 附录 A：来源与实测记录

### A.1 文档来源

| 内容 | 来源 |
| --- | --- |
| Agent 轻量 API 端点、请求与响应字段、错误码、限制 | `https://mineru.net/apiManage/docs` |
| 精准解析 API 限制与 ZIP 内容映射 | `https://mineru.net/apiManage/docs` |
| 输出文件语义、渲染目标、图片命名、块模型 | `https://opendatalab.github.io/MinerU/reference/output_files/` |
| 自部署输入格式、`page_idx` 从 0 开始 | `https://opendatalab.github.io/MinerU/usage/quick_usage/` |

### A.2 实测记录（2026-10-15）

| 项 | 值 |
| --- | --- |
| 样例文件 | `https://cdn-mineru.openxlab.org.cn/demo/example.pdf`（官方 demo，英文学术论文） |
| 提交方式 | `POST https://mineru.net/api/v1/agent/parse/url`，仅传 `url` |
| 返回 `task_id` | `506edf95-fd47-4852-b8ad-3e75fcc014bb10` |
| 状态流转 | `running` 到 `running` 到 `done`（轮询间隔 5 秒，第 3 次即完成，约 10 秒） |
| `markdown_url` | `https://cdn-mineru.openxlab.org.cn/pdf/2026-10-15/a5a09b12-06eb-4661-89a2-e5648db06257/full.md` |
| 产物大小 | 50,727 字符 / 257 行 |
| 结构统计 | `#` 1 个、`##` 17 个、`###` 0 个、HTML 表格 5 个（开闭平衡）、`$$` 行间公式块 7 个、`$` 行内公式定界符 128 处、`<!-- image-->` 占位 8 个、`Fig. N` 题注 5 条、`Table N` 题注 1 条 |
| 页码信息 | 全文未出现 `page N` 形式的页码标记 |
| 图片文件 | 未返回任何图片文件 |

---

## 附录 B：术语表

| 术语 | 含义 |
| --- | --- |
| **Agent 轻量解析 API** | MinerU 提供的免登录解析接口（`/api/v1/agent/*`），限制 10 MB / 20 页，仅输出 Markdown |
| **精准解析 API** | 需 Token 的解析接口（`/api/v4/extract/*`），限制 200 MB / 200 页，输出 ZIP（含 Markdown 与多份 JSON） |
| **`full.md`** | 解析产物的 Markdown 全文；Agent API 下是唯一产物，精准解析 API 下是 ZIP 的成员之一 |
| **`content_list.json`** | 内容列表，按阅读顺序排列的内容块数组，含块类型与页索引 |
| **`middle.json`** | 中间处理结果，含页级结构与块级边界框等版面信息 |
| **`page_idx`** | 页索引，从 0 开始 |
| **`bbox`** | 边界框，归一化坐标 `[x0, y0, x1, y1]` |
| **`continues_prev`** | 跨页续接标记，表示该块承接上一页内容 |
| **弱结构信号** | Agent API 方案下 Markdown 中可用的文本级结构（标题层级、HTML 表格、LaTeX 公式、图片占位），不含坐标 |

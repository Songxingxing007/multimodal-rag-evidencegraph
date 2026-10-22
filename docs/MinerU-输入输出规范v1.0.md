# MinerU 输入输出规范 v1.0（实测校准版）

> **文档性质**：本版基于**本地真实执行的 Pipeline 与产出的文件**编写，并逐项与 MinerU 官方文档比对。
> 凡官方文档与实地产物冲突处，**一律以实地产物为准**，冲突记录见第 2 节。
>
> **实测日期**：2026-10-18
> **实测样本**：`mineru-mvp/samples/sample_report.pdf`（自建 2 页 A4，含中文段落与两张数值表）
> **官方来源**：`https://mineru.net/apiManage/docs`（云端 API）
> **环境隔离**：`mineru-mvp/.venv`（uv 管理，Python 3.12.14）
> **上一版**：[MinerU-输入输出规范.md](MinerU-输入输出规范.md)（实测前的初版）

## 目录

- [0. 版本口径与关键结论](#0-版本口径与关键结论)
- [1. 完整 Pipeline 执行思路与脚本位置](#1-完整-pipeline-执行思路与脚本位置)
- [2. 实际产物 vs 官方文档：冲突清单与裁决](#2-实际产物-vs-官方文档冲突清单与裁决)
- [3. Pipeline 关键参数规范](#3-pipeline-关键参数规范)
- [4. 本地实际关键文件规范](#4-本地实际关键文件规范)
- [附录 A：实测记录](#附录-a实测记录)
- [附录 B：术语表](#附录-b术语表)

---

## 0. 版本口径与关键结论

### 0.1 一个重要发现：云端 API 跑的是 3.4.x，不是 4.0

精准模式产出的 `layout.json` 中存在版本标识：

| 字段 | 实测值 |
| --- | --- |
| `_backend` | `pipeline` |
| `_version_name` | **`3.4.4`** |

而 `opendatalab.github.io/MinerU` 文档站当前描述的是 **4.0** 的产物契约（`markdown.md`、`middle_json.json`、`structured_content.json`、`model_output.json`）。

**结论：云端 `/api/v4/extract/*` 端点返回的是 MinerU 3.4.4 的产物结构。接入云端 API 时，请使用本文第 4 节的 3.x 文件命名，不要套用文档站 4.0 的命名。**

### 0.2 五条必须记住的结论

1. Agent 轻量 API **只返回一个 `full.md`**，没有 ZIP、JSON、图片。
2. 精准 API 的 ZIP 实际有 **7 个成员**，比官方列出的多出 `*_content_list_v2.json` 与 `*_origin.pdf`。
3. ZIP 中**没有 `*_middle.json`**；中间结果文件名叫 `layout.json`（官方"layout.json 对应 middle.json"的说法成立，但文件名是 `layout.json`）。
4. 精准模式的 `full.md` **可能缺页**：被判定为 `header` 的块会被渲染器隐藏。实测 `page_idx=1`（第 2 页）的 7 个块全部被标成 `header`，`full.md` 因此只剩第 1 页。
5. 三种 JSON 的 `bbox` **不在同一坐标系**：`content_list` 是 0–1000 归一化，`layout.json` 是 PDF 点，`model.json` 是像素。混用会算错位置。

---

## 1. 完整 Pipeline 执行思路与脚本位置

### 1.1 整体架构

```
本地 PDF
   │
   ├─ ① 读 .env（配置 + Token）
   ├─ ② 预检：存在性 / 大小 / 页数 / 文本层
   ├─ ③ 建运行目录 output/<UTC时间>_<文件名>/
   │
   ├─ ④ 申请上传链接（签名上传）
   │      precise: POST /api/v4/file-urls/batch
   │      agent  : POST /api/v1/agent/parse/file
   ├─ ⑤ PUT 原始字节到签名 URL
   ├─ ⑥ 轮询任务状态
   │      precise: GET /api/v4/extract-results/batch/{batch_id}
   │      agent  : GET /api/v1/agent/parse/{task_id}
   ├─ ⑦ 下载产物
   │      precise: full_zip_url → source.zip → 安全解压
   │      agent  : markdown_url → full.md
   │
   ├─ ⑧ 归一化 full.md 到运行目录根部
   ├─ ⑨ precise：由 content_list.json 重建 content_list.md（保住 header 块）
   ├─ ⑩ 写 response.json / summary.json / meta.json
   └─ ⑪ 控制台输出报告与告警
```

### 1.2 逐步说明

| 步骤 | 动作 | 实现要点 |
| --- | --- | --- |
| ① | 加载配置 | `load_dotenv(ROOT/.env, override=True)`；所有密钥只来自 `.env` |
| ② | 预检 | 文件不存在直接失败；按模式校验大小与页数；PDF 用 `pypdf` 统计页数与文本层字符数 |
| ③ | 建目录 | `output/20261017T180436Z_sample_report/`，同名不覆盖（`exist_ok=False`） |
| ④ | 申请上传 | 只传文件名与解析参数，**不上传文件本体** |
| ⑤ | 上传 | `PUT` 原始字节；不设置 `Content-Type` |
| ⑥ | 轮询 | 间隔 5 s、总超时 300 s；HTTP 429 指数退避（上限 60 s） |
| ⑦ | 下载 | stream 下载；ZIP 解压前校验每个成员路径（防目录穿越） |
| ⑧ | 归一化 | 递归查找 `*.md`，优先 `full.md`，复制到运行目录根部 |
| ⑨ | 补全 | 遍历 `content_list.json` 的全部块，按顺序重建 `content_list.md`，**不丢弃 header** |
| ⑩ | 落盘 | `response.json` 保存 submit/poll/result 原始响应；`summary.json` 统计；`meta.json` 记录任务与产物清单 |
| ⑪ | 报告 | 打印耗时、字符数、表格行列、块类型分布；若 `full.md` 短于 `content_list.md` 则告警 |

### 1.3 双模式差异

| 维度 | 精准模式（precise） | Agent 轻量模式 |
| --- | --- | --- |
| Token | **必须** | **不需要** |
| 提交端点 | `/api/v4/file-urls/batch` | `/api/v1/agent/parse/file` |
| 轮询端点 | `/api/v4/extract-results/batch/{batch_id}` | `/api/v1/agent/parse/{task_id}` |
| 完成字段 | `extract_result[].full_zip_url` | `data.markdown_url` |
| 产物 | 7 个 ZIP 成员 | 单个 Markdown |
| 大小 / 页数 | 200 MB / 200 页 | 10 MB / 20 页 |
| 版面信息 | 有（JSON + bbox） | 无 |

### 1.4 实际测试脚本位置

根目录：`mineru-mvp/`

| 文件 | 作用 |
| --- | --- |
| `pipeline.py` | 主流程编排与 CLI |
| `mineru_client.py` | 双模式 API 客户端、轮询、429 退避、Token 脱敏 |
| `summarize.py` | 统计 summary；由 content_list 重建 Markdown |
| `make_sample_pdf.py` | 生成测试 PDF 并自检 |
| `.venv/` | **独立 uv 虚拟环境（Python 3.12.14）**，所有脚本必须在此运行 |
| `AGENTS.md` | 本目录的环境隔离与启动规则（Agent 必读） |
| `.env` | 全部配置（含真实 Token，已 gitignore） |
| `.env.example` | 脱敏模板 |
| `README.md` | 运行说明与实测结论 |
| `requirements.txt` | requests / python-dotenv / pypdf / reportlab |
| `samples/sample_report.pdf` | 34,981 字节，2 页，文本层 1,380 字符 |
| `output/<run_id>/` | 每次运行的产物 |

### 1.5 复跑命令

```powershell
cd mineru-mvp
.\.venv\Scripts\python.exe make_sample_pdf.py
.\.venv\Scripts\python.exe pipeline.py                 # 默认 precise（读 .env）
.\.venv\Scripts\python.exe pipeline.py --mode agent    # 免 Token 模式
.\.venv\Scripts\python.exe pipeline.py --file .\samples\sample_report.pdf
```

### 1.6 虚拟环境与启动说明

本组件与 LangExtract、其它 RAG 组件**共用同一台机器但使用独立环境**，避免依赖互相污染。

#### 1.6.1 环境规格

| 项 | 值 |
| --- | --- |
| 位置 | `mineru-mvp/.venv/` |
| 管理工具 | `uv` |
| Python | 3.12.14（MinerU 支持区间 3.10–3.15 内） |
| 依赖 | `requests 2.34.2`、`python-dotenv 1.2.4`、`pypdf 6.21.1`、`reportlab 5.0.1` |
| 隔离边界 | 仅作用于 `mineru-mvp/` 子树，不影响全局与其它项目 |

#### 1.6.2 启动前必须切换到子虚拟环境

Agent 与自动化脚本**一律使用解释器绝对路径**，不依赖 shell 激活状态：

```powershell
cd mineru-mvp
.\.venv\Scripts\python.exe pipeline.py --mode agent
```

人工交互终端可改为激活方式：

```powershell
cd mineru-mvp
.\.venv\Scripts\Activate.ps1
python pipeline.py
```

#### 1.6.3 启动前自检

```powershell
.\.venv\Scripts\python.exe -c "import sys; print(sys.executable)"
```

输出必须包含 `mineru-mvp\.venv\Scripts\python.exe`；否则停止执行。

#### 1.6.4 环境缺失时的重建

```powershell
cd mineru-mvp
uv venv .venv --python 3.12
uv pip install -r requirements.txt
```

#### 1.6.5 禁止事项

- 禁止使用全局 `python` / `py` 运行本目录脚本；
- 禁止把本目录依赖装到全局或其它项目环境；
- 禁止 `pip install --user`；
- 禁止在未确认解释器路径时批量执行。

> 完整规则见 `mineru-mvp/AGENTS.md`（适用于该目录及其全部子目录）。

---

## 2. 实际产物 vs 官方文档：冲突清单与裁决

### 2.1 冲突总表

| # | 官方文档说法 | 本地实测 | 裁决 |
| --- | --- | --- | --- |
| 1 | Agent API 仅输出 Markdown（`markdown_url`） | 运行目录只有 `full.md` + 3 个本地元数据文件 | **一致**，无冲突 |
| 2 | 精准 API 的 ZIP 含 `full.md`、`*_content_list.json`、`*_middle.json`、`*_model.json`、`images/` | ZIP 实有 7 项：`*_content_list.json`、`*_content_list_v2.json`、`*_model.json`、`full.md`、`layout.json`、`*_origin.pdf`、`images/*.jpg` | **以实测为准**：无 `*_middle.json`；多出 `_v2` 与 `_origin.pdf` |
| 3 | "layout.json 对应中间处理结果（middle.json）" | 文件名就是 `layout.json`，顶层键 `pdf_info / _backend / _version_name` | **以实测为准**：按 `layout.json` 读取 |
| 4 | 文档站 4.0 说落盘 `markdown.md`、`middle_json.json`、`structured_content.json`、`model_output.json` | 云端 ZIP 是 3.4.4 结构，命名完全不同 | **以实测为准**：4.0 命名不适用于云端 API |
| 5 | 官方未说明 `full.md` 会丢块 | `page_idx=1` 的块全被判为 `header`，`full.md` 只覆盖第 1 页 | **以实测为准**：需读 `content_list.json` 兜底 |
| 6 | 官方未说明 `images/` 何时产生 | 源 PDF 无插图，仍生成 1 个 JPEG（36,649 字节） | **以实测为准**：`images/` 不能当作"有插图"的判据 |
| 7 | 官方未给出 bbox 坐标制式 | `content_list` 0–1000 归一化；`layout.json` PDF 点；`model.json` 像素 | **以实测为准**：见 §4.5 |

### 2.2 版本错配的根因

```
官方文档站 (opendatalab.github.io/MinerU)  → 描述 MinerU 4.0
云端 API (/api/v4/extract/*)              → 实际返回 MinerU 3.4.4 产物
                      ↑
        layout.json._version_name = "3.4.4"
```

这不是"文档写错了"，而是**文档站已升级到 4.0，云端 API 仍跑 3.4.x**。因此：

- 阅读 4.0 文档用于理解**概念**（块模型、渲染目标）是可靠的；
- 但**文件命名与字段结构**必须按本文第 4 节的实测结果。

### 2.3 `full.md` 缺块的实测证据

精准模式（`model_version=pipeline`）的 `content_list.json` 共 17 个块：

| 块类型 | 数量 | 页分布（page_idx） |
| --- | --- | --- |
| `text` | 9 | 全部在 `0`（第 1 页） |
| `table` | 1 | `0`（第 1 页） |
| `header` | 7 | **全部在 `1`（第 2 页）** |

`page_idx=1` 的 7 个 header 块内容依次是：`3. Monthly Results`、月度说明段、月度表格文本、`Table 2` 题注、`4. Conclusion`、结论段、页脚说明。

而 MinerU 的 Markdown 渲染默认隐藏 `header` / `footer` / `page_number` 块，于是：

| 文件 | 字符数 | 覆盖范围 |
| --- | --- | --- |
| `full.md` | 1,569 | 仅 `page_idx=0` |
| `content_list.md`（Pipeline 重建） | 2,155 | `page_idx=0` + `page_idx=1` |

`content_list.md` 中已确认包含 `Table 2`、`0.471`、`12.5 percent`、`4. Conclusion`。

> **工程结论**：需要完整正文时，精准模式应优先消费 `content_list.json` 或 `content_list.md`；`full.md` 只能作为"渲染视图"。

### 2.4 两种 `model_version` 的实测差异

| model_version | task/batch id | 耗时 | `full.md` 字符 | ZIP 字节 | 是否缺第 2 页 |
| --- | --- | --- | --- | --- | --- |
| `vlm` | `857bb97c-c575-4525-bce0-acd81c479cdf` | 11.43 s | 1,069 | 66,073 | 是 |
| `pipeline` | `566bfe6b-1e58-4d61-9605-6f8d197a7dfc` | 11.26 s | 1,569 | 66,306 | 是 |
| `pipeline`（最终） | `ce9ee282-1b09-49ad-bb4f-0a2a75302db8` | 6.18 s | 1,569 | 66,305 | 是（已由 content_list.md 兜底） |

差异主要在表格 HTML 的写法：`pipeline` 会输出 `rowspan=1 colspan=1` 这类冗余属性，`vlm` 更简洁。两者都出现第 2 页被标为 header 的问题。

---

## 3. Pipeline 关键参数规范

### 3.1 三层参数模型

```
① .env              → 本地运行参数（模式、Token、轮询、路径）
② API 请求体         → 解析参数（语言、表格、公式、页码、模型版本）
③ API 响应体         → 任务状态与下载地址
```

三层不要混：`.env` 里的 `MINERU_*` 并非 MinerU 官方字段，而是本 Pipeline 的封装命名。

### 3.2 `.env` 参数（本地层）

| 参数 | 类型 | 默认 | 必填 | 实测行为 |
| --- | --- | --- | --- | --- |
| `MINERU_API_MODE` | `precise \| agent` | `precise` | 是 | 决定端点与产物形态 |
| `MINERU_TOKEN` | string | 空 | precise 必填 | 仅 precise 使用；agent 模式明确忽略 |
| `MINERU_BASE_URL` | url | `https://mineru.net` | 是 | 所有端点前缀 |
| `MINERU_MODEL_VERSION` | `pipeline \| vlm` | `pipeline` | 仅 precise | 实测两者都会丢 header 页 |
| `MINERU_LANGUAGE` | string | `ch` | 否 | 仅 PDF 生效 |
| `MINERU_ENABLE_TABLE` | bool | `true` | 否 | 关闭后表格会退化为纯文本 |
| `MINERU_ENABLE_FORMULA` | bool | `true` | 否 | 公式以 LaTeX 输出 |
| `MINERU_IS_OCR` | bool | `false` | 否 | 扫描件需设 `true` |
| `MINERU_PAGE_RANGE` | string | 空 | 否 | **两套格式不同**，见 §3.5 |
| `MINERU_POLL_INTERVAL_SEC` | float | `5` | 否 | 实测 2 页文档 1–3 次轮询完成 |
| `MINERU_POLL_TIMEOUT_SEC` | float | `300` | 否 | 超时立即失败，不取消远端任务 |
| `MINERU_OUTPUT_DIR` | path | `output` | 否 | 相对脚本目录 |
| `MINERU_SAMPLE_PDF` | path | `samples/sample_report.pdf` | 否 | CLI `--file` 可覆盖 |

### 3.3 精准 API 请求字段（`POST /api/v4/file-urls/batch`）

| 字段 | 类型 | 必填 | 本项目取值 | 说明 |
| --- | --- | --- | --- | --- |
| `files` | array | **是** | `[{name}]` | 每项含 `name`（必须带后缀） |
| `files[].name` | string | **是** | `sample_report.pdf` | 服务端据此判断类型 |
| `files[].data_id` | string | 否 | 未使用 | 业务侧唯一 ID |
| `files[].is_ocr` | bool | 否 | 未使用 | 文件级 OCR 开关 |
| `files[].page_ranges` | string | 否 | 空 | 支持 `"2,4-6"`、`"2--2"` |
| `enable_formula` | bool | 否 | `true` | 顶层参数 |
| `enable_table` | bool | 否 | `true` | 顶层参数 |
| `language` | string | 否 | `ch` | 默认 `ch` |
| `model_version` | string | 否 | `pipeline` | `pipeline` / `vlm` / `MinerU-HTML` |

**响应**：`data.batch_id`、`data.file_urls[]`。

### 3.4 Agent API 请求字段（`POST /api/v1/agent/parse/file`）

| 字段 | 类型 | 必填 | 本项目取值 | 说明 |
| --- | --- | --- | --- | --- |
| `file_name` | string | **是** | `sample_report.pdf` | 用于判断类型 |
| `language` | string | 否 | `ch` | **仅 PDF** |
| `enable_table` | bool | 否 | `true` | **仅 PDF** |
| `enable_formula` | bool | 否 | `true` | **仅 PDF** |
| `is_ocr` | bool | 否 | `false` | **仅 PDF** |
| `page_range` | string | 否 | 空 | `"1-10"` 或 `"5"`，**不支持逗号** |

**响应**：`data.task_id`、`data.file_url`。

### 3.5 两套字段的关键差异

| 差异点 | 精准 API | Agent API | 踩坑后果 |
| --- | --- | --- | --- |
| 页码字段名 | `files[].page_ranges` | `page_range` | 写错会被忽略或不报错 |
| 页码格式 | `"2,4-6"`、`"2--2"` | `"1-10"`、`"5"` | Agent 传逗号会参数错误 |
| OCR 字段位置 | `files[].is_ocr`（文件级） | `is_ocr`（顶层） | 放错层级不生效 |
| `model_version` | 支持 | **不支持** | Agent 传了也无意义 |
| 表格 / 公式 | 顶层 | 顶层 | 一致 |
| Token | 必需 | 禁止依赖 | Agent 无需 `Authorization` |

### 3.6 官方存在但本项目未使用的字段

这些字段在文档中存在，MVP 未发送；接入生产时按需启用：

| 字段 | 所属 | 用途 | 注意 |
| --- | --- | --- | --- |
| `data_id` | 两种 | 业务侧唯一标识 | 会原样回传，便于对账 |
| `callback` + `seed` | 精准 | 异步回调 | 需实现校验（uid+seed+content 的 SHA256） |
| `extra_formats` | 精准 | 额外导出 docx/html/latex | 对 HTML 源无效 |
| `no_cache` / `cache_tolerance` | 精准 | 缓存控制 | 默认缓存 900 s |

### 3.7 轮询与状态机

| 模式 | 状态字段 | 状态值 |
| --- | --- | --- |
| 精准 | `extract_result[].state` | `waiting-file` / `pending` / `running` / `converting` / `done` / `failed` |
| Agent | `data.state` | `waiting-file` / `uploading` / `pending` / `running` / `done` / `failed` |

| 参数 | 规范 |
| --- | --- |
| 轮询间隔 | 5 s（实测 2 页文档 1–3 次） |
| 总超时 | 300 s |
| 429 处理 | 指数退避 5 → 10 → 20 → 40 → 60 s 封顶 |
| `failed` 处理 | 输出 `err_code` + `err_msg`，退出码 2 |

### 3.8 预检阈值（本地拦截）

| 模式 | 大小 | 页数 |
| --- | --- | --- |
| precise | 200 MB | 200 |
| agent | 10 MB | 20 |

超限在本地直接报错，避免把文件传上去才失败。

---

## 4. 本地实际关键文件规范

> 本节**以本地实际产出的数量与类型为准**，不采用官方文档的列举。

### 4.1 精准模式 ZIP 实际成员（7 个）

实测 ZIP：`source.zip`，66,305 字节。

| # | 文件名（实际） | 字节 | 类型 | 内容 |
| --- | --- | --- | --- | --- |
| 1 | `*_content_list.json` | 5,460 | JSON 数组 | 内容列表 V1，17 个块 |
| 2 | `*_content_list_v2.json` | 9,286 | JSON 数组（二维） | 内容列表 V2，按页分组，10 + 7 个块 |
| 3 | `*_model.json` | 16,570 | JSON 数组 | 模型推理结果，2 页，每页含 `layout_dets` 与 `page_info` |
| 4 | `full.md` | 1,665 | Markdown | 渲染视图（**可能缺块**） |
| 5 | `layout.json` | 56,695 | JSON 对象 | 中间处理结果（官方所称 middle.json），2 页 |
| 6 | `*_origin.pdf` | 34,622 | PDF | 源文件副本 |
| 7 | `images/*.jpg` | 36,649 | JPEG | 图像裁片（源文档无插图也出现了 1 个） |

**与官方列举的差异**：

- 官方列出 5 类，实际 7 项；
- 实际**没有** `*_middle.json`；
- 实际**多出** `*_content_list_v2.json`、`*_origin.pdf`。

### 4.2 精准模式运行目录（12 个文件）

ZIP 解压后加上 Pipeline 自产文件，运行目录共 **12 个文件**：

| # | 文件 | 来源 | 说明 |
| --- | --- | --- | --- |
| 1 | `*_content_list.json` | ZIP | 内容列表 V1 |
| 2 | `*_content_list_v2.json` | ZIP | 内容列表 V2 |
| 3 | `*_model.json` | ZIP | 模型结果 |
| 4 | `*_origin.pdf` | ZIP | 源 PDF 副本 |
| 5 | `full.md` | ZIP | MinerU 渲染视图 |
| 6 | `layout.json` | ZIP | 中间结果 |
| 7 | `images/*.jpg` | ZIP | 图像资源 |
| 8 | `content_list.md` | **Pipeline 生成** | 由 content_list 重建，保留 header 块 |
| 9 | `meta.json` | **Pipeline 生成** | 运行元数据 |
| 10 | `response.json` | **Pipeline 生成** | 原始接口响应 |
| 11 | `summary.json` | **Pipeline 生成** | 统计摘要 |
| 12 | `source.zip` | **Pipeline 生成** | 原始下载包 |

### 4.3 Agent 模式产物（4 个文件）

实测运行目录：

| # | 文件 | 字节 | 说明 |
| --- | --- | --- | --- |
| 1 | `full.md` | 2,704 | 唯一解析产物，2,608 字符 |
| 2 | `meta.json` | 688 | 运行元数据 |
| 3 | `response.json` | 990 | 原始接口响应 |
| 4 | `summary.json` | 1,798 | 统计摘要 |

Agent 模式**没有** `content_list.md`、`source.zip`、`images/`、任何 JSON 解析产物。

### 4.4 各 JSON 的实际字段结构

#### 4.4.1 `*_content_list.json`（V1）

顶层是**数组**，共 17 个元素；每个元素字段：

| 字段 | 类型 | 实测示例 | 说明 |
| --- | --- | --- | --- |
| `type` | string | `text` / `table` / `header` | 块类型；实测仅出现这三种 |
| `text` | string | `Annual Flow and Rainfall Summary` | 文本内容（表格块无此字段） |
| `text_level` | int | `1` / `2` | 标题层级；仅标题块有 |
| `bbox` | int[4] | `228,68,769,95` | 0–1000 归一化坐标 |
| `page_idx` | int | `0` / `1` | 页索引，**从 0 开始** |

`table` 块额外字段：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `img_path` | string | 表格图像相对路径 |
| `table_caption` | list | 表格题注 |
| `table_footnote` | list | 表注 |
| `table_body` | string | 表格 HTML |

#### 4.4.2 `*_content_list_v2.json`

顶层是**二维数组**：外层为页，内层为该页的块。

| 项 | 实测值 |
| --- | --- |
| 外层长度 | 2（即 2 页） |
| 第 0 页块数 | 10 |
| 第 1 页块数 | 7 |

单个块字段：`type`、`content`、`bbox`。

- `type` 取值为 `title` / `paragraph` / `table` 等；
- `content` 是**对象**（如 `title_content`、`paragraph_content`），不是字符串；
- **没有 `page_idx`**，页号只能用外层下标。

#### 4.4.3 `layout.json`

顶层字段：

| 字段 | 实测值 | 说明 |
| --- | --- | --- |
| `pdf_info` | 数组，长度 2 | 每页一项 |
| `_backend` | `pipeline` | 后端标识 |
| `_version_name` | `3.4.4` | **服务端解析器版本** |

`pdf_info[]` 字段：

| 字段 | 说明 |
| --- | --- |
| `preproc_blocks` | 预处理块 |
| `page_idx` | 页索引（0 基） |
| `page_size` | `[595, 841]`（A4，单位 PDF 点） |
| `discarded_blocks` | 被丢弃的块（页眉/页脚等） |
| `para_blocks` | 段落块，含 `score` / `bbox` / `index` / `type` / `lines` / `level` |

#### 4.4.4 `*_model.json`

顶层是**数组**，长度 2（每页一项）；每项字段：

| 字段 | 说明 |
| --- | --- |
| `layout_dets` | 布局检测结果（第 0 页 24 项） |
| `page_info` | `{page_no, width, height}`，实测 `1654 × 2339`（A4 @200dpi） |

`layout_dets[]` 字段：`cls_id`、`label`、`score`、`bbox`、`index`。实测示例：

```json
{"cls_id": 6, "label": "doc_title", "score": 0.9241, "bbox": [379, 163, 1273, 223], "index": 1}
```

#### 4.4.5 `response.json`（Pipeline 自产）

| 字段 | 说明 |
| --- | --- |
| `submit` | 提交响应的完整 JSON |
| `poll` | 最后一次轮询响应的完整 JSON |
| `result` | 任务完成条目（精准为 `extract_result[0]`；Agent 为 `data`） |

#### 4.4.6 `meta.json` / `summary.json`（Pipeline 自产）

`meta.json` 字段：`source_file`、`mode`、`model_version`、`task_id`、`task_id_kind`、`state`、`started_at`、`finished_at`、`elapsed_sec`、`file_size_bytes`、`page_count`、`text_chars`、`token`（脱敏）、`content_list_blocks`、`content_list_by_type`、`recovered_markdown_chars`、`artifacts`。

`summary.json` 字段：`source_file`、`mode`、`task_id`、`state`、`elapsed_sec`、`markdown_chars`、`headings{h1,h2,h3,deeper}`、`tables[{caption,rows,cols,cells}]`、`images`、`formulas{display,inline}`、`pages`。

### 4.5 bbox 坐标系（三套，必须区分）

这是精准提取数值时最容易踩的坑。

| 文件 | bbox 制式 | 实测证据 | 换算方式 |
| --- | --- | --- | --- |
| `*_content_list.json` | **0–1000 归一化** | `[100,101,447,116]` × 595/1000 ≈ `[60,85,266,98]` | `x_pdf = x / 1000 * page_width` |
| `*_content_list_v2.json` | **0–1000 归一化**（与 V1 同值） | 同一块 bbox 与 V1 完全一致 | 同上 |
| `layout.json` | **PDF 点**（A4 595×841） | `page_size=[595,841]`，`max_x=533` | 直接使用 |
| `*_model.json` | **像素**（1654×2339 @200dpi） | `page_info={width:1654,height:2339}` | `x_pdf = x / 1654 * 595` |

**验证样例**：`content_list` 的 `[100,101,447,116]` 换算后为 `[59.5, 84.9, 266.0, 97.6]`，与 `layout.json` 中同块的 `[60,85,266,98]` 吻合（误差来自取整）。

### 4.6 关键统计对照

| 指标 | Agent 模式 | 精准模式 |
| --- | --- | --- |
| `full.md` 字节 / 字符 | 2,704 / 2,608 | 1,665 / 1,569 |
| 标题计数 | h1=1, h2=4 | h1=1, h2=2 |
| `full.md` 中表格 | **2**（5×5、4×4） | **1**（5×5） |
| `content_list.md` | 无 | 2,286 字节 / 2,155 字符 |
| 页数 | 无（null） | 2 |
| 耗时 | 11.21 s | 6.18 s |
| 任务 ID | `05033c31-ef48-4cab-8e6e-1be81e3dac2d10` | `ce9ee282-1b09-49ad-bb4f-0a2a75302db8` |
| 运行目录 | `output/20261017T180452Z_sample_report/` | `output/20261017T180436Z_sample_report/` |

> 有意思的是：**Agent 模式的 Markdown 反而比精准模式完整**（2 张表 vs 1 张表），因为 Agent 路径不走 header/footer 过滤。需要"完整文本"时，Agent 模式是被低估的选项。

---

## 附录 A：实测记录

### A.1 环境

| 项 | 值 |
| --- | --- |
| OS | Windows（PowerShell） |
| 全局 Python | 3.14.5（**不用于本项目脚本**） |
| 子虚拟环境 | `.venv`，Python 3.12.14，uv 管理 |
| 依赖 | requests / python-dotenv / pypdf / reportlab |
| 测试样本 | 自建 `sample_report.pdf`，34,981 字节，2 页，1,380 字符文本层 |

### A.2 运行记录

| # | 模式 | model_version | 任务 ID | 耗时 | `full.md` 字符 |
| --- | --- | --- | --- | --- | --- |
| 1 | agent | agent-light | `fbc4c17f-9af2-4402-bd08-d62d179e55a110` | 6.17 s | 2,608 |
| 2 | precise | vlm | `857bb97c-c575-4525-bce0-acd81c479cdf` | 11.43 s | 1,069 |
| 3 | precise | pipeline | `566bfe6b-1e58-4d61-9605-6f8d197a7dfc` | 11.26 s | 1,569 |
| 4 | precise | pipeline | `ce9ee282-1b09-49ad-bb4f-0a2a75302db8` | 6.18 s | 1,569 |
| 5 | agent | agent-light | `05033c31-ef48-4cab-8e6e-1be81e3dac2d10` | 11.21 s | 2,608 |

### A.3 安全校验

| 检查项 | 结果 |
| --- | --- |
| 产物中是否出现完整 Token | 0 次 |
| `meta.json` 的 Token 字段 | `sk-****vgVZ` |
| `.env.example` 是否含密钥 | 否 |
| `.env` 是否被 gitignore | 是 |
| 不存在的输入文件 | 退出码 2，报 `input file not found` |

---

## 附录 B：术语表

| 术语 | 含义 |
| --- | --- |
| **子虚拟环境** | `mineru-mvp/.venv`，隔离本组件依赖，所有脚本必须在此运行 |
| **签名上传** | 先用元数据换取一个临时 OSS URL，再 `PUT` 文件本体 |
| **`full.md`** | MinerU 的渲染视图；可能隐藏 `header` / `footer` 块 |
| **`content_list.json`** | 按阅读顺序排列的块列表（V1），含 `type` / `text` / `bbox` / `page_idx` |
| **`content_list_v2.json`** | 按页分组的块列表，块内容为对象，无 `page_idx` |
| **`layout.json`** | 中间处理结果（对应官方所称 `middle.json`），含 `pdf_info` 与 PDF 点坐标 |
| **`*_model.json`** | 模型推理结果，含 `layout_dets` 与像素坐标 |
| **`*_origin.pdf`** | 服务端保存的源文件副本 |
| **归一化 bbox** | 0–1000 坐标系，需按 `page_size/1000` 换算 |
| **`content_list.md`** | 本 Pipeline 由 content_list 重建的完整 Markdown（保留 header） |

from __future__ import annotations

import io
import json
import re
import threading
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import httpx
from pypdf import PdfReader

from .config import Settings
from .db import Database, new_id, utc_now
from .storage import LocalObjectStore


class PipelineError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class Block:
    block_id: str
    type: str
    text: str
    char_start: int
    char_end: int
    bbox: list[int] | None
    bbox_pdf: list[float] | None
    table_html: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "type": self.type,
            "text": self.text,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "bbox": self.bbox,
            "bbox_pdf": self.bbox_pdf,
            "table_html": self.table_html,
        }


@dataclass
class Page:
    page_idx: int
    width: float
    height: float
    text: str
    blocks: list[Block] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_idx": self.page_idx,
            "width": self.width,
            "height": self.height,
            "text": self.text,
            "blocks": [block.to_dict() for block in self.blocks],
        }


@dataclass
class ParsedDocument:
    pages: list[Page]
    parser_meta: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_bundle(self, document_id: str, run_id: str, source: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": "graph-ready-bundle/1.0",
            "document_id": document_id,
            "run_id": run_id,
            "source": source,
            "parser": self.parser_meta,
            "coordinate_system": "bbox_0_1000_by_page",
            "pages": [page.to_dict() for page in self.pages],
            "warnings": self.warnings,
        }


@dataclass
class ExtractionRecord:
    document_id: str
    page_idx: int
    extraction_class: str
    extraction_text: str
    char_interval: dict[str, int] | None
    alignment_status: str
    extraction_index: int
    group_index: int
    description: str | None
    attributes: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "page_idx": self.page_idx,
            "extraction_class": self.extraction_class,
            "extraction_text": self.extraction_text,
            "char_interval": self.char_interval,
            "alignment_status": self.alignment_status,
            "extraction_index": self.extraction_index,
            "group_index": self.group_index,
            "description": self.description,
            "attributes": self.attributes,
        }


def normalize_name(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def block_text(block: dict[str, Any]) -> str:
    kind = str(block.get("type") or "")
    if kind == "table":
        parts = []
        if block.get("table_body"):
            parts.append(str(block["table_body"]))
        for key in ("table_caption", "table_footnote"):
            for item in block.get(key) or []:
                parts.append(
                    str(item.get("content"))
                    if isinstance(item, dict)
                    else str(item)
                )
        return "\n".join(part for part in parts if part).strip()
    if kind in {"image", "chart"}:
        parts = []
        for key in ("image_caption", "image_footnote"):
            for item in block.get(key) or []:
                parts.append(
                    str(item.get("content"))
                    if isinstance(item, dict)
                    else str(item)
                )
        return "\n".join(part for part in parts if part).strip()
    return str(block.get("text") or "").strip()


def clean_bbox(value: Any) -> list[int] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        return [int(round(float(item))) for item in value]
    except (TypeError, ValueError):
        return None


def bbox_to_pdf(
    bbox: list[int] | None, width: float, height: float
) -> list[float] | None:
    if not bbox or width <= 0 or height <= 0:
        return None
    return [
        round(bbox[0] / 1000 * width, 2),
        round(bbox[1] / 1000 * height, 2),
        round(bbox[2] / 1000 * width, 2),
        round(bbox[3] / 1000 * height, 2),
    ]


class LocalPdfParser:
    name = "local_pdf"
    version = "1.0"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def parse(self, source: Path, run_dir: Path, source_info: dict[str, Any]) -> ParsedDocument:
        reader = PdfReader(str(source))
        if len(reader.pages) > self.settings.max_pages:
            raise PipelineError(
                "PAGE_LIMIT_EXCEEDED",
                f"document has {len(reader.pages)} pages; limit is {self.settings.max_pages}",
            )
        pages: list[Page] = []
        for page_idx, pdf_page in enumerate(reader.pages):
            text = (pdf_page.extract_text() or "").strip()
            if not text:
                raise PipelineError(
                    "PDF_TEXT_LAYER_EMPTY",
                    f"page {page_idx + 1} has no extractable text; OCR is required",
                )
            width = float(pdf_page.mediabox.width)
            height = float(pdf_page.mediabox.height)
            bbox = [0, 0, 1000, 1000]
            pages.append(
                Page(
                    page_idx=page_idx,
                    width=width,
                    height=height,
                    text=text,
                    blocks=[
                        Block(
                            block_id=f"{source_info['document_id']}:p{page_idx}:b0",
                            type="text",
                            text=text,
                            char_start=0,
                            char_end=len(text),
                            bbox=bbox,
                            bbox_pdf=bbox_to_pdf(bbox, width, height),
                        )
                    ],
                )
            )

        content_list = [
            {
                "type": block.type,
                "text": block.text,
                "bbox": block.bbox,
                "page_idx": page.page_idx,
            }
            for page in pages
            for block in page.blocks
        ]
        layout = {
            "pdf_info": [
                {
                    "page_idx": page.page_idx,
                    "page_size": [page.width, page.height],
                }
                for page in pages
            ],
            "_backend": "local",
            "_version_name": self.version,
        }
        (run_dir / "content_list.json").write_text(
            json.dumps(content_list, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "layout.json").write_text(
            json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "full.md").write_text(
            "\n\n".join(page.text for page in pages), encoding="utf-8"
        )
        return ParsedDocument(
            pages=pages,
            parser_meta={
                "provider": "local",
                "mode": "local_pdf",
                "model_version": self.version,
                "parser_version": self.version,
            },
        )


class MinerUPreciseParser:
    name = "mineru"
    version = "3.4.x-compatible"

    def __init__(self, settings: Settings, log: Any) -> None:
        self.settings = settings
        self.log = log

    def parse(self, source: Path, run_dir: Path, source_info: dict[str, Any]) -> ParsedDocument:
        if not self.settings.mineru_token:
            raise PipelineError(
                "MINERU_TOKEN_MISSING",
                "MINERU_TOKEN is required when PIPELINE_MODE=mineru",
            )
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            submit = self._request(
                client,
                "POST",
                "/api/v4/file-urls/batch",
                json={
                    "files": [
                        {
                            "name": source.name,
                            **(
                                {"page_ranges": self.settings.mineru_page_range}
                                if self.settings.mineru_page_range
                                else {}
                            ),
                        }
                    ],
                    "enable_formula": self.settings.mineru_enable_formula,
                    "enable_table": self.settings.mineru_enable_table,
                    "language": self.settings.mineru_language,
                    "model_version": self.settings.mineru_model_version,
                },
            )
            data = submit.get("data") or {}
            batch_id = data.get("batch_id")
            upload_urls = data.get("file_urls") or []
            if not batch_id or not upload_urls:
                raise PipelineError("MINERU_SUBMIT_INVALID", "MinerU did not return an upload URL", retryable=True)
            response = client.put(
                upload_urls[0],
                content=source.read_bytes(),
            )
            response.raise_for_status()
            result = self._poll(client, batch_id, source.name)
            zip_url = result.get("full_zip_url")
            if not zip_url:
                raise PipelineError("MINERU_ARTIFACT_INVALID", "MinerU result has no full_zip_url", retryable=True)
            archive = client.get(zip_url)
            archive.raise_for_status()
            zip_path = run_dir / "source.zip"
            zip_path.write_bytes(archive.content)
            self._safe_extract(zip_path, run_dir / "mineru")
            (run_dir / "mineru_response.json").write_text(
                json.dumps({"submit": submit, "result": result}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return self._bundle_from_artifacts(run_dir / "mineru", source_info)

    def _request(
        self,
        client: httpx.Client,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = client.request(
            method,
            self.settings.mineru_base_url.rstrip("/") + path,
            json=json,
            headers={"Authorization": f"Bearer {self.settings.mineru_token}"},
        )
        if response.status_code == 429:
            raise PipelineError("MINERU_RATE_LIMITED", "MinerU rate limited the request", retryable=True)
        response.raise_for_status()
        body = response.json()
        if body.get("code") != 0:
            raise PipelineError(
                "MINERU_TASK_FAILED",
                f"MinerU error code={body.get('code')} message={body.get('msg')}",
                retryable=True,
            )
        return body

    def _poll(
        self, client: httpx.Client, batch_id: str, filename: str
    ) -> dict[str, Any]:
        deadline = time.monotonic() + self.settings.mineru_poll_timeout_sec
        backoff = self.settings.mineru_poll_interval_sec
        while time.monotonic() < deadline:
            try:
                body = self._request(
                    client, "GET", f"/api/v4/extract-results/batch/{batch_id}"
                )
            except PipelineError as exc:
                if exc.code != "MINERU_RATE_LIMITED":
                    raise
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            items = (body.get("data") or {}).get("extract_result") or []
            item = next(
                (entry for entry in items if entry.get("file_name") == filename),
                items[0] if items else None,
            )
            if item:
                state = item.get("state")
                self.log(f"MinerU state={state}")
                if state == "done":
                    return item
                if state == "failed":
                    raise PipelineError(
                        "MINERU_TASK_FAILED",
                        str(item.get("err_msg") or "MinerU task failed"),
                        retryable=True,
                    )
            time.sleep(self.settings.mineru_poll_interval_sec)
        raise PipelineError("MINERU_TIMEOUT", "MinerU polling timed out", retryable=True)

    @staticmethod
    def _safe_extract(archive: Path, destination: Path) -> None:
        destination.mkdir(parents=True, exist_ok=True)
        root = destination.resolve()
        with zipfile.ZipFile(archive) as zipped:
            for info in zipped.infolist():
                name = PurePosixPath(info.filename)
                if name.is_absolute() or ".." in name.parts:
                    raise PipelineError("MINERU_ARTIFACT_INVALID", "unsafe ZIP member path")
                target = (destination / Path(*name.parts)).resolve()
                if root not in target.parents and target != root:
                    raise PipelineError("MINERU_ARTIFACT_INVALID", "unsafe ZIP member path")
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zipped.open(info) as source, target.open("wb") as handle:
                        while chunk := source.read(1024 * 1024):
                            handle.write(chunk)

    def _bundle_from_artifacts(
        self, root: Path, source_info: dict[str, Any]
    ) -> ParsedDocument:
        lists = sorted(root.rglob("*content_list.json"))
        lists = [path for path in lists if "content_list_v2" not in path.name]
        layouts = sorted(root.rglob("layout.json"))
        if not lists:
            raise PipelineError("MINERU_ARTIFACT_INVALID", "content_list.json is missing")
        blocks = json.loads(lists[0].read_text(encoding="utf-8"))
        layout = (
            json.loads(layouts[0].read_text(encoding="utf-8")) if layouts else {}
        )
        sizes = {
            int(item.get("page_idx", 0)): item.get("page_size") or [595, 841]
            for item in layout.get("pdf_info") or []
        }
        grouped: dict[int, list[dict[str, Any]]] = {}
        for item in blocks:
            grouped.setdefault(int(item.get("page_idx", 0)), []).append(item)
        pages: list[Page] = []
        for page_idx in sorted(grouped):
            size = sizes.get(page_idx, [595, 841])
            width, height = float(size[0]), float(size[1])
            parts: list[str] = []
            page_blocks: list[Block] = []
            cursor = 0
            for block_idx, item in enumerate(grouped[page_idx]):
                text = block_text(item)
                if not text:
                    continue
                if parts:
                    parts.append("\n\n")
                    cursor += 2
                start = cursor
                parts.append(text)
                cursor += len(text)
                bbox = clean_bbox(item.get("bbox"))
                page_blocks.append(
                    Block(
                        block_id=f"{source_info['document_id']}:p{page_idx}:b{block_idx}",
                        type=str(item.get("type") or "text"),
                        text=text,
                        char_start=start,
                        char_end=cursor,
                        bbox=bbox,
                        bbox_pdf=bbox_to_pdf(bbox, width, height),
                        table_html=item.get("table_body"),
                    )
                )
            page_text = "".join(parts)
            if page_text.strip():
                pages.append(
                    Page(
                        page_idx=page_idx,
                        width=width,
                        height=height,
                        text=page_text,
                        blocks=page_blocks,
                    )
                )
        if not pages:
            raise PipelineError("MINERU_ARTIFACT_INVALID", "no usable pages in content_list.json")
        return ParsedDocument(
            pages=pages,
            parser_meta={
                "provider": "mineru",
                "mode": "precise",
                "model_version": self.settings.mineru_model_version,
                "parser_version": str(layout.get("_version_name") or "unknown"),
            },
        )


class HeuristicExtractor:
    name = "heuristic"
    _stopwords = {
        "the",
        "annual",
        "monthly",
        "table",
        "conclusion",
        "summary",
        "report",
        "results",
        "method",
    }

    def extract(self, parsed: ParsedDocument, document_id: str) -> list[ExtractionRecord]:
        records: list[ExtractionRecord] = []
        for page in parsed.pages:
            page_document_id = f"{document_id}:p{page.page_idx}"
            candidates: list[tuple[int, int, str, str]] = []
            for match in re.finditer(
                r"(?<![A-Za-z])[A-Z][A-Za-z0-9-]*(?:[ \t]+[A-Z][A-Za-z0-9-]*){0,3}",
                page.text,
            ):
                text = match.group(0).strip()
                words = {word.casefold() for word in text.split()}
                if words & self._stopwords or len(text) < 2:
                    continue
                candidates.append((match.start(), match.end(), text, "named"))
            for match in re.finditer(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", page.text):
                end = min(len(page.text), match.end() + 10)
                suffix = page.text[match.end() : end]
                unit_match = re.match(r"\s*(mm|cm|km|kWh|MWh|°C|%|ha|m3|m³)", suffix)
                unit = unit_match.group(1) if unit_match else ""
                candidates.append((match.start(), match.end(), match.group(0), unit))
            seen: set[tuple[int, int]] = set()
            index = 0
            for start, end, text, marker in sorted(candidates):
                if (start, end) in seen:
                    continue
                seen.add((start, end))
                index += 1
                if marker == "named":
                    attributes = {"entity_type": self._entity_type(text)}
                else:
                    attributes = {
                        "entity_type": "metric",
                        "name": "numeric_value",
                        "unit": marker,
                    }
                records.append(
                    ExtractionRecord(
                        document_id=page_document_id,
                        page_idx=page.page_idx,
                        extraction_class="entity",
                        extraction_text=text,
                        char_interval={"start_pos": start, "end_pos": end},
                        alignment_status="match_exact",
                        extraction_index=index,
                        group_index=0,
                        description=None,
                        attributes=attributes,
                    )
                )
            for match in re.finditer(
                r"(?P<source>[A-Z][A-Za-z0-9 -]{1,40}?)[ \t]+is located in[ \t]+(?P<target>[A-Z][A-Za-z0-9 -]{1,40}?)(?=[.,;\n]|$)",
                page.text,
            ):
                index += 1
                records.append(
                    ExtractionRecord(
                        document_id=page_document_id,
                        page_idx=page.page_idx,
                        extraction_class="relation",
                        extraction_text=match.group(0).strip(),
                        char_interval={
                            "start_pos": match.start(),
                            "end_pos": match.end(),
                        },
                        alignment_status="match_exact",
                        extraction_index=index,
                        group_index=0,
                        description=None,
                        attributes={
                            "source": match.group("source").strip(),
                            "target": match.group("target").strip(),
                            "relation_type": "located_in",
                        },
                    )
                )
        return records

    @staticmethod
    def _entity_type(text: str) -> str:
        if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", text):
            return "metric"
        if re.search(r"(center|centre|station|plant|unit|server|datacenter)", text, re.I):
            return "equipment"
        if any(part in text for part in ("City", "Shanghai", "Beijing", "Creek", "Victoria")):
            return "location"
        if re.search(r"(Inc|Ltd|Corporation|Organization|Company)$", text):
            return "organization"
        return "concept"


class LangExtractDeepSeekExtractor:
    name = "langextract"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def extract(self, parsed: ParsedDocument, document_id: str) -> list[ExtractionRecord]:
        if not self.settings.deepseek_api_key:
            raise PipelineError(
                "DEEPSEEK_KEY_MISSING",
                "DEEPSEEK_API_KEY is required when EXTRACTOR_MODE=langextract",
            )
        try:
            import langextract as lx
            from langextract.core import tokenizer as lx_tokenizer
            from langextract.providers.openai import OpenAILanguageModel
        except Exception as exc:
            raise PipelineError("LANGEXTRACT_IMPORT_FAILED", str(exc)) from exc

        class DeepSeekLanguageModel(OpenAILanguageModel):
            thinking_mode: str = "disabled"

            def _build_chat_completions_params(self, prompt: str, config: Any) -> dict[str, Any]:
                params = super()._build_chat_completions_params(prompt, config)
                params["extra_body"] = {
                    "thinking": {"type": self.thinking_mode}
                }
                return params

        examples = [
            lx.data.ExampleData(
                text=(
                    "Traralgon Creek is located in Victoria. "
                    "Its mean annual rainfall is 1472 mm."
                ),
                extractions=[
                    lx.data.Extraction(
                        extraction_class="entity",
                        extraction_text="Traralgon Creek",
                        attributes={"entity_type": "location"},
                    ),
                    lx.data.Extraction(
                        extraction_class="entity",
                        extraction_text="Victoria",
                        attributes={"entity_type": "location"},
                    ),
                    lx.data.Extraction(
                        extraction_class="entity",
                        extraction_text="1472",
                        attributes={"entity_type": "metric", "unit": "mm"},
                    ),
                    lx.data.Extraction(
                        extraction_class="relation",
                        extraction_text="Traralgon Creek is located in Victoria",
                        attributes={
                            "source": "Traralgon Creek",
                            "target": "Victoria",
                            "relation_type": "located_in",
                        },
                    ),
                ],
            )
        ]
        config = lx.factory.ModelConfig(
            model_id=self.settings.deepseek_model,
            provider="openai",
            provider_kwargs={
                "api_key": self.settings.deepseek_api_key,
                "base_url": self.settings.deepseek_base_url,
                "max_output_tokens": self.settings.deepseek_max_output_tokens,
                "reasoning_effort": "low",
            },
        )
        prompt = (
            "从文档中抽取实体和关系。原文必须逐字来自输入。"
            "实体类别使用 entity，属性包含 entity_type；"
            "关系类别使用 relation，属性包含 source、target、relation_type。"
            "只输出 json 对象，顶层键为 extractions，"
            "每项以类别名为键，属性以「类别名_attributes」为键。"
        )
        documents = [
            lx.data.Document(
                text=page.text,
                document_id=f"{document_id}:p{page.page_idx}",
            )
            for page in parsed.pages
        ]
        results = lx.extract(
            text_or_documents=documents,
            prompt_description=prompt,
            examples=examples,
            config=config,
            tokenizer=lx_tokenizer.UnicodeTokenizer(),
            use_schema_constraints=False,
            max_workers=self.settings.llm_max_workers,
            show_progress=False,
        )
        if not isinstance(results, list):
            results = [results]
        records: list[ExtractionRecord] = []
        for result in results:
            page_idx = self._page_from_document_id(str(result.document_id))
            for item in result.extractions or []:
                interval = item.char_interval
                records.append(
                    ExtractionRecord(
                        document_id=str(result.document_id),
                        page_idx=page_idx,
                        extraction_class=item.extraction_class,
                        extraction_text=item.extraction_text,
                        char_interval=(
                            {
                                "start_pos": int(interval.start_pos),
                                "end_pos": int(interval.end_pos),
                            }
                            if interval
                            else None
                        ),
                        alignment_status=(
                            item.alignment_status.value
                            if getattr(item, "alignment_status", None)
                            else "unmatched"
                        ),
                        extraction_index=int(item.extraction_index),
                        group_index=int(item.group_index),
                        description=item.description,
                        attributes=item.attributes or {},
                    )
                )
        return records

    @staticmethod
    def _page_from_document_id(document_id: str) -> int:
        match = re.search(r":p(\d+)$", document_id)
        return int(match.group(1)) if match else 0


def build_graph(
    parsed: ParsedDocument,
    records: list[ExtractionRecord],
    document_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    pages = {page.page_idx: page for page in parsed.pages}
    nodes_by_id: dict[str, dict[str, Any]] = {}
    name_to_ids: dict[str, set[str]] = {}
    dropped_ungrounded = 0
    duplicates = 0

    def locate(page_idx: int, interval: dict[str, int] | None) -> Block | None:
        if interval is None:
            return None
        page = pages.get(page_idx)
        if page is None:
            return None
        start = interval["start_pos"]
        end = interval["end_pos"]
        best: tuple[int, Block] | None = None
        for block in page.blocks:
            overlap = max(
                0, min(end, block.char_end) - max(start, block.char_start)
            )
            if overlap and (best is None or overlap > best[0]):
                best = (overlap, block)
        return best[1] if best else None

    for record in records:
        if record.extraction_class != "entity":
            continue
        if not record.char_interval:
            dropped_ungrounded += 1
            continue
        entity_type = str(record.attributes.get("entity_type") or "other").casefold()
        node_id = f"{entity_type}:{normalize_name(record.extraction_text)}"
        block = locate(record.page_idx, record.char_interval)
        page = pages[record.page_idx]
        if node_id in nodes_by_id:
            duplicates += 1
            node = nodes_by_id[node_id]
            if record.extraction_text not in node["aliases"] and record.extraction_text != node["name"]:
                node["aliases"].append(record.extraction_text)
            node["evidence_count"] += 1
        else:
            node = {
                "id": node_id,
                "type": entity_type,
                "name": record.extraction_text,
                "aliases": [],
                "attributes": {
                    key: value
                    for key, value in record.attributes.items()
                    if key != "entity_type"
                },
                "document_id": document_id,
                "page_idx": record.page_idx,
                "block_id": block.block_id if block else None,
                "char_interval": record.char_interval,
                "bbox": block.bbox if block else None,
                "bbox_pdf": block.bbox_pdf if block else None,
                "evidence": page.text[
                    record.char_interval["start_pos"] : record.char_interval["end_pos"]
                ],
                "evidence_count": 1,
            }
            nodes_by_id[node_id] = node
        name_to_ids.setdefault(normalize_name(record.extraction_text), set()).add(node_id)

    edges_by_id: dict[str, dict[str, Any]] = {}
    dropped_dangling = 0
    for record in records:
        if record.extraction_class != "relation" or not record.char_interval:
            continue
        source_name = str(record.attributes.get("source") or "")
        target_name = str(record.attributes.get("target") or "")
        source_ids = name_to_ids.get(normalize_name(source_name), set())
        target_ids = name_to_ids.get(normalize_name(target_name), set())
        if not source_ids or not target_ids:
            dropped_dangling += 1
            continue
        source_id = sorted(source_ids)[0]
        target_id = sorted(target_ids)[0]
        relation_type = normalize_name(
            str(record.attributes.get("relation_type") or "related_to")
        ).replace(" ", "_")
        edge_id = f"rel:{source_id}:{relation_type}:{target_id}"
        block = locate(record.page_idx, record.char_interval)
        page = pages[record.page_idx]
        if edge_id in edges_by_id:
            edges_by_id[edge_id]["evidence_count"] += 1
            continue
        edges_by_id[edge_id] = {
            "id": edge_id,
            "type": relation_type,
            "source_id": source_id,
            "target_id": target_id,
            "attributes": {},
            "document_id": document_id,
            "page_idx": record.page_idx,
            "block_id": block.block_id if block else None,
            "char_interval": record.char_interval,
            "bbox": block.bbox if block else None,
            "bbox_pdf": block.bbox_pdf if block else None,
            "evidence": page.text[
                record.char_interval["start_pos"] : record.char_interval["end_pos"]
            ],
            "evidence_count": 1,
        }

    nodes = sorted(nodes_by_id.values(), key=lambda item: (item["type"], item["name"]))
    edges = sorted(edges_by_id.values(), key=lambda item: item["id"])
    provenance: list[dict[str, Any]] = []
    for item in [*nodes, *edges]:
        provenance.append(
            {
                "object_type": "node" if item in nodes else "edge",
                "object_id": item["id"],
                "document_id": document_id,
                "page_idx": item["page_idx"],
                "block_id": item.get("block_id"),
                "char_interval": item.get("char_interval"),
                "bbox": item.get("bbox"),
                "evidence": item.get("evidence"),
            }
        )
    stats = {
        "raw_entities": sum(1 for item in records if item.extraction_class == "entity"),
        "raw_relations": sum(1 for item in records if item.extraction_class == "relation"),
        "nodes": len(nodes),
        "edges": len(edges),
        "merged_nodes": len(nodes),
        "probable_duplicates": duplicates,
        "dropped_ungrounded": dropped_ungrounded,
        "dropped_dangling_relations": dropped_dangling,
    }
    return nodes, edges, provenance, stats


class PipelineRunner:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        storage: LocalObjectStore,
    ) -> None:
        self.settings = settings
        self.db = db
        self.storage = storage
        self.executor = ThreadPoolExecutor(
            max_workers=max(1, settings.llm_max_workers),
            thread_name_prefix="graphrag-pipeline",
        )
        self._closed = False

    def submit(self, job_id: str) -> None:
        self.executor.submit(self._run_safe, job_id)

    def shutdown(self) -> None:
        if not self._closed:
            self._closed = True
            self.executor.shutdown(wait=False, cancel_futures=False)

    def _run_safe(self, job_id: str) -> None:
        try:
            self._run(job_id)
        except Exception as exc:  # errors are persisted, never returned as raw stack
            job = self.db.get_job(job_id)
            if not job or job["status"] in {"cancelled", "failed", "succeeded"}:
                return
            code = exc.code if isinstance(exc, PipelineError) else "INTERNAL_ERROR"
            retryable = exc.retryable if isinstance(exc, PipelineError) else True
            message = str(exc)
            self.db.update_job(
                job_id,
                status="failed",
                error_json=json.dumps(
                    {"code": code, "message": message, "retryable": retryable},
                    ensure_ascii=False,
                ),
                finished_at=utc_now(),
            )
            self.db.update_document(
                job["document_id"],
                status="failed",
                updated_at=utc_now(),
            )
            self.db.add_event(
                job_id,
                "failed",
                job["stage"],
                job["progress"],
                {"code": code, "retryable": retryable, "message": message},
            )

    def _run(self, job_id: str) -> None:
        job = self.db.get_job(job_id)
        if not job:
            return
        document = self.db.get_document(job["document_id"])
        if not document:
            raise PipelineError("DOCUMENT_NOT_FOUND", "document disappeared")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"_{job_id[-6:]}"
        run_dir = self.storage.run_dir(document["document_id"], run_id)
        self.db.update_job(
            job_id,
            status="running",
            stage="parse",
            progress=10,
            run_id=run_id,
            started_at=utc_now(),
        )
        self.db.update_document(
            document["document_id"], status="parsing", updated_at=utc_now()
        )
        self.db.add_event(job_id, "stage", "parse", 10, {"message": "解析开始"})

        source = Path(document["object_path"])
        source_info = {
            "document_id": document["document_id"],
            "filename": document["filename"],
            "media_type": document["media_type"],
            "sha256": document["sha256"],
        }
        parser = (
            MinerUPreciseParser(self.settings, self._log(job_id))
            if self.settings.pipeline_mode == "mineru"
            else LocalPdfParser(self.settings)
        )
        parsed = parser.parse(source, run_dir, source_info)
        bundle = parsed.to_bundle(document["document_id"], run_id, source_info)
        (run_dir / "graph-ready-bundle.json").write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._register_run_artifacts(job, run_dir)

        self._advance(job_id, document["document_id"], "extract", 50, "解析完成，抽取开始", "extracting")
        extractor = (
            LangExtractDeepSeekExtractor(self.settings)
            if self.settings.extractor_mode == "langextract"
            else HeuristicExtractor()
        )
        records = extractor.extract(parsed, document["document_id"])
        extract_dir = run_dir / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        raw_path = extract_dir / "raw_result.jsonl"
        with raw_path.open("w", encoding="utf-8") as handle:
            for page in parsed.pages:
                page_records = [
                    item.to_dict()
                    for item in records
                    if item.page_idx == page.page_idx
                ]
                handle.write(
                    json.dumps(
                        {
                            "document_id": f"{document['document_id']}:p{page.page_idx}",
                            "text": page.text,
                            "extractions": page_records,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        self._advance(job_id, document["document_id"], "graph_build", 78, "抽取完成，构建图谱", "graph_building")
        nodes, edges, provenance, stats = build_graph(
            parsed, records, document["document_id"]
        )
        graph_dir = run_dir / "graph"
        graph_dir.mkdir(parents=True, exist_ok=True)
        files = {
            "nodes": graph_dir / "nodes.json",
            "edges": graph_dir / "edges.json",
            "provenance": graph_dir / "provenance.jsonl",
            "stats": graph_dir / "stats.json",
        }
        files["nodes"].write_text(json.dumps(nodes, ensure_ascii=False, indent=2), encoding="utf-8")
        files["edges"].write_text(json.dumps(edges, ensure_ascii=False, indent=2), encoding="utf-8")
        files["stats"].write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        with files["provenance"].open("w", encoding="utf-8") as handle:
            for item in provenance:
                handle.write(json.dumps(item, ensure_ascii=False) + "\n")

        self._register_run_artifacts(job, run_dir)
        self._advance(job_id, document["document_id"], "publish", 94, "图谱校验通过，发布快照", "graph_building")
        snapshot_id = new_id("gs")
        version = self.db.next_snapshot_version(document["document_id"])
        manifest_path = run_dir / "graph" / "manifest.json"
        manifest = {
            "schema_version": "kg-snapshot/1.0",
            "snapshot_id": snapshot_id,
            "document_id": document["document_id"],
            "job_id": job_id,
            "version": version,
            "parser": parsed.parser_meta,
            "extractor": {"provider": extractor.name},
            "counts": {
                "nodes": len(nodes),
                "edges": len(edges),
                "provenance": len(provenance),
                "pages": len(parsed.pages),
            },
            "files": {
                key: str(path.relative_to(run_dir))
                for key, path in files.items()
            },
            "validated": True,
            "created_at": utc_now(),
        }
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._register_run_artifacts(job, run_dir)
        self.db.create_snapshot(
            {
                "snapshot_id": snapshot_id,
                "document_id": document["document_id"],
                "job_id": job_id,
                "version": version,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "manifest": manifest,
                "created_at": utc_now(),
                "activated_at": utc_now(),
            }
        )
        self.db.activate_snapshot(document["document_id"], snapshot_id)
        self.db.update_document(
            document["document_id"],
            active_job_id=job_id,
            warning_json=json.dumps(parsed.warnings, ensure_ascii=False),
            updated_at=utc_now(),
        )
        self.db.update_job(
            job_id,
            status="succeeded",
            stage="done",
            progress=100,
            warnings_json=json.dumps(parsed.warnings, ensure_ascii=False),
            finished_at=utc_now(),
        )
        self.db.add_event(
            job_id,
            "completed",
            "done",
            100,
            {"graph_snapshot_id": snapshot_id},
        )

    def _advance(
        self,
        job_id: str,
        document_id: str,
        stage: str,
        progress: int,
        message: str,
        document_status: str,
    ) -> None:
        current = self.db.get_job(job_id)
        if not current or current["status"] == "cancelled":
            raise PipelineError("JOB_CANCELLED", "job was cancelled")
        self.db.update_job(job_id, stage=stage, progress=progress)
        self.db.update_document(document_id, status=document_status, updated_at=utc_now())
        self.db.add_event(job_id, "stage", stage, progress, {"message": message})

    def _register_run_artifacts(self, job: dict[str, Any], run_dir: Path) -> None:
        existing = {
            item["path"]
            for item in self.db.list_artifacts(job["document_id"])
        }
        type_map = {
            "content_list.json": "mineru_content_list",
            "layout.json": "mineru_layout",
            "full.md": "mineru_full_md",
            "graph-ready-bundle.json": "graph_ready_bundle",
            "source.zip": "mineru_zip",
            "mineru_response.json": "mineru_response",
            "raw_result.jsonl": "extraction_jsonl",
            "nodes.json": "graph_nodes",
            "edges.json": "graph_edges",
            "provenance.jsonl": "graph_provenance",
            "stats.json": "graph_stats",
            "manifest.json": "graph_manifest",
        }
        media = {
            ".json": "application/json",
            ".jsonl": "application/x-ndjson",
            ".md": "text/markdown",
            ".zip": "application/zip",
            ".pdf": "application/pdf",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
        }
        for path in run_dir.rglob("*"):
            if not path.is_file() or str(path) in existing:
                continue
            artifact_type = type_map.get(path.name)
            if artifact_type is None:
                if path.suffix in media:
                    artifact_type = "mineru_artifact"
                else:
                    continue
            self.db.add_artifact(
                {
                    "artifact_id": new_id("art"),
                    "document_id": job["document_id"],
                    "job_id": job["job_id"],
                    "artifact_type": artifact_type,
                    "path": str(path),
                    "media_type": media.get(path.suffix.lower(), "application/octet-stream"),
                    "size_bytes": path.stat().st_size,
                    "sha256": self.storage.sha256(path),
                    "schema_version": "1.0",
                    "created_at": utc_now(),
                }
            )

    def _log(self, job_id: str) -> Any:
        def emit(message: str) -> None:
            job = self.db.get_job(job_id)
            if job:
                self.db.add_event(
                    job_id,
                    "log",
                    job["stage"],
                    job["progress"],
                    {"message": message},
                )
        return emit


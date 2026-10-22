from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IndexingOptions(ApiModel):
    profile: Literal["graph_v1", "parse_only"] = "graph_v1"
    parser: Literal["mineru_precise", "local"] = "mineru_precise"
    language: str = "ch"
    ocr: Literal["auto", "true", "false"] = "auto"
    enable_table: bool = True
    enable_formula: bool = True
    page_range: str | None = None
    extraction_profile: str = "enterprise_kg_v1"


class UploadCreateRequest(ApiModel):
    filename: str = Field(min_length=1, max_length=180)
    media_type: str = Field(min_length=1, max_length=120)
    size_bytes: int = Field(gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    indexing: IndexingOptions = Field(default_factory=IndexingOptions)


class UploadCompleteRequest(ApiModel):
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    expected_size_bytes: int | None = Field(default=None, gt=0)
    force_reindex: bool = False


class ReindexRequest(ApiModel):
    indexing: IndexingOptions = Field(default_factory=IndexingOptions)
    activate_on_success: bool = True


class QAOptions(ApiModel):
    answer_style: Literal["concise", "standard", "detailed"] = "standard"
    answer_language: str = "zh-CN"
    max_tool_calls: int = Field(default=6, ge=1, le=10)
    top_k_pages: int = Field(default=3, ge=1, le=10)
    include_trace: bool = True
    include_evidence: bool = True
    strict_citations: bool = True


class QARequest(ApiModel):
    question: str = Field(min_length=1, max_length=4000)
    document_ids: list[str] = Field(min_length=1, max_length=10)
    conversation_id: str | None = None
    options: QAOptions = Field(default_factory=QAOptions)


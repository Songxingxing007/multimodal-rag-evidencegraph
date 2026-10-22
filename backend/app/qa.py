from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Database, new_id
from .schemas import QARequest


class QAError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def query_terms(question: str) -> list[str]:
    raw_terms = re.findall(
        r"[A-Za-z0-9][A-Za-z0-9_.%-]*|[\u4e00-\u9fff]{1,8}", question
    )
    stop = {
        "the",
        "and",
        "or",
        "is",
        "are",
        "what",
        "which",
        "how",
        "much",
        "many",
        "of",
        "in",
        "on",
        "for",
        "请",
        "请问",
        "多少",
        "什么",
        "分别",
        "文档",
        "资料",
        "a",
        "an",
        "about",
        "write",
        "who",
        "whom",
        "whose",
        "when",
        "where",
        "why",
        "please",
        "tell",
        "me",
    }
    terms: list[str] = []
    for raw in raw_terms:
        term = raw.casefold()
        if term in stop or len(term) < 2:
            continue
        terms.append(term)
        if re.fullmatch(r"[\u4e00-\u9fff]{3,}", term):
            terms.extend(term[index : index + 2] for index in range(len(term) - 1))
    return list(dict.fromkeys(terms))


@dataclass
class DocumentGraph:
    document_id: str
    snapshot_id: str
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    pages: list[dict[str, Any]]


class GraphLoader:
    def __init__(self, db: Database) -> None:
        self.db = db

    def load(self, document_id: str) -> DocumentGraph:
        document = self.db.get_document(document_id)
        if not document:
            raise QAError("DOCUMENT_NOT_FOUND", f"document not found: {document_id}", retryable=False)
        snapshot_id = document.get("active_graph_snapshot_id")
        if not snapshot_id:
            raise QAError("GRAPH_NOT_READY", f"graph is not ready: {document_id}")
        snapshot = self.db.get_snapshot(snapshot_id)
        if not snapshot:
            raise QAError("GRAPH_NOT_READY", "active graph snapshot is missing")
        artifacts = [
            item
            for item in self.db.list_artifacts(document_id)
            if item["job_id"] == snapshot["job_id"]
        ]
        by_type = {item["artifact_type"]: item for item in artifacts}
        nodes_art = by_type.get("graph_nodes")
        edges_art = by_type.get("graph_edges")
        bundle_art = by_type.get("graph_ready_bundle")
        if not nodes_art or not edges_art or not bundle_art:
            raise QAError("GRAPH_ARTIFACT_MISSING", "graph artifacts are incomplete")
        nodes = json.loads(Path(nodes_art["path"]).read_text(encoding="utf-8"))
        edges = json.loads(Path(edges_art["path"]).read_text(encoding="utf-8"))
        bundle = json.loads(Path(bundle_art["path"]).read_text(encoding="utf-8"))
        return DocumentGraph(
            document_id=document_id,
            snapshot_id=snapshot_id,
            nodes=nodes,
            edges=edges,
            pages=bundle["pages"],
        )


class KGIndex:
    def __init__(self, graphs: list[DocumentGraph]) -> None:
        self.nodes = {
            (graph.document_id, node["id"]): node
            for graph in graphs
            for node in graph.nodes
        }
        self.edges = [
            edge
            for graph in graphs
            for edge in graph.edges
        ]
        self.pages = [
            {
                **page,
                "document_id": graph.document_id,
            }
            for graph in graphs
            for page in graph.pages
        ]
        self.by_name: dict[str, list[tuple[str, str]]] = {}
        for (document_id, node_id), node in self.nodes.items():
            for name in {node["name"], *node.get("aliases", [])}:
                self.by_name.setdefault(normalize(name), []).append(
                    (document_id, node_id)
                )
        self.adjacency: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for edge in self.edges:
            source = (edge["document_id"], edge["source_id"])
            target = (edge["document_id"], edge["target_id"])
            self.adjacency.setdefault(source, []).append(edge)
            self.adjacency.setdefault(target, []).append(edge)

    def search_entities(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        q = normalize(query)
        scored: list[tuple[float, dict[str, Any]]] = []
        for node in self.nodes.values():
            name = normalize(node["name"])
            score = 0.0
            if name == q:
                score = 100
            elif q and q in name:
                score = 80
            elif name and name in q:
                score = 60
            else:
                score = sum(8 for term in query_terms(q) if term in name)
            if score:
                scored.append((score, node))
        scored.sort(key=lambda item: (-item[0], item[1]["name"]))
        return [
            {
                "id": node["id"],
                "document_id": node["document_id"],
                "name": node["name"],
                "type": node["type"],
                "page_idx": node["page_idx"],
                "char_interval": node["char_interval"],
                "bbox": node["bbox"],
                "evidence": node["evidence"],
                "score": round(score, 2),
            }
            for score, node in scored[:limit]
        ]

    def neighbors(
        self, document_id: str, entity_id: str, depth: int = 1
    ) -> dict[str, Any]:
        key = (document_id, entity_id)
        if key not in self.nodes:
            return {
                "center": None,
                "depth": depth,
                "nodes": [],
                "edges": [],
                "error": "entity not found",
            }
        seen = {key}
        frontier = [key]
        collected: dict[str, dict[str, Any]] = {}
        for _ in range(max(1, min(depth, 2))):
            new_frontier = []
            for current in frontier:
                for edge in self.adjacency.get(current, []):
                    collected[edge["id"]] = edge
                    other = (
                        (edge["document_id"], edge["target_id"])
                        if edge["source_id"] == current[1]
                        else (edge["document_id"], edge["source_id"])
                    )
                    if other not in seen:
                        seen.add(other)
                        new_frontier.append(other)
            frontier = new_frontier
        return {
            "center": self.nodes[key],
            "depth": depth,
            "nodes": [self.nodes[item] for item in sorted(seen)],
            "edges": list(collected.values()),
        }

    def search_pages(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        terms = query_terms(query)
        hits: list[dict[str, Any]] = []
        for page in self.pages:
            text = page["text"]
            low = text.casefold()
            score = 0.0
            first = -1
            matched: set[str] = set()
            for term in terms:
                count = low.count(term)
                if count:
                    score += count
                    matched.add(term)
                    position = low.find(term)
                    if position >= 0 and (first < 0 or position < first):
                        first = position
            if len(terms) > 1 and len(matched) < 2:
                continue
            if matched:
                coverage = len(matched) / max(1, len(terms))
                score += len(matched) * 2 + coverage
                if first < 0:
                    first = 0
                start = max(0, first - 120)
                end = min(len(text), first + 320)
                hits.append(
                    {
                        "document_id": page["document_id"],
                        "page_idx": page["page_idx"],
                        "score": score,
                        "snippet": text[start:end].replace("\n", " ").strip(),
                        "char_start": start,
                        "char_end": end,
                    }
                )
        hits.sort(key=lambda item: -item["score"])
        return hits[:top_k]

    def evidence(
        self, document_id: str, page_idx: int, start: int, end: int
    ) -> dict[str, Any] | None:
        page = next(
            (
                item
                for item in self.pages
                if item["document_id"] == document_id
                and item["page_idx"] == page_idx
            ),
            None,
        )
        if not page:
            return None
        text = page["text"]
        start = max(0, min(start, len(text)))
        end = max(start, min(end, len(text)))
        block = None
        for candidate in page["blocks"]:
            if candidate["char_start"] <= start < candidate["char_end"] or (
                start == end and candidate["char_start"] <= start <= candidate["char_end"]
            ):
                block = candidate
                break
        return {
            "document_id": document_id,
            "page_idx": page_idx,
            "char_interval": {"start_pos": start, "end_pos": end},
            "evidence": text[start:end],
            "block_id": block["block_id"] if block else None,
            "bbox": block.get("bbox") if block else None,
            "bbox_pdf": block.get("bbox_pdf") if block else None,
        }


class QAService:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.loader = GraphLoader(db)

    def answer(
        self,
        request: QARequest,
        request_id: str,
        conversation_id: str,
    ) -> dict[str, Any]:
        started = time.monotonic()
        documents = []
        for document_id in request.document_ids:
            document = self.db.get_document(document_id)
            if not document:
                raise QAError("DOCUMENT_NOT_FOUND", f"document not found: {document_id}", retryable=False)
            if document["status"] != "indexed" or not document["active_graph_snapshot_id"]:
                raise QAError("GRAPH_NOT_READY", f"graph is not ready: {document_id}")
            documents.append(document)
        graphs = [self.loader.load(document["document_id"]) for document in documents]
        index = KGIndex(graphs)
        tool_trace: list[dict[str, Any]] = []

        entity_started = time.monotonic()
        entities = index.search_entities(request.question, limit=request.options.max_tool_calls)
        tool_trace.append(
            {
                "step": len(tool_trace) + 1,
                "tool": "search_entities",
                "args": {"query": request.question, "limit": request.options.max_tool_calls},
                "status": "completed",
                "hits": len(entities),
                "latency_ms": round((time.monotonic() - entity_started) * 1000, 2),
            }
        )
        page_started = time.monotonic()
        pages = index.search_pages(
            request.question, top_k=request.options.top_k_pages
        )
        tool_trace.append(
            {
                "step": len(tool_trace) + 1,
                "tool": "search_pages",
                "args": {
                    "query": request.question,
                    "top_k": request.options.top_k_pages,
                },
                "status": "completed",
                "hits": len(pages),
                "latency_ms": round((time.monotonic() - page_started) * 1000, 2),
            }
        )

        if not pages:
            response = self._insufficient(
                request,
                request_id,
                conversation_id,
                documents,
                tool_trace,
                started,
            )
            self._persist(response, request.question)
            return response

        citations: list[dict[str, Any]] = []
        evidence_blocks: list[dict[str, Any]] = []
        for ordinal, hit in enumerate(pages, start=1):
            evidence = index.evidence(
                hit["document_id"],
                hit["page_idx"],
                hit["char_start"],
                hit["char_end"],
            )
            if not evidence or not evidence["evidence"]:
                continue
            document = next(
                item
                for item in documents
                if item["document_id"] == hit["document_id"]
            )
            citation = {
                "citation_id": f"cit_{ordinal}",
                "document_id": document["document_id"],
                "filename": document["filename"],
                "page_idx": hit["page_idx"],
                "page_number": hit["page_idx"] + 1,
                "block_id": evidence["block_id"],
                "char_interval": evidence["char_interval"],
                "bbox": evidence["bbox"],
                "bbox_pdf": evidence["bbox_pdf"],
                "evidence": evidence["evidence"],
                "source_kind": "page_text",
                "source_id": None,
            }
            citations.append(citation)
            evidence_blocks.append(
                {
                    "citation_id": citation["citation_id"],
                    "text": citation["evidence"],
                    "document_id": citation["document_id"],
                    "page_idx": citation["page_idx"],
                }
            )

        if self.settings.qa_mode == "deepseek":
            answer = self._deepseek_answer(request.question, evidence_blocks)
        else:
            answer = self._local_answer(request.question, pages[0], request.options.answer_style)

        grounded = bool(citations)
        if not grounded:
            response = self._insufficient(
                request,
                request_id,
                conversation_id,
                documents,
                tool_trace,
                started,
            )
            self._persist(response, request.question)
            return response

        total_ms = round((time.monotonic() - started) * 1000, 2)
        response = {
            "data": {
                "request_id": request_id,
                "conversation_id": conversation_id,
                "message_id": new_id("msg"),
                "answer_status": "answered",
                "answer": answer,
                "grounded": True,
                "citations": citations,
                "used_documents": [
                    {
                        "document_id": graph.document_id,
                        "filename": next(
                            item["filename"]
                            for item in documents
                            if item["document_id"] == graph.document_id
                        ),
                        "snapshot_id": graph.snapshot_id,
                    }
                    for graph in graphs
                ],
                "tool_trace": tool_trace,
                "retrieval": {
                    "strategy": "kg_mcp_keyword_local"
                    if self.settings.qa_mode != "deepseek"
                    else "kg_mcp_keyword_deepseek",
                    "entities_hit": len(entities),
                    "pages_hit": len(pages),
                    "evidence_blocks": len(citations),
                    "graph_snapshot_ids": [graph.snapshot_id for graph in graphs],
                },
                "usage": {
                    "model": "deterministic"
                    if self.settings.qa_mode != "deepseek"
                    else self.settings.deepseek_model,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "tool_calls": len(tool_trace),
                },
                "timings": {
                    "retrieval_ms": round(
                        sum(item["latency_ms"] for item in tool_trace), 2
                    ),
                    "generation_ms": 0
                    if self.settings.qa_mode != "deepseek"
                    else max(0.0, total_ms - sum(item["latency_ms"] for item in tool_trace)),
                    "total_ms": total_ms,
                },
                "warnings": [],
                "finish_reason": "stop",
            },
            "request_id": request_id,
            "meta": None,
        }
        self._persist(response, request.question)
        return response

    @staticmethod
    def _local_answer(
        question: str,
        best_page: dict[str, Any],
        style: str,
    ) -> str:
        snippet = str(best_page["snippet"])
        if any("\u4e00" <= char <= "\u9fff" for char in question):
            prefix = "根据检索到的文档原文："
        else:
            prefix = "Based on the retrieved document evidence: "
        detail = snippet
        if style == "concise":
            detail = snippet[:220]
        elif style == "detailed":
            detail = snippet[:600]
        return f"{prefix}{detail}"

    def _deepseek_answer(
        self, question: str, evidence: list[dict[str, Any]]
    ) -> str:
        if not self.settings.deepseek_api_key:
            raise QAError(
                "DEEPSEEK_KEY_MISSING",
                "DEEPSEEK_API_KEY is required when QA_MODE=deepseek",
            )
        try:
            from langchain_openai import ChatOpenAI
        except Exception as exc:
            raise QAError("LANGCHAIN_IMPORT_FAILED", str(exc)) from exc
        model = ChatOpenAI(
            model=self.settings.deepseek_model,
            api_key=self.settings.deepseek_api_key,
            base_url=self.settings.deepseek_base_url,
            temperature=0,
            max_tokens=2048,
            extra_body={
                "thinking": {"type": self.settings.deepseek_thinking}
            },
        )
        context = "\n\n".join(
            f"[{item['citation_id']}] page={item['page_idx']}\n{item['text']}"
            for item in evidence
        )
        prompt = (
            "你是一个严格基于给定证据的 GraphRAG 问答助手。"
            "只能使用下列证据，不得补充记忆；证据不足必须明确拒答。"
            "回答后保留 [cit_N] 引用。\n\n"
            f"问题：{question}\n\n证据：\n{context}"
        )
        response = model.invoke(prompt)
        content = response.content
        if isinstance(content, list):
            content = "".join(
                str(item.get("text", "")) if isinstance(item, dict) else str(item)
                for item in content
            )
        return str(content).strip()

    def _insufficient(
        self,
        request: QARequest,
        request_id: str,
        conversation_id: str,
        documents: list[dict[str, Any]],
        tool_trace: list[dict[str, Any]],
        started: float,
    ) -> dict[str, Any]:
        total_ms = round((time.monotonic() - started) * 1000, 2)
        answer = (
            "当前资料不足以回答该问题。"
            if any("\u4e00" <= char <= "\u9fff" for char in request.question)
            else "The selected documents do not provide enough evidence to answer this question."
        )
        return {
            "data": {
                "request_id": request_id,
                "conversation_id": conversation_id,
                "message_id": new_id("msg"),
                "answer_status": "insufficient_evidence",
                "answer": answer,
                "grounded": False,
                "citations": [],
                "used_documents": [
                    {
                        "document_id": document["document_id"],
                        "filename": document["filename"],
                        "snapshot_id": document["active_graph_snapshot_id"],
                    }
                    for document in documents
                ],
                "tool_trace": tool_trace,
                "retrieval": {
                    "strategy": "kg_mcp_keyword_local"
                    if self.settings.qa_mode != "deepseek"
                    else "kg_mcp_keyword_deepseek",
                    "entities_hit": 0,
                    "pages_hit": 0,
                    "evidence_blocks": 0,
                    "graph_snapshot_ids": [
                        document["active_graph_snapshot_id"]
                        for document in documents
                    ],
                },
                "usage": {
                    "model": "deterministic"
                    if self.settings.qa_mode != "deepseek"
                    else self.settings.deepseek_model,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "tool_calls": len(tool_trace),
                },
                "timings": {
                    "retrieval_ms": round(
                        sum(item["latency_ms"] for item in tool_trace), 2
                    ),
                    "generation_ms": 0,
                    "total_ms": total_ms,
                },
                "warnings": [],
                "finish_reason": "insufficient_evidence",
            },
            "request_id": request_id,
            "meta": None,
        }

    def _persist(self, response: dict[str, Any], question: str) -> None:
        data = response["data"]
        self.db.save_qa(
            {
                "request_id": data["request_id"],
                "conversation_id": data["conversation_id"],
                "scope": {
                    "document_ids": [
                        item["document_id"] for item in data["used_documents"]
                    ]
                },
                "question": question,
                "answer_status": data["answer_status"],
                "answer": data["answer"],
                "response": response,
                "created_at": _utc_now(),
            }
        )


def _utc_now() -> str:
    from .db import utc_now

    return utc_now()


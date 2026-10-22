const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')

export type DocumentStatus =
  | 'uploading'
  | 'validating'
  | 'queued'
  | 'parsing'
  | 'extracting'
  | 'graph_building'
  | 'indexed'
  | 'parse_ready'
  | 'failed'
  | 'deleting'
  | 'deleted'

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface DocumentView {
  document_id: string
  filename: string
  media_type: string
  size_bytes: number
  sha256: string
  indexing_profile: 'graph_v1' | 'parse_only'
  indexing: Record<string, unknown>
  status: DocumentStatus
  active_job_id: string | null
  active_graph_snapshot_id: string | null
  counts: { pages: number; nodes: number; edges: number }
  capabilities: { queryable: boolean; downloadable: boolean; reindexable: boolean }
  warnings: string[]
  created_at: string
  updated_at: string
  links: Record<string, string | null>
}

export interface JobStage {
  name: string
  status: string
  started_at: string | null
  finished_at: string | null
  progress: number
}

export interface JobView {
  job_id: string
  document_id: string
  status: JobStatus
  stage: string
  progress: number
  attempt: number
  created_at: string
  started_at: string | null
  finished_at: string | null
  stages: JobStage[]
  warnings: string[]
  error: { code?: string; message?: string; retryable?: boolean } | null
  links: Record<string, string>
}

export interface GraphNode {
  id: string
  type: string
  name: string
  aliases: string[]
  attributes: Record<string, unknown>
  document_id: string
  page_idx: number
  block_id: string | null
  char_interval: { start_pos: number; end_pos: number } | null
  bbox: number[] | null
  bbox_pdf: number[] | null
  evidence: string
  evidence_count: number
}

export interface GraphEdge {
  id: string
  type: string
  source_id: string
  target_id: string
  evidence: string
  page_idx: number
  char_interval: { start_pos: number; end_pos: number } | null
  bbox: number[] | null
  bbox_pdf: number[] | null
}

export interface GraphManifest {
  schema_version: string
  snapshot_id: string
  document_id: string
  job_id: string
  version: number
  parser: Record<string, unknown>
  extractor: Record<string, unknown>
  counts: { nodes: number; edges: number; provenance: number; pages: number }
  validated: boolean
  created_at: string
}

export interface ExtractionView {
  document_id: string
  page_idx: number
  extraction_class: string
  extraction_text: string
  char_interval: { start_pos: number; end_pos: number } | null
  alignment_status: string
  extraction_index: number
  group_index: number
  description: string | null
  attributes: Record<string, unknown>
}

export interface Citation {
  citation_id: string
  document_id: string
  filename: string
  page_idx: number
  page_number: number
  block_id: string | null
  char_interval: { start_pos: number; end_pos: number } | null
  bbox: number[] | null
  bbox_pdf: number[] | null
  evidence: string
  source_kind: string
  source_id: string | null
}

export interface ToolTrace {
  step: number
  tool: string
  args: Record<string, unknown>
  status: string
  hits: number
  latency_ms: number
}

export interface QAResult {
  request_id: string
  conversation_id: string
  message_id: string
  answer_status: 'answered' | 'insufficient_evidence' | 'failed'
  answer_mode?: 'knowledge' | 'general' | 'mixed' | 'insufficient_evidence'
  route_reason?: string
  answer: string
  grounded: boolean
  citations: Citation[]
  used_documents: Array<{ document_id: string; filename: string; snapshot_id: string }>
  tool_trace: ToolTrace[]
  retrieval: Record<string, unknown>
  usage: Record<string, unknown>
  timings: Record<string, number>
  warnings: string[]
  finish_reason: string
}

interface Envelope<T> {
  data: T
  request_id: string
  meta: { next_cursor?: string | null; has_more?: boolean } | null
}

interface ProblemDetails {
  code?: string
  detail?: string
  title?: string
  request_id?: string
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly requestId?: string

  constructor(status: number, payload: ProblemDetails | null, fallback: string) {
    super(payload?.detail || payload?.title || fallback)
    this.name = 'ApiError'
    this.status = status
    this.code = payload?.code || `HTTP_${status}`
    this.requestId = payload?.request_id
  }
}

function apiUrl(path: string): string {
  if (/^https?:\/\//.test(path)) return path
  if (path.startsWith('/api/') || path.startsWith('/health/')) return path
  return `${API_BASE}${path.startsWith('/') ? path : `/${path}`}`
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<Envelope<T>> {
  const response = await fetch(apiUrl(path), {
    ...init,
    headers: {
      Accept: 'application/json',
      ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
      ...(init?.headers || {}),
    },
  })
  const text = await response.text()
  let payload: unknown = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = null
    }
  }
  if (!response.ok) {
    throw new ApiError(response.status, payload as ProblemDetails | null, response.statusText)
  }
  return payload as Envelope<T>
}

async function sha256(file: File): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer())
  return Array.from(new Uint8Array(digest), (value) => value.toString(16).padStart(2, '0')).join('')
}

export async function checkHealth(): Promise<{ status: string; pipeline_mode: string }> {
  const response = await fetch('/health/ready', { headers: { Accept: 'application/json' } })
  if (!response.ok) throw new ApiError(response.status, null, 'backend health check failed')
  return response.json()
}

export async function listDocuments(status?: string): Promise<DocumentView[]> {
  const suffix = status ? `&status=${encodeURIComponent(status)}` : ''
  const response = await requestJson<DocumentView[]>(`/documents?limit=100${suffix}`)
  return response.data
}

export async function getDocument(documentId: string): Promise<DocumentView> {
  return (await requestJson<DocumentView>(`/documents/${encodeURIComponent(documentId)}`)).data
}

export async function getJob(jobId: string): Promise<JobView> {
  return (await requestJson<JobView>(`/jobs/${encodeURIComponent(jobId)}`)).data
}

export async function uploadPdf(file: File): Promise<{ document_id: string; job_id: string }> {
  const digest = await sha256(file)
  const session = (
    await requestJson<{
      upload_id: string
      upload: { url: string; method: string; headers: Record<string, string> }
    }>('/uploads', {
      method: 'POST',
      body: JSON.stringify({
        filename: file.name,
        media_type: file.type || 'application/pdf',
        size_bytes: file.size,
        sha256: digest,
        indexing: {
          profile: 'graph_v1',
          parser: 'mineru_precise',
          language: 'ch',
          ocr: 'auto',
          enable_table: true,
          enable_formula: true,
          extraction_profile: 'enterprise_kg_v1',
        },
      }),
    })
  ).data

  const uploadResponse = await fetch(apiUrl(session.upload.url), {
    method: session.upload.method || 'PUT',
    headers: { 'Content-Type': file.type || 'application/pdf' },
    body: file,
  })
  if (!uploadResponse.ok) {
    throw new ApiError(uploadResponse.status, null, 'file upload failed')
  }

  return (
    await requestJson<{ document_id: string; job_id: string }>(
      `/uploads/${encodeURIComponent(session.upload_id)}/complete`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': `ui:${digest}` },
        body: JSON.stringify({ sha256: digest, expected_size_bytes: file.size }),
      },
    )
  ).data
}

export async function reindexDocument(documentId: string): Promise<{ job_id: string; document_id: string }> {
  return (
    await requestJson<{ job_id: string; document_id: string }>(
      `/documents/${encodeURIComponent(documentId)}/reindex`,
      {
        method: 'POST',
        headers: { 'Idempotency-Key': `reindex:${documentId}:${Date.now()}` },
        body: JSON.stringify({
          indexing: {
            profile: 'graph_v1',
            parser: 'mineru_precise',
            language: 'ch',
            ocr: 'auto',
            enable_table: true,
            enable_formula: true,
            extraction_profile: 'enterprise_kg_v1',
          },
          activate_on_success: true,
        }),
      },
    )
  ).data
}

export async function getGraph(documentId: string): Promise<{
  manifest: GraphManifest
  nodes: GraphNode[]
  edges: GraphEdge[]
}> {
  const [manifest, nodes, edges] = await Promise.all([
    requestJson<GraphManifest>(`/documents/${encodeURIComponent(documentId)}/graph/manifest`),
    requestJson<GraphNode[]>(`/documents/${encodeURIComponent(documentId)}/graph/nodes?limit=200`),
    requestJson<GraphEdge[]>(`/documents/${encodeURIComponent(documentId)}/graph/edges?limit=500`),
  ])
  return { manifest: manifest.data, nodes: nodes.data, edges: edges.data }
}

export async function getExtractions(documentId: string): Promise<ExtractionView[]> {
  return (
    await requestJson<ExtractionView[]>(
      `/documents/${encodeURIComponent(documentId)}/extractions`,
    )
  ).data
}

export async function askQuestion(
  question: string,
  documentIds: string[],
  conversationId?: string,
): Promise<QAResult> {
  return (
    await requestJson<QAResult>('/qa/answers', {
      method: 'POST',
      body: JSON.stringify({
        question,
        document_ids: documentIds,
        conversation_id: conversationId,
        options: {
          answer_style: 'standard',
          answer_language: 'zh-CN',
          max_tool_calls: 6,
          top_k_pages: 3,
          include_trace: true,
          include_evidence: true,
          strict_citations: true,
        },
      }),
    })
  ).data
}

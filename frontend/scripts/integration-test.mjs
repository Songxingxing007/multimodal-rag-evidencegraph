import { createHash } from 'node:crypto'

const frontendUrl = process.env.FRONTEND_URL || 'http://127.0.0.1:8443'

function makeSamplePdf() {
  const stream = [
    'BT',
    '/F1 16 Tf',
    '72 760 Td',
    '(Acme Annual Report) Tj',
    '0 -30 Td',
    '/F1 11 Tf',
    '(Acme Data Center is located in Shanghai.) Tj',
    '0 -24 Td',
    '(Annual rainfall is 775 mm.) Tj',
    '0 -24 Td',
    '(The runoff ratio is 0.26.) Tj',
    'ET',
  ].join('\n')
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>',
    `<< /Length ${Buffer.byteLength(stream)} >>\nstream\n${stream}\nendstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
  ]
  let pdf = '%PDF-1.4\n'
  const offsets = [0]
  objects.forEach((object, index) => {
    offsets.push(Buffer.byteLength(pdf))
    pdf += `${index + 1} 0 obj\n${object}\nendobj\n`
  })
  const xrefOffset = Buffer.byteLength(pdf)
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`
  offsets.slice(1).forEach((offset) => {
    pdf += `${String(offset).padStart(10, '0')} 00000 n \n`
  })
  pdf += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`
  return Buffer.from(pdf)
}

async function json(url, init) {
  const response = await fetch(url, init)
  const text = await response.text()
  const payload = text ? JSON.parse(text) : null
  if (!response.ok) {
    throw new Error(`${init?.method || 'GET'} ${url} -> ${response.status} ${text}`)
  }
  return payload
}

async function waitForJob(jobId, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const response = await json(`${frontendUrl}/api/v1/jobs/${jobId}`)
    if (['succeeded', 'failed', 'cancelled'].includes(response.data.status)) {
      return response.data
    }
    await new Promise((resolve) => setTimeout(resolve, 500))
  }
  throw new Error(`job ${jobId} did not finish`)
}

const ready = await json(`${frontendUrl}/health/ready`)
const health = await json(`${frontendUrl}/api/v1/health/summary`)
const file = makeSamplePdf()
const sha256 = createHash('sha256').update(file).digest('hex')

const session = await json(`${frontendUrl}/api/v1/uploads`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    filename: 'smoke_sample.pdf',
    media_type: 'application/pdf',
    size_bytes: file.length,
    sha256,
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

const uploadUrl = new URL(session.data.upload.url, frontendUrl)
const uploadResponse = await fetch(uploadUrl, {
  method: 'PUT',
  headers: { 'Content-Type': 'application/pdf' },
  body: file,
})
if (!uploadResponse.ok) {
  throw new Error(`upload content -> ${uploadResponse.status}`)
}

const completed = await json(
  `${frontendUrl}/api/v1/uploads/${session.data.upload_id}/complete`,
  {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': `integration:${sha256}`,
    },
    body: JSON.stringify({ sha256, expected_size_bytes: file.length }),
  },
)
const job = await waitForJob(completed.data.job_id)
if (job.status !== 'succeeded') {
  throw new Error(`indexing failed: ${JSON.stringify(job.error)}`)
}

const documentId = completed.data.document_id
const document = await json(`${frontendUrl}/api/v1/documents/${documentId}`)
const documents = await json(`${frontendUrl}/api/v1/documents?limit=100`)
const manifest = await json(
  `${frontendUrl}/api/v1/documents/${documentId}/graph/manifest`,
)
const nodes = await json(
  `${frontendUrl}/api/v1/documents/${documentId}/graph/nodes?limit=200`,
)
const edges = await json(
  `${frontendUrl}/api/v1/documents/${documentId}/graph/edges?limit=500`,
)
const extractions = await json(
  `${frontendUrl}/api/v1/documents/${documentId}/extractions`,
)
const artifacts = await json(
  `${frontendUrl}/api/v1/documents/${documentId}/artifacts`,
)
const qa = await json(`${frontendUrl}/api/v1/qa/answers`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({
    question: 'What is annual rainfall?',
    document_ids: [documentId],
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

if (qa.data.answer_status !== 'answered' || !qa.data.citations.length) {
  throw new Error(`question answering did not return grounded citations: ${JSON.stringify(qa)}`)
}

const reindex = await json(
  `${frontendUrl}/api/v1/documents/${documentId}/reindex`,
  {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Idempotency-Key': `integration-reindex:${documentId}:${Date.now()}`,
    },
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
const reindexJob = await waitForJob(reindex.data.job_id)
if (reindexJob.status !== 'succeeded') {
  throw new Error(`reindex failed: ${JSON.stringify(reindexJob.error)}`)
}

console.log(
  JSON.stringify(
    {
      proxy: frontendUrl,
      health: health.data.status,
      ready: ready.status,
      documents: documents.data.length,
      job: {
        status: job.status,
        stage: job.stage,
        progress: job.progress,
      },
      document: {
        id: document.data.document_id,
        status: document.data.status,
        nodes: document.data.counts.nodes,
        edges: document.data.counts.edges,
      },
      graph: {
        manifest: Boolean(manifest.data.validated),
        nodes: nodes.data.length,
        edges: edges.data.length,
      },
      extractions: extractions.data.length,
      artifacts: artifacts.data.length,
      qa: {
        status: qa.data.answer_status,
        grounded: qa.data.grounded,
        citations: qa.data.citations.length,
        toolCalls: qa.data.tool_trace.length,
      },
      reindex: {
        status: reindexJob.status,
        progress: reindexJob.progress,
      },
    },
    null,
    2,
  ),
)

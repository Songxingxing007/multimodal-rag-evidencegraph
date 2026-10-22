import { useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  ApiError,
  askQuestion,
  checkHealth,
  getExtractions,
  getGraph,
  getJob,
  listDocuments,
  reindexDocument,
  uploadPdf,
  type Citation,
  type DocumentView,
  type GraphEdge,
  type GraphNode,
  type JobView,
  type QAResult,
} from './api'

type NavItem = '工作台' | '文档' | '图谱' | '问答' | '任务'
type Tone = 'neutral' | 'success' | 'warning' | 'info'

const navItems: { label: NavItem; icon: string }[] = [
  { label: '工作台', icon: '⌂' },
  { label: '文档', icon: '▤' },
  { label: '图谱', icon: '⌘' },
  { label: '问答', icon: '◌' },
  { label: '任务', icon: '≡' },
]

const stageDefinitions = [
  { keys: ['queued', 'validate'], label: '文件校验' },
  { keys: ['parse', 'mineru_submit', 'mineru_poll', 'mineru_download'], label: '文档解析' },
  { keys: ['extract'], label: '知识抽取' },
  { keys: ['graph_build'], label: '图谱构建' },
  { keys: ['publish', 'done'], label: '快照发布' },
]

const pageMeta: Record<NavItem, { crumb: string; title: string; description: string }> = {
  工作台: { crumb: '概览', title: '知识工作台', description: '将复杂文档转化为可追溯、可验证的知识资产。' },
  文档: { crumb: '文档库', title: '文档库', description: '集中管理文档、解析状态与知识快照。' },
  图谱: { crumb: '图谱探索', title: '知识图谱', description: '围绕实体与关系探索文档中的可验证知识。' },
  问答: { crumb: '问答工作台', title: '证据驱动问答', description: '基于已索引文档提问，每个结论都可回溯到原文。' },
  任务: { crumb: '任务中心', title: '任务中心', description: '监控当前后端可查询的解析与建图任务。' },
}

function Mark({ children, tone = 'neutral' }: { children: ReactNode; tone?: Tone }) {
  return <span className={`mark mark-${tone}`}>{children}</span>
}

function IconButton({ children, label, onClick }: { children: ReactNode; label: string; onClick?: () => void }) {
  return <button type="button" className="icon-button" aria-label={label} onClick={onClick}>{children}</button>
}

function EmptyState({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty-state">
      <strong>{title}</strong>
      <span>{detail}</span>
    </div>
  )
}

function statusLabel(status: DocumentView['status']) {
  const labels: Record<string, string> = {
    uploading: '上传中',
    validating: '校验中',
    queued: '排队中',
    parsing: '解析中',
    extracting: '抽取中',
    graph_building: '建图中',
    indexed: '已索引',
    parse_ready: '仅解析',
    failed: '失败',
    deleting: '删除中',
    deleted: '已删除',
  }
  return labels[status] || status
}

function statusTone(status: DocumentView['status']): Tone {
  if (status === 'indexed') return 'success'
  if (['queued', 'parsing', 'extracting', 'graph_building', 'uploading', 'validating'].includes(status)) return 'info'
  if (['failed', 'deleting', 'deleted'].includes(status)) return 'warning'
  return 'neutral'
}

function jobStatusLabel(status: JobView['status']) {
  return {
    queued: '排队中',
    running: '运行中',
    succeeded: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }[status]
}

function answerModeLabel(result: QAResult) {
  if (result.answer_mode === 'general') return '通用模型回答'
  if (result.answer_status === 'insufficient_evidence') return '证据不足'
  if (result.answer_status === 'failed') return '模型不可用'
  if (result.grounded) return '已引用原文'
  return '未验证回答'
}

function answerModeTone(result: QAResult): Tone {
  if (result.answer_mode === 'general') return 'info'
  if (result.answer_status !== 'answered' || !result.grounded) return 'warning'
  return 'success'
}

function stageLabel(stage: string) {
  const labels: Record<string, string> = {
    queued: '排队',
    validate: '文件校验',
    parse: '文档解析',
    mineru_submit: '提交解析',
    mineru_poll: '云端解析',
    mineru_download: '下载产物',
    extract: '知识抽取',
    graph_build: '图谱构建',
    publish: '快照发布',
    done: '已完成',
    cancelled: '已取消',
  }
  return labels[stage] || stage
}

function nodeTypeLabel(type: string) {
  return {
    location: '位置',
    metric: '指标',
    equipment: '设备',
    person: '人物',
    organization: '组织',
    concept: '概念',
    other: '其他',
  }[type.toLowerCase()] || type
}

function nodeClass(type: string) {
  if (type === 'location') return 'node-location'
  if (type === 'metric') return 'node-metric'
  return 'node-concept'
}

function formatDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function formatSize(bytes: number) {
  if (!bytes) return '—'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

function errorText(error: unknown) {
  if (error instanceof ApiError) return `${error.code}：${error.message}`
  return error instanceof Error ? error.message : String(error)
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

export default function App() {
  const [activeNav, setActiveNav] = useState<NavItem>('工作台')
  const [documents, setDocuments] = useState<DocumentView[]>([])
  const [jobs, setJobs] = useState<Record<string, JobView>>({})
  const [loadingDocuments, setLoadingDocuments] = useState(true)
  const [systemOnline, setSystemOnline] = useState<boolean | null>(null)
  const [selectedFile, setSelectedFile] = useState<File | null>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadStatus, setUploadStatus] = useState('')
  const [notice, setNotice] = useState('')
  const [question, setQuestion] = useState('')
  const [submittedQuestion, setSubmittedQuestion] = useState('')
  const [qaResult, setQaResult] = useState<QAResult | null>(null)
  const [qaLoading, setQaLoading] = useState(false)
  const [selectedDocumentIds, setSelectedDocumentIds] = useState<string[]>([])
  const [selectedCitationId, setSelectedCitationId] = useState('')
  const [showTrace, setShowTrace] = useState(false)
  const [activeJobId, setActiveJobId] = useState<string | null>(null)

  const indexedDocuments = documents.filter((document) => document.status === 'indexed')
  const runningJobs = Object.values(jobs).filter((job) => job.status === 'queued' || job.status === 'running')
  const latestJob = Object.values(jobs).sort((a, b) => b.created_at.localeCompare(a.created_at))[0] || null
  const activeJob = activeJobId ? jobs[activeJobId] || null : runningJobs[0] || latestJob
  const currentPage = pageMeta[activeNav]

  useEffect(() => {
    void loadWorkspace()
  }, [])

  useEffect(() => {
    if (!notice) return
    const timer = window.setTimeout(() => setNotice(''), 6000)
    return () => window.clearTimeout(timer)
  }, [notice])

  async function loadWorkspace() {
    setLoadingDocuments(true)
    const health = checkHealth()
      .then(() => true)
      .catch(() => false)
    try {
      const nextDocuments = await listDocuments()
      setDocuments(nextDocuments)
      setSelectedDocumentIds((current) => {
        const available = nextDocuments.filter((document) => document.status === 'indexed')
        const retained = current.filter((id) => available.some((document) => document.document_id === id))
        return retained.length ? retained : available.slice(0, 1).map((document) => document.document_id)
      })
      await hydrateJobs(nextDocuments)
    } catch (error) {
      setNotice(`后端连接失败：${errorText(error)}`)
    } finally {
      setSystemOnline(await health)
      setLoadingDocuments(false)
    }
  }

  async function hydrateJobs(nextDocuments: DocumentView[]) {
    const jobIds = [...new Set(
      nextDocuments
        .map((document) => document.active_job_id)
        .filter((jobId): jobId is string => Boolean(jobId)),
    )]
    const results = await Promise.allSettled(jobIds.map((jobId) => getJob(jobId)))
    const nextJobs: Record<string, JobView> = {}
    results.forEach((result) => {
      if (result.status === 'fulfilled') nextJobs[result.value.job_id] = result.value
    })
    setJobs(nextJobs)
  }

  function reportMissing(feature: string) {
    setNotice(`${feature}：当前后端 V1 未实现，已按现有接口降级。`)
  }

  function handleFile(file: File | null) {
    if (!file) {
      setSelectedFile(null)
      setUploadStatus('')
      return
    }
    if (!file.name.toLowerCase().endsWith('.pdf') || (file.type && file.type !== 'application/pdf')) {
      setSelectedFile(null)
      setNotice('当前 Graph V1 仅支持 PDF 文件。')
      return
    }
    if (file.size > 200 * 1024 * 1024) {
      setSelectedFile(null)
      setNotice('文件超过后端 200 MB 限制。')
      return
    }
    setSelectedFile(file)
    setUploadStatus('格式预检通过，等待上传')
    setNotice('')
  }

  async function trackJob(jobId: string) {
    setActiveJobId(jobId)
    for (let attempt = 0; attempt < 300; attempt += 1) {
      try {
        const job = await getJob(jobId)
        setJobs((current) => ({ ...current, [job.job_id]: job }))
        setUploadStatus(`${stageLabel(job.stage)} · ${job.progress}%`)
        if (job.status === 'succeeded') {
          setActiveJobId(null)
          setUploadStatus('文档已索引，可开始提问')
          setNotice('解析、抽取和知识图谱发布已完成。')
          await loadWorkspace()
          return
        }
        if (job.status === 'failed' || job.status === 'cancelled') {
          setActiveJobId(null)
          const detail = job.error?.message || '任务未完成'
          setUploadStatus(`${jobStatusLabel(job.status)}：${detail}`)
          setNotice(`${jobStatusLabel(job.status)}：${detail}`)
          await loadWorkspace()
          return
        }
      } catch (error) {
        setNotice(`任务状态查询失败：${errorText(error)}`)
        return
      }
      await delay(700)
    }
    setNotice('任务轮询超时，请稍后在任务中心刷新。')
  }

  async function handleUpload() {
    if (!selectedFile) {
      setNotice('请先选择一个 PDF 文件。')
      return
    }
    setUploading(true)
    setUploadStatus('正在创建上传会话…')
    setNotice('')
    try {
      const created = await uploadPdf(selectedFile)
      setUploadStatus('文件已提交，正在排队…')
      setNotice('文档已进入真实 Indexing Pipeline。')
      setSelectedFile(null)
      await trackJob(created.job_id)
    } catch (error) {
      setUploadStatus(`上传失败：${errorText(error)}`)
      setNotice(`上传失败：${errorText(error)}`)
    } finally {
      setUploading(false)
    }
  }

  async function submitQuestion() {
    const cleanQuestion = question.trim()
    if (!cleanQuestion || qaLoading) return
    const scope = selectedDocumentIds.filter((id) =>
      indexedDocuments.some((document) => document.document_id === id),
    )
    if (!scope.length) {
      setNotice('请选择一个已索引文档作为问答范围。')
      return
    }
    setQaLoading(true)
    setSubmittedQuestion(cleanQuestion)
    setQuestion('')
    setActiveNav('问答')
    try {
      const result = await askQuestion(cleanQuestion, scope, qaResult?.conversation_id)
      setQaResult(result)
      setSelectedCitationId(result.citations[0]?.citation_id || '')
      if (result.answer_status === 'insufficient_evidence') {
        setNotice('后端返回：当前资料不足以回答该问题。')
      }
    } catch (error) {
      setNotice(`问答失败：${errorText(error)}`)
    } finally {
      setQaLoading(false)
    }
  }

  async function handleReindex(documentId: string) {
    try {
      const created = await reindexDocument(documentId)
      setNotice('重新索引任务已创建。')
      setActiveNav('任务')
      await trackJob(created.job_id)
    } catch (error) {
      setNotice(`重索引失败：${errorText(error)}`)
    }
  }

  function askDocument(documentId: string) {
    setSelectedDocumentIds([documentId])
    setQaResult(null)
    setSubmittedQuestion('')
    setSelectedCitationId('')
    setShowTrace(false)
    setActiveNav('问答')
  }

  function clearAnswerContext(clearQuestion = false) {
    setQaResult(null)
    setSubmittedQuestion('')
    setSelectedCitationId('')
    setShowTrace(false)
    if (clearQuestion) setQuestion('')
  }

  function newConversation() {
    clearAnswerContext(true)
    setNotice('已清空当前客户端会话；历史会话接口当前后端未实现。')
  }

  function toggleDocumentScope(documentId: string) {
    clearAnswerContext()
    setSelectedDocumentIds((current) => {
      if (current.includes(documentId)) {
        return current.length === 1 ? current : current.filter((id) => id !== documentId)
      }
      return [...current, documentId].slice(0, 10)
    })
  }

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><span></span><span></span><span></span></div>
          <div><strong>EvidenceGraph</strong><small>知识工作台</small></div>
        </div>

        <div className="workspace">
          <div className="workspace-avatar">AI</div>
          <div><small>工作空间</small><strong>本地知识服务</strong></div>
          <span className="chevron">⌄</span>
        </div>

        <nav className="navigation" aria-label="主导航">
          {navItems.map((item) => (
            <button
              key={item.label}
              className={`nav-item ${activeNav === item.label ? 'active' : ''}`}
              onClick={() => setActiveNav(item.label)}
            >
              <span className="nav-icon" aria-hidden="true">{item.icon}</span>{item.label}
              {item.label === '任务' && runningJobs.length > 0 && <span className="nav-count">{runningJobs.length}</span>}
            </button>
          ))}
        </nav>

        <div className="sidebar-bottom">
          <div className="service-state">
            <span className={systemOnline === false ? 'offline-dot' : ''}></span>
            {systemOnline === null ? '正在检查知识服务' : systemOnline ? '知识服务运行正常' : '后端服务不可用'}
          </div>
          <button type="button" className="user-card" onClick={() => reportMissing('用户配置')}>
            <div className="user-avatar">本</div>
            <div><strong>本地用户</strong><small>V1 单租户模式</small></div>
            <span>···</span>
          </button>
        </div>
      </aside>

      <section className="main-content">
        <header className="topbar">
          <div className="crumb"><span>EvidenceGraph</span><span className="crumb-sep">/</span><strong>{currentPage.crumb}</strong></div>
          <div className="top-actions">
            <button className="help-button" onClick={() => setNotice('请通过文档、图谱、问答和任务页面使用真实后端数据。')}>使用说明</button>
            <IconButton label="查看通知" onClick={() => reportMissing('通知中心')}>♧</IconButton>
          </div>
        </header>
        {notice && <div className="toast" role="status">{notice}<button type="button" aria-label="关闭提示" onClick={() => setNotice('')}>×</button></div>}

        <div className="page">
          <section className="intro">
            <div>
              <div className="eyebrow"><span></span>{systemOnline === false ? '后端离线' : '真实数据已连接'}</div>
              <h1>{currentPage.title}</h1>
              <p>{currentPage.description}</p>
            </div>
            <div className="date-block">
              <strong>{new Intl.DateTimeFormat('zh-CN', { dateStyle: 'long' }).format(new Date())}</strong>
              <span>{new Intl.DateTimeFormat('zh-CN', { weekday: 'long', hour: '2-digit', minute: '2-digit' }).format(new Date())}</span>
            </div>
          </section>

          {activeNav === '工作台' && (
            <DashboardContent
              documents={documents}
              jobs={jobs}
              activeJob={activeJob}
              loading={loadingDocuments}
              selectedFile={selectedFile}
              uploading={uploading}
              uploadStatus={uploadStatus}
              qaResult={qaResult}
              submittedQuestion={submittedQuestion}
              question={question}
              setQuestion={setQuestion}
              submitQuestion={submitQuestion}
              qaLoading={qaLoading}
              selectedCitationId={selectedCitationId}
              setSelectedCitationId={setSelectedCitationId}
              showTrace={showTrace}
              setShowTrace={setShowTrace}
              setActiveNav={setActiveNav}
              onFile={handleFile}
              onUpload={handleUpload}
              onMissing={reportMissing}
            />
          )}
          {activeNav === '文档' && (
            <DocumentsPage
              documents={documents}
              jobs={jobs}
              loading={loadingDocuments}
              onUpload={() => setActiveNav('工作台')}
              onAsk={askDocument}
              onTasks={() => setActiveNav('任务')}
              onMissing={reportMissing}
              setNotice={setNotice}
            />
          )}
          {activeNav === '图谱' && (
            <GraphPage
              documents={documents}
              onMissing={reportMissing}
              setNotice={setNotice}
            />
          )}
          {activeNav === '问答' && (
            <AskPage
              documents={documents}
              selectedDocumentIds={selectedDocumentIds}
              toggleDocumentScope={toggleDocumentScope}
              question={question}
              setQuestion={setQuestion}
              submitQuestion={submitQuestion}
              submittedQuestion={submittedQuestion}
              qaResult={qaResult}
              qaLoading={qaLoading}
              selectedCitationId={selectedCitationId}
              setSelectedCitationId={setSelectedCitationId}
              showTrace={showTrace}
              setShowTrace={setShowTrace}
              newConversation={newConversation}
              onMissing={reportMissing}
            />
          )}
          {activeNav === '任务' && (
            <TasksPage
              documents={documents}
              jobs={jobs}
              loading={loadingDocuments}
              onReindex={handleReindex}
              onRefresh={loadWorkspace}
              onAsk={askDocument}
              onMissing={reportMissing}
            />
          )}
        </div>
      </section>
    </main>
  )
}

interface DashboardProps {
  documents: DocumentView[]
  jobs: Record<string, JobView>
  activeJob: JobView | null
  loading: boolean
  selectedFile: File | null
  uploading: boolean
  uploadStatus: string
  qaResult: QAResult | null
  submittedQuestion: string
  question: string
  setQuestion: (value: string) => void
  submitQuestion: () => void
  qaLoading: boolean
  selectedCitationId: string
  setSelectedCitationId: (value: string) => void
  showTrace: boolean
  setShowTrace: (value: boolean) => void
  setActiveNav: (value: NavItem) => void
  onFile: (file: File | null) => void
  onUpload: () => void
  onMissing: (feature: string) => void
}

function DashboardContent(props: DashboardProps) {
  const totalNodes = props.documents.reduce((sum, document) => sum + document.counts.nodes, 0)
  const indexedCount = props.documents.filter((document) => document.status === 'indexed').length
  const runningCount = Object.values(props.jobs).filter((job) => ['queued', 'running'].includes(job.status)).length
  const recentDocuments = [...props.documents].sort((a, b) => b.updated_at.localeCompare(a.updated_at)).slice(0, 3)
  const activeDocument = props.activeJob
    ? props.documents.find((document) => document.document_id === props.activeJob?.document_id)
    : null
  const selectedCitation = props.qaResult?.citations.find((citation) => citation.citation_id === props.selectedCitationId)
    || props.qaResult?.citations[0]

  return (
    <>
      <section className="upload-panel">
        <div className="upload-copy">
          <span className="panel-kicker">连接真实 Indexing Pipeline</span>
          <h2>上传文档，构建证据图谱</h2>
          <p>文件将直接提交至后端 MinerU → LangExtract → Graph Snapshot 链路。</p>
          <div className="capabilities"><Mark tone="success">PDF 可建图</Mark><span>最大 200 MB</span><span>当前后端本地测试模式</span></div>
        </div>
        <label className={`dropzone ${props.selectedFile ? 'has-file' : ''}`}>
          <input
            type="file"
            accept=".pdf,application/pdf"
            onChange={(event) => props.onFile(event.target.files?.[0] || null)}
          />
          <div className="upload-symbol">↥</div>
          <div>
            <strong>{props.selectedFile?.name || '拖入 PDF，或点击选择文件'}</strong>
            <span>{props.selectedFile ? formatSize(props.selectedFile.size) : '当前 Graph V1 仅支持 PDF'}</span>
          </div>
        </label>
        <button className="primary-button" onClick={props.onUpload} disabled={props.uploading}>
          {props.uploading ? '正在处理…' : '上传并建图'} <span>→</span>
        </button>
        {props.uploadStatus && <div className="upload-notice" role="status">{props.uploadStatus}</div>}
      </section>

      <section className="metric-grid" aria-label="知识库概览">
        <article className="metric-card"><div className="metric-label"><span className="metric-icon blue">▤</span>全部文档</div><strong>{props.documents.length}</strong><p>来自 /documents</p></article>
        <article className="metric-card"><div className="metric-label"><span className="metric-icon violet">⌘</span>知识节点</div><strong>{totalNodes}</strong><p>{indexedCount} 份文档已索引</p></article>
        <article className="metric-card"><div className="metric-label"><span className="metric-icon green">⊹</span>本次引用</div><strong>{props.qaResult ? props.qaResult.citations.length : '—'}</strong><p>{props.qaResult?.answer_status === 'answered' ? '真实引用' : '先发起一次问答'}</p></article>
        <article className="metric-card"><div className="metric-label"><span className="metric-icon yellow">◷</span>运行中任务</div><strong>{runningCount}</strong><p>由 active_job_id 汇总</p></article>
      </section>

      <div className="work-grid">
        <section className="card task-card">
          <div className="section-heading"><div><span className="panel-kicker">真实任务状态</span><h2>{props.activeJob && ['queued', 'running'].includes(props.activeJob.status) ? '正在处理' : '最近任务'}</h2></div><button className="quiet-button" onClick={() => props.setActiveNav('任务')}>任务中心 <span>→</span></button></div>
          {props.loading ? <EmptyState title="正在读取任务" detail="等待后端返回文档和 active_job 状态。" /> : props.activeJob ? (
            <>
              <article className="task-item">
                <div className="doc-icon">PDF</div>
                <div className="task-main">
                  <div className="task-title"><strong>{activeDocument?.filename || props.activeJob.document_id}</strong><Mark tone={props.activeJob.status === 'failed' ? 'warning' : 'info'}>{jobStatusLabel(props.activeJob.status)}</Mark></div>
                  <div className="progress-meta"><span>{stageLabel(props.activeJob.stage)}</span><strong>{props.activeJob.progress}%</strong></div>
                  <div className="progress-track"><span style={{ width: `${props.activeJob.progress}%` }}></span></div>
                  <div className="task-time">{props.activeJob.job_id} · 第 {props.activeJob.attempt} 次尝试</div>
                </div>
              </article>
              <div className="stage-line">
                {stageDefinitions.map((stage, index) => {
                  const stageState = props.activeJob?.stages.find((item) => stage.keys.includes(item.name))?.status
                  const stateLabel = stageState === 'succeeded' ? '已完成' : stageState === 'running' ? '进行中' : stageState === 'failed' ? '失败' : '等待中'
                  return <div className={`stage ${stageState === 'succeeded' ? 'done' : stageState === 'running' ? 'current' : ''}`} key={stage.label}>
                    <span>{stageState === 'succeeded' ? '✓' : index + 1}</span><div><strong>{stage.label}</strong><small>{stateLabel}</small></div>
                  </div>
                })}
              </div>
            </>
          ) : (
            <EmptyState title="暂无运行中任务" detail="上传文档后，这里会显示后端真实阶段和进度。" />
          )}
        </section>

        <section className="card quality-card">
          <div className="section-heading"><div><span className="panel-kicker">当前可用统计</span><h2>索引质量</h2></div><Mark tone="warning">聚合质量接口未实现</Mark></div>
          <div className="quality-score">
            <div className="score-ring"><strong>{indexedCount}</strong><span>/{props.documents.length}</span></div>
            <div><strong>索引就绪率</strong><p>基于文档状态接口实时计算</p></div>
          </div>
          <div className="quality-row"><span>索引完成</span><div className="mini-track"><i style={{ width: `${props.documents.length ? indexedCount / props.documents.length * 100 : 0}%` }}></i></div><strong>{indexedCount}</strong></div>
          <div className="quality-row"><span>失败文档</span><div className="mini-track"><i style={{ width: `${props.documents.length ? props.documents.filter((document) => document.status === 'failed').length / props.documents.length * 100 : 0}%` }}></i></div><strong>{props.documents.filter((document) => document.status === 'failed').length}</strong></div>
          <div className="quality-alert"><span>!</span><p><strong>未提供</strong> 全局实体对齐率与历史引用覆盖率。</p></div>
        </section>
      </div>

      <div className="bottom-grid">
        <section className="card documents-card">
          <div className="section-heading"><div><span className="panel-kicker">实时文档</span><h2>最近文档</h2></div><button className="quiet-button" onClick={() => props.setActiveNav('文档')}>文档库 <span>→</span></button></div>
          <div className="document-row header-row"><span>文档</span><span>状态</span><span>规模</span><span>更新时间</span></div>
          {recentDocuments.length ? recentDocuments.map((document, index) => (
            <button className="document-row" key={document.document_id} onClick={() => document.capabilities.queryable ? props.setActiveNav('问答') : props.setActiveNav('任务')}>
              <div className="document-name"><span className={`file-tile tile-${index % 3}`}>PDF</span><strong>{document.filename}</strong></div>
              <Mark tone={statusTone(document.status)}>{statusLabel(document.status)}</Mark>
              <span>{document.counts.pages} 页 · {document.counts.nodes} 节点</span><span>{formatDate(document.updated_at)}</span>
            </button>
          )) : <EmptyState title="文档库为空" detail="上传 PDF 后会显示后端真实文档。" />}
        </section>

        <section className="card ask-card">
          <div className="section-heading"><div><span className="panel-kicker">真实问答</span><h2>继续提问</h2></div><Mark tone="success">已选 {props.qaResult?.used_documents.length || 0} 份</Mark></div>
          {props.qaResult ? (
            <div className="ask-answer">
              <div className="answer-top"><span className="answer-spark">✦</span><strong>{answerModeLabel(props.qaResult)}</strong><span>{props.qaResult.finish_reason}</span></div>
              <p>{props.submittedQuestion}</p>
              <div className="answer-result"><span>{props.qaResult.citations.length} 条可验证引用</span><button onClick={() => props.setActiveNav('问答')}>打开会话 →</button></div>
            </div>
          ) : <EmptyState title="还没有真实问答结果" detail="当前后端未提供历史会话查询接口。" />}
          <div className="inline-ask">
            <input aria-label="输入问题" value={props.question} onChange={(event) => props.setQuestion(event.target.value)} onKeyDown={(event) => event.key === 'Enter' && props.submitQuestion()} placeholder="基于已索引文档提问…" />
            <button aria-label="发送问题" onClick={props.submitQuestion} disabled={props.qaLoading}>{props.qaLoading ? '…' : '↑'}</button>
          </div>
        </section>
      </div>

      <section className="card evidence-preview">
        <div className="section-heading"><div><span className="panel-kicker">后端 citation 数据</span><h2>证据与原文定位</h2></div><button className="quiet-button" onClick={() => props.setActiveNav('问答')}>问答工作台 <span>→</span></button></div>
        {props.qaResult && selectedCitation ? (
          <div className="evidence-layout">
            <div className="answer-panel">
              <div className="answer-top"><span className="answer-spark">✦</span><Mark tone={answerModeTone(props.qaResult)}>{answerModeLabel(props.qaResult)}</Mark><span>{props.qaResult.route_reason || `工具调用 ${props.qaResult.tool_trace.length} 次`}</span></div>
              <h3>{props.submittedQuestion}</h3>
              <p className="answer-body">{props.qaResult.answer}</p>
              <div className="citation-list">{props.qaResult.citations.map((citation) => (
                <button key={citation.citation_id} className={`citation ${props.selectedCitationId === citation.citation_id ? 'selected' : ''}`} onClick={() => props.setSelectedCitationId(citation.citation_id)}>
                  <span>[{citation.citation_id.replace('cit_', '')}]</span>第 {citation.page_number} 页 · {citation.filename}
                </button>
              ))}</div>
              <button className="trace-button" onClick={() => props.setShowTrace(!props.showTrace)}>{props.showTrace ? '⌃' : '⌄'} Agent 检索轨迹 <span>{props.qaResult.tool_trace.length} 个步骤</span></button>
              {props.showTrace && <ol className="trace">{props.qaResult.tool_trace.map((item) => <li key={item.step}>{item.tool} · {item.hits} 命中 · {item.latency_ms.toFixed(1)} ms</li>)}</ol>}
            </div>
            <aside className="source-panel">
              <div className="source-head"><div><span>原文证据</span><strong>{selectedCitation.filename}</strong></div><IconButton label="关闭证据预览" onClick={() => props.setSelectedCitationId('')}>×</IconButton></div>
              <div className="paper">
                <div className="paper-header"><span>{selectedCitation.source_kind}</span><span>第 {selectedCitation.page_number} 页</span></div>
                <p>{selectedCitation.evidence}</p>
                <div className="highlight-box"><strong>{selectedCitation.char_interval ? `[${selectedCitation.char_interval.start_pos}, ${selectedCitation.char_interval.end_pos})` : '无字符区间'}</strong></div>
              </div>
              <div className="source-meta"><span>block：{selectedCitation.block_id || '—'}</span><button onClick={() => navigator.clipboard?.writeText(selectedCitation.evidence)}>复制证据</button></div>
            </aside>
          </div>
        ) : <EmptyState title="暂无引用" detail="发起一次可回答问题后，这里会展示后端返回的结构化 citations。" />}
      </section>
    </>
  )
}

interface DocumentsProps {
  documents: DocumentView[]
  jobs: Record<string, JobView>
  loading: boolean
  onUpload: () => void
  onAsk: (documentId: string) => void
  onTasks: () => void
  onMissing: (feature: string) => void
  setNotice: (value: string) => void
}

function DocumentsPage(props: DocumentsProps) {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<'全部' | '已索引' | '处理中' | '失败'>('全部')
  const [abilityFilter, setAbilityFilter] = useState<'全部' | '可建图' | '仅解析'>('全部')
  const filtered = props.documents.filter((document) => {
    const keyword = search.trim().toLowerCase()
    const searchable = `${document.filename} ${document.media_type} ${document.document_id}`.toLowerCase()
    if (keyword && !searchable.includes(keyword)) return false
    if (abilityFilter === '可建图' && document.indexing_profile !== 'graph_v1') return false
    if (abilityFilter === '仅解析' && document.indexing_profile !== 'parse_only') return false
    if (statusFilter === '已索引' && document.status !== 'indexed') return false
    if (statusFilter === '失败' && document.status !== 'failed') return false
    if (statusFilter === '处理中' && !['queued', 'parsing', 'extracting', 'graph_building'].includes(document.status)) return false
    return true
  })
  const cycleStatus = () => setStatusFilter((value) => value === '全部' ? '已索引' : value === '已索引' ? '处理中' : value === '处理中' ? '失败' : '全部')
  const cycleAbility = () => setAbilityFilter((value) => value === '全部' ? '可建图' : value === '可建图' ? '仅解析' : '全部')

  return (
    <section className="card list-page">
      <div className="list-toolbar">
        <div className="search-box">⌕ <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索文件名或文档 ID…" /></div>
        <button className="primary-button" onClick={props.onUpload}>上传文档 <span>+</span></button>
      </div>
      <div className="filter-row">
        <Mark tone="info">全部文档 · {props.documents.length}</Mark>
        <button onClick={cycleStatus}>状态：{statusFilter}⌄</button>
        <button onClick={cycleAbility}>能力：{abilityFilter}⌄</button>
        <button onClick={() => props.onMissing('按时间筛选')}>时间筛选 · 未实现</button>
        <Mark tone="warning">实体全局搜索未实现</Mark>
      </div>
      <div className="document-table">
        <div className="table-grid table-head"><span>文档</span><span>能力</span><span>状态</span><span>规模</span><span>更新时间</span><span></span></div>
        {props.loading ? <EmptyState title="正在加载文档" detail="数据来自 GET /api/v1/documents。" /> : filtered.length ? filtered.map((document, index) => {
          const capability = document.indexing_profile === 'graph_v1' ? '可建图' : '仅解析'
          const action = document.capabilities.queryable
            ? '提问'
            : document.active_job_id && props.jobs[document.active_job_id]?.status === 'running'
              ? '查看任务'
              : '查看'
          return (
            <div className="table-grid" key={document.document_id}>
              <div className="document-name">
                <span className={`file-tile tile-${index % 3}`}>PDF</span>
                <div><strong>{document.filename}</strong><small>{document.media_type} · {formatSize(document.size_bytes)}</small></div>
              </div>
              <Mark tone={capability === '可建图' ? 'success' : 'warning'}>{capability}</Mark>
              <Mark tone={statusTone(document.status)}>{statusLabel(document.status)}</Mark>
              <span>{document.counts.pages} 页 · {document.counts.nodes} 节点</span>
              <span>{formatDate(document.updated_at)}</span>
              <button className="row-action" onClick={() => {
                if (document.capabilities.queryable) props.onAsk(document.document_id)
                else if (document.active_job_id) props.onTasks()
                else props.setNotice(`${document.filename}：${statusLabel(document.status)}，当前页面未实现文档详情视图。`)
              }}>{action} →</button>
            </div>
          )
        }) : <EmptyState title="没有匹配文档" detail="清除当前筛选条件或上传 PDF。" />}
      </div>
    </section>
  )
}

interface GraphProps {
  documents: DocumentView[]
  onMissing: (feature: string) => void
  setNotice: (value: string) => void
}

function GraphPage(props: GraphProps) {
  const indexed = props.documents.filter((document) => document.status === 'indexed')
  const [documentId, setDocumentId] = useState(indexed[0]?.document_id || '')
  const [nodes, setNodes] = useState<GraphNode[]>([])
  const [edges, setEdges] = useState<GraphEdge[]>([])
  const [extractionCount, setExtractionCount] = useState(0)
  const [selectedNodeId, setSelectedNodeId] = useState('')
  const [nodeFilter, setNodeFilter] = useState('全部')
  const [relationFilter, setRelationFilter] = useState('全部')
  const [zoom, setZoom] = useState(1)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!documentId && indexed.length) setDocumentId(indexed[0].document_id)
  }, [documentId, indexed])

  useEffect(() => {
    if (!documentId) {
      setNodes([])
      setEdges([])
      setExtractionCount(0)
      return
    }
    let alive = true
    setLoading(true)
    setError('')
    Promise.all([getGraph(documentId), getExtractions(documentId)])
      .then(([graph, extractions]) => {
        if (!alive) return
        setNodes(graph.nodes)
        setEdges(graph.edges)
        setExtractionCount(extractions.length)
        setSelectedNodeId(graph.nodes[0]?.id || '')
        setNodeFilter('全部')
        setRelationFilter('全部')
      })
      .catch((reason) => {
        if (alive) setError(errorText(reason))
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [documentId])

  const nodeTypes = [...new Set(nodes.map((node) => node.type))].sort()
  const relationTypes = [...new Set(edges.map((edge) => edge.type))].sort()
  const visibleNodes = nodes.filter((node) => nodeFilter === '全部' || node.type === nodeFilter)
  const visibleEdges = edges.filter((edge) => relationFilter === '全部' || edge.type === relationFilter)
  const selectedNode = nodes.find((node) => node.id === selectedNodeId) || visibleNodes[0]
  const positions = useMemo(() => {
    const result: Record<string, { x: number; y: number }> = {}
    const source = visibleNodes.length ? visibleNodes : nodes
    source.forEach((node, index) => {
      const angle = source.length === 1 ? 0 : (index / source.length) * Math.PI * 2 - Math.PI / 2
      result[node.id] = {
        x: 50 + Math.cos(angle) * 31,
        y: 50 + Math.sin(angle) * 30,
      }
    })
    return result
  }, [visibleNodes, nodes])
  const nextNodeType = () => {
    const options = ['全部', ...nodeTypes]
    setNodeFilter((current) => options[(options.indexOf(current) + 1) % options.length] || '全部')
  }
  const nextRelationType = () => {
    const options = ['全部', ...relationTypes]
    setRelationFilter((current) => options[(options.indexOf(current) + 1) % options.length] || '全部')
  }

  return (
    <section className="graph-page">
      <div className="graph-tools">
        <div>
          <select className="graph-select" value={documentId} onChange={(event) => setDocumentId(event.target.value)}>
            {indexed.map((document) => <option key={document.document_id} value={document.document_id}>{document.filename}</option>)}
          </select>
          <Mark tone="success">{visibleNodes.length} 个可见节点</Mark>
          <Mark tone="info">{extractionCount} 条抽取</Mark>
          <button type="button" onClick={nextNodeType}>节点类型：{nodeFilter === '全部' ? '全部' : nodeTypeLabel(nodeFilter)}⌄</button>
          <button type="button" onClick={nextRelationType}>关系类型：{relationFilter}⌄</button>
          <button type="button" onClick={() => props.onMissing('跨文档全局图谱')}>全局图谱 · 未实现</button>
        </div>
        <div>
          <IconButton label="缩小" onClick={() => setZoom((value) => Math.max(0.7, value - 0.1))}>−</IconButton>
          <IconButton label="放大" onClick={() => setZoom((value) => Math.min(1.4, value + 0.1))}>+</IconButton>
          <IconButton label="重置图谱" onClick={() => { setZoom(1); setNodeFilter('全部'); setRelationFilter('全部'); setSelectedNodeId(nodes[0]?.id || '') }}>⟲</IconButton>
        </div>
      </div>
      <div className="graph-canvas">
        {loading ? <div className="graph-empty"><EmptyState title="正在读取知识图谱" detail="调用 manifest、nodes 与 edges 接口。" /></div>
          : error ? <div className="graph-empty"><EmptyState title="图谱不可用" detail={error} /></div>
            : !indexed.length ? <div className="graph-empty"><EmptyState title="没有已索引文档" detail="请先完成一份 PDF 的解析与图谱发布。" /></div>
              : (
                <div className="graph-stage" style={{ transform: `scale(${zoom})` }}>
                  <svg viewBox="0 0 800 460" preserveAspectRatio="none" aria-hidden="true">
                    {visibleEdges.map((edge) => {
                      const source = positions[edge.source_id]
                      const target = positions[edge.target_id]
                      if (!source || !target) return null
                      return <line key={edge.id} x1={source.x * 8} y1={source.y * 4.6} x2={target.x * 8} y2={target.y * 4.6} />
                    })}
                  </svg>
                  {visibleNodes.map((node) => (
                    <button
                      type="button"
                      className={`graph-node ${nodeClass(node.type)} ${selectedNode?.id === node.id ? 'selected-node' : ''}`}
                      style={{ left: `${positions[node.id]?.x || 50}%`, top: `${positions[node.id]?.y || 50}%` }}
                      key={node.id}
                      onClick={() => setSelectedNodeId(node.id)}
                    >{node.name}</button>
                  ))}
                </div>
              )}
        <div className="graph-legend">
          <strong>缩放 {Math.round(zoom * 100)}%</strong>
          <span><i className="location"></i>位置</span>
          <span><i className="metric"></i>指标</span>
          <span><i className="concept"></i>概念</span>
        </div>
      </div>
      <aside className="graph-inspector">
        <span className="panel-kicker">节点检查器</span>
        {selectedNode ? (
          <>
            <h3>{selectedNode.name}</h3>
            <Mark tone={selectedNode.type === 'location' ? 'warning' : selectedNode.type === 'metric' ? 'info' : 'success'}>{nodeTypeLabel(selectedNode.type)}</Mark>
            <p>页码：第 {selectedNode.page_idx + 1} 页 · 证据数 {selectedNode.evidence_count}</p>
            <div className="inspector-evidence">{selectedNode.evidence || '无证据文本'}</div>
            <p>字符区间：{selectedNode.char_interval ? `[${selectedNode.char_interval.start_pos}, ${selectedNode.char_interval.end_pos})` : '无'}</p>
            <button type="button" className="primary-button" onClick={() => props.setNotice('节点证据已在检查器展示；跨页面原文查看接口当前后端未实现。')}>原文定位 · 未实现</button>
          </>
        ) : <EmptyState title="未选择节点" detail="当前文档没有可展示的图节点。" />}
      </aside>
    </section>
  )
}

interface AskProps {
  documents: DocumentView[]
  selectedDocumentIds: string[]
  toggleDocumentScope: (documentId: string) => void
  question: string
  setQuestion: (value: string) => void
  submitQuestion: () => void
  submittedQuestion: string
  qaResult: QAResult | null
  qaLoading: boolean
  selectedCitationId: string
  setSelectedCitationId: (value: string) => void
  showTrace: boolean
  setShowTrace: (value: boolean) => void
  newConversation: () => void
  onMissing: (feature: string) => void
}

function AskPage(props: AskProps) {
  const indexed = props.documents.filter((document) => document.status === 'indexed')
  const selectedCitation: Citation | undefined = props.qaResult?.citations.find((citation) => citation.citation_id === props.selectedCitationId)
    || props.qaResult?.citations[0]

  return (
    <section className="ask-workbench">
      <aside className="conversations">
        <button className="primary-button" onClick={props.newConversation}>+ 新建会话</button>
        <span className="panel-kicker">最近会话</span>
        <Mark tone="warning">会话历史接口未实现</Mark>
        {props.qaResult && <button className="conversation active">{props.submittedQuestion || '当前会话'}<span>刚刚 · {props.qaResult.citations.length} 条引用</span></button>}
        <div className="scope-picker">
          <span className="panel-kicker">文档范围</span>
          {indexed.length ? indexed.map((document) => (
            <button
              type="button"
              key={document.document_id}
              className={`scope-option ${props.selectedDocumentIds.includes(document.document_id) ? 'selected' : ''}`}
              onClick={() => props.toggleDocumentScope(document.document_id)}
            >
              {props.selectedDocumentIds.includes(document.document_id) ? '✓ ' : ''}{document.filename}
            </button>
          )) : <Mark tone="warning">暂无已索引文档</Mark>}
        </div>
      </aside>
      <div className="thread">
        <div className="scope">
          <span>当前范围</span>
          {selectedDocumentNames(props.documents, props.selectedDocumentIds).map((name) => <Mark tone="success" key={name}>{name}</Mark>)}
          {!props.selectedDocumentIds.length && <Mark tone="warning">请选择文档</Mark>}
        </div>
        {props.submittedQuestion && <div className="user-message">{props.submittedQuestion}</div>}
        {props.qaLoading ? (
          <div className="thread-answer"><div className="answer-top"><span className="answer-spark">✦</span><Mark tone="info">Agent 正在检索</Mark></div><p>正在调用知识图谱、页文本与证据接口…</p></div>
        ) : props.qaResult ? (
          <div className="thread-answer">
            <div className="answer-top">
              <span className="answer-spark">✦</span>
              <Mark tone={answerModeTone(props.qaResult)}>{answerModeLabel(props.qaResult)}</Mark>
              <span>{props.qaResult.tool_trace.length} 次工具调用</span>
            </div>
            <p className="answer-body">{props.qaResult.answer}</p>
            <div className="citation-list">{props.qaResult.citations.map((citation) => (
              <button key={citation.citation_id} className={`citation ${selectedCitation?.citation_id === citation.citation_id ? 'selected' : ''}`} onClick={() => props.setSelectedCitationId(citation.citation_id)}>
                [{citation.citation_id.replace('cit_', '')}] 第 {citation.page_number} 页 · {citation.filename}
              </button>
            ))}</div>
            <button className="trace-button" onClick={() => props.setShowTrace(!props.showTrace)}>{props.showTrace ? '⌃' : '⌄'} Agent 检索轨迹 <span>{props.qaResult.tool_trace.length} 个步骤</span></button>
            {props.showTrace && <ol className="trace">{props.qaResult.tool_trace.map((item) => <li key={item.step}>{item.tool} · {item.hits} 命中 · {item.latency_ms.toFixed(1)} ms</li>)}</ol>}
          </div>
        ) : (
          <div className="thread-answer"><EmptyState title="等待真实提问" detail="选择已索引文档后输入问题；回答与引用来自 POST /qa/answers。" /></div>
        )}
        <div className="thread-input">
          <textarea
            value={props.question}
            onChange={(event) => props.setQuestion(event.target.value)}
            placeholder="输入问题，Enter 发送，Shift + Enter 换行"
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) {
                event.preventDefault()
                props.submitQuestion()
              }
            }}
          />
          <button onClick={props.submitQuestion} disabled={props.qaLoading}>{props.qaLoading ? '…' : '↑'}</button>
        </div>
      </div>
      <aside className="evidence-sidebar">
        <span className="panel-kicker">原文证据</span>
        {selectedCitation ? (
          <>
            <h3>{selectedCitation.filename}</h3>
            <div className="paper">
              <div className="paper-header"><span>{selectedCitation.source_kind}</span><span>第 {selectedCitation.page_number} 页</span></div>
              <p>{selectedCitation.evidence}</p>
              <div className="highlight-box"><strong>{selectedCitation.char_interval ? `字符区间 [${selectedCitation.char_interval.start_pos}, ${selectedCitation.char_interval.end_pos})` : '无字符区间'}</strong></div>
            </div>
            <p className="evidence-position">block：{selectedCitation.block_id || '—'}</p>
            <button type="button" className="quiet-button" onClick={() => props.onMissing('PDF 页面可视化定位')}>打开 PDF 高亮 · 未实现</button>
          </>
        ) : <EmptyState title="暂无引用" detail="完成一次 grounded 问答后显示后端证据。" />}
      </aside>
    </section>
  )
}

function selectedDocumentNames(documents: DocumentView[], ids: string[]) {
  return ids
    .map((id) => documents.find((document) => document.document_id === id)?.filename)
    .filter((name): name is string => Boolean(name))
}

interface TasksProps {
  documents: DocumentView[]
  jobs: Record<string, JobView>
  loading: boolean
  onReindex: (documentId: string) => void
  onRefresh: () => void
  onAsk: (documentId: string) => void
  onMissing: (feature: string) => void
}

function TasksPage(props: TasksProps) {
  const [statusFilter, setStatusFilter] = useState<'全部' | JobView['status']>('全部')
  const rows = props.documents.flatMap((document) => {
    const job = document.active_job_id ? props.jobs[document.active_job_id] : undefined
    return job ? [{ document, job }] : []
  })
  const filtered = statusFilter === '全部' ? rows : rows.filter((row) => row.job.status === statusFilter)
  const completed = rows.filter((row) => row.job.finished_at && row.job.started_at)
  const averageMs = completed.length
    ? completed.reduce((sum, row) => sum + (new Date(row.job.finished_at!).getTime() - new Date(row.job.started_at!).getTime()), 0) / completed.length
    : 0
  const cycleStatus = () => {
    const values: Array<'全部' | JobView['status']> = ['全部', 'queued', 'running', 'succeeded', 'failed', 'cancelled']
    setStatusFilter((current) => values[(values.indexOf(current) + 1) % values.length])
  }

  return (
    <section className="card list-page">
      <div className="task-stat-grid">
        <article><span>排队中</span><strong>{rows.filter((row) => row.job.status === 'queued').length}</strong></article>
        <article><span>运行中</span><strong>{rows.filter((row) => row.job.status === 'running').length}</strong></article>
        <article><span>失败</span><strong className="danger-number">{rows.filter((row) => row.job.status === 'failed').length}</strong></article>
        <article><span>平均耗时</span><strong>{averageMs ? `${(averageMs / 1000).toFixed(1)}s` : '—'}</strong></article>
      </div>
      <div className="filter-row">
        <Mark tone="info">当前任务 · {rows.length}</Mark>
        <button onClick={cycleStatus}>状态：{statusFilter === '全部' ? '全部' : jobStatusLabel(statusFilter)}⌄</button>
        <button onClick={() => props.onMissing('按阶段筛选任务')}>阶段筛选 · 未实现</button>
        <button onClick={() => props.onMissing('按时间筛选任务')}>时间筛选 · 未实现</button>
        <Mark tone="warning">全局任务列表接口未实现</Mark>
        <button onClick={props.onRefresh}>刷新</button>
      </div>
      <div className="job-list">
        {props.loading ? <EmptyState title="正在加载任务" detail="从文档 active_job_id 逐个查询。" />
          : filtered.length ? filtered.map(({ document, job }) => (
            <article className="job-row" key={job.job_id}>
              <div><strong>{document.filename}</strong><small>{job.job_id} · 第 {job.attempt} 次尝试</small></div>
              <span>{stageLabel(job.stage)}</span>
              <div className="job-progress"><i style={{ width: `${job.progress}%` }}></i><em>{job.progress}%</em></div>
              <Mark tone={job.status === 'failed' ? 'warning' : job.status === 'running' ? 'info' : job.status === 'succeeded' ? 'success' : 'neutral'}>{jobStatusLabel(job.status)}</Mark>
              <button className="row-action" onClick={() => {
                if (job.status === 'failed') props.onReindex(document.document_id)
                else if (job.status === 'succeeded' && document.capabilities.queryable) props.onAsk(document.document_id)
                else props.onRefresh()
              }}>{job.status === 'failed' ? '重试' : job.status === 'succeeded' ? '提问' : '刷新'}</button>
            </article>
          )) : <EmptyState title="暂无任务" detail="全局任务列表接口当前后端未实现；这里只展示有 active_job_id 的文档任务。" />}
      </div>
    </section>
  )
}

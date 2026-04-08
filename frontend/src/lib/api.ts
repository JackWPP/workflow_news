import type {
  ChatResponse,
  Conversation,
  ConversationDetail,
  EvaluationSummary,
  QualityFeedback,
  QualityOverview,
  Report,
  ReportSettings,
  RetrievalCandidate,
  RetrievalQuery,
  RetrievalRun,
  SourceRule,
  User,
} from '../types'

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...init,
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(init.headers ?? {}),
    },
  })

  if (!response.ok) {
    const payload = await response.json().catch(() => ({ detail: 'Request failed' }))
    throw new Error(payload.detail ?? 'Request failed')
  }

  return response.json() as Promise<T>
}

export const api = {
  login(email: string, password: string) {
    return request<User>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
  },
  register(email: string, password: string) {
    return request<User>('/api/auth/register', {
      method: 'POST',
      body: JSON.stringify({ email, password }),
    })
  },
  logout() {
    return request<{ status: string }>('/api/auth/logout', { method: 'POST' })
  },
  me() {
    return request<User>('/api/me')
  },
  listReports(limit = 30) {
    return request<{ reports: Report[] }>(`/api/reports?limit=${limit}`)
  },
  todayReport() {
    return request<Report>('/api/reports/today')
  },
  getReport(id: number) {
    return request<Report>(`/api/reports/${id}`)
  },
  runReport() {
    return request<{ run_id: number; status: string }>('/api/reports/run', {
      method: 'POST',
      body: JSON.stringify({ shadow_mode: false }),
    })
  },
  runStatus() {
    return request<{ status: string; run_id: number | null }>('/api/reports/run/status')
  },
  streamProgress(runId: number, handlers: {
    onStep?: (data: any) => void
    onPhase?: (data: any) => void
    onComplete?: (data: any) => void
    onError?: (data: any) => void
  }) {
    const es = new EventSource(`/api/reports/run/${runId}/stream`)
    es.addEventListener('step', (e) => handlers.onStep?.(JSON.parse(e.data)))
    es.addEventListener('phase', (e) => handlers.onPhase?.(JSON.parse(e.data)))
    es.addEventListener('complete', (e) => {
      handlers.onComplete?.(JSON.parse(e.data))
      es.close()
    })
    es.addEventListener('error', (e) => {
      if (e instanceof MessageEvent) {
        handlers.onError?.(JSON.parse(e.data))
      }
      es.close()
    })
    es.addEventListener('done', () => es.close())
    return es
  },
  listConversations() {
    return request<{ conversations: Conversation[] }>('/api/conversations')
  },
  createConversation(title: string) {
    return request<Conversation>('/api/conversations', {
      method: 'POST',
      body: JSON.stringify({ title }),
    })
  },
  getConversation(id: number) {
    return request<ConversationDetail>(`/api/conversations/${id}`)
  },
  async sendChatStream(content: string, conversationId?: number): Promise<ChatResponse> {
    const response = await fetch('/api/chat/stream', {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, conversation_id: conversationId ?? null }),
    })

    if (!response.ok) {
      const payload = await response.json().catch(() => ({ detail: 'Request failed' }))
      throw new Error(payload.detail ?? 'Request failed')
    }

    const raw = await response.text()
    const line = raw
      .split('\n')
      .map((item) => item.trim())
      .find((item) => item.startsWith('data: '))

    if (!line) {
      throw new Error('Invalid stream response')
    }

    return JSON.parse(line.slice(6)) as ChatResponse
  },
  favoriteReport(id: number) {
    return request<{ status: string; item_id: number }>(`/api/favorites/reports/${id}`, { method: 'POST' })
  },
  unfavoriteReport(id: number) {
    return request<{ status: string; item_id: number }>(`/api/favorites/reports/${id}`, { method: 'DELETE' })
  },
  favoriteConversation(id: number) {
    return request<{ status: string; item_id: number }>(`/api/favorites/conversations/${id}`, { method: 'POST' })
  },
  unfavoriteConversation(id: number) {
    return request<{ status: string; item_id: number }>(`/api/favorites/conversations/${id}`, { method: 'DELETE' })
  },
  listRetrievalRuns() {
    return request<{ runs: RetrievalRun[] }>('/api/retrieval-runs')
  },
  getRetrievalQueries(runId: number) {
    return request<{ queries: RetrievalQuery[] }>(`/api/retrieval-runs/${runId}/queries`)
  },
  getRetrievalCandidates(runId: number) {
    return request<{ candidates: RetrievalCandidate[] }>(`/api/retrieval-runs/${runId}/candidates`)
  },
  getSourceRules() {
    return request<{ sources: SourceRule[] }>('/api/admin/source-rules')
  },
  updateSourceRules(sources: SourceRule[]) {
    return request<{ sources: SourceRule[] }>('/api/admin/source-rules', {
      method: 'PUT',
      body: JSON.stringify({ sources }),
    })
  },
  getReportSettings() {
    return request<ReportSettings>('/api/admin/report-settings')
  },
  updateReportSettings(payload: ReportSettings) {
    return request<ReportSettings>('/api/admin/report-settings', {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
  },
  listQualityFeedback(limit = 50) {
    return request<{ items: QualityFeedback[] }>(`/api/admin/quality-feedback?limit=${limit}`)
  },
  createQualityFeedback(payload: {
    target_type: string
    target_id: number
    label: string
    reason?: string
    note?: string
  }) {
    return request<QualityFeedback>('/api/admin/quality-feedback', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
  },
  getQualityOverview(days = 7) {
    return request<QualityOverview>(`/api/admin/quality-overview?days=${days}`)
  },
  getEvaluationSummary(days = 7) {
    return request<EvaluationSummary>(`/api/admin/evaluation-summary?days=${days}`)
  },
  listAgentRuns(limit = 30) {
    return request<any>(`/api/agent-runs?limit=${limit}`)
  },
  getAgentRunTrace(id: number) {
    return request<any>(`/api/agent-runs/${id}/trace`)
  },
  getAgentStepDetail(runId: number, stepNumber: number) {
    return request<any>(`/api/agent-runs/${runId}/steps/${stepNumber}`)
  },
}

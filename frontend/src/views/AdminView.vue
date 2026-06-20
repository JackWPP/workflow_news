<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { Database, Settings, Activity, ShieldAlert, BarChart3, ListTree, Code, Fingerprint, AlertCircle, CheckCircle2 } from 'lucide-vue-next'

import StatusPill from '../components/StatusPill.vue'
import { api } from '../lib/api'
import type {
  EvaluationSummary,
  QualityFeedback,
  QualityOverview,
  ReportSettings,
  RetrievalCandidate,
  RetrievalQuery,
  RetrievalRun,
  SourceRule,
} from '../types'

const sourcesJson = ref('[]')
const runs = ref<RetrievalRun[]>([])
const selectedRunId = ref<number | null>(null)
const queries = ref<RetrievalQuery[]>([])
const candidates = ref<RetrievalCandidate[]>([])
const feedbackItems = ref<QualityFeedback[]>([])
const qualityOverview = ref<QualityOverview | null>(null)
const evaluationSummary = ref<EvaluationSummary | null>(null)
const settings = ref<ReportSettings>({
  report_hour: 10,
  report_minute: 0,
  ai_report_enabled: true,
  ai_report_hour: 10,
  ai_report_minute: 5,
  ai_rss_feed_url: 'https://imjuya.github.io/juya-ai-daily/rss.xml',
  shadow_mode: true,
  scrape_timeout_seconds: 20,
  scrape_concurrency: 3,
  max_extractions_per_run: 18,
  report_primary_model: 'google/gemini-3-flash-preview',
  report_fallback_model: 'minimax/minimax-m2.7',
})
const error = ref('')
const saved = ref('')
const wechatTokenConfigured = ref(false)
const wechatTokenInput = ref('')
const wechatCookieInput = ref('')
const wechatAccountName = ref('英蓝云展')
const wechatSyncing = ref(false)
const wechatSyncResult = ref('')
const wechatSyncPages = ref(0)
const wechatSyncArticles = ref(0)
let wechatPollTimer: ReturnType<typeof setInterval> | null = null
const feedbackDraft = ref({
  target_type: 'candidate',
  target_id: 0,
  label: 'bad_off_topic',
  reason: '',
  note: '',
})

async function loadAdmin() {
  error.value = ''
  try {
    const [sourcePayload, settingsPayload, runPayload, feedbackPayload, overviewPayload, evaluationPayload] = await Promise.all([
      api.getSourceRules(),
      api.getReportSettings(),
      api.listRetrievalRuns(),
      api.listQualityFeedback(),
      api.getQualityOverview(),
      api.getEvaluationSummary(),
    ])
    sourcesJson.value = JSON.stringify(sourcePayload.sources, null, 2)
    settings.value = settingsPayload
    runs.value = runPayload.runs
    feedbackItems.value = feedbackPayload.items
    qualityOverview.value = overviewPayload
    evaluationSummary.value = evaluationPayload
    if (runs.value.length > 0) {
      await inspectRun(runs.value[0].id)
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '后台加载失败'
  }
}

async function inspectRun(runId: number) {
  selectedRunId.value = runId
  const [queryPayload, candidatePayload] = await Promise.all([
    api.getRetrievalQueries(runId),
    api.getRetrievalCandidates(runId),
  ])
  queries.value = queryPayload.queries
  candidates.value = candidatePayload.candidates
}

async function submitFeedback() {
  saved.value = ''
  error.value = ''
  try {
    if (feedbackDraft.value.target_id <= 0) {
      throw new Error('请填写有效的 target_id')
    }
    await api.createQualityFeedback({
      target_type: feedbackDraft.value.target_type,
      target_id: feedbackDraft.value.target_id,
      label: feedbackDraft.value.label,
      reason: feedbackDraft.value.reason || undefined,
      note: feedbackDraft.value.note || undefined,
    })
    saved.value = '质量反馈已记录'
    feedbackDraft.value.reason = ''
    feedbackDraft.value.note = ''
    const [feedbackPayload, overviewPayload, evaluationPayload] = await Promise.all([
      api.listQualityFeedback(),
      api.getQualityOverview(),
      api.getEvaluationSummary(),
    ])
    feedbackItems.value = feedbackPayload.items
    qualityOverview.value = overviewPayload
    evaluationSummary.value = evaluationPayload
  } catch (err) {
    error.value = err instanceof Error ? err.message : '质量反馈保存失败'
  }
}

async function markCandidate(candidate: RetrievalCandidate, label: string) {
  feedbackDraft.value.target_type = 'candidate'
  feedbackDraft.value.target_id = candidate.id
  feedbackDraft.value.label = label
  feedbackDraft.value.reason = candidate.rejection_reason ?? candidate.status
  feedbackDraft.value.note = candidate.title
  await submitFeedback()
}

async function saveSources() {
  saved.value = ''
  error.value = ''
  try {
    const payload = JSON.parse(sourcesJson.value) as SourceRule[]
    await api.updateSourceRules(payload)
    saved.value = '来源规则已保存'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '来源规则保存失败'
  }
}

async function saveSettings() {
  saved.value = ''
  error.value = ''
  try {
    await api.updateReportSettings(settings.value)
    saved.value = '调度配置已保存'
  } catch (err) {
    error.value = err instanceof Error ? err.message : '调度配置保存失败'
  }
}

function formatFeedbackSummary(summary: Record<string, number> | undefined) {
  if (!summary) return '-'
  return Object.entries(summary).map(([key, value]) => `${key}:${value}`).join(' / ')
}

function formatScore(value: unknown) {
  const score = Number(value ?? 0)
  return Number.isFinite(score) ? score.toFixed(1) : '0.0'
}

async function loadWeChatStatus() {
  try {
    const res = await api.getWeChatTokenStatus()
    wechatTokenConfigured.value = res.configured
  } catch {
    wechatTokenConfigured.value = false
  }
}

async function clearWeChatToken() {
  error.value = ''
  saved.value = ''
  try {
    await api.clearWeChatToken()
    wechatTokenConfigured.value = false
    wechatTokenInput.value = ''
    wechatCookieInput.value = ''
    saved.value = 'WeChat Token 已清除'
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Token 清除失败'
  }
}

async function saveWeChatToken() {
  error.value = ''
  saved.value = ''
  if (!wechatTokenInput.value.trim() || !wechatCookieInput.value.trim()) {
    error.value = '请填写 Token 和 Cookie'
    return
  }
  try {
    await api.setWeChatToken(wechatTokenInput.value.trim(), wechatCookieInput.value.trim())
    wechatTokenConfigured.value = true
    wechatTokenInput.value = ''
    wechatCookieInput.value = ''
    saved.value = 'WeChat Token 已保存'
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Token 保存失败'
  }
}

async function syncWeChatAccount() {
  error.value = ''
  wechatSyncResult.value = ''
  wechatSyncPages.value = 0
  wechatSyncArticles.value = 0
  if (!wechatAccountName.value.trim()) {
    error.value = '请填写公众号名称'
    return
  }
  wechatSyncing.value = true
  try {
    await api.syncWeChatAccount(wechatAccountName.value.trim())
    startWeChatPolling()
  } catch (err) {
    wechatSyncing.value = false
    error.value = err instanceof Error ? err.message : '同步启动失败'
  }
}

function startWeChatPolling() {
  if (wechatPollTimer) clearInterval(wechatPollTimer)
  wechatPollTimer = setInterval(async () => {
    try {
      const status = await api.getWeChatSyncStatus()
      wechatSyncPages.value = status.pages_done
      wechatSyncArticles.value = status.articles_added
      if (!status.running) {
        stopWeChatPolling()
        wechatSyncing.value = false
        if (status.error) {
          error.value = `同步出错：${status.error}`
        } else {
          wechatSyncResult.value = `同步完成：共新增 ${status.articles_added} 篇文章（${status.pages_done} 页）`
          saved.value = wechatSyncResult.value
        }
      }
    } catch {
      // ignore polling errors
    }
  }, 2000)
}

function stopWeChatPolling() {
  if (wechatPollTimer) {
    clearInterval(wechatPollTimer)
    wechatPollTimer = null
  }
}

onMounted(() => {
  void loadAdmin()
  void loadWeChatStatus()
})

onUnmounted(() => {
  stopWeChatPolling()
})
</script>

<template>
  <div class="max-w-7xl mx-auto space-y-8 pb-12 relative z-10 w-full md:px-0">
    <div class="flex items-center gap-3 pb-6 border-b border-[var(--line)]">
      <div class="w-12 h-12 rounded-2xl bg-purple-50 flex items-center justify-center border border-purple-100 text-[var(--accent-policy)]">
        <ShieldAlert class="w-6 h-6" />
      </div>
      <div>
        <h1 class="text-xl md:text-3xl font-bold text-[var(--text-primary)] tracking-tight">管理后台</h1>
        <p class="text-sm text-[var(--accent-policy)] font-mono mt-1 uppercase tracking-widest">System Administration</p>
      </div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
      <section class="glass-panel flex flex-col h-[500px]">
        <div class="p-4 md:p-6 border-b border-[var(--line)] flex justify-between items-center shrink-0">
          <div class="flex items-center gap-2">
            <Database class="w-5 h-5 text-[var(--accent-primary)]" />
            <h2 class="text-base md:text-lg font-bold text-[var(--text-primary)]">来源规则 (JSON)</h2>
          </div>
          <button class="btn-primary text-xs py-1.5" @click="saveSources">保存规则</button>
        </div>
        <div class="flex-1 p-4 bg-gray-50">
          <textarea v-model="sourcesJson" class="w-full h-full bg-white border border-gray-200 rounded-lg text-xs font-mono text-[var(--text-secondary)] p-4 focus:border-[var(--accent-primary)] transition-colors focus:outline-none resize-none" spellcheck="false" />
        </div>
      </section>

      <section class="glass-panel flex flex-col h-[500px]">
        <div class="p-4 md:p-6 border-b border-[var(--line)] flex justify-between items-center shrink-0">
          <div class="flex items-center gap-2">
            <Settings class="w-5 h-5 text-[var(--accent-primary)]" />
            <h2 class="text-base md:text-lg font-bold text-[var(--text-primary)]">调度与模型配置</h2>
          </div>
          <button class="btn-primary text-xs py-1.5" @click="saveSettings">保存配置</button>
        </div>
        <div class="flex-1 overflow-y-auto p-4 md:p-6 space-y-4">
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              主日报 Hour
              <input v-model.number="settings.report_hour" type="number" min="0" max="23" class="bg-white border border-gray-200 rounded-lg px-3 py-2 text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]" />
            </label>
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              主日报 Min
              <input v-model.number="settings.report_minute" type="number" min="0" max="59" class="bg-white border border-gray-200 rounded-lg px-3 py-2 text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]" />
            </label>
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              AI 日报 Hour
              <input v-model.number="settings.ai_report_hour" type="number" min="0" max="23" class="bg-white border border-gray-200 rounded-lg px-3 py-2 text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]" />
            </label>
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              AI 日报 Min
              <input v-model.number="settings.ai_report_minute" type="number" min="0" max="59" class="bg-white border border-gray-200 rounded-lg px-3 py-2 text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]" />
            </label>
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              抽取超时 (Sec)
              <input v-model.number="settings.scrape_timeout_seconds" type="number" min="5" max="120" class="bg-white border border-gray-200 rounded-lg px-3 py-2 text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]" />
            </label>
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              最大抽取数
              <input v-model.number="settings.max_extractions_per_run" type="number" min="3" max="50" class="bg-white border border-gray-200 rounded-lg px-3 py-2 text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-primary)]" />
            </label>
          </div>
          
          <div class="flex flex-col gap-4 mt-4">
            <label class="flex items-center gap-3 text-sm text-[var(--text-primary)] bg-gray-50 p-4 rounded-xl border border-gray-200 select-none cursor-pointer">
              <input v-model="settings.shadow_mode" type="checkbox" class="w-4 h-4 accent-[var(--accent-primary)]" />
              <div class="flex flex-col">
                <span class="font-bold">开启 Shadow-mode (只读日志)</span>
                <span class="text-xs text-[var(--text-muted)] font-normal">记录抽取结果但不实际影响外网展示</span>
              </div>
            </label>
            <label class="flex items-center gap-3 text-sm text-[var(--text-primary)] bg-gray-50 p-4 rounded-xl border border-gray-200 select-none cursor-pointer">
              <input v-model="settings.ai_report_enabled" type="checkbox" class="w-4 h-4 accent-[var(--accent-primary)]" />
              <div class="flex flex-col">
                <span class="font-bold">启用 AI RSS 日报</span>
                <span class="text-xs text-[var(--text-muted)] font-normal">每天独立同步 Juya AI Daily RSS</span>
              </div>
            </label>
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              AI RSS Feed
              <input v-model="settings.ai_rss_feed_url" type="text" class="bg-white border border-gray-200 rounded-lg px-3 py-2.5 text-[var(--text-primary)] font-mono text-xs focus:outline-none focus:border-[var(--accent-primary)]" />
            </label>
            
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              主模型引擎 <span class="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">Primary Model</span>
              <input v-model="settings.report_primary_model" type="text" class="bg-white border border-gray-200 rounded-lg px-3 py-2.5 text-[var(--text-primary)] font-mono text-xs focus:outline-none focus:border-[var(--accent-primary)]" />
            </label>
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              备用引擎 <span class="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">Fallback Model</span>
              <input v-model="settings.report_fallback_model" type="text" class="bg-white border border-gray-200 rounded-lg px-3 py-2.5 text-[var(--text-primary)] font-mono text-xs focus:outline-none focus:border-[var(--accent-primary)]" />
            </label>
          </div>
        </div>
      </section>
    </div>

    <section class="glass-panel">
      <div class="p-4 md:p-6 border-b border-[var(--line)] flex items-center gap-2">
        <Database class="w-5 h-5 text-[var(--status-ok)]" />
        <h2 class="text-base md:text-lg font-bold text-[var(--text-primary)]">公众号同步</h2>
        <span class="ml-auto text-xs font-mono" :class="wechatTokenConfigured ? 'text-[var(--status-ok)]' : 'text-[var(--status-error)]'">
          Token: {{ wechatTokenConfigured ? '已配置' : '未配置' }}
        </span>
      </div>
      <div class="p-4 md:p-6 space-y-4">
        <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div class="space-y-3">
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              Token
              <input v-model="wechatTokenInput" type="text" placeholder="从 mp.weixin.qq.com 抓取的 token" class="bg-white border border-gray-200 rounded-lg px-3 py-2 text-[var(--text-primary)] font-mono text-xs focus:outline-none focus:border-green-400" />
            </label>
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              Cookie
              <textarea v-model="wechatCookieInput" placeholder="从 mp.weixin.qq.com 抓取的 Cookie" rows="2" class="bg-white border border-gray-200 rounded-lg px-3 py-2 text-[var(--text-primary)] font-mono text-xs focus:outline-none focus:border-green-400 resize-none" />
            </label>
            <div class="flex gap-2">
              <button @click="saveWeChatToken" class="btn-primary bg-green-500/20 text-[var(--status-ok)] border-green-500/30 hover:bg-green-500/30 text-sm">保存 Token</button>
              <button @click="clearWeChatToken" class="btn-ghost border border-white/10 text-[var(--text-muted)] hover:text-[var(--text-primary)] text-sm">清除 Token</button>
            </div>
          </div>
          <div class="space-y-3">
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              公众号名称
              <input v-model="wechatAccountName" type="text" placeholder="例: 英蓝云展" class="bg-white border border-gray-200 rounded-lg px-3 py-2 text-[var(--text-primary)] focus:outline-none focus:border-green-400" />
            </label>
            <button @click="syncWeChatAccount" :disabled="wechatSyncing || !wechatTokenConfigured" class="btn-primary bg-green-500/20 text-[var(--status-ok)] border-green-500/30 hover:bg-green-500/30 disabled:opacity-40 disabled:cursor-not-allowed text-sm">
              {{ wechatSyncing ? '同步中...' : '增量同步（最多100篇）' }}
            </button>
            <div v-if="wechatSyncing" class="text-xs text-[var(--status-ok)] bg-green-500/10 border border-green-500/20 rounded-lg px-3 py-2 flex items-center gap-2">
              <div class="w-3 h-3 border-2 border-green-400 border-t-transparent rounded-full animate-spin"></div>
              正在同步：已翻 {{ wechatSyncPages }} 页，新增 {{ wechatSyncArticles }} 篇
            </div>
            <p v-else-if="wechatSyncResult" class="text-xs text-[var(--status-ok)] bg-green-500/10 border border-green-500/20 rounded-lg px-3 py-2">{{ wechatSyncResult }}</p>
          </div>
        </div>
      </div>
    </section>

    <div v-if="error" class="bg-[var(--status-error)]/10 border border-[var(--status-error)]/20 text-[var(--status-error)] p-4 rounded-xl flex items-center gap-3">
      <AlertCircle class="w-5 h-5 shrink-0" />
      <span class="text-sm font-medium">{{ error }}</span>
    </div>
    <div v-if="saved" class="bg-[var(--status-ok)]/10 border border-[var(--status-ok)]/20 text-[var(--status-ok)] p-4 rounded-xl flex items-center gap-3">
      <CheckCircle2 class="w-5 h-5 shrink-0" />
      <span class="text-sm font-medium">{{ saved }}</span>
    </div>

    <section class="glass-panel">
      <div class="p-4 md:p-6 border-b border-[var(--line)]">
        <div class="flex items-center gap-2">
          <BarChart3 class="w-5 h-5 text-[var(--accent-industry)]" />
          <h2 class="text-base md:text-lg font-bold text-[var(--text-primary)]">质量分析与模型指标</h2>
        </div>
      </div>
      
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 p-4 md:p-6">
        <div class="glass-card p-5 bg-gray-50 border-gray-100 flex flex-col gap-3">
          <h3 class="text-sm font-bold text-[var(--text-primary)] flex items-center gap-1.5 mb-2"><ListTree class="w-4 h-4 text-[var(--text-muted)]" /> 新增加工反馈</h3>
          
          <div class="space-y-3">
            <select v-model="feedbackDraft.target_type" class="w-full bg-white border border-gray-200 rounded-lg px-3 py-2.5 text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-industry)]">
              <option value="candidate">Candidate (文章)</option>
              <option value="report_item">Report Item (报告项)</option>
            </select>
            <input v-model.number="feedbackDraft.target_id" type="number" placeholder="Target ID" min="1" class="w-full bg-white border border-gray-200 rounded-lg px-3 py-2.5 text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-industry)]" />
            <select v-model="feedbackDraft.label" class="w-full bg-white border border-gray-200 rounded-lg px-3 py-2.5 text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-industry)]">
              <option value="good">Good (标记为优)</option>
              <option value="bad_off_topic">Bad: Off Topic (离题)</option>
              <option value="bad_source">Bad: Source (弱来源)</option>
              <option value="bad_pr_like">Bad: PR Like (公关文)</option>
              <option value="keep_borderline">Keep: Borderline (边缘收录)</option>
            </select>
            <input v-model="feedbackDraft.reason" type="text" placeholder="理由简述 (可选)" class="w-full bg-white border border-gray-200 rounded-lg px-3 py-2.5 text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-industry)]" />
            <textarea v-model="feedbackDraft.note" placeholder="内部备注 (可选)" rows="2" class="w-full bg-white border border-gray-200 rounded-lg px-3 py-2.5 text-sm text-[var(--text-primary)] focus:outline-none focus:border-[var(--accent-industry)] resize-none" />
            <button @click="submitFeedback" class="btn-primary w-full bg-[var(--accent-industry)]/20 text-[var(--accent-industry)] border-[var(--accent-industry)]/40 hover:bg-[var(--accent-industry)]/30">提交打标</button>
          </div>
        </div>

        <div class="glass-card p-5 bg-gray-50 border-gray-100 lg:col-span-2">
          <h3 class="text-sm font-bold text-[var(--text-primary)] flex items-center gap-1.5 mb-4"><Activity class="w-4 h-4 text-[var(--accent-policy)]" /> 质量大盘数据</h3>
          
           <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6" v-if="qualityOverview">
             <div class="flex flex-col border-l-2 border-[var(--status-ok)] pl-3">
               <span class="text-[10px] uppercase tracking-widest text-[var(--text-muted)]">Avg Score</span>
               <span class="text-lg md:text-xl font-bold text-[var(--text-primary)] tabular-nums">{{ formatScore(qualityOverview.average_daily_report_score) }}</span>
             </div>
             <div class="flex flex-col border-l-2 border-[var(--accent-industry)] pl-3">
               <span class="text-[10px] uppercase tracking-widest text-[var(--text-muted)]">Policy Fill</span>
               <span class="text-lg md:text-xl font-bold text-[var(--text-primary)] tabular-nums">{{ qualityOverview.policy_fill_rate || 0 }}</span>
             </div>
             <div class="flex flex-col border-l-2 border-[var(--accent-academic)] pl-3">
               <span class="text-[10px] uppercase tracking-widest text-[var(--text-muted)]">Image Fill</span>
               <span class="text-lg md:text-xl font-bold text-[var(--text-primary)] tabular-nums">{{ qualityOverview.image_fill_rate || 0 }}</span>
             </div>
             <div class="flex flex-col border-l-2 border-[var(--status-error)] pl-3">
               <span class="text-[10px] uppercase tracking-widest text-[var(--text-muted)]">Off-topic Escapes</span>
               <span class="text-lg md:text-xl font-bold text-[var(--text-primary)] tabular-nums">{{ qualityOverview.off_topic_escape_count || 0 }}</span>
             </div>
           </div>

          <div class="text-xs text-[var(--text-secondary)] space-y-2 font-mono leading-relaxed bg-gray-50 p-4 rounded-lg border border-gray-100">
            <p v-if="qualityOverview">feedback: <span class="text-[var(--text-muted)]">{{ formatFeedbackSummary(qualityOverview.feedback_summary) }}</span></p>
            <p v-if="qualityOverview">images req: <span class="text-[var(--text-muted)]">{{ qualityOverview.no_image_rejections }}</span> | img rejections: <span class="text-[var(--text-muted)]">{{ qualityOverview.duplicate_image_hits }}</span></p>
            <p v-if="qualityOverview?.report_score_trend?.length" class="text-[var(--accent-industry)] opacity-80 overflow-auto whitespace-nowrap scrollbar-hide">report trend: {{ qualityOverview.report_score_trend.map(i => `${i.date}:${formatScore(i.score)}`).join(' ➜ ') }}</p>
            <p v-if="qualityOverview?.benchmark_score_trend?.length" class="text-[var(--accent-policy)] opacity-80 overflow-auto whitespace-nowrap scrollbar-hide">bench trend: {{ qualityOverview.benchmark_score_trend.map(i => `${i.date}:${formatScore(i.score)}`).join(' ➜ ') }}</p>
            <p v-if="qualityOverview?.extended_window_usage?.length">extended windows: <span class="text-[var(--text-primary)]">{{ qualityOverview.extended_window_usage.map(i => `${i.date}:${i.extended_window_selected}`).join(' | ') }}</span></p>
          </div>
        </div>
      </div>
    </section>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
      <section class="glass-panel flex flex-col min-h-[300px] md:min-h-[500px] h-auto md:h-[600px]">
        <div class="p-4 md:p-6 border-b border-[var(--line)] shrink-0 flex items-center gap-2">
          <Fingerprint class="w-5 h-5 text-[var(--accent-primary)]" />
          <h2 class="text-base md:text-lg font-bold text-[var(--text-primary)]">流水线执行日志</h2>
        </div>
        <div class="flex-1 overflow-y-auto p-4 space-y-3">
          <button 
            v-for="run in runs" 
            :key="run.id"
            @click="inspectRun(run.id)"
            class="w-full text-left bg-gray-50 hover:bg-white border relative rounded-xl p-4 transition-all"
            :class="selectedRunId === run.id ? 'border-[var(--accent-primary)]/40 hover:border-[var(--accent-primary)]/50' : 'border-gray-100 hover:border-gray-200 text-[var(--text-muted)]'"
          >
            <div v-if="selectedRunId === run.id" class="absolute left-0 top-0 bottom-0 w-1 bg-[var(--accent-primary)]"></div>
            <div class="flex items-center justify-between mb-2">
              <strong class="font-mono text-[var(--text-primary)] tracking-tight flex items-center gap-2">
                <Code class="w-3.5 h-3.5 opacity-50" />
                Run #{{ run.id }}
              </strong>
              <StatusPill :status="run.status" />
            </div>
            <p class="text-[10px] text-[var(--text-muted)] mb-2">{{ new Date(run.started_at).toLocaleString() }}</p>
            <div class="flex flex-wrap gap-x-3 gap-y-1 text-xs text-[var(--text-secondary)] font-mono opacity-80">
              <span>q:{{ run.query_count }}</span>
              <span>c:{{ run.candidate_count }}</span>
              <span>e:{{ run.extracted_count }}</span>
            </div>
            <div v-if="run.error_message" class="mt-2 text-[10px] text-[var(--status-error)] bg-[var(--status-error)]/10 p-2 rounded truncate border border-[var(--status-error)]/20">
              {{ run.error_message }}
            </div>
          </button>
        </div>
      </section>

      <section class="glass-panel flex flex-col min-h-[300px] md:min-h-[500px] h-auto md:h-[600px]">
        <div class="p-4 md:p-6 border-b border-[var(--line)] shrink-0 flex items-center gap-2">
          <ListTree class="w-5 h-5 text-[var(--status-info)]" />
          <h2 class="text-base md:text-lg font-bold text-[var(--text-primary)]">Candidates (#{{ selectedRunId || '-' }})</h2>
        </div>
        <div class="flex-1 overflow-y-auto p-4 space-y-3">
          <div v-if="!candidates.length" class="text-center text-[var(--text-muted)] text-sm py-12 flex flex-col items-center gap-2">
            <ShieldAlert class="w-8 h-8 opacity-20" />
            请选择左侧流水线或暂无记录
          </div>
          
          <div 
            v-for="c in candidates.slice(0, 15)" 
            :key="c.id" 
            class="bg-gray-50 border border-gray-100 rounded-xl p-4 group"
          >
            <p class="text-xs text-[10px] uppercase font-bold tracking-widest mb-1.5" :class="c.status === 'publish-ready' ? 'text-[var(--status-ok)]' : 'text-[var(--status-error)]'">
              {{ c.status }} <span class="text-[var(--text-muted)] lowercase font-normal ml-2">{{ c.domain }}</span>
            </p>
            <h4 class="text-sm font-medium text-[var(--text-primary)] line-clamp-2 leading-snug mb-2 group-hover:text-[var(--accent-primary)] transition-colors">
              <a :href="c.url" target="_blank">{{ c.title }}</a>
            </h4>
            <p v-if="c.rejection_reason" class="text-xs text-[var(--status-warn)] mb-3 opacity-90 p-2 bg-[var(--status-warn)]/10 rounded border border-[var(--status-warn)]/20 line-clamp-2">{{ c.rejection_reason }}</p>
            
            <div class="flex gap-2 flex-wrap">
              <button class="btn-primary text-xs px-2.5 py-1.5 flex items-center gap-1 opacity-60 hover:opacity-100" @click="markCandidate(c, 'good')"><CheckCircle2 class="w-3 h-3"/> Good</button>
              <button class="btn-ghost border border-white/10 text-xs px-2.5 py-1.5 text-[var(--text-muted)] hover:text-[var(--text-primary)]" @click="markCandidate(c, 'keep_borderline')">Bordeline</button>
              <button class="btn-ghost border border-[var(--status-error)]/30 bg-[var(--status-error)]/5 text-[var(--status-error)] text-xs px-2.5 py-1.5 hover:bg-[var(--status-error)]/20" @click="markCandidate(c, 'bad_off_topic')">Off-topic</button>
            </div>
          </div>
          <div v-if="candidates.length > 15" class="text-center text-[10px] text-[var(--text-muted)]">
            + {{ candidates.length - 15 }} more candidates in this run
          </div>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.scrollbar-hide::-webkit-scrollbar { display: none; }
.scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
</style>

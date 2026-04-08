<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Database, Settings, Activity, ShieldAlert, BarChart3, ListTree, Code, Fingerprint } from 'lucide-vue-next'

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
  shadow_mode: true,
  scrape_timeout_seconds: 20,
  scrape_concurrency: 3,
  max_extractions_per_run: 18,
  report_primary_model: 'google/gemini-3-flash-preview',
  report_fallback_model: 'minimax/minimax-m2.7',
})
const error = ref('')
const saved = ref('')
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

onMounted(() => {
  void loadAdmin()
})
</script>

<template>
  <div class="max-w-7xl mx-auto space-y-8 pb-12 relative z-10 w-full pl-4 md:pl-0 pr-4">
    <!-- Header -->
    <div class="flex items-center gap-3 pb-6 border-b border-[var(--line)]">
      <div class="w-12 h-12 rounded-2xl bg-[var(--accent-policy)]/20 flex items-center justify-center border border-[var(--accent-policy)]/30 text-[var(--accent-policy)] shadow-[0_0_20px_rgba(167,139,250,0.2)]">
        <ShieldAlert class="w-6 h-6" />
      </div>
      <div>
        <h1 class="text-3xl font-bold text-white tracking-tight">管理后台</h1>
        <p class="text-sm text-[var(--accent-policy)] font-mono mt-1 uppercase tracking-widest">System Administration</p>
      </div>
    </div>

    <!-- Top Grid: Sources & Scheduling -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
      
      <!-- Source Rules -->
      <section class="glass-panel flex flex-col h-[500px]">
        <div class="p-6 border-b border-[var(--line)] flex justify-between items-center shrink-0">
          <div class="flex items-center gap-2">
            <Database class="w-5 h-5 text-[var(--accent-primary)]" />
            <h2 class="text-lg font-bold text-white">来源规则 (JSON)</h2>
          </div>
          <button class="btn-primary text-xs py-1.5" @click="saveSources">保存规则</button>
        </div>
        <div class="flex-1 p-4 bg-black/20">
          <textarea v-model="sourcesJson" class="w-full h-full bg-black/40 border border-white/5 rounded-lg text-xs font-mono text-[var(--text-secondary)] p-4 focus:border-[var(--accent-primary)] transition-colors focus:outline-none resize-none" spellcheck="false" />
        </div>
      </section>

      <!-- Scheduler Settings -->
      <section class="glass-panel flex flex-col h-[500px]">
        <div class="p-6 border-b border-[var(--line)] flex justify-between items-center shrink-0">
          <div class="flex items-center gap-2">
            <Settings class="w-5 h-5 text-[var(--accent-primary)]" />
            <h2 class="text-lg font-bold text-white">调度与模型配置</h2>
          </div>
          <button class="btn-primary text-xs py-1.5" @click="saveSettings">保存配置</button>
        </div>
        <div class="flex-1 overflow-y-auto p-6 space-y-4">
          <div class="grid grid-cols-2 gap-4">
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              执行时机 (Hour)
              <input v-model.number="settings.report_hour" type="number" min="0" max="23" class="bg-black/40 border border-white/10 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-[var(--accent-primary)]" />
            </label>
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              执行时机 (Min)
              <input v-model.number="settings.report_minute" type="number" min="0" max="59" class="bg-black/40 border border-white/10 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-[var(--accent-primary)]" />
            </label>
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              抽取超时 (Sec)
              <input v-model.number="settings.scrape_timeout_seconds" type="number" min="5" max="120" class="bg-black/40 border border-white/10 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-[var(--accent-primary)]" />
            </label>
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              最大抽取数
              <input v-model.number="settings.max_extractions_per_run" type="number" min="3" max="50" class="bg-black/40 border border-white/10 rounded-lg px-3 py-2 text-white focus:outline-none focus:border-[var(--accent-primary)]" />
            </label>
          </div>
          
          <div class="flex flex-col gap-4 mt-4">
            <label class="flex items-center gap-3 text-sm text-[var(--text-primary)] bg-white/5 p-4 rounded-xl border border-white/10 select-none cursor-pointer">
              <input v-model="settings.shadow_mode" type="checkbox" class="w-4 h-4 accent-[var(--accent-primary)]" />
              <div class="flex flex-col">
                <span class="font-bold">开启 Shadow-mode (只读日志)</span>
                <span class="text-xs text-[var(--text-muted)] font-normal">记录抽取结果但不实际影响外网展示</span>
              </div>
            </label>
            
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              主模型引擎 <span class="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">Primary Model</span>
              <input v-model="settings.report_primary_model" type="text" class="bg-black/40 border border-white/10 rounded-lg px-3 py-2.5 text-white font-mono text-xs focus:outline-none focus:border-[var(--accent-primary)]" />
            </label>
            <label class="flex flex-col gap-2 text-sm text-[var(--text-secondary)]">
              备用引擎 <span class="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">Fallback Model</span>
              <input v-model="settings.report_fallback_model" type="text" class="bg-black/40 border border-white/10 rounded-lg px-3 py-2.5 text-white font-mono text-xs focus:outline-none focus:border-[var(--accent-primary)]" />
            </label>
          </div>
        </div>
      </section>
    </div>

    <!-- Alert Bar -->
    <div v-if="error" class="bg-[var(--status-error)]/10 border border-[var(--status-error)]/20 text-[var(--status-error)] p-4 rounded-xl flex items-center gap-3">
      <AlertCircle class="w-5 h-5 shrink-0" />
      <span class="text-sm font-medium">{{ error }}</span>
    </div>
    <div v-if="saved" class="bg-[var(--status-ok)]/10 border border-[var(--status-ok)]/20 text-[var(--status-ok)] p-4 rounded-xl flex items-center gap-3">
      <CheckCircle2 class="w-5 h-5 shrink-0" />
      <span class="text-sm font-medium">{{ saved }}</span>
    </div>

    <!-- Quality Overview Section -->
    <section class="glass-panel">
      <div class="p-6 border-b border-[var(--line)]">
        <div class="flex items-center gap-2">
          <BarChart3 class="w-5 h-5 text-[var(--accent-industry)]" />
          <h2 class="text-lg font-bold text-white">质量分析与模型指标</h2>
        </div>
      </div>
      
      <div class="grid grid-cols-1 lg:grid-cols-3 gap-6 p-6">
        <!-- New Feedback form -->
        <div class="glass-card p-5 bg-black/20 border-white/5 flex flex-col gap-3">
          <h3 class="text-sm font-bold text-white flex items-center gap-1.5 mb-2"><ListTree class="w-4 h-4 text-[var(--text-muted)]" /> 新增加工反馈</h3>
          
          <div class="space-y-3">
            <select v-model="feedbackDraft.target_type" class="w-full bg-black/40 border border-white/10 rounded-lg px-3 py-2.5 text-sm text-white focus:outline-none focus:border-[var(--accent-industry)]">
              <option value="candidate">Candidate (文章)</option>
              <option value="report_item">Report Item (报告项)</option>
            </select>
            <input v-model.number="feedbackDraft.target_id" type="number" placeholder="Target ID" min="1" class="w-full bg-black/40 border border-white/10 rounded-lg px-3 py-2.5 text-sm text-white focus:outline-none focus:border-[var(--accent-industry)]" />
            <select v-model="feedbackDraft.label" class="w-full bg-black/40 border border-white/10 rounded-lg px-3 py-2.5 text-sm text-white focus:outline-none focus:border-[var(--accent-industry)]">
              <option value="good">🟢 Good (标记为优)</option>
              <option value="bad_off_topic">🔴 Bad: Off Topic (离题)</option>
              <option value="bad_source">🔴 Bad: Source (弱来源)</option>
              <option value="bad_pr_like">🔴 Bad: PR Like (公关文)</option>
              <option value="keep_borderline">🟡 Keep: Borderline (边缘收录)</option>
            </select>
            <input v-model="feedbackDraft.reason" type="text" placeholder="理由简述 (可选)" class="w-full bg-black/40 border border-white/10 rounded-lg px-3 py-2.5 text-sm text-white focus:outline-none focus:border-[var(--accent-industry)]" />
            <textarea v-model="feedbackDraft.note" placeholder="内部备注 (可选)" rows="2" class="w-full bg-black/40 border border-white/10 rounded-lg px-3 py-2.5 text-sm text-white focus:outline-none focus:border-[var(--accent-industry)] resize-none" />
            <button @click="submitFeedback" class="btn-primary w-full bg-[var(--accent-industry)]/20 text-[var(--accent-industry)] border-[var(--accent-industry)]/40 hover:bg-[var(--accent-industry)]/30">提交打标</button>
          </div>
        </div>

        <!-- Global Metrics -->
        <div class="glass-card p-5 bg-black/20 border-white/5 lg:col-span-2">
          <h3 class="text-sm font-bold text-white flex items-center gap-1.5 mb-4"><Activity class="w-4 h-4 text-[var(--accent-policy)]" /> 质量大盘数据</h3>
          
          <div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6" v-if="qualityOverview">
             <div class="flex flex-col border-l-2 border-[var(--status-ok)] pl-3">
               <span class="text-[10px] uppercase tracking-widest text-[var(--text-muted)]">Avg Score</span>
               <span class="text-xl font-bold text-white tabular-nums glow-primary">{{ formatScore(qualityOverview.average_daily_report_score) }}</span>
             </div>
             <div class="flex flex-col border-l-2 border-[var(--accent-industry)] pl-3">
               <span class="text-[10px] uppercase tracking-widest text-[var(--text-muted)]">Policy Fill</span>
               <span class="text-xl font-bold text-white tabular-nums">{{ qualityOverview.policy_fill_rate || 0 }}</span>
             </div>
             <div class="flex flex-col border-l-2 border-[var(--accent-academic)] pl-3">
               <span class="text-[10px] uppercase tracking-widest text-[var(--text-muted)]">Image Fill</span>
               <span class="text-xl font-bold text-white tabular-nums">{{ qualityOverview.image_fill_rate || 0 }}</span>
             </div>
             <div class="flex flex-col border-l-2 border-[var(--status-error)] pl-3">
               <span class="text-[10px] uppercase tracking-widest text-[var(--text-muted)]">Off-topic Escapes</span>
               <span class="text-xl font-bold text-white tabular-nums">{{ qualityOverview.off_topic_escape_count || 0 }}</span>
             </div>
          </div>

          <div class="text-xs text-[var(--text-secondary)] space-y-2 font-mono leading-relaxed bg-black/40 p-4 rounded-lg border border-white/5">
            <p v-if="qualityOverview">feedback: <span class="text-[var(--text-muted)]">{{ formatFeedbackSummary(qualityOverview.feedback_summary) }}</span></p>
            <p v-if="qualityOverview">images req: <span class="text-[var(--text-muted)]">{{ qualityOverview.no_image_rejections }}</span> | img rejections: <span class="text-[var(--text-muted)]">{{ qualityOverview.duplicate_image_hits }}</span></p>
            <p v-if="qualityOverview?.report_score_trend?.length" class="text-[var(--accent-industry)] opacity-80 overflow-auto whitespace-nowrap scrollbar-hide">report trend: {{ qualityOverview.report_score_trend.map(i => `${i.date}:${formatScore(i.score)}`).join(' ➜ ') }}</p>
            <p v-if="qualityOverview?.benchmark_score_trend?.length" class="text-[var(--accent-policy)] opacity-80 overflow-auto whitespace-nowrap scrollbar-hide">bench trend: {{ qualityOverview.benchmark_score_trend.map(i => `${i.date}:${formatScore(i.score)}`).join(' ➜ ') }}</p>
            <p v-if="qualityOverview?.extended_window_usage?.length">extended windows: <span class="text-white">{{ qualityOverview.extended_window_usage.map(i => `${i.date}:${i.extended_window_selected}`).join(' | ') }}</span></p>
          </div>
        </div>
      </div>
    </section>

    <!-- Lower Grids: Runs & Candidates -->
    <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
      <!-- Left: Recent Runs List -->
      <section class="glass-panel flex flex-col min-h-[500px] h-[600px]">
        <div class="p-6 border-b border-[var(--line)] shrink-0 flex items-center gap-2">
          <Fingerprint class="w-5 h-5 text-[var(--accent-primary)]" />
          <h2 class="text-lg font-bold text-white">流水线执行日志</h2>
        </div>
        <div class="flex-1 overflow-y-auto p-4 space-y-3">
          <button 
            v-for="run in runs" 
            :key="run.id"
            @click="inspectRun(run.id)"
            class="w-full text-left bg-black/20 hover:bg-black/40 border relative rounded-xl p-4 transition-all"
            :class="selectedRunId === run.id ? 'border-[var(--accent-primary)]/40 hover:border-[var(--accent-primary)]/50 shadow-[inset_0_0_20px_rgba(100,180,255,0.05)]' : 'border-white/5 hover:border-white/10 text-[var(--text-muted)]'"
          >
            <div v-if="selectedRunId === run.id" class="absolute left-0 top-0 bottom-0 w-1 bg-[var(--accent-primary)]"></div>
            <div class="flex items-center justify-between mb-2">
              <strong class="font-mono text-white tracking-tight flex items-center gap-2">
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

      <!-- Right: Sub-detail (Candidates / Context) -->
      <section class="glass-panel flex flex-col min-h-[500px] h-[600px]">
        <div class="p-6 border-b border-[var(--line)] shrink-0 flex items-center gap-2">
          <ListTree class="w-5 h-5 text-[var(--status-info)]" />
          <h2 class="text-lg font-bold text-white">Candidates (#{{ selectedRunId || '-' }})</h2>
        </div>
        <div class="flex-1 overflow-y-auto p-4 space-y-3">
          <div v-if="!candidates.length" class="text-center text-[var(--text-muted)] text-sm py-12 flex flex-col items-center gap-2">
            <ShieldAlert class="w-8 h-8 opacity-20" />
            请选择左侧流水线或暂无记录
          </div>
          
          <div 
            v-for="c in candidates.slice(0, 15)" 
            :key="c.id" 
            class="bg-black/20 border border-white/5 rounded-xl p-4 group"
          >
            <p class="text-xs text-[10px] uppercase font-bold tracking-widest mb-1.5" :class="c.status === 'publish-ready' ? 'text-[var(--status-ok)]' : 'text-[var(--status-error)]'">
              {{ c.status }} <span class="text-[var(--text-muted)] lowercase font-normal ml-2">{{ c.domain }}</span>
            </p>
            <h4 class="text-sm font-medium text-white line-clamp-2 leading-snug mb-2 group-hover:text-[var(--accent-primary)] transition-colors">
              <a :href="c.url" target="_blank">{{ c.title }}</a>
            </h4>
            <p v-if="c.rejection_reason" class="text-xs text-[var(--status-warn)] mb-3 opacity-90 p-2 bg-[var(--status-warn)]/10 rounded border border-[var(--status-warn)]/20 line-clamp-2">{{ c.rejection_reason }}</p>
            
            <div class="flex gap-2 flex-wrap">
              <button class="btn-primary text-[10px] px-2 py-1 flex items-center gap-1 opacity-60 hover:opacity-100" @click="markCandidate(c, 'good')"><CheckCircle2 class="w-3 h-3"/> Good</button>
              <button class="btn-ghost border border-white/10 text-[10px] px-2 py-1 text-[var(--text-muted)] hover:text-white" @click="markCandidate(c, 'keep_borderline')">Bordeline</button>
              <button class="btn-ghost border border-[var(--status-error)]/30 bg-[var(--status-error)]/5 text-[var(--status-error)] text-[10px] px-2 py-1 hover:bg-[var(--status-error)]/20" @click="markCandidate(c, 'bad_off_topic')">Off-topic</button>
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
/* Base mappings */
.flex { display: flex; }
.flex-col { flex-direction: column; }
.flex-wrap { flex-wrap: wrap; }
.justify-between { justify-content: space-between; }
.justify-center { justify-content: center; }
.items-center { align-items: center; }
.items-start { align-items: flex-start; }
.gap-1 { gap: 0.25rem; }
.gap-1\.5 { gap: 0.375rem; }
.gap-2 { gap: 0.5rem; }
.gap-3 { gap: 0.75rem; }
.gap-4 { gap: 1rem; }
.gap-6 { gap: 1.5rem; }
.gap-8 { gap: 2rem; }
.gap-x-3 { column-gap: 0.75rem; }
.gap-y-1 { row-gap: 0.25rem; }
.w-full { width: 100%; }
.h-full { height: 100%; }
.max-w-7xl { max-width: 80rem; }
.h-\[500px\] { height: 500px; }
.h-\[600px\] { height: 600px; }
.min-h-\[500px\] { min-height: 500px; }
.w-1 { width: 0.25rem; }
.w-3 { width: 0.75rem; }
.h-3 { height: 0.75rem; }
.w-3\.5 { width: 0.875rem; }
.h-3\.5 { height: 0.875rem; }
.w-4 { width: 1rem; }
.h-4 { height: 1rem; }
.w-5 { width: 1.25rem; }
.h-5 { height: 1.25rem; }
.w-6 { width: 1.5rem; }
.h-6 { height: 1.5rem; }
.w-8 { width: 2rem; }
.h-8 { height: 2rem; }
.w-12 { width: 3rem; }
.h-12 { height: 3rem; }
.mx-auto { margin-left: auto; margin-right: auto; }
.pl-4 { padding-left: 1rem; }
.pr-4 { padding-right: 1rem; }
.pb-6 { padding-bottom: 1.5rem; }
.pb-12 { padding-bottom: 3rem; }
.p-2 { padding: 0.5rem; }
.p-4 { padding: 1rem; }
.p-5 { padding: 1.25rem; }
.p-6 { padding: 1.5rem; }
.px-2 { padding-left: 0.5rem; padding-right: 0.5rem; }
.px-3 { padding-left: 0.75rem; padding-right: 0.75rem; }
.py-1 { padding-top: 0.25rem; padding-bottom: 0.25rem; }
.py-1\.5 { padding-top: 0.375rem; padding-bottom: 0.375rem; }
.py-2 { padding-top: 0.5rem; padding-bottom: 0.5rem; }
.py-2\.5 { padding-top: 0.625rem; padding-bottom: 0.625rem; }
.py-12 { padding-top: 3rem; padding-bottom: 3rem; }
.pl-3 { padding-left: 0.75rem; }
.mt-1 { margin-top: 0.25rem; }
.mt-2 { margin-top: 0.5rem; }
.mt-4 { margin-top: 1rem; }
.mb-1\.5 { margin-bottom: 0.375rem; }
.mb-2 { margin-bottom: 0.5rem; }
.mb-3 { margin-bottom: 0.75rem; }
.mb-4 { margin-bottom: 1rem; }
.mb-6 { margin-bottom: 1.5rem; }
.space-y-2 > :not([hidden]) ~ :not([hidden]) { margin-top: 0.5rem; }
.space-y-3 > :not([hidden]) ~ :not([hidden]) { margin-top: 0.75rem; }
.space-y-4 > :not([hidden]) ~ :not([hidden]) { margin-top: 1rem; }
.space-y-8 > :not([hidden]) ~ :not([hidden]) { margin-top: 2rem; }
.relative { position: relative; }
.absolute { position: absolute; }
.left-0 { left: 0; }
.top-0 { top: 0; }
.bottom-0 { bottom: 0; }
.z-10 { z-index: 10; }
.flex-1 { flex: 1 1 0%; }
.shrink-0 { flex-shrink: 0; }
.overflow-y-auto { overflow-y: auto; }
.overflow-hidden { overflow: hidden; }
.overflow-auto { overflow: auto; }
.resize-none { resize: none; }
.rounded { border-radius: 0.25rem; }
.rounded-lg { border-radius: 0.5rem; }
.rounded-xl { border-radius: 0.75rem; }
.rounded-2xl { border-radius: 1rem; }
.border { border-width: 1px; }
.border-b { border-bottom-width: 1px; }
.border-l-2 { border-left-width: 2px; }
.border-white\/5 { border-color: rgba(255, 255, 255, 0.05); }
.border-white\/10 { border-color: rgba(255, 255, 255, 0.1); }
.border-\[var\(--line\)\] { border-color: var(--line); }
.border-\[var\(--accent-primary\)\]\/40 { border-color: rgba(100, 180, 255, 0.4); }
.border-\[var\(--accent-policy\)\]\/30 { border-color: rgba(167, 139, 250, 0.3); }
.border-\[var\(--status-ok\)\] { border-color: var(--status-ok); }
.border-\[var\(--status-ok\)\]\/20 { border-color: rgba(52, 211, 153, 0.2); }
.border-\[var\(--status-error\)\]\/20 { border-color: rgba(248, 113, 113, 0.2); }
.border-\[var\(--status-error\)\]\/30 { border-color: rgba(248, 113, 113, 0.3); }
.border-\[var\(--status-warn\)\]\/20 { border-color: rgba(251, 191, 36, 0.2); }
.border-\[var\(--accent-industry\)\] { border-color: var(--accent-industry); }
.border-[var\(--accent-academic\)] { border-color: var(--accent-academic); }
.bg-black\/20 { background-color: rgba(0, 0, 0, 0.2); }
.bg-black\/40 { background-color: rgba(0, 0, 0, 0.4); }
.bg-white\/5 { background-color: rgba(255, 255, 255, 0.05); }
.bg-\[var\(--accent-policy\)\]\/20 { background-color: rgba(167, 139, 250, 0.2); }
.bg-\[var\(--accent-industry\)\]\/20 { background-color: rgba(74, 222, 128, 0.2); }
.bg-\[var\(--status-error\)\]\/5 { background-color: rgba(248, 113, 113, 0.05); }
.bg-\[var\(--status-error\)\]\/10 { background-color: rgba(248, 113, 113, 0.1); }
.bg-\[var\(--status-warn\)\]\/10 { background-color: rgba(251, 191, 36, 0.1); }
.bg-\[var\(--status-ok\)\]\/10 { background-color: rgba(52, 211, 153, 0.1); }
.bg-\[var\(--accent-primary\)\] { background-color: var(--accent-primary); }
.bg-transparent { background-color: transparent; }
.text-xs { font-size: 0.75rem; line-height: 1rem; }
.text-\[10px\] { font-size: 0.625rem; line-height: 1rem; }
.text-sm { font-size: 0.875rem; line-height: 1.25rem; }
.text-lg { font-size: 1.125rem; line-height: 1.75rem; }
.text-xl { font-size: 1.25rem; line-height: 1.75rem; }
.text-3xl { font-size: 1.875rem; line-height: 2.25rem; }
.font-bold { font-weight: 700; }
.font-medium { font-weight: 500; }
.font-normal { font-weight: 400; }
.font-mono { font-family: var(--font-mono); }
.uppercase { text-transform: uppercase; }
.lowercase { text-transform: lowercase; }
.tracking-tight { letter-spacing: -0.025em; }
.tracking-wider { letter-spacing: 0.05em; }
.tracking-widest { letter-spacing: 0.1em; }
.tabular-nums { font-variant-numeric: tabular-nums; }
.text-white { color: white; }
.text-black { color: #000; }
.text-\[var\(--text-muted\)\] { color: var(--text-muted); }
.text-\[var\(--text-secondary\)\] { color: var(--text-secondary); }
.text-\[var\(--text-primary\)\] { color: var(--text-primary); }
.text-\[var\(--status-ok\)\] { color: var(--status-ok); }
.text-\[var\(--status-error\)\] { color: var(--status-error); }
.text-\[var\(--status-warn\)\] { color: var(--status-warn); }
.text-\[var\(--status-info\)\] { color: var(--status-info); }
.text-\[var\(--accent-primary\)\] { color: var(--accent-primary); }
.text-\[var\(--accent-industry\)\] { color: var(--accent-industry); }
.text-\[var\(--accent-policy\)\] { color: var(--accent-policy); }
.shadow-\[0_0_20px_rgba\(167\,139\,250\,0\.2\)\] { box-shadow: 0 0 20px rgba(167, 139, 250, 0.2); }
.shadow-\[inset_0_0_20px_rgba\(100\,180\,255\,0\.05\)\] { box-shadow: inset 0 0 20px rgba(100, 180, 255, 0.05); }
.opacity-20 { opacity: 0.2; }
.opacity-50 { opacity: 0.5; }
.opacity-60 { opacity: 0.6; }
.opacity-80 { opacity: 0.8; }
.opacity-90 { opacity: 0.9; }
.line-clamp-2 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.leading-snug { line-height: 1.375; }
.leading-relaxed { line-height: 1.625; }
.whitespace-nowrap { white-space: nowrap; }
.truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.transition-colors { transition-property: color, background-color, border-color, text-decoration-color, fill, stroke; transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1); transition-duration: 150ms; }
.transition-all { transition-property: all; transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1); transition-duration: 150ms; }
.hover\:bg-black\/40:hover { background-color: rgba(0, 0, 0, 0.4); }
.hover\:border-white\/10:hover { border-color: rgba(255, 255, 255, 0.1); }
.hover\:text-white:hover { color: white; }
.hover\:bg-\[var\(--accent-industry\)\]\/30:hover { background-color: rgba(74, 222, 128, 0.3); }
.hover\:bg-\[var\(--status-error\)\]\/20:hover { background-color: rgba(248, 113, 113, 0.2); }
.hover\:border-\[var\(--accent-primary\)\]\/50:hover { border-color: rgba(100, 180, 255, 0.5); }
.hover\:opacity-100:hover { opacity: 1; }
.focus\:outline-none:focus { outline: 2px solid transparent; outline-offset: 2px; }
.focus\:border-\[var\(--accent-primary\)\]:focus { border-color: var(--accent-primary); }
.focus\:border-\[var\(--accent-industry\)\]:focus { border-color: var(--accent-industry); }
.group:hover .group-hover\:text-\[var\(--accent-primary\)\] { color: var(--accent-primary); }
.select-none { user-select: none; }
.cursor-pointer { cursor: pointer; }
.accent-\[var\(--accent-primary\)\] { accent-color: var(--accent-primary); }
.text-center { text-align: center; }

/* Grid Utils */
.grid { display: grid; }
.grid-cols-1 { grid-template-columns: repeat(1, minmax(0, 1fr)); }
.grid-cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
@media (min-width: 768px) {
  .md\:grid-cols-4 { grid-template-columns: repeat(4, minmax(0, 1fr)); }
  .md\:pl-0 { padding-left: 0; }
}
@media (min-width: 1024px) {
  .lg\:grid-cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .lg\:grid-cols-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .lg\:col-span-2 { grid-column: span 2 / span 2; }
}

.scrollbar-hide::-webkit-scrollbar { display: none; }
.scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
</style>

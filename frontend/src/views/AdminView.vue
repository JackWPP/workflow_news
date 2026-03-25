<script setup lang="ts">
import { onMounted, ref } from 'vue'

import StatusPill from '../components/StatusPill.vue'
import { api } from '../lib/api'
import type {
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
    const [sourcePayload, settingsPayload, runPayload, feedbackPayload, overviewPayload] = await Promise.all([
      api.getSourceRules(),
      api.getReportSettings(),
      api.listRetrievalRuns(),
      api.listQualityFeedback(),
      api.getQualityOverview(),
    ])
    sourcesJson.value = JSON.stringify(sourcePayload.sources, null, 2)
    settings.value = settingsPayload
    runs.value = runPayload.runs
    feedbackItems.value = feedbackPayload.items
    qualityOverview.value = overviewPayload
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
    const [feedbackPayload, overviewPayload] = await Promise.all([api.listQualityFeedback(), api.getQualityOverview()])
    feedbackItems.value = feedbackPayload.items
    qualityOverview.value = overviewPayload
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

function formatExcludedDomains(payload: Record<string, unknown> | null | undefined) {
  const value = payload?.excluded_domains
  return Array.isArray(value) ? value.join(', ') : '-'
}

function formatFeedbackSummary(summary: Record<string, number> | undefined) {
  if (!summary) {
    return '-'
  }
  return Object.entries(summary)
    .map(([key, value]) => `${key}:${value}`)
    .join(' / ')
}

function formatMetricMap(value: unknown) {
  if (!value || typeof value !== 'object') {
    return '-'
  }
  return Object.entries(value as Record<string, unknown>)
    .map(([key, count]) => `${key}:${String(count)}`)
    .join(' / ')
}

onMounted(() => {
  void loadAdmin()
})
</script>

<template>
  <section class="admin-grid">
    <section class="panel">
      <div class="section-head">
        <div>
          <p class="eyebrow">Source Rules</p>
          <h2>来源规则</h2>
        </div>
        <button class="primary-button" @click="saveSources">保存来源</button>
      </div>
      <textarea v-model="sourcesJson" class="admin-textarea" />
    </section>

    <section class="panel">
      <div class="section-head">
        <div>
          <p class="eyebrow">Scheduler</p>
          <h2>调度与模型</h2>
        </div>
        <button class="primary-button" @click="saveSettings">保存配置</button>
      </div>
      <div class="admin-form">
        <label>
          执行小时
          <input v-model.number="settings.report_hour" type="number" min="0" max="23" />
        </label>
        <label>
          执行分钟
          <input v-model.number="settings.report_minute" type="number" min="0" max="59" />
        </label>
        <label class="toggle-row">
          <input v-model="settings.shadow_mode" type="checkbox" />
          开启 shadow-run
        </label>
        <label>
          抽取超时（秒）
          <input v-model.number="settings.scrape_timeout_seconds" type="number" min="5" max="120" />
        </label>
        <label>
          抽取并发
          <input v-model.number="settings.scrape_concurrency" type="number" min="1" max="8" />
        </label>
        <label>
          最大抽取数
          <input v-model.number="settings.max_extractions_per_run" type="number" min="3" max="50" />
        </label>
        <label>
          主模型
          <input v-model="settings.report_primary_model" type="text" />
        </label>
        <label>
          备模型
          <input v-model="settings.report_fallback_model" type="text" />
        </label>
      </div>
    </section>

    <section class="panel admin-runs">
      <div class="section-head">
        <div>
          <p class="eyebrow">Quality</p>
          <h2>质量反馈与概览</h2>
        </div>
      </div>
      <div class="detail-grid">
        <div>
          <h3>新增反馈</h3>
          <div class="admin-form">
            <label>
              target_type
              <select v-model="feedbackDraft.target_type">
                <option value="candidate">candidate</option>
                <option value="report_item">report_item</option>
              </select>
            </label>
            <label>
              target_id
              <input v-model.number="feedbackDraft.target_id" type="number" min="1" />
            </label>
            <label>
              label
              <select v-model="feedbackDraft.label">
                <option value="good">good</option>
                <option value="bad_off_topic">bad_off_topic</option>
                <option value="bad_source">bad_source</option>
                <option value="bad_pr_like">bad_pr_like</option>
                <option value="keep_borderline">keep_borderline</option>
              </select>
            </label>
            <label>
              reason
              <input v-model="feedbackDraft.reason" type="text" />
            </label>
            <label>
              note
              <input v-model="feedbackDraft.note" type="text" />
            </label>
            <button class="primary-button" @click="submitFeedback">提交反馈</button>
          </div>
        </div>
        <div>
          <h3>质量概览</h3>
          <p>feedback {{ formatFeedbackSummary(qualityOverview?.feedback_summary) }}</p>
          <p v-if="qualityOverview?.duplicate_trend?.length">
            duplicate {{ qualityOverview.duplicate_trend.map((item) => `${item.date}:${item.duplicate_ratio}`).join(' / ') }}
          </p>
          <p v-if="qualityOverview?.extended_window_usage?.length">
            extended {{ qualityOverview.extended_window_usage.map((item) => `${item.date}:${item.extended_window_selected}`).join(' / ') }}
          </p>
          <article v-for="item in qualityOverview?.hard_rejects ?? []" :key="item.reason" class="mini-card">
            <strong>{{ item.reason }}</strong>
            <p>{{ item.count }}</p>
          </article>
        </div>
      </div>
      <div class="detail-grid">
        <div>
          <h3>最近反馈</h3>
          <article v-for="item in feedbackItems.slice(0, 8)" :key="item.id" class="mini-card">
            <strong>{{ item.label }}</strong>
            <p>{{ item.target_domain || '-' }} · {{ item.target_type }}#{{ item.target_id }}</p>
            <p>{{ item.target_title || item.reason || '-' }}</p>
          </article>
        </div>
        <div>
          <h3>高风险域名</h3>
          <article v-for="item in qualityOverview?.flagged_domains ?? []" :key="item.domain" class="mini-card">
            <strong>{{ item.domain }}</strong>
            <p>off-topic {{ item.bad_off_topic }} / pr {{ item.bad_pr_like }} / bad-source {{ item.bad_source }}</p>
          </article>
          <article v-for="item in qualityOverview?.source_rule_hotspots ?? []" :key="`source-rule-${item.domain}`" class="mini-card">
            <strong>source-rule {{ item.domain }}</strong>
            <p>{{ item.count }}</p>
          </article>
          <article v-for="item in qualityOverview?.high_tier_false_rejects ?? []" :key="`high-tier-${item.domain}`" class="mini-card">
            <strong>high-tier {{ item.domain }}</strong>
            <p>{{ item.count }}</p>
          </article>
          <article v-for="item in qualityOverview?.top_policy_misses ?? []" :key="`policy-miss-${item.reason}`" class="mini-card">
            <strong>policy miss {{ item.reason }}</strong>
            <p>{{ item.count }}</p>
          </article>
          <article v-for="item in qualityOverview?.dominant_domain_runs ?? []" :key="`dominant-domain-${item.domain}`" class="mini-card">
            <strong>dominant {{ item.domain }}</strong>
            <p>{{ item.count }}</p>
          </article>
        </div>
      </div>
    </section>

    <section class="panel admin-runs">
      <div class="section-head">
        <div>
          <p class="eyebrow">Observability</p>
          <h2>检索运行记录</h2>
        </div>
      </div>
      <p v-if="error" class="error-box">{{ error }}</p>
      <p v-if="saved" class="success-box">{{ saved }}</p>
      <article v-for="run in runs" :key="run.id" class="run-card">
        <div class="run-card-head" @click="inspectRun(run.id)">
          <strong>#{{ run.id }} {{ new Date(run.started_at).toLocaleString() }}</strong>
          <StatusPill :status="run.status" />
        </div>
        <p>query {{ run.query_count }} / candidates {{ run.candidate_count }} / extracted {{ run.extracted_count }}</p>
        <p v-if="run.debug_payload?.planner_model || run.debug_payload?.writer_model">
          planner {{ String(run.debug_payload?.planner_model || '-') }} / writer {{ String(run.debug_payload?.writer_model || '-') }}
        </p>
        <p v-if="run.debug_payload?.quality_gate_counts">
          quality {{ JSON.stringify(run.debug_payload?.quality_gate_counts) }}
        </p>
        <p v-if="run.debug_payload?.duplicate_ratio">
          duplicate {{ String(run.debug_payload?.duplicate_ratio) }}
        </p>
        <p v-if="run.debug_payload?.excluded_domains">
          excluded {{ formatExcludedDomains(run.debug_payload) }}
        </p>
        <p v-if="run.debug_payload?.section_candidate_counts">
          candidates by section {{ formatMetricMap(run.debug_payload?.section_candidate_counts) }}
        </p>
        <p v-if="run.debug_payload?.section_selected_counts">
          selected by section {{ formatMetricMap(run.debug_payload?.section_selected_counts) }}
        </p>
        <p v-if="run.debug_payload?.window_bucket_counts">
          windows {{ formatMetricMap(run.debug_payload?.window_bucket_counts) }}
        </p>
        <p v-if="run.debug_payload?.source_rule_rejections">
          source rules {{ formatMetricMap(run.debug_payload?.source_rule_rejections) }}
        </p>
        <p v-if="run.debug_payload?.high_tier_rejections">
          high-tier rejects {{ formatMetricMap(run.debug_payload?.high_tier_rejections) }}
        </p>
        <p v-if="run.debug_payload?.policy_candidate_count || run.debug_payload?.policy_selected_count">
          policy candidates {{ String(run.debug_payload?.policy_candidate_count || 0) }} / selected {{ String(run.debug_payload?.policy_selected_count || 0) }}
        </p>
        <p v-if="run.debug_payload?.extended_window_selected || run.debug_payload?.metadata_fallback_count">
          extended {{ String(run.debug_payload?.extended_window_selected || 0) }} / metadata fallback {{ String(run.debug_payload?.metadata_fallback_count || 0) }}
        </p>
        <p v-if="run.debug_payload?.per_domain_selected">
          domains {{ formatMetricMap(run.debug_payload?.per_domain_selected) }}
        </p>
        <p v-if="run.debug_payload?.selected_count || run.debug_payload?.section_coverage">
          selected {{ String(run.debug_payload?.selected_count || 0) }} / sections {{ String(run.debug_payload?.section_coverage || 0) }} / high-confidence {{ String(run.debug_payload?.high_confidence_count || 0) }}
        </p>
        <p v-if="run.debug_payload?.feedback_hits">
          feedback hits {{ String(run.debug_payload?.feedback_hits || 0) }}
        </p>
        <p v-if="run.error_message" class="error-inline">{{ run.error_message }}</p>
      </article>
    </section>

    <section class="panel admin-runs">
      <div class="section-head">
        <div>
          <p class="eyebrow">Run Detail</p>
          <h2>运行明细 #{{ selectedRunId ?? '-' }}</h2>
        </div>
      </div>
      <div class="detail-grid">
        <div>
          <h3>Queries</h3>
          <article v-for="query in queries" :key="query.id" class="mini-card">
            <strong>{{ query.section }} / {{ query.language }}</strong>
            <p>{{ query.query_text }}</p>
            <p>{{ query.response_status }} · {{ query.result_count }}</p>
          </article>
        </div>
        <div>
          <h3>Candidates</h3>
          <article v-for="candidate in candidates.slice(0, 12)" :key="candidate.id" class="mini-card">
            <strong>{{ candidate.title }}</strong>
            <p>{{ candidate.domain }} · {{ candidate.status }}</p>
            <p v-if="candidate.rejection_reason">{{ candidate.rejection_reason }}</p>
            <div class="candidate-actions">
              <button class="secondary-button" @click="markCandidate(candidate, 'good')">good</button>
              <button class="secondary-button" @click="markCandidate(candidate, 'bad_off_topic')">off-topic</button>
              <button class="secondary-button" @click="markCandidate(candidate, 'bad_pr_like')">pr-like</button>
              <button class="secondary-button" @click="markCandidate(candidate, 'keep_borderline')">keep</button>
            </div>
          </article>
        </div>
      </div>
    </section>
  </section>
</template>

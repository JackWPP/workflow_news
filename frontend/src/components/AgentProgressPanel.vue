<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import {
  Search,
  FileText,
  PenTool,
  AlertTriangle,
  Info,
  Loader,
  CheckCircle2,
  XCircle,
} from 'lucide-vue-next'

interface StepEvent {
  tool_name: string
  thought?: string
  result_summary?: string
  duration?: number
  step_index?: number
  harness_blocked?: boolean
}

interface PhaseEvent {
  phase: number
  name: string
  step_index?: number
  article_count?: number
}

interface StatsEvent {
  phase?: string
  seed_count?: number
  seed_window?: string
  [key: string]: any
}

interface WarningEvent {
  warning_code?: string
  message?: string
  step_index?: number
}

const props = defineProps<{
  active: boolean
}>()

const emit = defineEmits<{
  (e: 'complete', data: { report_id: number }): void
  (e: 'error', message: string): void
}>()

const currentPhase = ref<PhaseEvent | null>(null)
const steps = ref<StepEvent[]>([])
const warnings = ref<WarningEvent[]>([])
const stats = ref<StatsEvent | null>(null)
const errorMessage = ref('')

const stepListEl = ref<HTMLElement | null>(null)

const TOOL_LABELS: Record<string, string> = {
  web_search: '🔍 联网检索',
  read_page: '📖 抓取正文',
  read_pool_article: '📥 读取候选',
  evaluate_article: '⭐ 价值评估',
  search_images: '🖼️ 配图检索',
  verify_image: '✅ 配图校验',
  write_section: '✍️ 撰写板块',
  compare_sources: '🔬 多源对比',
  check_coverage: '📊 覆盖度检查',
  follow_references: '🔗 引文追溯',
  finish: '🏁 完成报告',
}

const stages = [
  { label: '检索来源', detail: '查找今日相关信息', icon: Search },
  { label: '整理内容', detail: '筛选重点和来源依据', icon: FileText },
  { label: '生成简报', detail: '组织今日智能日报', icon: PenTool },
]

const stageIndex = computed(() => {
  const phase = Number(currentPhase.value?.phase ?? 0)
  if (phase >= 98) return 2  // 收尾/完成
  if (phase >= 3) return 2
  if (phase === 2) return 1
  if (phase === 1) return 0
  // 没收到 phase 时按最近一个 tool 推断
  const latestTool = steps.value.at(-1)?.tool_name || ''
  if (['write_section', 'finish', 'check_coverage'].includes(latestTool)) return 2
  if (['read_page', 'read_pool_article', 'evaluate_article', 'search_images', 'verify_image'].includes(latestTool)) return 1
  return 0
})

const stepsToShow = computed(() => steps.value.slice(-50))
const currentStep = computed(() => steps.value.at(-1) ?? null)
const totalDuration = computed(() => steps.value.reduce((acc, s) => acc + (s.duration || 0), 0))

function toolLabel(name: string): string {
  return TOOL_LABELS[name] || name
}

function handleStep(data: StepEvent) {
  steps.value.push(data)
  void nextTick(() => {
    if (stepListEl.value) {
      stepListEl.value.scrollTop = stepListEl.value.scrollHeight
    }
  })
}

function handlePhase(data: PhaseEvent) {
  currentPhase.value = data
}

function handleStats(data: StatsEvent) {
  stats.value = data
}

function handleWarning(data: WarningEvent) {
  warnings.value.push(data)
}

function handleComplete(data: any) {
  emit('complete', data)
}

function handleError(data: any) {
  errorMessage.value = data.message || '本次更新未完成，请稍后重试'
  emit('error', errorMessage.value)
}

function reset() {
  steps.value = []
  warnings.value = []
  stats.value = null
  currentPhase.value = null
  errorMessage.value = ''
}

defineExpose({
  handleStep,
  handlePhase,
  handleStats,
  handleWarning,
  handleComplete,
  handleError,
  reset,
})
</script>

<template>
  <div v-if="active" class="agent-progress-panel">
    <div class="phase-status">
      <Loader class="w-4 h-4 animate-spin" />
      <span>正在更新今日简报</span>
      <span v-if="currentPhase" class="phase-name">· {{ currentPhase.name }}</span>
    </div>

    <div class="phase-bar">
      <div
        v-for="(stage, index) in stages"
        :key="stage.label"
        class="phase-dot"
        :class="{
          'phase-active': stageIndex === index,
          'phase-done': stageIndex > index,
        }"
      >
        <component :is="stage.icon" class="w-4 h-4" />
        <span class="phase-label">{{ stage.label }}</span>
        <span class="phase-detail">{{ stage.detail }}</span>
      </div>
    </div>

    <div v-if="stats" class="info-bar">
      <Info class="w-4 h-4" />
      <span>
        <template v-if="stats.seed_count !== undefined">
          已加载 {{ stats.seed_count }} 条种子
          <span v-if="stats.seed_window" class="text-xs opacity-70">（窗口 {{ stats.seed_window }}）</span>
        </template>
      </span>
    </div>

    <div v-for="(w, idx) in warnings" :key="idx" class="warning-bar">
      <AlertTriangle class="w-4 h-4" />
      <span>{{ w.message || w.warning_code }}</span>
    </div>

    <div v-if="errorMessage" class="error-bar">
      <XCircle class="w-4 h-4" />
      <span>{{ errorMessage }}</span>
    </div>

    <div v-if="steps.length > 0" class="step-summary">
      <div class="step-summary-row">
        <span>步骤 {{ steps.length }}</span>
        <span v-if="currentStep">· 当前：{{ toolLabel(currentStep.tool_name) }}</span>
        <span class="ml-auto">累计耗时 {{ totalDuration.toFixed(1) }}s</span>
      </div>
    </div>

    <div ref="stepListEl" class="step-list">
      <div
        v-for="(s, idx) in stepsToShow"
        :key="`${s.step_index}-${idx}`"
        class="step-row"
        :class="{
          'step-blocked': s.harness_blocked,
          'step-current': s === currentStep,
        }"
      >
        <span class="step-index">#{{ s.step_index ?? idx + 1 }}</span>
        <span class="step-tool">{{ toolLabel(s.tool_name) }}</span>
        <span class="step-summary-text">{{ s.result_summary || '执行中…' }}</span>
        <span v-if="s.duration !== undefined" class="step-duration">{{ s.duration.toFixed(1) }}s</span>
        <CheckCircle2 v-if="!s.harness_blocked" class="w-3 h-3 step-ok" />
        <XCircle v-else class="w-3 h-3 step-fail" />
      </div>
    </div>

    <p class="stats-bar">完成后将自动展示最新内容。</p>
  </div>
</template>

<style scoped>
.agent-progress-panel {
  background: var(--bg-surface);
  border: 1px solid var(--border-glow);
  box-shadow: var(--shadow);
  border-radius: 16px;
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.phase-bar {
  display: flex;
  justify-content: center;
  gap: 32px;
}

.phase-dot {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  opacity: 0.3;
  transition: all 0.3s;
}

.phase-dot.phase-active {
  opacity: 1;
  color: var(--accent-primary);
}

.phase-dot.phase-done {
  opacity: 0.65;
  color: #34d399;
}

.phase-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.phase-detail {
  font-size: 10px;
  color: var(--text-muted);
}

.phase-status {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: center;
  font-size: 14px;
  color: var(--text-secondary);
}

.phase-name {
  color: var(--accent-primary);
  font-weight: 500;
}

.info-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--status-info);
  background: rgba(37,99,235,0.06);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 12px;
}

.warning-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--status-warn);
  background: rgba(217,119,6,0.06);
  border-radius: 8px;
  padding: 8px 12px;
  font-size: 12px;
}

.error-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--status-error);
  background: rgba(220,38,38,0.06);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 13px;
}

.step-summary {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: var(--text-secondary);
  border-top: 1px dashed var(--border-glow);
  padding-top: 8px;
}

.step-summary-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.step-list {
  max-height: 220px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  background: #f8fafc;
  border: 1px solid var(--border-glow);
  border-radius: 8px;
  padding: 10px 12px;
}

.step-list::-webkit-scrollbar {
  width: 6px;
}

.step-list::-webkit-scrollbar-thumb {
  background: rgba(0,0,0,0.15);
  border-radius: 3px;
}

.step-row {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--text-secondary);
  padding: 2px 4px;
  border-radius: 4px;
}

.step-row.step-current {
  background: rgba(43,87,151,0.06);
  color: var(--text-primary);
}

.step-row.step-blocked {
  color: #fbbf24;
}

.step-index {
  color: var(--text-muted);
  width: 32px;
  flex-shrink: 0;
}

.step-tool {
  width: 96px;
  flex-shrink: 0;
  color: var(--accent-primary);
}

.step-summary-text {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.step-duration {
  color: var(--text-muted);
  font-size: 11px;
}

.step-ok {
  color: #34d399;
  flex-shrink: 0;
}

.step-fail {
  color: #fbbf24;
  flex-shrink: 0;
}

.stats-bar {
  text-align: center;
  font-size: 11px;
  color: var(--text-muted);
  margin: 0;
}
</style>

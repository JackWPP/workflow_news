<script setup lang="ts">
import { computed, ref } from 'vue'
import { Search, FileText, PenTool, AlertTriangle, Loader } from 'lucide-vue-next'

interface StepEvent {
  tool_name: string
  thought: string
  result_summary: string
  duration: number
  step_index: number
  harness_blocked: boolean
}

interface PhaseEvent {
  phase: number
  name: string
  article_count?: number
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
const errorMessage = ref('')

const stageIndex = computed(() => {
  const phase = Number(currentPhase.value?.phase ?? 1)
  if (phase >= 3) return 2
  if (phase >= 2) return 1
  const latestTool = steps.value.at(-1)?.tool_name || ''
  if (['write_section', 'finish', 'check_coverage'].includes(latestTool)) return 2
  if (['read_page', 'evaluate_article', 'search_images'].includes(latestTool)) return 1
  return 0
})

const stages = [
  { label: '检索来源', detail: '正在查找今日相关信息', icon: Search },
  { label: '整理内容', detail: '正在筛选重点和来源依据', icon: FileText },
  { label: '生成简报', detail: '正在组织今日智能日报', icon: PenTool },
]

function handleStep(data: StepEvent) {
  steps.value.push(data)
}

function handlePhase(data: PhaseEvent) {
  currentPhase.value = data
}

function handleComplete(data: any) {
  emit('complete', data)
}

function handleError(data: any) {
  errorMessage.value = data.message || '本次更新未完成，请稍后重试'
  emit('error', errorMessage.value)
}

defineExpose({ handleStep, handlePhase, handleComplete, handleError })
</script>

<template>
  <div v-if="active" class="agent-progress-panel">
    <div class="phase-status">
      <Loader class="w-4 h-4 animate-spin" />
      <span>正在更新今日简报</span>
    </div>

    <div class="phase-bar">
      <div
        v-for="(stage, index) in stages"
        :key="stage.label"
        class="phase-dot"
        :class="{
          'phase-active': stageIndex === index,
          'phase-done': stageIndex > index
        }"
      >
        <component :is="stage.icon" class="w-4 h-4" />
        <span class="phase-label">{{ stage.label }}</span>
        <span class="phase-detail">{{ stage.detail }}</span>
      </div>
    </div>

    <!-- Error -->
    <div v-if="errorMessage" class="error-bar">
      <AlertTriangle class="w-4 h-4" />
      <span>{{ errorMessage }}</span>
    </div>

    <p class="stats-bar">更新完成后将自动显示最新内容。</p>
  </div>
</template>

<style scoped>
.agent-progress-panel {
  background: rgba(15, 20, 35, 0.9);
  backdrop-filter: blur(12px);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: 16px;
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
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
  gap: 6px;
  opacity: 0.3;
  transition: all 0.3s;
}

.phase-dot.phase-active {
  opacity: 1;
  color: var(--accent-primary);
}

.phase-dot.phase-done {
  opacity: 0.6;
  color: #34d399;
}

.phase-label {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.phase-status {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: center;
  font-size: 14px;
  color: var(--text-secondary);
}

.steps-log {
  max-height: 320px;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 4px;
}

.steps-log::-webkit-scrollbar {
  width: 4px;
}
.steps-log::-webkit-scrollbar-thumb {
  background: rgba(255,255,255,0.15);
  border-radius: 2px;
}

.step-item {
  background: rgba(255,255,255,0.03);
  border-radius: 8px;
  padding: 10px 14px;
  border-left: 3px solid rgba(255,255,255,0.08);
  font-size: 13px;
}

.step-item.step-blocked {
  border-left-color: #f59e0b;
}

.step-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.step-tool {
  color: var(--accent-primary);
  font-weight: 600;
  font-size: 12px;
  text-transform: uppercase;
}

.step-duration {
  font-size: 11px;
  opacity: 0.5;
}

.step-thought {
  color: var(--text-muted);
  font-size: 12px;
  margin: 2px 0;
  line-height: 1.4;
}

.step-result {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.4;
}

.error-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  color: #ef4444;
  background: rgba(239,68,68,0.1);
  border-radius: 8px;
  padding: 10px 14px;
  font-size: 13px;
}

.stats-bar {
  display: flex;
  gap: 20px;
  justify-content: center;
  font-size: 12px;
  color: var(--text-muted);
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
.animate-spin {
  animation: spin 1s linear infinite;
}
</style>

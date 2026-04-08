<script setup lang="ts">
import { ref, nextTick } from 'vue'
import { Search, FileText, Link, PenTool, AlertTriangle, Loader } from 'lucide-vue-next'

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
const stepsContainer = ref<HTMLElement | null>(null)

function handleStep(data: StepEvent) {
  steps.value.push(data)
  nextTick(() => {
    if (stepsContainer.value) {
      stepsContainer.value.scrollTop = stepsContainer.value.scrollHeight
    }
  })
}

function handlePhase(data: PhaseEvent) {
  currentPhase.value = data
}

function handleComplete(data: any) {
  emit('complete', data)
}

function handleError(data: any) {
  errorMessage.value = data.message || 'Unknown error'
  emit('error', errorMessage.value)
}

defineExpose({ handleStep, handlePhase, handleComplete, handleError })
</script>

<template>
  <div v-if="active" class="agent-progress-panel">
    <!-- Phase indicator -->
    <div class="phase-bar">
      <div
        v-for="p in [1, 2, 2.5, 3]"
        :key="p"
        class="phase-dot"
        :class="{
          'phase-active': currentPhase?.phase === p,
          'phase-done': currentPhase && currentPhase.phase > p
        }"
      >
        <Search v-if="p === 1" class="w-4 h-4" />
        <FileText v-else-if="p === 2" class="w-4 h-4" />
        <Link v-else-if="p === 2.5" class="w-4 h-4" />
        <PenTool v-else class="w-4 h-4" />
        <span class="phase-label">{{ p === 2.5 ? '验证' : ['', '搜索', '处理', '', '综合'][p as number] }}</span>
      </div>
    </div>

    <!-- Current phase description -->
    <div v-if="currentPhase" class="phase-status">
      <Loader class="w-4 h-4 animate-spin" />
      <span>{{ currentPhase.name }}...</span>
      <span v-if="currentPhase.article_count" class="text-xs opacity-60">
        ({{ currentPhase.article_count }} articles)
      </span>
    </div>

    <!-- Step log -->
    <div ref="stepsContainer" class="steps-log">
      <div
        v-for="(step, idx) in steps"
        :key="idx"
        class="step-item"
        :class="{ 'step-blocked': step.harness_blocked }"
      >
        <div class="step-header">
          <span class="step-tool">{{ step.tool_name }}</span>
          <span class="step-duration">{{ step.duration }}s</span>
        </div>
        <p v-if="step.thought" class="step-thought">{{ step.thought }}</p>
        <p class="step-result">{{ step.result_summary }}</p>
      </div>
      <div v-if="steps.length === 0" class="step-item opacity-50">
        <Loader class="w-4 h-4 animate-spin inline" /> Waiting for first step...
      </div>
    </div>

    <!-- Error -->
    <div v-if="errorMessage" class="error-bar">
      <AlertTriangle class="w-4 h-4" />
      <span>{{ errorMessage }}</span>
    </div>

    <!-- Stats bar -->
    <div class="stats-bar">
      <span>Steps: {{ steps.length }}</span>
      <span>Searches: {{ steps.filter(s => s.tool_name === 'web_search').length }}</span>
    </div>
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

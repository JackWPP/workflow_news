<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'

import MarkdownPanel from '../components/MarkdownPanel.vue'
import ReportItemCard from '../components/ReportItemCard.vue'
import HeroSection from '../components/HeroSection.vue'
import CoverageGauge from '../components/CoverageGauge.vue'
import SectionDivider from '../components/SectionDivider.vue'
import AgentProgressPanel from '../components/AgentProgressPanel.vue'
import { api } from '../lib/api'
import type { Report } from '../types'
import { LayoutGrid, FileText } from 'lucide-vue-next'

const report = ref<Report | null>(null)
const loading = ref(false)
const generating = ref(false)
const error = ref('')
const viewMode = ref<'cards' | 'markdown'>('cards')
const progressPanel = ref<InstanceType<typeof AgentProgressPanel> | null>(null)
let activeES: EventSource | null = null

const heroItem = computed(() => report.value?.items.find((item) => item.has_verified_image) ?? report.value?.items[0] ?? null)

const groupedItems = computed(() => {
  const buckets: Record<string, Report['items']> = { industry: [], academic: [], policy: [] }
  for (const item of report.value?.items ?? []) {
    buckets[item.section] = [...(buckets[item.section] ?? []), item]
  }
  return buckets
})

const stats = computed(() => ({
  academic: groupedItems.value.academic?.length || 0,
  industry: groupedItems.value.industry?.length || 0,
  policy: groupedItems.value.policy?.length || 0,
  images: (report.value?.image_review_summary?.verified_image_count as number | undefined) ?? report.value?.items.filter(i => i.has_verified_image).length ?? 0
}))

async function loadReport() {
  loading.value = true
  error.value = ''
  try {
    report.value = await api.todayReport()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function regenerate() {
  generating.value = true
  error.value = ''
  report.value = null
  try {
    const { run_id } = await api.runReport()
    activeES = api.streamProgress(run_id, {
      onStep: (data) => progressPanel.value?.handleStep(data),
      onPhase: (data) => progressPanel.value?.handlePhase(data),
      onComplete: async (data) => {
        activeES = null
        generating.value = false
        loading.value = true
        try {
          if (data.report_id) {
            report.value = await api.getReport(data.report_id)
          } else {
            report.value = await api.todayReport()
          }
        } catch {
          report.value = await api.todayReport()
        } finally {
          loading.value = false
        }
      },
      onError: (data) => {
        progressPanel.value?.handleError(data)
        activeES = null
        generating.value = false
        error.value = data.message || '报告生成失败'
      },
    })
  } catch (err) {
    generating.value = false
    error.value = err instanceof Error ? err.message : '生成失败'
  }
}

onMounted(() => {
  void loadReport()
})

onUnmounted(() => {
  activeES?.close()
})
</script>

<template>
  <div class="flex flex-col gap-6 relative max-w-7xl mx-auto pb-12">
    <!-- Hero Banner -->
    <HeroSection :report="report" :heroItem="heroItem" :loading="loading" @regenerate="regenerate" />

    <p v-if="error" class="status-error status-pill w-max mx-auto px-4 py-2">{{ error }}</p>

    <!-- Agent Progress Panel (shown during generation) -->
    <AgentProgressPanel ref="progressPanel" :active="generating" />

    <template v-if="report && !generating">
      <!-- Toolbar & Analytics Row -->
      <div class="flex flex-col lg:flex-row gap-6 items-end justify-between mb-2">
        <!-- Coverage Gauge Analytics -->
        <div class="w-full lg:w-[480px]">
          <CoverageGauge 
            :academicCount="stats.academic" 
            :industryCount="stats.industry" 
            :policyCount="stats.policy" 
            :imageCount="stats.images"
          />
        </div>

        <!-- View Toggle Buttons -->
        <div class="flex items-center gap-2 bg-black/40 p-1 rounded-xl border border-white/10 shrink-0">
          <button 
            @click="viewMode = 'cards'" 
            class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            :class="viewMode === 'cards' ? 'bg-[var(--accent-primary)] text-black shadow-[0_0_15px_rgba(100,180,255,0.3)]' : 'text-[var(--text-secondary)] hover:text-white'"
          >
            <LayoutGrid class="w-4 h-4" /> 聚合卡片
          </button>
          <button 
            @click="viewMode = 'markdown'" 
            class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            :class="viewMode === 'markdown' ? 'bg-[var(--accent-primary)] text-black shadow-[0_0_15px_rgba(100,180,255,0.3)]' : 'text-[var(--text-secondary)] hover:text-white'"
          >
            <FileText class="w-4 h-4" /> 全景速览报告
          </button>
        </div>
      </div>

      <!-- Main Content Flow -->
      <div v-if="viewMode === 'cards'" class="flex flex-col gap-4 mt-6">
        <template v-for="section in ['industry', 'academic', 'policy']" :key="section">
          <div v-show="groupedItems[section]?.length > 0">
            <SectionDivider :section="section" :count="groupedItems[section].length" />
            
            <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
              <ReportItemCard v-for="item in groupedItems[section]" :key="item.id" :item="item" />
            </div>
          </div>
        </template>
      </div>

      <div v-else class="glass-panel mt-6">
        <MarkdownPanel :content="report.markdown_content || ''" />
      </div>
    </template>
    
    <div v-else-if="loading && !generating" class="flex flex-col items-center justify-center p-20 gap-4">
      <div class="w-12 h-12 border-4 border-[var(--accent-primary)] border-t-transparent rounded-full animate-spin glow-[0_0_15px_rgba(100,180,255,0.4)]"></div>
      <p class="text-[var(--text-secondary)] animate-pulse">正在获取情报矩阵...</p>
    </div>
  </div>
</template>

<style scoped>
.flex { display: flex; }
.flex-col { flex-direction: column; }
.gap-2 { gap: 0.5rem; }
.gap-4 { gap: 1rem; }
.gap-6 { gap: 1.5rem; }
.relative { position: relative; }
.max-w-7xl { max-width: 80rem; }
.mx-auto { margin-left: auto; margin-right: auto; }
.pb-12 { padding-bottom: 3rem; }
.w-max { width: max-content; }
.px-4 { padding-left: 1rem; padding-right: 1rem; }
.py-2 { padding-top: 0.5rem; padding-bottom: 0.5rem; }
.items-end { align-items: flex-end; }
.items-center { align-items: center; }
.justify-between { justify-content: space-between; }
.justify-center { justify-content: center; }
.mb-2 { margin-bottom: 0.5rem; }
.w-full { width: 100%; }
.shrink-0 { flex-shrink: 0; }
.bg-black\/40 { background-color: rgba(0,0,0,0.4); }
.p-1 { padding: 0.25rem; }
.rounded-xl { border-radius: 0.75rem; }
.rounded-lg { border-radius: 0.5rem; }
.rounded-full { border-radius: 9999px; }
.border { border-width: 1px; }
.border-white\/10 { border-color: rgba(255,255,255,0.1); }
.text-sm { font-size: 0.875rem; }
.font-medium { font-weight: 500; }
.transition-colors { transition-property: color, background-color, border-color; transition-duration: 300ms; }
.w-4 { width: 1rem; }
.h-4 { height: 1rem; }
.mt-6 { margin-top: 1.5rem; }
.grid { display: grid; }
.grid-cols-1 { grid-template-columns: repeat(1, minmax(0, 1fr)); }
.p-20 { padding: 5rem; }
.w-12 { width: 3rem; }
.h-12 { height: 3rem; }
.border-4 { border-width: 4px; }
.border-\[var\(--accent-primary\)\] { border-color: var(--accent-primary); }
.border-t-transparent { border-top-color: transparent; }
.text-black { color: #000; }
.bg-\[var\(--accent-primary\)\] { background-color: var(--accent-primary); }
.shadow-\[0_0_15px_rgba\(100\,180\,255\,0\.3\)\] { box-shadow: 0 0 15px rgba(100,180,255,0.3); }

/* Animation Utils */
@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
.animate-spin { animation: spin 1s linear infinite; }
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
.animate-pulse { animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; }

/* Responsive Grid */
@media (min-width: 768px) {
  .md\:grid-cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (min-width: 1024px) {
  .lg\:grid-cols-3 { grid-template-columns: repeat(3, minmax(0, 1fr)); }
  .lg\:flex-row { flex-direction: row; }
  .lg\:w-\[480px\] { width: 480px; }
}
</style>

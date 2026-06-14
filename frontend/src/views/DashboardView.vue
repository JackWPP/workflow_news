<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'

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
const reportType = ref<'global' | 'ai' | 'lab'>('global')
const activeCategory = ref<'all' | '高材制造' | '清洁能源'>('all')
const progressPanel = ref<InstanceType<typeof AgentProgressPanel> | null>(null)
let activeES: EventSource | null = null

const heroItem = computed(() => filteredItems.value.find((item) => item.has_verified_image) ?? filteredItems.value[0] ?? null)

const filteredItems = computed(() => {
  const items = report.value?.items ?? []
  if (activeCategory.value === 'all') return items
  return items.filter(item => item.decision_trace?.category === activeCategory.value)
})

const isLabReport = computed(() => report.value?.report_type === 'lab')
const isAiReport = computed(() => report.value?.report_type === 'ai')

const activeSections = computed(() => {
  if (isLabReport.value) return ['patent', 'wechat', 'lab_news']
  return ['industry', 'academic', 'policy']
})

const groupedItems = computed(() => {
  const buckets: Record<string, Report['items']> = {}
  for (const s of activeSections.value) buckets[s] = []
  for (const item of filteredItems.value) {
    if (buckets[item.section]) buckets[item.section].push(item)
    else buckets[item.section] = [item]
  }
  return buckets
})

const langGroupedItems = computed(() => {
  const result: Record<string, { zh: Report['items']; en: Report['items'] }> = {}
  for (const s of activeSections.value) result[s] = { zh: [], en: [] }
  for (const item of report.value?.items ?? []) {
    const lang = item.language === 'en' ? 'en' : 'zh'
    if (!result[item.section]) result[item.section] = { zh: [], en: [] }
    result[item.section][lang].push(item)
  }
  return result
})

const stats = computed(() => ({
  academic: groupedItems.value.academic?.length || 0,
  industry: groupedItems.value.industry?.length || 0,
  policy: groupedItems.value.policy?.length || 0,
  patent: groupedItems.value.patent?.length || 0,
  wechat: groupedItems.value.wechat?.length || 0,
  lab_news: groupedItems.value.lab_news?.length || 0,
  images: (report.value?.image_review_summary?.verified_image_count as number | undefined) ?? filteredItems.value.filter(i => i.has_verified_image).length ?? 0
}))

const qualityNote = computed(() => {
  if (!report.value) return ''
  const items = filteredItems.value
  const itemCount = items.length
  const sectionCount = new Set(items.map((item) => item.section).filter(Boolean)).size
  if (isLabReport.value && itemCount === 0) return '暂无今天的实验室日报内容，专利和公众号文章将在采集后自动展示。'
  if (isLabReport.value) return ''
  if (itemCount >= 4 && sectionCount >= 2) return ''
  if (itemCount === 0) return '今日暂未形成可发布内容，可稍后更新今日简报。'
  return `本期已收录 ${itemCount} 条高相关内容，覆盖 ${sectionCount} 个板块。`
})

async function loadReport() {
  loading.value = true
  error.value = ''
  try {
    if (reportType.value === 'lab') {
      report.value = await api.todayLabReport()
    } else if (reportType.value === 'ai') {
      report.value = await api.todayAiReport()
    } else {
      report.value = await api.todayGlobalReport()
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

watch(reportType, () => {
  // 报告生成进行中时，切换 reportType 会触发并发的 loadReport()，
  // 跟 SSE 流抢 report.value，体验上看起来是"刚开始就被覆盖"。
  // 这种切换由模板上的 :disabled 拦住；这里再做一次保险。
  if (generating.value) return
  void loadReport()
})

async function regenerate() {
  generating.value = true
  error.value = ''
  report.value = null
  progressPanel.value?.reset()
  try {
    const { run_id } = await api.runReport(reportType.value)
    activeES = api.streamProgress(run_id, {
      onStep: (data) => progressPanel.value?.handleStep(data),
      onPhase: (data) => progressPanel.value?.handlePhase(data),
      onStats: (data) => progressPanel.value?.handleStats(data),
      onWarning: (data) => progressPanel.value?.handleWarning(data),
      onComplete: async (data) => {
        activeES = null
        generating.value = false
        loading.value = true
        try {
          if (data.report_id) {
            report.value = await api.getReport(data.report_id)
          } else {
            await loadReport()
          }
        } catch {
          await loadReport()
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
    <HeroSection :report="report" :heroItem="heroItem" :loading="loading || generating" @regenerate="regenerate" />

    <p v-if="error" class="status-error status-pill w-max mx-auto px-4 py-2">{{ error }}</p>

    <AgentProgressPanel ref="progressPanel" :active="generating" />

    <template v-if="report && !generating">
      <div class="flex items-center gap-2 bg-gray-100 p-1 rounded-xl border border-gray-200 w-max">
        <button
          @click="reportType = 'global'"
          :disabled="generating"
          class="px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          :class="reportType === 'global' ? 'bg-[var(--accent-primary)] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'"
        >全球日报</button>
        <button
          @click="reportType = 'ai'"
          :disabled="generating"
          class="px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          :class="reportType === 'ai' ? 'bg-[var(--accent-primary)] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'"
        >AI 日报</button>
        <button
          @click="reportType = 'lab'"
          :disabled="generating"
          class="px-4 py-2 rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          :class="reportType === 'lab' ? 'bg-[var(--accent-primary)] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'"
        >实验室日报</button>
      </div>

      <div v-if="!isLabReport && !isAiReport" class="flex items-center gap-2 bg-gray-100 p-1 rounded-xl border border-gray-200 w-max mt-4">
        <button
          v-for="cat in (['all', '高材制造', '清洁能源'] as const)"
          :key="cat"
          @click="activeCategory = cat"
          class="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
          :class="activeCategory === cat ? 'bg-[var(--accent-primary)] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'"
        >{{ cat === 'all' ? '全部' : cat }}</button>
      </div>

      <div class="flex flex-col lg:flex-row gap-6 items-end justify-between mb-2">
        <div v-if="isLabReport" class="w-full lg:w-[480px]">
          <div class="glass-panel p-5">
            <h3 class="text-sm font-semibold tracking-wider text-[var(--text-secondary)] uppercase mb-3">实验室日报</h3>
            <div class="flex gap-4 text-sm">
              <span class="text-amber-400">专利 {{ stats.patent }} 条</span>
              <span class="text-green-400">公众号 {{ stats.wechat }} 条</span>
              <span class="text-purple-400">资讯 {{ stats.lab_news }} 条</span>
            </div>
          </div>
        </div>
        <div v-else-if="isAiReport" class="w-full lg:w-[480px]">
          <div class="glass-panel p-5">
            <h3 class="text-sm font-semibold tracking-wider text-[var(--text-secondary)] uppercase mb-3">AI 日报</h3>
            <div class="flex gap-4 text-sm">
              <span class="text-blue-400">产业 {{ stats.industry }} 条</span>
              <span class="text-cyan-400">研究 {{ stats.academic }} 条</span>
              <span class="text-orange-400">政策 {{ stats.policy }} 条</span>
            </div>
          </div>
        </div>
        <div v-else class="w-full lg:w-[480px]">
          <CoverageGauge
            :academicCount="stats.academic"
            :industryCount="stats.industry"
            :policyCount="stats.policy"
          />
        </div>

        <div class="flex items-center gap-2 bg-gray-100 p-1 rounded-xl border border-gray-200 shrink-0">
          <button 
            @click="viewMode = 'cards'" 
            class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            :class="viewMode === 'cards' ? 'bg-[var(--accent-primary)] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'"
          >
            <LayoutGrid class="w-4 h-4" /> 聚合卡片
          </button>
          <button 
            @click="viewMode = 'markdown'" 
            class="flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-colors"
            :class="viewMode === 'markdown' ? 'bg-[var(--accent-primary)] text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'"
          >
            <FileText class="w-4 h-4" /> 全景速览报告
          </button>
        </div>
      </div>

      <div v-if="qualityNote" class="text-sm text-[var(--text-secondary)] bg-blue-50 border border-blue-100 rounded-xl px-4 py-3">
        {{ qualityNote }}
      </div>

      <div v-if="viewMode === 'cards'" class="flex flex-col gap-4 mt-6">
        <template v-for="section in activeSections" :key="section">
          <div v-show="groupedItems[section]?.length > 0">
            <SectionDivider :section="section" :count="groupedItems[section].length" />
            <template v-for="lang in (['zh', 'en'] as const)" :key="`${section}-${lang}`">
              <div v-if="langGroupedItems[section]?.[lang]?.length > 0" class="mb-4">
                <div class="text-xs text-[var(--text-secondary)] uppercase tracking-wider mb-2 px-1">
                  {{ lang === 'zh' ? '中文来源' : '英文来源' }}
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                  <ReportItemCard v-for="item in langGroupedItems[section][lang]" :key="item.id" :item="item" />
                </div>
              </div>
            </template>
          </div>
        </template>
      </div>

      <div v-else class="glass-panel mt-6">
        <MarkdownPanel :content="report.markdown_content || ''" />
      </div>
    </template>
    
    <div v-else-if="loading && !generating" class="flex flex-col items-center justify-center p-20 gap-4">
      <div class="w-12 h-12 border-4 border-[var(--accent-primary)] border-t-transparent rounded-full animate-spin"></div>
      <p class="text-[var(--text-secondary)] animate-pulse">正在读取今日简报...</p>
    </div>

    <div v-else class="flex flex-col items-center justify-center p-20 gap-4 bg-white border border-gray-200 rounded-2xl text-center">
      <p class="text-[var(--text-primary)] text-xl font-semibold">今日日报尚未生成</p>
      <p class="text-[var(--text-secondary)] max-w-xl">可以先更新今日简报。系统会优先整理高质量行业动态、政策信号和研究进展，再输出可阅读的日报版本。</p>
    </div>
  </div>
</template>

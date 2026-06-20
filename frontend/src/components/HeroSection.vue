<script setup lang="ts">
import { computed } from 'vue'
import { Play, Calendar, Layers } from 'lucide-vue-next'
import type { Report } from '../types'

const props = defineProps<{ 
  report: Report | null,
  heroItem: any,
  loading: boolean 
}>()

defineEmits(['regenerate'])

const itemCount = computed(() => props.report?.items?.length ?? 0)
const activeSectionCount = computed(() => {
  const items = props.report?.items ?? []
  return new Set(items.map((item) => item.section).filter(Boolean)).size
})
const thinReportNote = computed(() => {
  if (!props.report) return ''
  if (itemCount.value >= 4 && activeSectionCount.value >= 2) return ''
  const hints: string[] = []
  if (itemCount.value < 4) hints.push(`本期收录 ${itemCount.value} 条重点内容`)
  if (activeSectionCount.value < 2) hints.push(`覆盖 ${activeSectionCount.value} 个板块`)
  return hints.join('，')
})

const supervisorSummary = computed(() => {
  const actions = props.report?.supervisor_actions ?? []
  if (!actions.length) return ''
  return `本期已进行 ${actions.length} 轮来源复核。`
})
</script>

<template>
  <div class="glass-panel overflow-hidden relative min-h-[240px] md:min-h-[300px] flex group mb-8">
    <div class="absolute inset-0 z-0">
      <img v-if="heroItem?.image_url" :src="heroItem.image_url" class="w-full h-full object-cover opacity-10 transition-transform duration-1000 group-hover:scale-105" />
      <div v-else class="w-full h-full bg-gradient-to-br from-blue-50 to-slate-50"></div>
      <div class="absolute inset-0 bg-gradient-to-r from-white via-white/90 to-transparent"></div>
    </div>

    <div class="relative z-10 p-5 sm:p-8 md:p-12 flex flex-col justify-between w-full max-w-2xl">
      <div>
        <div class="flex items-center gap-3 mb-4">
          <span class="px-3 py-1 bg-blue-50 rounded-full text-xs font-semibold tracking-widest text-[var(--accent-academic)] border border-blue-100 uppercase">
            今日日报
          </span>
          <span v-if="report && report.status === 'running'" class="flex items-center gap-2 text-xs text-[var(--accent-academic)] bg-blue-50 px-3 py-1 rounded-full border border-blue-100">
            <span class="w-2 h-2 rounded-full bg-[var(--accent-academic)] animate-pulse-glow"></span>
            正在更新...
          </span>
        </div>
        
        <h2 class="text-2xl sm:text-3xl md:text-4xl font-bold text-[var(--text-primary)] mb-4 leading-tight tracking-tight">
          {{ report?.title || '高分子材料加工智能日报' }}
        </h2>
        
        <p class="text-[var(--text-secondary)] text-base md:text-lg leading-relaxed line-clamp-3 md:line-clamp-none max-w-xl">
          {{ report?.summary || '聚焦产业动态、政策信号与前沿研究，优先交付一份稳定、可信、可读的行业日报。' }}
        </p>
        <div v-if="thinReportNote || supervisorSummary" class="mt-4 flex flex-col gap-2 max-w-xl">
          <p v-if="thinReportNote" class="text-sm text-[var(--text-secondary)] bg-blue-50 border border-blue-100 rounded-xl px-4 py-3">
            <strong class="text-[var(--text-primary)]">本期说明：</strong>{{ thinReportNote }}。
          </p>
          <p v-if="supervisorSummary" class="text-xs text-[var(--text-muted)]">
            {{ supervisorSummary }}
          </p>
        </div>
      </div>

      <div class="mt-8 flex flex-wrap items-center gap-4">
        <button 
          @click="$emit('regenerate')" 
          :disabled="loading"
          class="flex items-center gap-2 bg-[var(--accent-academic)] text-white hover:bg-[#1e3f73] font-bold px-5 py-2.5 rounded-xl transition-all disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Play class="w-5 h-5 fill-current" /> 
          {{ loading ? '正在更新...' : '更新今日简报' }}
        </button>
        
        <div v-if="report" class="flex items-center gap-6 px-4 py-2 border-l-0 md:border-l border-[var(--line)] ml-0 md:ml-2">
          <div class="flex flex-col">
            <span class="text-[10px] text-[var(--text-muted)] uppercase tracking-wider flex items-center gap-1"><Calendar class="w-3 h-3"/> 日期</span>
            <span class="text-sm text-[var(--text-primary)] font-medium">{{ report.report_date }}</span>
          </div>
          <div class="flex flex-col">
            <span class="text-[10px] text-[var(--text-muted)] uppercase tracking-wider flex items-center gap-1"><Layers class="w-3 h-3"/> 覆盖</span>
            <span class="text-sm text-[var(--text-primary)] font-medium">{{ itemCount }} 条 / {{ activeSectionCount }} 板块</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

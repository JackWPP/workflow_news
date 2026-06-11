<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Calendar, Image as ImageIcon, Sparkles, FileText } from 'lucide-vue-next'

import ReportItemCard from '../components/ReportItemCard.vue'
import { api } from '../lib/api'
import type { Report } from '../types'

const reports = ref<Report[]>([])
const selected = ref<Report | null>(null)
const loading = ref(false)
const error = ref('')

async function loadReports() {
  loading.value = true
  error.value = ''
  try {
    const payload = await api.listReports()
    reports.value = payload.reports
    selected.value = payload.reports[0] ?? null
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadReports()
})
</script>

<template>
  <div class="flex h-[calc(100vh-80px)] gap-6 max-w-7xl mx-auto w-full relative">
    <aside class="w-80 flex-shrink-0 flex flex-col gap-4 border-r border-white/5 pr-4 relative z-10">
      <div class="flex items-center justify-between pb-4 border-b border-[var(--line)] shrink-0">
        <div>
          <h2 class="text-xl font-bold text-white flex items-center gap-2">
            <Calendar class="w-5 h-5 text-[var(--accent-industry)]" />
            历史日报
          </h2>
          <p class="text-xs text-[var(--text-muted)] mt-1 tracking-wider">按日期查看往期智能日报</p>
        </div>
      </div>

      <div class="flex-1 overflow-y-auto space-y-3 pr-2 scrollbar-hide">
        <div v-if="loading" class="text-center text-[var(--text-muted)] py-8 text-sm animate-pulse">
          读取档案中...
        </div>
        <div v-else-if="error" class="text-[var(--status-error)] p-3 rounded-lg bg-[var(--status-error)]/10 border border-[var(--status-error)]/20 text-sm">
          {{ error }}
        </div>
        
        <button
          v-for="report in reports"
          :key="report.id"
          class="w-full text-left p-4 rounded-xl border transition-all duration-300 group flex flex-col gap-2 relative overflow-hidden"
          :class="selected?.id === report.id 
            ? 'bg-[var(--bg-surface)] border-[var(--accent-industry)]/40 shadow-[inset_0_0_20px_rgba(74,222,128,0.1)]' 
            : 'bg-black/20 border-white/5 hover:border-white/10 hover:bg-black/40'"
          @click="selected = report"
        >
          <div v-if="selected?.id === report.id" class="absolute left-0 top-0 bottom-0 w-1 bg-[var(--accent-industry)] shadow-[0_0_10px_var(--accent-industry)]"></div>
          
          <div class="flex justify-between items-start gap-2">
            <strong class="text-white font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white to-[var(--text-secondary)]">
              {{ report.report_date }}
            </strong>
          </div>
          
          <p class="text-sm text-[var(--text-secondary)] line-clamp-2 leading-relaxed">
            {{ report.summary || report.title }}
          </p>
          
          <div class="flex items-center gap-4 mt-1 pt-2 border-t border-white/5 text-[10px] text-[var(--text-muted)] uppercase tracking-wider">
            <span class="flex items-center gap-1"><FileText class="w-3 h-3"/> {{ report.items?.length || 0 }} 条</span>
          </div>
        </button>
      </div>
    </aside>

    <main class="flex-1 flex flex-col min-w-0 glass-panel border border-[var(--line)] rounded-2xl overflow-hidden relative z-10 shadow-2xl">
      <template v-if="selected">
        <div class="relative shrink-0 border-b border-[var(--line)] bg-black/40 backdrop-blur-md overflow-hidden">
          <div class="absolute inset-0 opacity-10">
            <img v-if="selected.items?.find((item) => item.image_url)" 
                 :src="selected.items.find((item) => item.image_url)?.image_url ?? ''" 
                 class="w-full h-full object-cover blur-sm" />
          </div>
          <div class="relative px-8 py-6 z-10">
            <p class="text-[10px] text-[var(--accent-industry)] uppercase tracking-widest font-bold mb-2 flex items-center gap-2">
              <Sparkles class="w-3 h-3" /> 智能日报详情
            </p>
            <h2 class="text-2xl md:text-3xl font-bold text-white leading-tight drop-shadow-md">
              {{ selected.title }}
            </h2>
            <div class="flex items-center gap-4 mt-4 text-xs font-medium">
              <span class="text-[var(--text-secondary)] bg-white/5 px-3 py-1 rounded-full border border-white/10">{{ selected.report_date }}</span>
            </div>
          </div>
        </div>

        <div class="flex-1 overflow-y-auto p-6 md:p-8 scroll-smooth z-0 bg-[rgba(0,0,0,0.1)]">
          <div class="grid grid-cols-1 xl:grid-cols-2 gap-6 pb-8">
            <ReportItemCard v-for="item in selected.items" :key="item.id" :item="item" />
          </div>
        </div>
      </template>
      
      <div v-else class="flex-1 flex items-center justify-center flex-col gap-4 opacity-50">
        <ImageIcon class="w-16 h-16 text-[var(--text-muted)]" />
        <p class="text-sm font-medium text-[var(--text-secondary)]">选择左侧存档查看详情</p>
      </div>
    </main>
  </div>
</template>

<style scoped>
.scrollbar-hide::-webkit-scrollbar { display: none; }
.scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
</style>

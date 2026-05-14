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
    <!-- Sidebar: History List -->
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
          <!-- Active Indicator line -->
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

    <!-- Main Content: selected report -->
    <main class="flex-1 flex flex-col min-w-0 glass-panel border border-[var(--line)] rounded-2xl overflow-hidden relative z-10 shadow-2xl">
      <template v-if="selected">
        <!-- Header / Hero inside pane -->
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

        <!-- Scrollable Grid -->
        <div class="flex-1 overflow-y-auto p-6 md:p-8 scroll-smooth z-0 bg-[rgba(0,0,0,0.1)]">
          <div class="grid grid-cols-1 xl:grid-cols-2 gap-6 pb-8">
            <ReportItemCard v-for="item in selected.items" :key="item.id" :item="item" />
          </div>
        </div>
      </template>
      
      <!-- Empty State -->
      <div v-else class="flex-1 flex items-center justify-center flex-col gap-4 opacity-50">
        <ImageIcon class="w-16 h-16 text-[var(--text-muted)]" />
        <p class="text-sm font-medium text-[var(--text-secondary)]">选择左侧存档查看详情</p>
      </div>
    </main>
  </div>
</template>

<style scoped>
.flex { display: flex; }
.flex-col { flex-direction: column; }
.justify-between { justify-content: space-between; }
.justify-center { justify-content: center; }
.items-center { align-items: center; }
.items-start { align-items: flex-start; }
.gap-1 { gap: 0.25rem; }
.gap-2 { gap: 0.5rem; }
.gap-4 { gap: 1rem; }
.gap-6 { gap: 1.5rem; }
.relative { position: relative; }
.absolute { position: absolute; }
.inset-0 { inset: 0; }
.left-0 { left: 0; }
.top-0 { top: 0; }
.bottom-0 { bottom: 0; }
.z-0 { z-index: 0; }
.z-10 { z-index: 10; }
.w-full { width: 100%; }
.h-full { height: 100%; }
.w-1 { width: 0.25rem; }
.w-3 { width: 0.75rem; }
.h-3 { height: 0.75rem; }
.w-5 { width: 1.25rem; }
.h-5 { height: 1.25rem; }
.w-16 { width: 4rem; }
.h-16 { height: 4rem; }
.w-80 { width: 20rem; }
.h-\[calc\(100vh-80px\)\] { height: calc(100vh - 80px); }
.max-w-7xl { max-width: 80rem; }
.mx-auto { margin-left: auto; margin-right: auto; }
.min-w-0 { min-width: 0; }
.flex-1 { flex: 1 1 0%; }
.shrink-0 { flex-shrink: 0; }
.flex-shrink-0 { flex-shrink: 0; }
.overflow-hidden { overflow: hidden; }
.overflow-y-auto { overflow-y: auto; }
.scroll-smooth { scroll-behavior: smooth; }
.mt-1 { margin-top: 0.25rem; }
.mt-4 { margin-top: 1rem; }
.pb-4 { padding-bottom: 1rem; }
.pb-8 { padding-bottom: 2rem; }
.pr-2 { padding-right: 0.5rem; }
.pr-4 { padding-right: 1rem; }
.p-3 { padding: 0.75rem; }
.p-4 { padding: 1rem; }
.px-8 { padding-left: 2rem; padding-right: 2rem; }
.py-6 { padding-top: 1.5rem; padding-bottom: 1.5rem; }
.py-8 { padding-top: 2rem; padding-bottom: 2rem; }
.px-3 { padding-left: 0.75rem; padding-right: 0.75rem; }
.py-1 { padding-top: 0.25rem; padding-bottom: 0.25rem; }
.border-b { border-bottom-width: 1px; }
.border-r { border-right-width: 1px; }
.border-t { border-top-width: 1px; }
.border { border-width: 1px; }
.border-white\/5 { border-color: rgba(255, 255, 255, 0.05); }
.border-white\/10 { border-color: rgba(255, 255, 255, 0.1); }
.border-\[var\(--line\)\] { border-color: var(--line); }
.border-\[var\(--accent-industry\)\]\/40 { border-color: rgba(74, 222, 128, 0.4); }
.border-\[var\(--status-error\)\]\/20 { border-color: rgba(248, 113, 113, 0.2); }
.rounded-full { border-radius: 9999px; }
.rounded-lg { border-radius: 0.5rem; }
.rounded-xl { border-radius: 0.75rem; }
.rounded-2xl { border-radius: 1rem; }
.text-left { text-align: left; }
.text-center { text-align: center; }
.text-\[10px\] { font-size: 0.625rem; }
.text-xs { font-size: 0.75rem; line-height: 1rem; }
.text-sm { font-size: 0.875rem; line-height: 1.25rem; }
.text-xl { font-size: 1.25rem; line-height: 1.75rem; }
.text-2xl { font-size: 1.5rem; line-height: 2rem; }
.font-medium { font-weight: 500; }
.font-bold { font-weight: 700; }
.tracking-tight { letter-spacing: -0.025em; }
.tracking-wider { letter-spacing: 0.05em; }
.tracking-widest { letter-spacing: 0.1em; }
.uppercase { text-transform: uppercase; }
.text-white { color: white; }
.text-transparent { color: transparent; }
.text-\[var\(--text-muted\)\] { color: var(--text-muted); }
.text-\[var\(--text-secondary\)\] { color: var(--text-secondary); }
.text-\[var\(--accent-industry\)\] { color: var(--accent-industry); }
.text-\[var\(--status-error\)\] { color: var(--status-error); }
.text-\[var\(--status-ok\)\] { color: var(--status-ok); }
.bg-clip-text { -webkit-background-clip: text; background-clip: text; }
.bg-gradient-to-r { background-image: linear-gradient(to right, var(--tw-gradient-stops)); }
.from-white { --tw-gradient-from: #fff; --tw-gradient-stops: var(--tw-gradient-from), var(--tw-gradient-to); }
.to-\[var\(--text-secondary\)\] { --tw-gradient-to: var(--text-secondary); }
.bg-black\/20 { background-color: rgba(0, 0, 0, 0.2); }
.bg-black\/40 { background-color: rgba(0, 0, 0, 0.4); }
.bg-white\/5 { background-color: rgba(255, 255, 255, 0.05); }
.bg-\[var\(--bg-surface\)\] { background-color: var(--bg-surface); }
.bg-\[var\(--status-error\)\]\/10 { background-color: rgba(248, 113, 113, 0.1); }
.bg-\[var\(--accent-industry\)\] { background-color: var(--accent-industry); }
.bg-\[rgba\(0\,0\,0\,0\.1\)\] { background-color: rgba(0,0,0,0.1); }
.opacity-10 { opacity: 0.1; }
.opacity-50 { opacity: 0.5; }
.opacity-70 { opacity: 0.7; }
.shadow-2xl { box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25); }
.shadow-\[inset_0_0_20px_rgba\(74\,222\,128\,0\.1\)\] { box-shadow: inset 0 0 20px rgba(74, 222, 128, 0.1); }
.shadow-\[0_0_10px_var\(--accent-industry\)\] { box-shadow: 0 0 10px var(--accent-industry); }
.drop-shadow-md { filter: drop-shadow(0 4px 3px rgba(0, 0, 0, 0.2)); }
.backdrop-blur-md { backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); }
.blur-sm { filter: blur(4px); }
.object-cover { object-fit: cover; }
.line-clamp-2 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.leading-relaxed { line-height: 1.625; }
.leading-tight { line-height: 1.25; }
.transition-all { transition-property: all; transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1); }
.duration-300 { transition-duration: 300ms; }
.hover\:border-white\/10:hover { border-color: rgba(255, 255, 255, 0.1); }
.hover\:bg-black\/40:hover { background-color: rgba(0, 0, 0, 0.4); }

.grid { display: grid; }
.grid-cols-1 { grid-template-columns: repeat(1, minmax(0, 1fr)); }
@media (min-width: 768px) {
  .md\:p-8 { padding: 2rem; }
  .md\:text-3xl { font-size: 1.875rem; line-height: 2.25rem; }
}
@media (min-width: 1280px) {
  .xl\:grid-cols-2 { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
.animate-pulse { animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
.scrollbar-hide::-webkit-scrollbar { display: none; }
.scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
</style>

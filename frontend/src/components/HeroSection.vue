<script setup lang="ts">
import { Play, Calendar, CheckSquare, Layers } from 'lucide-vue-next'
import type { Report } from '../types'

defineProps<{ 
  report: Report | null,
  heroItem: any,
  loading: boolean 
}>()

defineEmits(['regenerate'])
</script>

<template>
  <div class="glass-panel overflow-hidden relative min-h-[300px] flex group mb-8">
    <!-- Parallax/Hero Background -->
    <div class="absolute inset-0 z-0">
      <img v-if="heroItem?.image_url" :src="heroItem.image_url" class="w-full h-full object-cover opacity-20 transition-transform duration-1000 group-hover:scale-105" />
      <div v-else class="w-full h-full bg-gradient-to-br from-[#0a0e1a] to-[#1a2640]"></div>
      <!-- Gradient Overlay -->
      <div class="absolute inset-0 bg-gradient-to-r from-[var(--bg-surface)] via-[var(--bg-surface)]/80 to-transparent"></div>
    </div>

    <!-- Content -->
    <div class="relative z-10 p-8 md:p-12 flex flex-col justify-between w-full max-w-2xl">
      <div>
        <div class="flex items-center gap-3 mb-4">
          <span class="px-3 py-1 bg-white/10 rounded-full text-xs font-semibold tracking-widest text-[var(--accent-academic)] border border-white/10 uppercase drop-shadow-md">
            Visual Daily
          </span>
          <span v-if="report && report.status === 'running'" class="flex items-center gap-2 text-xs text-[var(--accent-academic)] bg-[var(--accent-academic)]/10 px-3 py-1 rounded-full border border-[var(--accent-academic)]/20">
            <span class="w-2 h-2 rounded-full bg-[var(--accent-academic)] animate-pulse-glow"></span>
            Agent 同步执行中...
          </span>
        </div>
        
        <h2 class="text-4xl font-bold text-white mb-4 leading-tight tracking-tight drop-shadow-[0_2px_10px_rgba(0,0,0,0.5)]">
          {{ report?.title || '今日暂无分析摘要' }}
        </h2>
        
        <p class="text-[var(--text-secondary)] text-lg leading-relaxed line-clamp-3 md:line-clamp-none max-w-xl">
          {{ report?.summary || '受控多轮 agent harness 正在驱动每日图文发布，先保证质量，再自动发布最佳版本。' }}
        </p>
      </div>

      <div class="mt-8 flex flex-wrap items-center gap-4">
        <button 
          @click="$emit('regenerate')" 
          :disabled="loading"
          class="flex items-center gap-2 bg-[var(--accent-primary)] text-[#0a0e1a] hover:bg-[var(--accent-primary)]/90 font-bold px-6 py-3 rounded-xl transition-all shadow-[0_0_20px_var(--accent-primary)] disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <Play class="w-5 h-5 fill-current" /> 
          {{ loading ? '重新生成分析...' : '触发 Agent 深度挖掘' }}
        </button>
        
        <div v-if="report" class="flex items-center gap-6 px-4 py-2 border-l border-white/10 ml-2">
          <div class="flex flex-col">
            <span class="text-[10px] text-[var(--text-muted)] uppercase tracking-wider flex items-center gap-1"><Calendar class="w-3 h-3"/> Date</span>
            <span class="text-sm text-white font-medium">{{ report.report_date }}</span>
          </div>
          <div class="flex flex-col">
            <span class="text-[10px] text-[var(--text-muted)] uppercase tracking-wider flex items-center gap-1"><Layers class="w-3 h-3"/> Rounds</span>
            <span class="text-sm text-white font-medium">{{ report.round_count || 1 }}</span>
          </div>
          <div class="flex flex-col">
            <span class="text-[10px] text-[var(--text-muted)] uppercase tracking-wider flex items-center gap-1"><CheckSquare class="w-3 h-3"/> Assets</span>
            <span class="text-sm text-white font-medium">{{ report.image_review_summary?.verified_image_count ?? 0 }} imgs</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.min-h-\[300px\] { min-height: 300px; }
.relative { position: relative; }
.absolute { position: absolute; }
.inset-0 { inset: 0; }
.z-0 { z-index: 0; }
.z-10 { z-index: 10; }
.overflow-hidden { overflow: hidden; }
.flex { display: flex; }
.flex-col { flex-direction: column; }
.flex-wrap { flex-wrap: wrap; }
.justify-between { justify-content: space-between; }
.items-center { align-items: center; }
.w-full { width: 100%; }
.h-full { height: 100%; }
.max-w-2xl { max-width: 42rem; }
.max-w-xl { max-width: 36rem; }
.object-cover { object-fit: cover; }
.opacity-20 { opacity: 0.2; }
.group:hover .group-hover\:scale-105 { transform: scale(1.05); }
.transition-transform { transition-property: transform; transition-duration: 1000ms; }
.bg-gradient-to-br { background-image: linear-gradient(to bottom right, var(--tw-gradient-stops)); }
.bg-gradient-to-r { background-image: linear-gradient(to right, var(--tw-gradient-stops)); }
.from-\[\#0a0e1a\] { --tw-gradient-from: #0a0e1a; --tw-gradient-stops: var(--tw-gradient-from), var(--tw-gradient-to); }
.to-\[\#1a2640\] { --tw-gradient-to: #1a2640; }
.from-\[var\(--bg-surface\)\] { --tw-gradient-from: var(--bg-surface); --tw-gradient-stops: var(--tw-gradient-from), var(--tw-gradient-to); }
.via-\[var\(--bg-surface\)\]\/80 { --tw-gradient-stops: var(--tw-gradient-from), rgba(15, 20, 40, 0.68), var(--tw-gradient-to); }
.to-transparent { --tw-gradient-to: transparent; }
.p-8 { padding: 2rem; }
.md\:p-12 { padding: 3rem; }
.px-3 { padding-left: 0.75rem; padding-right: 0.75rem; }
.py-1 { padding-top: 0.25rem; padding-bottom: 0.25rem; }
.px-6 { padding-left: 1.5rem; padding-right: 1.5rem; }
.py-3 { padding-top: 0.75rem; padding-bottom: 0.75rem; }
.px-4 { padding-left: 1rem; padding-right: 1rem; }
.py-2 { padding-top: 0.5rem; padding-bottom: 0.5rem; }
.mb-4 { margin-bottom: 1rem; }
.mb-8 { margin-bottom: 2rem; }
.mt-8 { margin-top: 2rem; }
.ml-2 { margin-left: 0.5rem; }
.gap-1 { gap: 0.25rem; }
.gap-2 { gap: 0.5rem; }
.gap-3 { gap: 0.75rem; }
.gap-4 { gap: 1rem; }
.gap-6 { gap: 1.5rem; }
.bg-white\/10 { background-color: rgba(255, 255, 255, 0.1); }
.bg-\[var\(--accent-academic\)\]\/10 { background-color: rgba(108, 180, 255, 0.1); }
.bg-\[var\(--accent-primary\)\] { background-color: var(--accent-primary); }
.hover\:bg-\[var\(--accent-primary\)\]\/90:hover { background-color: rgba(108, 180, 255, 0.9); }
.rounded-full { border-radius: 9999px; }
.rounded-xl { border-radius: 0.75rem; }
.border { border-width: 1px; }
.border-l { border-left-width: 1px; }
.border-white\/10 { border-color: rgba(255, 255, 255, 0.1); }
.border-\[var\(--accent-academic\)\]\/20 { border-color: rgba(108, 180, 255, 0.2); }
.text-xs { font-size: 0.75rem; line-height: 1rem; }
.text-sm { font-size: 0.875rem; line-height: 1.25rem; }
.text-lg { font-size: 1.125rem; line-height: 1.75rem; }
.text-4xl { font-size: 2.25rem; line-height: 2.5rem; }
.text-\[10px\] { font-size: 0.625rem; }
.font-semibold { font-weight: 600; }
.font-bold { font-weight: 700; }
.font-medium { font-weight: 500; }
.tracking-widest { letter-spacing: 0.1em; }
.tracking-wider { letter-spacing: 0.05em; }
.tracking-tight { letter-spacing: -0.025em; }
.uppercase { text-transform: uppercase; }
.text-\[var\(--accent-academic\)\] { color: var(--accent-academic); }
.text-white { color: white; }
.text-\[var\(--text-secondary\)\] { color: var(--text-secondary); }
.text-\[var\(--text-muted\)\] { color: var(--text-muted); }
.text-\[\#0a0e1a\] { color: #0a0e1a; }
.drop-shadow-md { filter: drop-shadow(0 4px 3px rgba(0, 0, 0, 0.2)); }
.drop-shadow-\[0_2px_10px_rgba\(0\,0\,0\,0\.5\)\] { filter: drop-shadow(0 2px 10px rgba(0,0,0,0.5)); }
.shadow-\[0_0_20px_var\(--accent-primary\)\] { box-shadow: 0 0 20px var(--accent-primary); }
.leading-tight { line-height: 1.25; }
.leading-relaxed { line-height: 1.625; }
.line-clamp-3 { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
.w-2 { width: 0.5rem; }
.h-2 { height: 0.5rem; }
.w-3 { width: 0.75rem; }
.h-3 { height: 0.75rem; }
.w-5 { width: 1.25rem; }
.h-5 { height: 1.25rem; }
.fill-current { fill: currentColor; }
.transition-all { transition-property: all; transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1); transition-duration: 300ms; }
.disabled\:opacity-50:disabled { opacity: 0.5; }
.disabled\:cursor-not-allowed:disabled { cursor: not-allowed; }
</style>

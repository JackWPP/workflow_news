<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  academicCount: number
  industryCount: number
  policyCount: number
  imageCount: number
}>()

const total = computed(() => props.academicCount + props.industryCount + props.policyCount || 1)
const activeSections = computed(() => [props.industryCount, props.academicCount, props.policyCount].filter((count) => count > 0).length)
const coverageStatus = computed(() => {
  if (total.value >= 4 && activeSections.value >= 2 && props.imageCount >= 1) return '覆盖达标'
  if (total.value >= 3 && activeSections.value >= 2) return '基础可读'
  return '内容较少'
})

const academicWidth = computed(() => `${(props.academicCount / total.value) * 100}%`)
const industryWidth = computed(() => `${(props.industryCount / total.value) * 100}%`)
const policyWidth = computed(() => `${(props.policyCount / total.value) * 100}%`)
</script>

<template>
  <div class="glass-panel p-5 flex flex-col gap-4">
    <div class="flex justify-between items-center">
      <h3 class="text-sm font-semibold tracking-wider text-[var(--text-secondary)] uppercase">今日覆盖</h3>
      <div class="flex items-center gap-2 text-xs text-[var(--status-ok)] bg-[var(--status-ok)]/10 px-2 py-1 rounded-full border border-[var(--status-ok)]/20 shadow-[0_0_10px_rgba(52,211,153,0.1)]">
        <span class="w-1.5 h-1.5 rounded-full bg-[var(--status-ok)] animate-pulse-glow"></span>
        {{ coverageStatus }}
      </div>
    </div>

    <!-- Progress Bar -->
    <div class="h-2 w-full flex bg-black/40 rounded-full overflow-hidden shadow-inner">
      <div class="h-full bg-[var(--accent-industry)] transition-all duration-1000 shadow-[0_0_10px_var(--accent-industry)]" :style="{ width: industryWidth }"></div>
      <div class="h-full bg-[var(--accent-academic)] transition-all duration-1000 shadow-[0_0_10px_var(--accent-academic)]" :style="{ width: academicWidth }"></div>
      <div class="h-full bg-[var(--accent-policy)] transition-all duration-1000 shadow-[0_0_10px_var(--accent-policy)]" :style="{ width: policyWidth }"></div>
    </div>

    <!-- Legend -->
    <div class="flex justify-between text-sm mt-1">
      <div class="flex items-center gap-2">
        <div class="w-3 h-3 rounded-sm bg-[var(--accent-industry)] shadow-[0_0_8px_var(--accent-industry)]/50"></div>
        <span class="text-[var(--text-primary)]">产业 <strong class="tabular-nums">{{ industryCount }}</strong></span>
      </div>
      <div class="flex items-center gap-2">
        <div class="w-3 h-3 rounded-sm bg-[var(--accent-academic)] shadow-[0_0_8px_var(--accent-academic)]/50"></div>
        <span class="text-[var(--text-primary)]">学术 <strong class="tabular-nums">{{ academicCount }}</strong></span>
      </div>
      <div class="flex items-center gap-2">
        <div class="w-3 h-3 rounded-sm bg-[var(--accent-policy)] shadow-[0_0_8px_var(--accent-policy)]/50"></div>
        <span class="text-[var(--text-primary)]">政策 <strong class="tabular-nums">{{ policyCount }}</strong></span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.flex { display: flex; }
.flex-col { flex-direction: column; }
.gap-2 { gap: 0.5rem; }
.gap-4 { gap: 1rem; }
.justify-between { justify-content: space-between; }
.items-center { align-items: center; }
.p-5 { padding: 1.25rem; }
.text-xs { font-size: 0.75rem; }
.text-sm { font-size: 0.875rem; }
.font-semibold { font-weight: 600; }
.tracking-wider { letter-spacing: 0.05em; }
.uppercase { text-transform: uppercase; }
.px-2 { padding-left: 0.5rem; padding-right: 0.5rem; }
.py-1 { padding-top: 0.25rem; padding-bottom: 0.25rem; }
.rounded-full { border-radius: 9999px; }
.rounded-sm { border-radius: 0.125rem; }
.border { border-width: 1px; }
.border-t { border-top-width: 1px; }
.w-full { width: 100%; }
.h-2 { height: 0.5rem; }
.h-full { height: 100%; }
.w-1\.5 { width: 0.375rem; }
.h-1\.5 { height: 0.375rem; }
.w-3 { width: 0.75rem; }
.h-3 { height: 0.75rem; }
.overflow-hidden { overflow: hidden; }
.shadow-inner { box-shadow: inset 0 2px 4px 0 rgba(0, 0, 0, 0.5); }
.mt-1 { margin-top: 0.25rem; }
.mt-2 { margin-top: 0.5rem; }
.pt-3 { padding-top: 0.75rem; }
.text-white { color: white; }
.tabular-nums { font-variant-numeric: tabular-nums; }
.transition-all { transition-property: all; transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1); }
.duration-1000 { transition-duration: 1000ms; }
.bg-black\/40 { background-color: rgba(0,0,0,0.4); }
</style>

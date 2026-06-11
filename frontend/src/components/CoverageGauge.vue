<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  academicCount: number
  industryCount: number
  policyCount: number
}>()

const total = computed(() => props.academicCount + props.industryCount + props.policyCount || 1)

const academicWidth = computed(() => `${(props.academicCount / total.value) * 100}%`)
const industryWidth = computed(() => `${(props.industryCount / total.value) * 100}%`)
const policyWidth = computed(() => `${(props.policyCount / total.value) * 100}%`)
</script>

<template>
  <div class="glass-panel p-5 flex flex-col gap-4">
    <div class="flex justify-between items-center">
      <h3 class="text-sm font-semibold tracking-wider text-[var(--text-secondary)] uppercase">今日覆盖</h3>
    </div>

    <div class="h-2 w-full flex bg-black/40 rounded-full overflow-hidden shadow-inner">
      <div class="h-full bg-[var(--accent-industry)] transition-all duration-1000 shadow-[0_0_10px_var(--accent-industry)]" :style="{ width: industryWidth }"></div>
      <div class="h-full bg-[var(--accent-academic)] transition-all duration-1000 shadow-[0_0_10px_var(--accent-academic)]" :style="{ width: academicWidth }"></div>
      <div class="h-full bg-[var(--accent-policy)] transition-all duration-1000 shadow-[0_0_10px_var(--accent-policy)]" :style="{ width: policyWidth }"></div>
    </div>

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

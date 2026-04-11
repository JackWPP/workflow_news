<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status: string }>()

const label = computed(() => {
  const map: Record<string, string> = {
    complete: '完整',
    partial: '待补强',
    degraded: '降级发布',
    failed: '未生成',
    running: '运行中',
  }
  return map[props.status] ?? props.status
})

const toneClass = computed(() => {
  const map: Record<string, string> = {
    complete: 'status-ok',
    partial: 'status-warn',
    degraded: 'status-info',
    failed: 'status-error',
    running: 'status-info',
  }
  return map[props.status] ?? 'status-info'
})
</script>

<template>
  <span class="status-pill" :class="toneClass">{{ label }}</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status: string }>()

const label = computed(() => {
  const map: Record<string, string> = {
    complete: '已更新',
    partial: '内容较少',
    degraded: '暂未完成',
    failed: '未完成',
    running: '更新中',
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

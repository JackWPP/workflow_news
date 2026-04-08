<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ status: string }>()

const label = computed(() => {
  const map: Record<string, string> = {
    complete: '完整',
    partial: '部分完成',
    degraded: '降级',
    failed: '失败',
    running: '运行中',
  }
  return map[props.status] ?? props.status
})
</script>

<template>
  <span class="status-pill" :class="`status-${status}`">{{ label }}</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Microscope, Construction, ScrollText, FileText, MessageCircle, FlaskConical } from 'lucide-vue-next'

const props = defineProps<{
  section: string
  count: number
}>()

const icon = computed(() => {
  if (props.section === 'academic') return Microscope
  if (props.section === 'industry') return Construction
  if (props.section === 'policy') return ScrollText
  if (props.section === 'patent') return FileText
  if (props.section === 'wechat') return MessageCircle
  if (props.section === 'lab_news') return FlaskConical
  return Microscope
})

const sectionLabel = computed(() => {
  const map: Record<string, string> = {
    academic: '学术前沿', industry: '产业动态', policy: '政策标准',
    patent: '专利精选', wechat: '英蓝云展', lab_news: '实验室资讯',
  }
  return map[props.section] || props.section
})

const colorClass = computed(() => {
  if (props.section === 'academic') return 'text-[var(--accent-academic)] glow-academic'
  if (props.section === 'industry') return 'text-[var(--accent-industry)] glow-industry'
  if (props.section === 'policy') return 'text-[var(--accent-policy)] glow-policy'
  if (props.section === 'patent') return 'text-amber-400 glow-patent'
  if (props.section === 'wechat') return 'text-green-400 glow-wechat'
  if (props.section === 'lab_news') return 'text-purple-400 glow-lab'
  return 'text-[var(--accent-academic)]'
})
</script>

<template>
  <div class="flex items-center gap-4 my-8 relative w-full group">
    <div class="flex-1 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent flex items-center justify-center"></div>
    <div :class="['flex items-center gap-3 px-4 py-2 rounded-full border border-white/5 bg-black/40 backdrop-blur-sm transition-all duration-300 group-hover:bg-white/5', colorClass]">
      <component :is="icon" class="w-5 h-5 flex-shrink-0" />
      <h2 class="font-bold tracking-widest text-lg">{{ sectionLabel }}</h2>
      <span class="ml-2 text-xs font-mono opacity-60 tabular-nums">[{{ count }} 条]</span>
    </div>
    <div class="flex-1 h-px bg-gradient-to-r from-transparent via-white/10 to-transparent"></div>
  </div>
</template>

<style scoped>
.glow-academic { box-shadow: 0 0 15px rgba(108, 180, 255, 0.2); border-color: rgba(108, 180, 255, 0.4); }
.glow-industry { box-shadow: 0 0 15px rgba(74, 222, 128, 0.2); border-color: rgba(74, 222, 128, 0.4); }
.glow-policy { box-shadow: 0 0 15px rgba(167, 139, 250, 0.2); border-color: rgba(167, 139, 250, 0.4); }
.glow-patent { box-shadow: 0 0 15px rgba(245, 158, 11, 0.2); border-color: rgba(245, 158, 11, 0.4); }
.glow-wechat { box-shadow: 0 0 15px rgba(34, 197, 94, 0.2); border-color: rgba(34, 197, 94, 0.4); }
.glow-lab { box-shadow: 0 0 15px rgba(139, 92, 246, 0.2); border-color: rgba(139, 92, 246, 0.4); }
</style>

<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ReportItem } from '../types'
import { ExternalLink, CheckCircle2, ChevronDown, ChevronUp } from 'lucide-vue-next'
import { cardBrief, evidenceLabel, presentSourceName, sourceKindLabel, sourceReliabilityLabel, trustLabel } from '../lib/reportPresentation'

import fallbackManufacturing from '../assets/fallback-manufacturing.png'
import fallbackEnergy from '../assets/fallback-energy.png'
import fallbackAi from '../assets/fallback-ai.png'
import fallbackDefault from '../assets/fallback-default.png'

function categoryFallback(category?: string): string {
  switch (category) {
    case '高材制造': return fallbackManufacturing
    case '清洁能源': return fallbackEnergy
    case 'AI': return fallbackAi
    default: return fallbackDefault
  }
}

const props = defineProps<{ item: ReportItem }>()

const showTrace = ref(false)

const hasTrace = computed(() => {
  const t = props.item.decision_trace
  return t && (
    t.search_query || t.evaluation_reason || t.key_finding ||
    t.source_tier || t.page_kind || t.evidence_strength
  )
})

const sectionColor = computed(() => {
  switch (props.item.section) {
    case 'academic': return 'var(--accent-academic)'
    case 'industry': return 'var(--accent-industry)'
    case 'policy': return 'var(--accent-policy)'
    default: return 'var(--accent-academic)'
  }
})

const cardStyle = computed(() => {
  return {
    '--card-accent': sectionColor.value,
    '--card-glow': props.item.section === 'industry' ? 'var(--glow-green)' :
                   props.item.section === 'policy' ? 'var(--glow-purple)' : 'var(--glow-blue)'
  }
})

const friendlySourceName = computed(() => {
  return presentSourceName(props.item.source_name, props.item.source_url)
})

const sourceTrust = computed(() => trustLabel(props.item.decision_trace))
const evidenceText = computed(() => evidenceLabel(props.item.decision_trace))
const sourceKindText = computed(() => sourceKindLabel(props.item.decision_trace))
const sourceReliabilityText = computed(() => sourceReliabilityLabel(props.item.decision_trace))
const brief = computed(() => cardBrief(props.item))

const publishedLabel = computed(() => {
  if (!props.item.published_at) return '时间待确认'
  const publishedAt = new Date(props.item.published_at)
  if (Number.isNaN(publishedAt.getTime())) return '时间待确认'
  const diffMs = Date.now() - publishedAt.getTime()
  const diffHours = Math.max(0, Math.floor(diffMs / (1000 * 60 * 60)))
  if (diffHours < 1) return '1小时内'
  if (diffHours < 24) return `${diffHours}小时前`
  const diffDays = Math.floor(diffHours / 24)
  return `${diffDays}天前`
})
</script>

<template>
  <article class="glass-card report-card flex flex-col overflow-hidden relative group" :style="cardStyle">
    <!-- Accent Line -->
    <div class="absolute top-0 left-0 w-full h-1 bg-[var(--card-accent)] opacity-50 group-hover:opacity-100 transition-opacity"></div>

    <!-- Image Area -->
      <div v-if="item.image_url" class="relative max-h-48 overflow-hidden bg-black/40 border-b border-white/5">
        <img :src="item.image_url" :alt="item.image_caption || item.title" loading="lazy" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105" />
        <div v-if="item.has_verified_image" class="absolute top-2 right-2 bg-black/60 backdrop-blur-md rounded-full px-2 py-1 flex items-center gap-1 text-[10px] text-[var(--status-ok)] border border-[var(--status-ok)]/30">
          <CheckCircle2 class="w-3 h-3" /> 高质量配图
        </div>
      </div>
    <div v-else class="relative h-32 overflow-hidden bg-black/20 border-b border-white/5">
      <img :src="categoryFallback(item.category)" :alt="item.category || '资讯'" class="w-full h-full object-cover opacity-60" />
    </div>

    <!-- Content Area -->
    <div class="p-5 flex-1 flex flex-col gap-3">
      <div class="flex justify-between items-start gap-3">
        <h3 class="font-bold text-white text-lg leading-snug line-clamp-2 group-hover:text-[var(--card-accent)] transition-colors">
          {{ item.title }}
        </h3>
        <span class="w-6 h-6 shrink-0 rounded-full flex items-center justify-center bg-[var(--card-accent)]/10 text-[var(--card-accent)] font-bold text-xs ring-1 ring-[var(--card-accent)]/30">
          {{ item.rank }}
        </span>
      </div>

      <div class="flex flex-wrap items-center gap-2 text-[10px]">
        <span class="px-2 py-1 rounded-full bg-white/5 text-[var(--text-muted)] border border-white/10">{{ sourceTrust }}</span>
        <span class="px-2 py-1 rounded-full bg-[var(--card-accent)]/10 text-[var(--card-accent)] border border-[var(--card-accent)]/20">{{ evidenceText }}</span>
        <span class="px-2 py-1 rounded-full bg-white/5 text-white/80 border border-white/10">{{ sourceKindText }}</span>
        <span class="px-2 py-1 rounded-full source-reliability-chip">{{ sourceReliabilityText }}</span>
        <span v-if="item.decision_trace?.supports_numeric_claims" class="px-2 py-1 rounded-full bg-emerald-500/10 text-emerald-300 border border-emerald-400/20">数字可引</span>
      </div>

      <p class="text-[var(--text-secondary)] text-sm line-clamp-3 leading-relaxed">
        {{ brief }}
      </p>

      <div class="mt-auto pt-4 flex flex-col gap-3">
        <div class="p-3 rounded-lg bg-[var(--card-accent)]/5 border border-[var(--card-accent)]/10">
          <p class="text-xs text-[var(--card-accent)] leading-relaxed line-clamp-2">
            <strong class="opacity-70 mr-1">洞察:</strong> {{ item.research_signal }}
          </p>
        </div>

        <div class="flex items-center justify-between text-xs mt-2">
          <div class="flex items-center gap-2 text-[var(--text-muted)]">
            <span class="px-2 py-1 rounded bg-white/5">{{ friendlySourceName }}</span>
            <span>{{ publishedLabel }}</span>
          </div>

          <div class="flex items-center gap-3">
            <a :href="item.source_url" target="_blank" rel="noreferrer" class="flex items-center gap-1 text-[var(--text-secondary)] hover:text-[var(--card-accent)] transition-colors">
              <ExternalLink class="w-3 h-3" /> 原文
            </a>
          </div>
        </div>

        <!-- AI Decision Trace Toggle -->
        <button
          v-if="hasTrace"
          @click="showTrace = !showTrace"
          class="flex items-center gap-1 text-[10px] text-[var(--text-muted)] hover:text-[var(--card-accent)] transition-colors mt-1"
        >
          <component :is="showTrace ? ChevronUp : ChevronDown" class="w-3 h-3" />
          {{ showTrace ? '收起 AI 分析过程' : '查看 AI 分析过程' }}
        </button>

        <!-- Decision Trace Panel -->
        <div v-if="showTrace && hasTrace" class="trace-panel mt-2 p-3 rounded-lg bg-white/[0.03] border border-white/[0.06] text-xs leading-relaxed">
          <div v-if="item.decision_trace!.search_query" class="mb-2">
            <span class="opacity-50">🔍 搜索发现：</span>
            <span class="text-[var(--text-secondary)]">{{ item.decision_trace!.search_query }}</span>
          </div>
          <div v-if="item.decision_trace!.key_finding" class="mb-2">
            <span class="opacity-50">💡 核心发现：</span>
            <span class="text-[var(--card-accent)]">{{ item.decision_trace!.key_finding }}</span>
          </div>
          <div v-if="item.decision_trace!.evaluation_reason">
            <span class="opacity-50">📊 评估理由：</span>
            <span class="text-[var(--text-secondary)]">{{ item.decision_trace!.evaluation_reason }}</span>
          </div>
          <div v-if="item.decision_trace!.source_reliability_label" class="mt-2">
            <span class="opacity-50">🛡 来源等级：</span>
            <span class="text-[var(--text-secondary)]">{{ item.decision_trace!.source_tier }} / {{ sourceReliabilityText }}</span>
          </div>
          <div v-if="item.decision_trace!.source_kind || item.decision_trace!.page_kind" class="mt-2">
            <span class="opacity-50">🧭 证据类型：</span>
            <span class="text-[var(--text-secondary)]">{{ item.decision_trace!.source_kind || 'unknown' }} · {{ item.decision_trace!.page_kind || 'article' }}</span>
          </div>
          <div v-if="item.decision_trace!.evidence_strength" class="mt-2">
            <span class="opacity-50">📌 证据强度：</span>
            <span class="text-[var(--text-secondary)]">
              {{ item.decision_trace!.evidence_strength }}
              <span v-if="item.decision_trace!.supports_numeric_claims"> · 可支撑数字事实</span>
              <span v-else> · 不支撑数字外推</span>
            </span>
          </div>
          <div v-if="item.decision_trace!.selection_reason" class="mt-2">
            <span class="opacity-50">✅ 入选原因：</span>
            <span class="text-[var(--text-secondary)]">{{ item.decision_trace!.selection_reason }}</span>
          </div>
        </div>
      </div>
    </div>
  </article>
</template>

<style scoped>
.report-card {
  transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
}

.report-card:hover {
  box-shadow: 0 12px 32px rgba(0,0,0,0.5), var(--card-glow);
  border-color: rgba(255, 255, 255, 0.15);
  transform: translateY(-4px);
}

.trace-panel {
  animation: trace-fade-in 0.2s ease-out;
}

.source-reliability-chip {
  background: rgba(56, 189, 248, 0.12);
  color: #bae6fd;
  border: 1px solid rgba(56, 189, 248, 0.24);
}

@keyframes trace-fade-in {
  from { opacity: 0; max-height: 0; }
  to { opacity: 1; max-height: 200px; }
}

.line-clamp-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;  
  overflow: hidden;
}

.line-clamp-3 {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;  
  overflow: hidden;
}

/* Base utility classes for layout */
.flex { display: flex; }
.flex-col { flex-direction: column; }
.h-full { height: 100%; }
.w-full { width: 100%; }
.overflow-hidden { overflow: hidden; }
.relative { position: relative; }
.absolute { position: absolute; }
.top-0 { top: 0; }
.left-0 { left: 0; }
.top-2 { top: 0.5rem; }
.right-2 { right: 0.5rem; }
.h-1 { height: 0.25rem; }
.h-16 { height: 4rem; }
.max-h-48 { max-height: 12rem; }
.bg-black\/40 { background-color: rgba(0, 0, 0, 0.4); }
.bg-black\/60 { background-color: rgba(0, 0, 0, 0.6); }
.bg-black\/20 { background-color: rgba(0, 0, 0, 0.2); }
.bg-white\/5 { background-color: rgba(255, 255, 255, 0.05); }
.border-b { border-bottom-width: 1px; }
.border-dashed { border-style: dashed; }
.border-white\/5 { border-color: rgba(255, 255, 255, 0.05); }
.object-cover { object-fit: cover; }
.backdrop-blur-md { backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); }
.rounded-full { border-radius: 9999px; }
.rounded-lg { border-radius: 0.5rem; }
.rounded { border-radius: 0.25rem; }
.px-2 { padding-left: 0.5rem; padding-right: 0.5rem; }
.py-1 { padding-top: 0.25rem; padding-bottom: 0.25rem; }
.p-5 { padding: 1.25rem; }
.p-3 { padding: 0.75rem; }
.pt-4 { padding-top: 1rem; }
.mt-auto { margin-top: auto; }
.mt-2 { margin-top: 0.5rem; }
.items-center { align-items: center; }
.items-start { align-items: flex-start; }
.justify-center { justify-content: center; }
.justify-between { justify-content: space-between; }
.gap-1 { gap: 0.25rem; }
.gap-2 { gap: 0.5rem; }
.gap-3 { gap: 0.75rem; }
.text-xs { font-size: 0.75rem; line-height: 1rem; }
.text-sm { font-size: 0.875rem; line-height: 1.25rem; }
.text-lg { font-size: 1.125rem; line-height: 1.75rem; }
.font-bold { font-weight: 700; }
.leading-snug { line-height: 1.375; }
.leading-relaxed { line-height: 1.625; }
.text-white { color: #ffffff; }
.opacity-50 { opacity: 0.5; }
.opacity-70 { opacity: 0.7; }
.shrink-0 { flex-shrink: 0; }
.ring-1 { box-shadow: 0 0 0 1px var(--tw-ring-color); }
.w-3 { width: 0.75rem; }
.h-3 { height: 0.75rem; }
.w-5 { width: 1.25rem; }
.h-5 { height: 1.25rem; }
.w-6 { width: 1.5rem; }
.h-6 { height: 1.5rem; }
.transition-opacity { transition-property: opacity; transition-duration: 300ms; }
.transition-transform { transition-property: transform; transition-duration: 700ms; }
.transition-colors { transition-property: color; transition-duration: 300ms; }
.group:hover .group-hover\:opacity-100 { opacity: 1; }
.group:hover .group-hover\:scale-105 { transform: scale(1.05); }
.group:hover .group-hover\:text-\[var\(--card-accent\)\] { color: var(--card-accent); }
.hover\:text-\[var\(--card-accent\)\]:hover { color: var(--card-accent); }
</style>

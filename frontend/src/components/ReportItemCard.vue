<script setup lang="ts">
import { computed, ref } from 'vue'
import type { ReportItem } from '../types'
import { ExternalLink, CheckCircle2, ChevronDown, ChevronUp } from 'lucide-vue-next'
import { cardBrief, presentSourceName, selectionBasis } from '../lib/reportPresentation'

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

const categoryLabel = computed(() => {
  const cat = props.item.decision_trace?.patent_category || props.item.category
  return cat || ''
})

const props = defineProps<{ item: ReportItem }>()

const showBasis = ref(false)

const hasBasis = computed(() => {
  const t = props.item.decision_trace
  return t && (
    t.evaluation_reason || t.key_finding || t.selection_reason ||
    t.source_tier || t.source_reliability_label || t.keywords?.length ||
    t.patent_number || t.inventors
  )
})

const sectionColor = computed(() => {
  switch (props.item.section) {
    case 'academic': return 'var(--accent-academic)'
    case 'industry': return 'var(--accent-industry)'
    case 'policy': return 'var(--accent-policy)'
    case 'patent': return '#f59e0b'
    case 'wechat': return '#22c55e'
    case 'lab_news': return '#8b5cf6'
    default: return 'var(--accent-academic)'
  }
})

const cardStyle = computed(() => {
  return {
    '--card-accent': sectionColor.value,
    '--card-glow': props.item.section === 'industry' ? 'var(--glow-green)' :
                   props.item.section === 'policy' ? 'var(--glow-purple)' :
                   props.item.section === 'patent' ? '0 0 20px rgba(245,158,11,0.2)' :
                   props.item.section === 'wechat' ? '0 0 20px rgba(34,197,94,0.2)' :
                   'var(--glow-blue)'
  }
})

const friendlySourceName = computed(() => {
  return presentSourceName(props.item.source_name, props.item.source_url)
})

const sectionLabel = computed(() => {
  const map: Record<string, string> = {
    academic: '学术前沿', industry: '产业动态', policy: '政策监管',
    patent: '专利精选', wechat: '英蓝云展', lab_news: '实验室资讯',
  }
  return map[props.item.section] || props.item.section
})

const languageLabel = computed(() => props.item.language === 'zh' ? '中文' : '英文')

const keywords = computed(() => {
  const kws = props.item.decision_trace?.keywords
  if (Array.isArray(kws) && kws.length > 0) return kws.slice(0, 4)
  const fallback = extractKeywords(props.item.title, props.item.summary)
  return fallback.slice(0, 4)
})

function extractKeywords(title: string, summary: string): string[] {
  const text = (title + ' ' + summary).toLowerCase()
  const candidates: Record<string, string[]> = {
    '聚乙烯': ['PE', '聚乙烯', 'polyethylene'],
    '聚丙烯': ['PP', '聚丙烯', 'polypropylene'],
    '聚氯乙烯': ['PVC', '聚氯乙烯'],
    '聚苯乙烯': ['PS', '聚苯乙烯'],
    '聚酯': ['PET', '聚酯', 'polyester'],
    '聚碳酸酯': ['PC', '聚碳酸酯', 'polycarbonate'],
    '尼龙': ['PA', '尼龙', 'nylon'],
    '聚氨酯': ['PU', '聚氨酯', 'polyurethane'],
    '环氧树脂': ['epoxy', '环氧树脂'],
    '碳纤维': ['碳纤维', 'carbon fiber'],
    '玻璃纤维': ['玻璃纤维', 'glass fiber'],
    '复合材料': ['复合材料', 'composite'],
    '注塑': ['注塑', 'injection molding'],
    '挤出': ['挤出', 'extrusion'],
    '吹塑': ['吹塑', 'blow molding'],
    '3D打印': ['3D打印', '增材制造', 'additive manufacturing'],
    '回收': ['回收', 'recycling', '再生'],
    '降解': ['降解', 'biodegradable', '可降解'],
    '生物基': ['生物基', 'bio-based'],
    '涂料': ['涂料', 'coating'],
    '隔膜': ['隔膜', 'separator'],
    '锂电池': ['锂电池', 'lithium battery'],
    '光伏': ['光伏', 'solar'],
    '风电': ['风电', 'wind'],
    '氢能': ['氢能', 'hydrogen'],
    '储能': ['储能', 'energy storage'],
    'AI': ['AI', '人工智能', 'machine learning'],
    '大模型': ['大模型', 'LLM'],
    '机器人': ['机器人', 'robot'],
  }
  const found: string[] = []
  for (const [label, aliases] of Object.entries(candidates)) {
    if (aliases.some(a => text.includes(a.toLowerCase()))) {
      found.push(label)
      if (found.length >= 4) break
    }
  }
  return found
}

const brief = computed(() => cardBrief(props.item))
const basis = computed(() => selectionBasis(props.item))

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
    <div class="absolute top-0 left-0 w-full h-1 bg-[var(--card-accent)] opacity-40 group-hover:opacity-80 transition-opacity"></div>

    <div v-if="item.image_url" class="relative max-h-48 overflow-hidden bg-gray-100 border-b border-[var(--line)]">
      <img :src="item.image_url" :alt="item.image_caption || item.title" loading="lazy" class="w-full h-full object-cover transition-transform duration-700 group-hover:scale-105" />
      <div v-if="item.has_verified_image" class="absolute top-2 right-2 bg-white/80 backdrop-blur-sm rounded-full px-2 py-1 flex items-center gap-1 text-[10px] text-[var(--status-ok)] border border-[var(--status-ok)]/30">
        <CheckCircle2 class="w-3 h-3" />
      </div>
    </div>
    <div v-else class="relative h-32 overflow-hidden bg-gray-50 border-b border-[var(--line)]">
      <img :src="categoryFallback(item.category)" :alt="item.category || '资讯'" class="w-full h-full object-cover opacity-60" />
    </div>

    <div class="p-5 flex-1 flex flex-col gap-3">
      <div class="flex justify-between items-start gap-3">
        <h3 class="font-bold text-[var(--text-primary)] text-lg leading-snug line-clamp-2 group-hover:text-[var(--card-accent)] transition-colors">
          {{ item.title }}
        </h3>
        <span class="w-6 h-6 shrink-0 rounded-full flex items-center justify-center bg-[var(--card-accent)]/10 text-[var(--card-accent)] font-bold text-xs ring-1 ring-[var(--card-accent)]/30">
          {{ item.rank }}
        </span>
      </div>

      <div class="flex flex-wrap items-center gap-2 text-[10px]">
        <span class="px-2 py-1 rounded-full bg-[var(--card-accent)]/8 text-[var(--card-accent)] border border-[var(--card-accent)]/15">{{ sectionLabel }}</span>
        <span v-if="categoryLabel" class="px-2 py-1 rounded-full bg-gray-100 text-gray-700 border border-gray-200">{{ categoryLabel }}</span>
        <span class="px-2 py-1 rounded-full bg-gray-100 text-gray-700 border border-gray-200">{{ languageLabel }}</span>
        <span v-for="kw in keywords" :key="kw" class="px-2 py-1 rounded-full bg-gray-50 text-[var(--text-muted)] border border-gray-100">{{ kw }}</span>
      </div>

      <p class="text-[var(--text-secondary)] text-sm line-clamp-3 leading-relaxed">
        {{ brief }}
      </p>

      <div class="mt-auto pt-4 flex flex-col gap-3">
        <div class="p-3 rounded-lg bg-[var(--card-accent)]/5 border border-[var(--card-accent)]/10">
          <p class="text-xs text-[var(--card-accent)] leading-relaxed line-clamp-2">
            <strong class="opacity-70 mr-1">关注点：</strong> {{ item.research_signal }}
          </p>
        </div>

        <div class="flex items-center justify-between text-xs mt-2">
          <div class="flex items-center gap-2 text-[var(--text-muted)]">
            <span class="px-2 py-1 rounded bg-gray-100">{{ friendlySourceName }}</span>
            <span>{{ publishedLabel }}</span>
          </div>

          <div class="flex items-center gap-3">
            <a :href="item.source_url" target="_blank" rel="noreferrer" class="flex items-center gap-1 text-[var(--text-secondary)] hover:text-[var(--card-accent)] transition-colors">
              <ExternalLink class="w-3 h-3" /> 原文
            </a>
          </div>
        </div>

        <button
          v-if="hasBasis"
          @click="showBasis = !showBasis"
          class="flex items-center gap-1 text-[10px] text-[var(--text-muted)] hover:text-[var(--card-accent)] transition-colors mt-1"
        >
          <component :is="showBasis ? ChevronUp : ChevronDown" class="w-3 h-3" />
          {{ showBasis ? '收起入选依据' : '查看入选依据' }}
        </button>

        <div v-if="showBasis && hasBasis" class="trace-panel mt-2 p-3 rounded-lg bg-gray-50 border border-gray-100 text-xs leading-relaxed">
          <div class="mb-2">
            <span class="opacity-50">为什么重要：</span>
            <span class="text-[var(--text-secondary)]">{{ basis.why }}</span>
          </div>
          <div class="mb-2">
            <span class="opacity-50">来源依据：</span>
            <span class="text-[var(--text-secondary)]">{{ basis.source }}</span>
          </div>
          <div>
            <span class="opacity-50">相关方向：</span>
            <span class="text-[var(--card-accent)]">{{ basis.relevance }}</span>
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
  box-shadow: 0 8px 24px rgba(0,0,0,0.08);
  border-color: #cbd5e1;
  transform: translateY(-4px);
}

.trace-panel {
  animation: trace-fade-in 0.2s ease-out;
}

@keyframes trace-fade-in {
  from { opacity: 0; max-height: 0; }
  to { opacity: 1; max-height: 200px; }
}
</style>

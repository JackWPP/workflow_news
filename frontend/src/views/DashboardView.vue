<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import MarkdownPanel from '../components/MarkdownPanel.vue'
import ReportItemCard from '../components/ReportItemCard.vue'
import StatusPill from '../components/StatusPill.vue'
import { api } from '../lib/api'
import { useSessionStore } from '../stores/session'
import type { Report } from '../types'

const session = useSessionStore()
const report = ref<Report | null>(null)
const loading = ref(false)
const error = ref('')
const viewMode = ref<'cards' | 'markdown'>('cards')

const isFavorited = computed(() => {
  if (!report.value || !session.user?.favorite_report_ids) {
    return false
  }
  return session.user.favorite_report_ids.includes(report.value.id)
})

async function loadReport() {
  loading.value = true
  error.value = ''
  try {
    report.value = await api.todayReport()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function regenerate() {
  loading.value = true
  error.value = ''
  try {
    report.value = await api.runReport()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '生成失败'
  } finally {
    loading.value = false
  }
}

async function toggleFavorite() {
  if (!report.value || !session.user) {
    return
  }
  if (isFavorited.value) {
    await api.unfavoriteReport(report.value.id)
    session.user.favorite_report_ids = (session.user.favorite_report_ids ?? []).filter((id) => id !== report.value?.id)
  } else {
    await api.favoriteReport(report.value.id)
    session.user.favorite_report_ids = [...(session.user.favorite_report_ids ?? []), report.value.id]
  }
}

onMounted(() => {
  void loadReport()
})
</script>

<template>
  <section class="hero-panel">
    <div>
      <p class="eyebrow">Today</p>
      <h2>今日日报</h2>
      <p class="lead">结构化卡片和 Markdown 双视图并行，先保证可读，再保证可追溯。</p>
    </div>
    <div class="hero-actions">
      <button class="primary-button" :disabled="loading" @click="regenerate">手动生成</button>
      <button
        class="ghost-button"
        :disabled="!session.user || !report"
        @click="toggleFavorite"
      >
        {{ isFavorited ? '取消收藏' : '收藏日报' }}
      </button>
    </div>
  </section>

  <p v-if="error" class="error-box">{{ error }}</p>
  <p v-else-if="loading" class="loading-box">正在读取最新日报…</p>

  <template v-if="report">
    <section class="panel report-summary">
      <div>
        <h3>{{ report.title }}</h3>
        <p>{{ report.summary || '暂无摘要' }}</p>
      </div>
      <div class="summary-meta">
        <StatusPill :status="report.status" />
        <span>{{ report.report_date }}</span>
      </div>
    </section>

    <section class="toggle-bar">
      <button class="toggle-button" :class="{ active: viewMode === 'cards' }" @click="viewMode = 'cards'">卡片视图</button>
      <button class="toggle-button" :class="{ active: viewMode === 'markdown' }" @click="viewMode = 'markdown'">Markdown 视图</button>
    </section>

    <section v-if="viewMode === 'cards'" class="cards-grid">
      <ReportItemCard v-for="item in report.items" :key="item.id" :item="item" />
    </section>
    <MarkdownPanel v-else :content="report.markdown_content" />
  </template>
</template>

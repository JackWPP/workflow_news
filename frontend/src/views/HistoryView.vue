<script setup lang="ts">
import { onMounted, ref } from 'vue'

import ReportItemCard from '../components/ReportItemCard.vue'
import StatusPill from '../components/StatusPill.vue'
import { api } from '../lib/api'
import type { Report } from '../types'

const reports = ref<Report[]>([])
const selected = ref<Report | null>(null)
const loading = ref(false)
const error = ref('')

async function loadReports() {
  loading.value = true
  error.value = ''
  try {
    const payload = await api.listReports()
    reports.value = payload.reports
    selected.value = payload.reports[0] ?? null
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  void loadReports()
})
</script>

<template>
  <section class="split-layout">
    <aside class="panel history-list">
      <div class="section-head">
        <div>
          <p class="eyebrow">Archive</p>
          <h2>历史日报</h2>
        </div>
      </div>
      <p v-if="loading">正在加载历史日报…</p>
      <p v-else-if="error" class="error-box">{{ error }}</p>
      <button
        v-for="report in reports"
        :key="report.id"
        class="history-row"
        :class="{ active: selected?.id === report.id }"
        @click="selected = report"
      >
        <div>
          <strong>{{ report.report_date }}</strong>
          <p>{{ report.summary || report.title }}</p>
        </div>
        <StatusPill :status="report.status" />
      </button>
    </aside>

    <section class="panel">
      <div v-if="selected" class="section-head">
        <div>
          <p class="eyebrow">Detail</p>
          <h2>{{ selected.title }}</h2>
        </div>
      </div>
      <div v-if="selected" class="cards-grid">
        <ReportItemCard v-for="item in selected.items" :key="item.id" :item="item" />
      </div>
    </section>
  </section>
</template>

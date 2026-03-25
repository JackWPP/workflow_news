<script setup lang="ts">
import type { ReportItem } from '../types'

defineProps<{ item: ReportItem }>()
</script>

<template>
  <article class="report-card">
    <div class="report-card-head">
      <span class="rank">{{ item.rank }}</span>
      <div>
        <h3>{{ item.title }}</h3>
        <p class="meta">
          <span>{{ item.source_name }}</span>
          <span>{{ item.published_at ? new Date(item.published_at).toLocaleString() : '时间未知' }}</span>
        </p>
      </div>
    </div>
    <p class="summary">{{ item.summary }}</p>
    <p class="signal">{{ item.research_signal }}</p>
    <div class="card-actions">
      <a :href="item.source_url" target="_blank" rel="noreferrer">查看原文</a>
      <span class="score">评分 {{ item.combined_score.toFixed(2) }}</span>
    </div>
  </article>
</template>

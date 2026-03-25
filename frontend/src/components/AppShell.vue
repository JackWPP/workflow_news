<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'

import { useSessionStore } from '../stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()

const navItems = computed(() => [
  { to: '/', label: '今日日报' },
  { to: '/history', label: '历史日报' },
  { to: '/chat', label: '研究助手' },
  ...(session.isAdmin ? [{ to: '/admin', label: '后台' }] : []),
])

async function handleLogout() {
  await session.logout()
  await router.push('/login')
}
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div>
        <p class="eyebrow">Polymer Research Desk</p>
        <h1>高分子加工情报台</h1>
      </div>
      <div class="topbar-actions">
        <div class="user-card">
          <span v-if="session.user">{{ session.user.email }}</span>
          <span v-else>未登录</span>
        </div>
        <button v-if="session.user" class="ghost-button" @click="handleLogout">退出</button>
        <RouterLink v-else class="ghost-button" to="/login">登录</RouterLink>
      </div>
    </header>

    <nav class="nav-tabs">
      <RouterLink
        v-for="item in navItems"
        :key="item.to"
        class="nav-tab"
        :class="{ active: route.path === item.to }"
        :to="item.to"
      >
        {{ item.label }}
      </RouterLink>
    </nav>

    <main class="page-content">
      <slot />
    </main>
  </div>
</template>

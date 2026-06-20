<script setup lang="ts">
import { computed, ref, watch, onUnmounted } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { 
  LayoutDashboard, 
  History, 
  MessageSquare, 
  Settings, 
  LogOut,
  User,
  Menu,
  X
} from 'lucide-vue-next'

import { useSessionStore } from '../stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()

const navItems = computed(() => [
  { to: '/', label: '今日日报', icon: LayoutDashboard },
  { to: '/history', label: '历史日报', icon: History },
  { to: '/chat', label: '研究助手', icon: MessageSquare },
  ...(session.isAdmin ? [{ to: '/admin', label: '管理后台', icon: Settings }] : []),
])

async function handleLogout() {
  mobileNavOpen.value = false
  await session.logout()
  await router.push('/login')
}

const mobileNavOpen = ref(false)

watch(() => route.path, () => {
  mobileNavOpen.value = false
})

watch(mobileNavOpen, (open) => {
  document.body.style.overflow = open ? 'hidden' : ''
})

onUnmounted(() => {
  document.body.style.overflow = ''
})
</script>

<template>
  <div class="h-[var(--app-vh)] w-full flex overflow-hidden bg-[var(--bg-primary)]">
    <div class="mobile-topbar fixed top-0 left-0 right-0 z-30 bg-[rgba(255,255,255,0.92)] backdrop-blur-md border-b border-[var(--line)] safe-area-top md:hidden">
      <div class="flex items-center gap-3 px-4 h-[var(--app-bar-h)] safe-area-x">
        <button @click="mobileNavOpen = true" class="p-2 -ml-2 rounded-lg hover:bg-[rgba(0,0,0,0.05)] transition-colors" aria-label="打开导航菜单">
          <Menu class="w-5 h-5 text-[var(--text-primary)]" />
        </button>
        <img src="/logo.png" alt="" class="w-7 h-7 rounded-lg object-contain" />
        <span class="text-base font-bold text-[var(--text-primary)] tracking-tight">高分子视野</span>
      </div>
    </div>

    <div v-if="mobileNavOpen" class="fixed inset-0 z-40 bg-black/40 md:hidden" @click="mobileNavOpen = false" aria-hidden="true"></div>

    <div
      class="mobile-drawer fixed top-0 left-0 bottom-0 z-50 w-[var(--sidebar-w)] max-w-[85vw] bg-[var(--bg-surface)] border-r border-[var(--line)] flex flex-col transform transition-transform duration-300 ease-in-out md:hidden safe-area-top"
      :class="mobileNavOpen ? 'translate-x-0' : '-translate-x-full'"
      :aria-hidden="!mobileNavOpen"
    >
      <div class="flex items-center justify-between px-4 py-3 border-b border-[var(--line)]">
        <div class="flex items-center gap-2">
          <img src="/logo.png" alt="" class="w-8 h-8 rounded-lg object-contain" />
          <span class="font-bold text-[var(--text-primary)]">导航</span>
        </div>
        <button @click="mobileNavOpen = false" class="p-2 rounded-lg hover:bg-[rgba(0,0,0,0.05)] transition-colors" aria-label="关闭导航菜单">
          <X class="w-5 h-5 text-[var(--text-secondary)]" />
        </button>
      </div>

      <nav class="flex-1 px-4 py-4 space-y-2 overflow-y-auto">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-300"
          :class="[route.path === item.to ? 'active-link' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[rgba(0,0,0,0.03)]']"
        >
          <component :is="item.icon" class="w-5 h-5" />
          <span class="font-medium">{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="p-4 border-t border-[var(--line)] safe-area-bottom safe-area-x">
        <div v-if="session.user" class="flex flex-col gap-3">
          <div class="flex items-center gap-3 px-2">
            <div class="w-8 h-8 rounded-full bg-[rgba(0,0,0,0.05)] flex items-center justify-center">
              <User class="w-4 h-4 text-[var(--text-secondary)]" />
            </div>
            <div class="flex-1 overflow-hidden">
              <p class="text-sm text-[var(--text-primary)] truncate">{{ session.user.email }}</p>
              <p class="text-xs text-[var(--status-ok)] flex items-center gap-1">
                <span class="w-1.5 h-1.5 rounded-full bg-[var(--status-ok)] animate-pulse"></span> 在线
              </p>
            </div>
          </div>
          <button @click="handleLogout" class="flex items-center justify-center gap-2 w-full py-2.5 text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)] bg-[rgba(0,0,0,0.02)] hover:bg-[rgba(0,0,0,0.05)] rounded-lg transition-colors">
            <LogOut class="w-4 h-4" /> 退出系统
          </button>
        </div>
        <div v-else class="flex justify-center">
          <RouterLink to="/login" class="btn-primary w-full text-center py-2.5">
            系统登录
          </RouterLink>
        </div>
      </div>
    </div>

    <aside class="sidebar w-64 flex-shrink-0 flex flex-col z-10 bg-[var(--bg-surface)] border-r border-[var(--line)] border-y-0 border-l-0 rounded-none h-full relative hidden md:flex">
      <div class="p-6 flex flex-col items-center gap-2 text-center">
        <img src="/logo.png" alt="logo" class="logo-img" />
        <div>
          <h1 class="text-lg font-bold tracking-tight text-[var(--text-primary)] leading-tight">高分子视野</h1>
          <p class="text-[10px] text-[var(--accent-academic)] tracking-widest">智能情报平台</p>
        </div>
      </div>

      <nav class="flex-1 px-4 py-4 space-y-2 overflow-y-auto">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="nav-link flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-300"
          :class="[route.path === item.to ? 'active-link' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[rgba(0,0,0,0.03)]']"
        >
          <component :is="item.icon" class="w-5 h-5" />
          <span class="font-medium">{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="p-4 border-t border-[var(--line)]">
        <div v-if="session.user" class="flex flex-col gap-3">
          <div class="flex items-center gap-3 px-2">
            <div class="w-8 h-8 rounded-full bg-[rgba(0,0,0,0.05)] flex items-center justify-center">
              <User class="w-4 h-4 text-[var(--text-secondary)]" />
            </div>
            <div class="flex-1 overflow-hidden">
              <p class="text-sm text-[var(--text-primary)] truncate">{{ session.user.email }}</p>
              <p class="text-xs text-[var(--status-ok)] flex items-center gap-1">
                <span class="w-1.5 h-1.5 rounded-full bg-[var(--status-ok)] animate-pulse"></span> 在线
              </p>
            </div>
          </div>
          <button @click="handleLogout" class="flex items-center justify-center gap-2 w-full py-2.5 text-sm text-[var(--text-muted)] hover:text-[var(--text-primary)] bg-[rgba(0,0,0,0.02)] hover:bg-[rgba(0,0,0,0.05)] rounded-lg transition-colors">
            <LogOut class="w-4 h-4" /> 退出系统
          </button>
        </div>
        <div v-else class="flex justify-center">
          <RouterLink to="/login" class="btn-primary w-full text-center py-2.5">
            系统登录
          </RouterLink>
        </div>
      </div>
    </aside>

    <main class="flex-1 flex flex-col min-w-0 z-10 relative overflow-y-auto scroll-smooth pt-[var(--app-bar-h)] md:pt-0">
      <div class="w-full max-w-7xl mx-auto p-4 md:p-8 min-h-full">
        <slot />
      </div>
    </main>
  </div>
</template>

<style scoped>
.sidebar {
  box-shadow: none;
  background: var(--bg-surface);
}

.active-link {
  color: var(--accent-academic) !important;
  background: rgba(43, 87, 151, 0.08) !important;
  font-weight: 600;
  border-left: 3px solid var(--accent-academic);
}

.logo-img {
  width: 8rem;
  height: 8rem;
  object-fit: contain;
  border-radius: 1.25rem;
}
</style>

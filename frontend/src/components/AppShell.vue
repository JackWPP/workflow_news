<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { 
  LayoutDashboard, 
  History, 
  MessageSquare, 
  Settings, 
  LogOut,
  User,
  Activity
} from 'lucide-vue-next'

import { useSessionStore } from '../stores/session'
import { ParticleSystem } from '../lib/particles'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const bgCanvas = ref<HTMLCanvasElement | null>(null)
let particles: ParticleSystem | null = null

const navItems = computed(() => [
  { to: '/', label: '今日日报', icon: LayoutDashboard },
  { to: '/history', label: '历史日报', icon: History },
  { to: '/chat', label: '研究助手', icon: MessageSquare },
  ...(session.isAdmin ? [{ to: '/admin', label: '管理后台', icon: Settings }] : []),
])

onMounted(() => {
  if (bgCanvas.value) {
    particles = new ParticleSystem(bgCanvas.value)
  }
})

onUnmounted(() => {
  if (particles) {
    particles.destroy()
  }
})

async function handleLogout() {
  await session.logout()
  await router.push('/login')
}
</script>

<template>
  <div class="h-screen w-full flex overflow-hidden bg-[var(--bg-primary)]">
    <!-- Particles Background -->
    <canvas ref="bgCanvas" class="fixed inset-0 pointer-events-none z-0 mix-blend-screen opacity-40"></canvas>
    
    <!-- Sidebar Navigation -->
    <aside class="sidebar w-64 flex-shrink-0 flex flex-col z-10 glass-panel border-y-0 border-l-0 rounded-none h-full relative">
      <div class="p-6 flex items-center gap-3">
        <Activity class="w-8 h-8 text-[var(--accent-primary)] animate-pulse-glow" />
        <div>
          <h1 class="text-lg font-bold tracking-tight text-white leading-tight">高分子视野</h1>
          <p class="text-[10px] text-[var(--accent-academic)] tracking-widest uppercase">Agent Console</p>
        </div>
      </div>

      <nav class="flex-1 px-4 py-4 space-y-2 overflow-y-auto">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="nav-link flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-300"
          :class="[route.path === item.to ? 'active-link' : 'text-[var(--text-secondary)] hover:text-white hover:bg-[rgba(255,255,255,0.05)]']"
        >
          <component :is="item.icon" class="w-5 h-5" />
          <span class="font-medium">{{ item.label }}</span>
        </RouterLink>
      </nav>

      <div class="p-4 border-t border-[var(--line)]">
        <div v-if="session.user" class="flex flex-col gap-3">
          <div class="flex items-center gap-3 px-2">
            <div class="w-8 h-8 rounded-full bg-[rgba(255,255,255,0.1)] flex items-center justify-center">
              <User class="w-4 h-4 text-[var(--text-secondary)]" />
            </div>
            <div class="flex-1 overflow-hidden">
              <p class="text-sm text-white truncate">{{ session.user.email }}</p>
              <p class="text-xs text-[var(--status-ok)] flex items-center gap-1">
                <span class="w-1.5 h-1.5 rounded-full bg-[var(--status-ok)] animate-pulse-glow"></span> Online
              </p>
            </div>
          </div>
          <button @click="handleLogout" class="flex items-center justify-center gap-2 w-full py-2.5 text-sm text-[var(--text-muted)] hover:text-white bg-[rgba(255,255,255,0.03)] hover:bg-[rgba(255,255,255,0.08)] rounded-lg transition-colors">
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

    <!-- Main Content Area -->
    <main class="flex-1 flex flex-col min-w-0 z-10 relative overflow-y-auto scroll-smooth">
      <div class="w-full max-w-7xl mx-auto p-4 md:p-8 min-h-full">
        <slot />
      </div>
    </main>
  </div>
</template>

<style scoped>
.sidebar {
  box-shadow: 4px 0 24px rgba(0, 0, 0, 0.4);
}

.active-link {
  color: white;
  background: rgba(100, 180, 255, 0.1) !important;
  border-left: 3px solid var(--accent-primary);
  box-shadow: inset 0 0 20px rgba(100, 180, 255, 0.05);
}

/* Add Tailwind-like utility classes used above since we dropped Tailwind but I used some Tailwind classes in the template out of habit */
.h-screen { height: 100vh; }
.w-full { width: 100%; }
.flex { display: flex; }
.flex-col { flex-direction: column; }
.items-center { align-items: center; }
.justify-center { justify-content: center; }
.justify-between { justify-content: space-between; }
.gap-1 { gap: 0.25rem; }
.gap-2 { gap: 0.5rem; }
.gap-3 { gap: 0.75rem; }
.gap-8 { gap: 2rem; }
.px-2 { padding-left: 0.5rem; padding-right: 0.5rem; }
.px-4 { padding-left: 1rem; padding-right: 1rem; }
.py-2\.5 { padding-top: 0.625rem; padding-bottom: 0.625rem; }
.py-3 { padding-top: 0.75rem; padding-bottom: 0.75rem; }
.p-4 { padding: 1rem; }
.p-6 { padding: 1.5rem; }
.p-8 { padding: 2rem; }
.text-xs { font-size: 0.75rem; line-height: 1rem; }
.text-sm { font-size: 0.875rem; line-height: 1.25rem; }
.text-lg { font-size: 1.125rem; line-height: 1.75rem; }
.text-center { text-align: center; }
.font-medium { font-weight: 500; }
.font-bold { font-weight: 700; }
.uppercase { text-transform: uppercase; }
.tracking-tight { letter-spacing: -0.025em; }
.tracking-widest { letter-spacing: 0.1em; }
.rounded-lg { border-radius: 0.5rem; }
.rounded-xl { border-radius: 0.75rem; }
.rounded-full { border-radius: 9999px; }
.rounded-none { border-radius: 0; }
.w-1\.5 { width: 0.375rem; }
.h-1\.5 { height: 0.375rem; }
.w-4 { width: 1rem; }
.h-4 { height: 1rem; }
.w-5 { width: 1.25rem; }
.h-5 { height: 1.25rem; }
.w-8 { width: 2rem; }
.h-8 { height: 2rem; }
.w-64 { width: 16rem; }
.flex-1 { flex: 1 1 0%; }
.flex-shrink-0 { flex-shrink: 0; }
.min-w-0 { min-width: 0; }
.min-h-full { min-height: 100%; }
.truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.overflow-hidden { overflow: hidden; }
.overflow-y-auto { overflow-y: auto; }
.scroll-smooth { scroll-behavior: smooth; }
.relative { position: relative; }
.fixed { position: fixed; }
.absolute { position: absolute; }
.inset-0 { inset: 0; }
.z-0 { z-index: 0; }
.z-10 { z-index: 10; }
.pointer-events-none { pointer-events: none; }
.mix-blend-screen { mix-blend-mode: screen; }
.opacity-40 { opacity: 0.4; }
.transition-colors { transition-property: color, background-color, border-color, text-decoration-color, fill, stroke; transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1); transition-duration: 150ms; }
.transition-all { transition-property: all; transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1); transition-duration: 150ms; }
.duration-300 { transition-duration: 300ms; }
.border-t { border-top-width: 1px; }
.border-y-0 { border-top-width: 0; border-bottom-width: 0; }
.border-l-0 { border-left-width: 0; }
.max-w-7xl { max-width: 80rem; }
.mx-auto { margin-left: auto; margin-right: auto; }
@media (min-width: 768px) {
  .md\:p-8 { padding: 2rem; }
}
</style>

import { createRouter, createWebHashHistory } from 'vue-router'

import { useSessionStore } from './stores/session'
import AdminView from './views/AdminView.vue'
import ChatView from './views/ChatView.vue'
import DashboardView from './views/DashboardView.vue'
import HistoryView from './views/HistoryView.vue'
import LoginView from './views/LoginView.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', component: DashboardView },
    { path: '/history', component: HistoryView },
    { path: '/chat', component: ChatView, meta: { requiresAuth: true } },
    { path: '/admin', component: AdminView, meta: { requiresAuth: true, requiresAdmin: true } },
    { path: '/login', component: LoginView },
  ],
})

router.beforeEach(async (to) => {
  const session = useSessionStore()
  await session.ensureLoaded()

  if (to.meta.requiresAuth && !session.isLoggedIn) {
    return '/login'
  }

  if (to.meta.requiresAdmin && !session.isAdmin) {
    return '/'
  }

  if (to.path === '/login' && session.isLoggedIn) {
    return '/'
  }

  return true
})

export default router

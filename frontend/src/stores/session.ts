import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api } from '../lib/api'
import type { User } from '../types'

export const useSessionStore = defineStore('session', () => {
  const user = ref<User | null>(null)
  const loaded = ref(false)

  const isLoggedIn = computed(() => !!user.value)
  const isAdmin = computed(() => !!user.value?.is_admin)

  async function ensureLoaded() {
    if (loaded.value) {
      return
    }
    try {
      user.value = await api.me()
    } catch {
      user.value = null
    } finally {
      loaded.value = true
    }
  }

  async function login(email: string, password: string) {
    user.value = await api.login(email, password)
    loaded.value = true
  }

  async function register(email: string, password: string) {
    user.value = await api.register(email, password)
    loaded.value = true
  }

  async function logout() {
    await api.logout()
    user.value = null
    loaded.value = true
  }

  return { user, loaded, isLoggedIn, isAdmin, ensureLoaded, login, register, logout }
})

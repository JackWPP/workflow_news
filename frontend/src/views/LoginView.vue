<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'

import { useSessionStore } from '../stores/session'

const router = useRouter()
const session = useSessionStore()
const email = ref('')
const password = ref('')
const mode = ref<'login' | 'register'>('login')
const error = ref('')
const loading = ref(false)

async function submit() {
  loading.value = true
  error.value = ''
  try {
    if (mode.value === 'login') {
      await session.login(email.value, password.value)
    } else {
      await session.register(email.value, password.value)
    }
    await router.push('/')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '提交失败'
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <section class="min-h-screen flex items-center justify-center p-8 bg-[var(--bg-primary)]">
    <form class="max-w-md w-full p-10 bg-white border border-gray-200 rounded-2xl shadow-lg" @submit.prevent="submit">
      <p class="text-xs font-semibold uppercase tracking-[0.1em] text-[var(--accent-primary)] mb-2">Account</p>
      <h2 class="text-2xl font-bold text-[var(--text-primary)] mb-3">{{ mode === 'login' ? '登录' : '注册' }}</h2>
      <p class="text-[var(--text-secondary)] text-sm mb-6 leading-relaxed">先打开会话能力，再进入日报收藏和后台配置。</p>
      <label class="block mb-4 text-sm font-medium text-[var(--text-primary)]">
        邮箱
        <input v-model="email" type="email" required class="block w-full mt-2 px-4 py-3 bg-white border border-gray-200 rounded-lg text-[var(--text-primary)] text-sm transition-[border-color,box-shadow] focus:outline-none focus:border-[var(--accent-primary)] focus:shadow-[0_0_0_3px_rgba(43,87,151,0.1)]" />
      </label>
      <label class="block mb-4 text-sm font-medium text-[var(--text-primary)]">
        密码
        <input v-model="password" type="password" required minlength="6" class="block w-full mt-2 px-4 py-3 bg-white border border-gray-200 rounded-lg text-[var(--text-primary)] text-sm transition-[border-color,box-shadow] focus:outline-none focus:border-[var(--accent-primary)] focus:shadow-[0_0_0_3px_rgba(43,87,151,0.1)]" />
      </label>
      <p v-if="error" class="px-4 py-3 bg-[rgba(248,113,113,0.1)] border border-[rgba(248,113,113,0.3)] rounded-lg text-[var(--status-error)] text-sm mb-4">{{ error }}</p>
      <button class="block w-full px-6 py-3 bg-[var(--accent-primary)] text-white font-semibold text-sm rounded-lg cursor-pointer transition-[opacity,transform] hover:opacity-90 hover:-translate-y-0.5 disabled:opacity-50 disabled:cursor-not-allowed mb-3" :disabled="loading">{{ mode === 'login' ? '登录' : '注册' }}</button>
      <button class="block w-full px-6 py-3 bg-transparent text-[var(--text-secondary)] font-medium text-sm rounded-lg border border-gray-200 cursor-pointer transition-[background,color] hover:bg-gray-50 hover:text-[var(--text-primary)]" type="button" @click="mode = mode === 'login' ? 'register' : 'login'">
        {{ mode === 'login' ? '切到注册' : '切到登录' }}
      </button>
    </form>
  </section>
</template>

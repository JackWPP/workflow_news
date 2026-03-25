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
  <section class="auth-wrap">
    <form class="auth-card panel" @submit.prevent="submit">
      <p class="eyebrow">Account</p>
      <h2>{{ mode === 'login' ? '登录' : '注册' }}</h2>
      <p>先打开会话能力，再进入日报收藏和后台配置。</p>
      <label>
        邮箱
        <input v-model="email" type="email" required />
      </label>
      <label>
        密码
        <input v-model="password" type="password" required minlength="6" />
      </label>
      <p v-if="error" class="error-box">{{ error }}</p>
      <button class="primary-button" :disabled="loading">{{ mode === 'login' ? '登录' : '注册' }}</button>
      <button class="ghost-button" type="button" @click="mode = mode === 'login' ? 'register' : 'login'">
        {{ mode === 'login' ? '切到注册' : '切到登录' }}
      </button>
    </form>
  </section>
</template>

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

<style scoped>
.auth-wrap {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 2rem;
  background: var(--bg-primary);
}

.auth-card {
  max-width: 400px;
  width: 100%;
  padding: 2.5rem;
  background: var(--bg-surface);
  border: 1px solid var(--border-glow);
  border-radius: 16px;
  box-shadow: var(--shadow);
  backdrop-filter: blur(20px);
}

.eyebrow {
  font-size: 0.75rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--accent-primary);
  margin-bottom: 0.5rem;
}

h2 {
  font-size: 1.75rem;
  font-weight: 700;
  color: #ffffff;
  margin-bottom: 0.75rem;
}

p {
  color: var(--text-secondary);
  font-size: 0.875rem;
  margin-bottom: 1.5rem;
  line-height: 1.5;
}

label {
  display: block;
  margin-bottom: 1rem;
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--text-primary);
}

input {
  display: block;
  width: 100%;
  margin-top: 0.5rem;
  padding: 0.75rem 1rem;
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: 8px;
  color: var(--text-primary);
  font-size: 0.875rem;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}

input:focus {
  outline: none;
  border-color: var(--accent-primary);
  box-shadow: 0 0 0 3px rgba(100, 180, 255, 0.15);
}

input::placeholder {
  color: var(--text-muted);
}

.error-box {
  padding: 0.75rem 1rem;
  background: rgba(248, 113, 113, 0.1);
  border: 1px solid rgba(248, 113, 113, 0.3);
  border-radius: 8px;
  color: var(--status-error);
  font-size: 0.875rem;
  margin-bottom: 1rem;
}

.primary-button {
  display: block;
  width: 100%;
  padding: 0.75rem 1.5rem;
  background: linear-gradient(135deg, var(--accent-primary), #4a9eff);
  color: #ffffff;
  font-weight: 600;
  font-size: 0.875rem;
  border-radius: 8px;
  border: none;
  cursor: pointer;
  transition: opacity 0.2s ease, transform 0.2s ease;
  margin-bottom: 0.75rem;
}

.primary-button:hover:not(:disabled) {
  opacity: 0.9;
  transform: translateY(-1px);
}

.primary-button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.ghost-button {
  display: block;
  width: 100%;
  padding: 0.75rem 1.5rem;
  background: transparent;
  color: var(--text-secondary);
  font-weight: 500;
  font-size: 0.875rem;
  border-radius: 8px;
  border: 1px solid var(--line);
  cursor: pointer;
  transition: background 0.2s ease, color 0.2s ease;
}

.ghost-button:hover {
  background: var(--bg-hover);
  color: var(--text-primary);
}
</style>

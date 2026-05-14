<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { Bot, Plus, MessageSquare, Send, Sparkles, Loader2 } from 'lucide-vue-next'

import { api } from '../lib/api'
import { useSessionStore } from '../stores/session'
import type { Conversation, ConversationDetail } from '../types'
import ChatBubble from '../components/ChatBubble.vue'

const session = useSessionStore()
const conversations = ref<Conversation[]>([])
const activeConversation = ref<ConversationDetail | null>(null)
const draft = ref('')
const loading = ref(false)
const error = ref('')
const listRef = ref<HTMLDivElement | null>(null)

async function loadConversations() {
  const payload = await api.listConversations()
  conversations.value = payload.conversations
  if (!activeConversation.value && conversations.value.length > 0) {
    await openConversation(conversations.value[0].id)
  }
}

async function openConversation(id: number) {
  activeConversation.value = await api.getConversation(id)
  scrollToBottom()
}

async function newConversation() {
  const conversation = await api.createConversation(`研究对话 ${new Date().toLocaleTimeString()}`)
  await loadConversations()
  await openConversation(conversation.id)
}

function scrollToBottom() {
  nextTick(() => {
    if (listRef.value) {
      listRef.value.scrollTo({ top: listRef.value.scrollHeight, behavior: 'smooth' })
    }
  })
}

async function sendMessage() {
  if (!draft.value.trim()) {
    return
  }
  const currentDraft = draft.value
  draft.value = ''
  
  // Optimistically add user message
  if (activeConversation.value) {
    activeConversation.value.messages.push({
      id: Date.now(),
      role: 'user',
      content: currentDraft,
      citations: [],
      retrieval_mode: '',
      created_at: new Date().toISOString()
    })
    scrollToBottom()
  }

  loading.value = true
  error.value = ''
  try {
    const payload = await api.sendChatStream(currentDraft, activeConversation.value?.id)
    if (!activeConversation.value) {
      activeConversation.value = await api.getConversation(payload.conversation_id as number)
    } else {
      // Remove the optimistic user message and push real ones
      activeConversation.value.messages.pop()
      activeConversation.value.messages.push(payload.user_message, payload.assistant_message)
    }
    await loadConversations()
    scrollToBottom()
  } catch (err) {
    error.value = err instanceof Error ? err.message : '发送失败'
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  if (session.user) {
    void loadConversations()
  }
})
</script>

<template>
  <div class="flex h-[calc(100vh-80px)] gap-6 max-w-7xl mx-auto w-full relative">
    <!-- Sidebar: Conversation History -->
    <aside class="w-72 hidden md:flex flex-col gap-4 border-r border-white/5 pr-4 relative z-10">
      <div class="flex items-center justify-between pb-4 border-b border-[var(--line)]">
        <div>
          <h2 class="text-xl font-bold text-white flex items-center gap-2">
            <Sparkles class="w-5 h-5 text-[var(--accent-policy)]" />
            研究助手
          </h2>
        </div>
        <button @click="newConversation" class="p-2 rounded-lg bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] hover:bg-[var(--accent-primary)]/20 transition-colors border border-[var(--accent-primary)]/20 shadow-[0_0_10px_rgba(100,180,255,0.1)]">
          <Plus class="w-5 h-5" />
        </button>
      </div>

      <div class="flex-1 overflow-y-auto space-y-2 pr-2 scrollbar-hide">
        <button
          v-for="conversation in conversations"
          :key="conversation.id"
          class="w-full text-left p-3 rounded-xl border transition-all duration-300 group flex items-start gap-3"
          :class="activeConversation?.id === conversation.id 
            ? 'bg-[var(--accent-policy)]/10 border-[var(--accent-policy)]/30 shadow-[inset_0_0_15px_rgba(167,139,250,0.1)]' 
            : 'bg-black/20 border-white/5 hover:border-white/10 hover:bg-black/40'"
          @click="openConversation(conversation.id)"
        >
          <MessageSquare class="w-4 h-4 mt-1 flex-shrink-0" :class="activeConversation?.id === conversation.id ? 'text-[var(--accent-policy)]' : 'text-[var(--text-muted)] group-hover:text-[var(--text-secondary)]'" />
          <div class="flex-1 overflow-hidden">
            <p class="text-sm font-medium text-white truncate">{{ conversation.title }}</p>
            <p class="text-[10px] text-[var(--text-muted)] mt-1">{{ new Date(conversation.last_message_at).toLocaleDateString() }}</p>
          </div>
        </button>
      </div>
    </aside>

    <!-- Main Chat Area -->
    <main class="flex-1 flex flex-col min-w-0 glass-panel border border-[var(--line)] rounded-2xl overflow-hidden shadow-2xl relative z-10">
      <div class="px-6 py-4 border-b border-[var(--line)] bg-black/40 backdrop-blur-md flex items-center justify-between z-10 shrink-0">
        <div>
          <h3 class="text-lg font-bold text-white tracking-tight">{{ activeConversation?.title || '新对话' }}</h3>
          <p class="text-xs text-[var(--status-ok)] flex items-center gap-1.5 mt-0.5">
            <span class="w-1.5 h-1.5 rounded-full bg-[var(--status-ok)] animate-pulse"></span>
            助手已就绪
          </p>
        </div>
      </div>

      <!-- Messages List -->
      <div ref="listRef" class="flex-1 overflow-y-auto p-4 md:p-6 space-y-6 scroll-smooth z-0">
        <div v-if="error" class="bg-[var(--status-error)]/10 border border-[var(--status-error)]/20 text-[var(--status-error)] p-3 rounded-lg text-sm text-center">
          {{ error }}
        </div>

        <template v-if="activeConversation?.messages.length">
          <ChatBubble 
            v-for="message in activeConversation.messages" 
            :key="message.id" 
            :message="message" 
          />
        </template>
        <template v-else>
          <!-- Empty State -->
          <div class="h-full flex flex-col items-center justify-center text-center opacity-70">
            <div class="w-16 h-16 rounded-2xl bg-[var(--accent-primary)]/10 border border-[var(--accent-primary)]/20 flex items-center justify-center mb-4 shadow-[0_0_20px_rgba(100,180,255,0.15)]">
              <Bot class="w-8 h-8 text-[var(--accent-primary)]" />
            </div>
            <h4 class="text-xl font-bold text-white mb-2">研究助手在等待指示</h4>
            <p class="text-[var(--text-secondary)] text-sm max-w-sm">可基于日报、来源和公开资料回答高分子材料加工相关问题。</p>
          </div>
        </template>
        
        <!-- Loading Indicator -->
        <div v-if="loading" class="flex justify-center py-4">
          <div class="flex items-center gap-2 text-[var(--accent-policy)] bg-black/40 px-4 py-2 rounded-full border border-[var(--accent-policy)]/20">
            <Loader2 class="w-4 h-4 animate-spin" />
            <span class="text-xs font-medium">正在整理回答...</span>
          </div>
        </div>
      </div>

      <!-- Composer Area -->
      <div class="p-4 bg-black/40 border-t border-[var(--line)] backdrop-blur-md z-10 shrink-0">
        <div class="relative max-w-4xl mx-auto flex items-end gap-3">
          <textarea 
            v-model="draft" 
            placeholder="问日报、问来源、问某项设备或政策的影响..." 
            class="flex-1 min-h-[56px] max-h-40 bg-[rgba(255,255,255,0.03)] border border-white/10 rounded-xl px-4 py-3 text-white placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent-primary)] focus:bg-[rgba(255,255,255,0.06)] transition-all resize-y shadow-inner"
            @keydown.ctrl.enter="sendMessage"
            @keydown.meta.enter="sendMessage"
          />
          <button 
            @click="sendMessage"
            :disabled="loading || !draft.trim()"
            class="h-14 px-6 rounded-xl bg-[var(--accent-primary)] text-black font-bold flex items-center gap-2 transition-all hover:bg-[var(--accent-primary)]/90 disabled:opacity-50 disabled:cursor-not-allowed shrink-0 shadow-[0_0_15px_rgba(100,180,255,0.2)] hover:shadow-[0_0_20px_rgba(100,180,255,0.4)] hover:-translate-y-0.5"
          >
            <Send class="w-5 h-5" /> 
            <span class="hidden sm:inline">发送</span>
          </button>
        </div>
        <p class="text-center text-[10px] text-[var(--text-muted)] mt-2">提示：您可以使用 Ctrl+Enter 快捷发送</p>
      </div>
    </main>
  </div>
</template>

<style scoped>
.flex { display: flex; }
.flex-col { flex-direction: column; }
.items-center { align-items: center; }
.items-start { align-items: flex-start; }
.items-end { align-items: flex-end; }
.justify-between { justify-content: space-between; }
.justify-center { justify-content: center; }
.gap-1\.5 { gap: 0.375rem; }
.gap-2 { gap: 0.5rem; }
.gap-3 { gap: 0.75rem; }
.gap-4 { gap: 1rem; }
.gap-6 { gap: 1.5rem; }
.w-full { width: 100%; }
.h-full { height: 100%; }
.h-\[calc\(100vh-80px\)\] { height: calc(100vh - 80px); }
.max-w-7xl { max-width: 80rem; }
.max-w-sm { max-width: 24rem; }
.max-w-4xl { max-width: 56rem; }
.min-w-0 { min-width: 0; }
.min-h-\[56px\] { min-height: 56px; }
.max-h-40 { max-height: 10rem; }
.w-1\.5 { width: 0.375rem; }
.h-1\.5 { height: 0.375rem; }
.w-4 { width: 1rem; }
.h-4 { height: 1rem; }
.w-5 { width: 1.25rem; }
.h-5 { height: 1.25rem; }
.w-8 { width: 2rem; }
.h-8 { height: 2rem; }
.w-16 { width: 4rem; }
.h-16 { height: 4rem; }
.w-72 { width: 18rem; }
.h-14 { height: 3.5rem; }
.mx-auto { margin-left: auto; margin-right: auto; }
.mt-0\.5 { margin-top: 0.125rem; }
.mt-1 { margin-top: 0.25rem; }
.mt-2 { margin-top: 0.5rem; }
.mb-2 { margin-bottom: 0.5rem; }
.mb-4 { margin-bottom: 1rem; }
.pr-2 { padding-right: 0.5rem; }
.pr-4 { padding-right: 1rem; }
.pb-4 { padding-bottom: 1rem; }
.p-2 { padding: 0.5rem; }
.p-3 { padding: 0.75rem; }
.p-4 { padding: 1rem; }
.px-4 { padding-left: 1rem; padding-right: 1rem; }
.px-6 { padding-left: 1.5rem; padding-right: 1.5rem; }
.py-2 { padding-top: 0.5rem; padding-bottom: 0.5rem; }
.py-3 { padding-top: 0.75rem; padding-bottom: 0.75rem; }
.py-4 { padding-top: 1rem; padding-bottom: 1rem; }
.relative { position: relative; }
.z-0 { z-index: 0; }
.z-10 { z-index: 10; }
.flex-1 { flex: 1 1 0%; }
.shrink-0 { flex-shrink: 0; }
.flex-shrink-0 { flex-shrink: 0; }
.overflow-hidden { overflow: hidden; }
.overflow-y-auto { overflow-y: auto; }
.scroll-smooth { scroll-behavior: smooth; }
.truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.text-center { text-align: center; }
.text-left { text-align: left; }
.text-\[10px\] { font-size: 0.625rem; }
.text-xs { font-size: 0.75rem; }
.text-sm { font-size: 0.875rem; }
.text-lg { font-size: 1.125rem; }
.text-xl { font-size: 1.25rem; }
.font-medium { font-weight: 500; }
.font-bold { font-weight: 700; }
.tracking-tight { letter-spacing: -0.025em; }
.rounded-lg { border-radius: 0.5rem; }
.rounded-xl { border-radius: 0.75rem; }
.rounded-2xl { border-radius: 1rem; }
.rounded-full { border-radius: 9999px; }
.border { border-width: 1px; }
.border-b { border-bottom-width: 1px; }
.border-r { border-right-width: 1px; }
.border-t { border-top-width: 1px; }
.border-white\/5 { border-color: rgba(255, 255, 255, 0.05); }
.border-white\/10 { border-color: rgba(255, 255, 255, 0.1); }
.border-\[var\(--line\)\] { border-color: var(--line); }
.border-\[var\(--accent-primary\)\]\/20 { border-color: rgba(100, 180, 255, 0.2); }
.border-\[var\(--accent-policy\)\]\/20 { border-color: rgba(167, 139, 250, 0.2); }
.border-\[var\(--accent-policy\)\]\/30 { border-color: rgba(167, 139, 250, 0.3); }
.border-\[var\(--status-error\)\]\/20 { border-color: rgba(248, 113, 113, 0.2); }
.bg-black\/20 { background-color: rgba(0, 0, 0, 0.2); }
.bg-black\/40 { background-color: rgba(0, 0, 0, 0.4); }
.bg-\[rgba\(255\,255\,255\,0\.03\)\] { background-color: rgba(255, 255, 255, 0.03); }
.bg-\[var\(--accent-primary\)\] { background-color: var(--accent-primary); }
.bg-\[var\(--accent-primary\)\]\/10 { background-color: rgba(100, 180, 255, 0.1); }
.bg-\[var\(--accent-policy\)\]\/10 { background-color: rgba(167, 139, 250, 0.1); }
.bg-\[var\(--status-error\)\]\/10 { background-color: rgba(248, 113, 113, 0.1); }
.text-white { color: white; }
.text-black { color: #000; }
.text-\[var\(--text-muted\)\] { color: var(--text-muted); }
.text-\[var\(--text-secondary\)\] { color: var(--text-secondary); }
.text-\[var\(--status-ok\)\] { color: var(--status-ok); }
.text-\[var\(--status-error\)\] { color: var(--status-error); }
.text-\[var\(--accent-primary\)\] { color: var(--accent-primary); }
.text-\[var\(--accent-policy\)\] { color: var(--accent-policy); }
.opacity-70 { opacity: 0.7; }
.shadow-md { box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06); }
.shadow-2xl { box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25); }
.shadow-inner { box-shadow: inset 0 2px 4px 0 rgba(0, 0, 0, 0.5); }
.shadow-\[0_0_10px_rgba\(100\,180\,255\,0\.1\)\] { box-shadow: 0 0 10px rgba(100, 180, 255, 0.1); }
.shadow-\[0_0_15px_rgba\(100\,180\,255\,0\.2\)\] { box-shadow: 0 0 15px rgba(100, 180, 255, 0.2); }
.shadow-\[0_0_20px_rgba\(100\,180\,255\,0\.15\)\] { box-shadow: 0 0 20px rgba(100, 180, 255, 0.15); }
.shadow-\[inset_0_0_15px_rgba\(167\,139\,250\,0\.1\)\] { box-shadow: inset 0 0 15px rgba(167, 139, 250, 0.1); }
.backdrop-blur-md { backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); }
.resize-y { resize: vertical; }
.space-y-2 > :not([hidden]) ~ :not([hidden]) { margin-top: 0.5rem; }
.space-y-6 > :not([hidden]) ~ :not([hidden]) { margin-top: 1.5rem; }
.transition-all { transition-property: all; transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1); transition-duration: 300ms; }
.transition-colors { transition-property: color, background-color, border-color, text-decoration-color, fill, stroke; transition-timing-function: cubic-bezier(0.4, 0, 0.2, 1); transition-duration: 150ms; }
.duration-300 { transition-duration: 300ms; }
.hover\:-translate-y-0\.5:hover { transform: translateY(-0.125rem); }
.hover\:shadow-\[0_0_20px_rgba\(100\,180\,255\,0\.4\)\]:hover { box-shadow: 0 0 20px rgba(100, 180, 255, 0.4); }
.hover\:bg-\[var\(--accent-primary\)\]\/20:hover { background-color: rgba(100, 180, 255, 0.2); }
.hover\:bg-\[var\(--accent-primary\)\]\/90:hover { background-color: rgba(100, 180, 255, 0.9); }
.hover\:bg-black\/40:hover { background-color: rgba(0, 0, 0, 0.4); }
.hover\:border-white\/10:hover { border-color: rgba(255, 255, 255, 0.1); }
.group:hover .group-hover\:text-\[var\(--text-secondary\)\] { color: var(--text-secondary); }
.focus\:outline-none:focus { outline: 2px solid transparent; outline-offset: 2px; }
.focus\:border-\[var\(--accent-primary\)\]:focus { border-color: var(--accent-primary); }
.focus\:bg-\[rgba\(255\,255\,255\,0\.06\)\]:focus { background-color: rgba(255, 255, 255, 0.06); }
.placeholder-\[var\(--text-muted\)\]::placeholder { color: var(--text-muted); }
.disabled\:opacity-50:disabled { opacity: 0.5; }
.disabled\:cursor-not-allowed:disabled { cursor: not-allowed; }

/* Custom Scrollbar for hidden look */
.scrollbar-hide::-webkit-scrollbar { display: none; }
.scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }

/* Animation */
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
.animate-pulse { animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
@keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
.animate-spin { animation: spin 1s linear infinite; }

@media (min-width: 640px) { .sm\:inline { display: inline; } }
@media (min-width: 768px) {
  .md\:flex { display: flex; }
  .md\:p-6 { padding: 1.5rem; }
}
.hidden { display: none; }
</style>

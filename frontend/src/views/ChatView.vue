<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { Bot, Plus, MessageSquare, Send, Sparkles, Loader2, Menu, X } from 'lucide-vue-next'

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
const convDrawerOpen = ref(false)

watch(convDrawerOpen, (open) => {
  document.body.style.overflow = open ? 'hidden' : ''
})

onUnmounted(() => {
  document.body.style.overflow = ''
})

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
  <div class="flex h-[calc(var(--app-vh)-var(--app-bar-h)-2rem)] md:h-[calc(var(--app-vh)-4rem)] gap-6 max-w-7xl mx-auto w-full relative">
    <aside class="w-72 hidden md:flex flex-col gap-4 border-r border-[var(--line)] pr-4 relative z-10">
      <div class="flex items-center justify-between pb-4 border-b border-[var(--line)]">
        <div>
          <h2 class="text-xl font-bold text-[var(--text-primary)] flex items-center gap-2">
            <Sparkles class="w-5 h-5 text-[var(--accent-policy)]" />
            研究助手
          </h2>
        </div>
        <button @click="newConversation" class="p-2 rounded-lg bg-blue-50 text-[var(--accent-primary)] hover:bg-blue-100 transition-colors border border-blue-100">
          <Plus class="w-5 h-5" />
        </button>
      </div>

      <div class="flex-1 overflow-y-auto space-y-2 pr-2 scrollbar-hide">
        <button
          v-for="conversation in conversations"
          :key="conversation.id"
          class="w-full text-left p-3 rounded-xl border transition-all duration-300 group flex items-start gap-3"
          :class="activeConversation?.id === conversation.id 
            ? 'bg-blue-50 border-blue-200' 
            : 'bg-gray-50 border-gray-100 hover:border-gray-200 hover:bg-gray-100'"
          @click="openConversation(conversation.id)"
        >
          <MessageSquare class="w-4 h-4 mt-1 flex-shrink-0" :class="activeConversation?.id === conversation.id ? 'text-[var(--accent-policy)]' : 'text-[var(--text-muted)] group-hover:text-[var(--text-secondary)]'" />
          <div class="flex-1 overflow-hidden">
            <p class="text-sm font-medium text-[var(--text-primary)] truncate">{{ conversation.title }}</p>
            <p class="text-[10px] text-[var(--text-muted)] mt-1">{{ new Date(conversation.last_message_at).toLocaleDateString() }}</p>
          </div>
        </button>
      </div>
    </aside>

    <main class="flex-1 flex flex-col min-w-0 glass-panel border border-[var(--line)] rounded-2xl overflow-hidden shadow-2xl relative z-10">
      <div class="px-4 md:px-6 py-4 border-b border-[var(--line)] bg-white flex items-center justify-between z-10 shrink-0">
        <div class="flex items-center gap-3">
          <button @click="convDrawerOpen = true" class="p-2 -ml-2 rounded-lg hover:bg-[rgba(0,0,0,0.05)] transition-colors md:hidden" aria-label="会话列表">
            <Menu class="w-5 h-5 text-[var(--text-primary)]" />
          </button>
          <div>
            <h3 class="text-lg font-bold text-[var(--text-primary)] tracking-tight">{{ activeConversation?.title || '新对话' }}</h3>
            <p class="text-xs text-[var(--status-ok)] flex items-center gap-1.5 mt-0.5">
              <span class="w-1.5 h-1.5 rounded-full bg-[var(--status-ok)] animate-pulse"></span>
              助手已就绪
            </p>
          </div>
        </div>
      </div>

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
          <div class="h-full flex flex-col items-center justify-center text-center opacity-70">
            <div class="w-16 h-16 rounded-2xl bg-blue-50 border border-blue-100 flex items-center justify-center mb-4">
              <Bot class="w-8 h-8 text-[var(--accent-primary)]" />
            </div>
            <h4 class="text-xl font-bold text-[var(--text-primary)] mb-2">研究助手在等待指示</h4>
            <p class="text-[var(--text-secondary)] text-sm max-w-sm">可基于日报、来源和公开资料回答高分子材料加工相关问题。</p>
          </div>
        </template>
        
        <div v-if="loading" class="flex justify-center py-4">
          <div class="flex items-center gap-2 text-[var(--accent-policy)] bg-white px-4 py-2 rounded-full border border-gray-200 shadow-sm">
            <Loader2 class="w-4 h-4 animate-spin" />
            <span class="text-xs font-medium">正在整理回答...</span>
          </div>
        </div>
      </div>

      <div class="p-4 bg-white border-t border-[var(--line)] z-10 shrink-0 safe-area-bottom">
        <div class="relative max-w-4xl mx-auto flex items-end gap-3">
          <textarea 
            v-model="draft" 
            placeholder="问日报、问来源、问某项设备或政策的影响..." 
            class="flex-1 min-h-[56px] max-h-40 bg-white border border-gray-200 rounded-xl px-4 py-3 text-[var(--text-primary)] placeholder-[var(--text-muted)] focus:outline-none focus:border-[var(--accent-primary)] focus:bg-white transition-all resize-y"
            @focus="scrollToBottom"
            @keydown.ctrl.enter="sendMessage"
            @keydown.meta.enter="sendMessage"
          />
          <button 
            @click="sendMessage"
            :disabled="loading || !draft.trim()"
            class="h-14 px-6 rounded-xl bg-[var(--accent-primary)] text-white font-bold flex items-center gap-2 transition-all hover:bg-[#1e3f73] disabled:opacity-50 disabled:cursor-not-allowed shrink-0"
          >
            <Send class="w-5 h-5" /> 
            <span class="hidden sm:inline">发送</span>
          </button>
        </div>
        <p class="text-center text-[10px] text-[var(--text-muted)] mt-2">提示：您可以使用 Ctrl+Enter 快捷发送</p>
      </div>
    </main>

    <div v-if="convDrawerOpen" class="fixed inset-0 z-40 bg-black/40 md:hidden" @click="convDrawerOpen = false" aria-hidden="true"></div>

    <div
      class="mobile-drawer fixed top-0 left-0 bottom-0 z-50 w-[var(--sidebar-w)] max-w-[85vw] bg-[var(--bg-surface)] border-r border-[var(--line)] flex flex-col transform transition-transform duration-300 ease-in-out md:hidden safe-area-top"
      :class="convDrawerOpen ? 'translate-x-0' : '-translate-x-full'"
      :aria-hidden="!convDrawerOpen"
    >
      <div class="flex items-center justify-between px-4 py-3 border-b border-[var(--line)]">
        <div class="flex items-center gap-2">
          <Sparkles class="w-5 h-5 text-[var(--accent-policy)]" />
          <span class="font-bold text-[var(--text-primary)]">研究助手</span>
        </div>
        <div class="flex items-center gap-1">
          <button @click="newConversation" class="p-2 rounded-lg bg-blue-50 text-[var(--accent-primary)] hover:bg-blue-100 transition-colors border border-blue-100" aria-label="新建对话">
            <Plus class="w-4 h-4" />
          </button>
          <button @click="convDrawerOpen = false" class="p-2 rounded-lg hover:bg-[rgba(0,0,0,0.05)] transition-colors" aria-label="关闭会话列表">
            <X class="w-5 h-5 text-[var(--text-secondary)]" />
          </button>
        </div>
      </div>

      <div class="flex-1 overflow-y-auto px-4 py-4 space-y-2">
        <button
          v-for="conversation in conversations"
          :key="conversation.id"
          class="w-full text-left p-3 rounded-xl border transition-all duration-300 group flex items-start gap-3"
          :class="activeConversation?.id === conversation.id 
            ? 'bg-blue-50 border-blue-200' 
            : 'bg-gray-50 border-gray-100 hover:border-gray-200 hover:bg-gray-100'"
          @click="convDrawerOpen = false; openConversation(conversation.id)"
        >
          <MessageSquare class="w-4 h-4 mt-1 flex-shrink-0" :class="activeConversation?.id === conversation.id ? 'text-[var(--accent-policy)]' : 'text-[var(--text-muted)] group-hover:text-[var(--text-secondary)]'" />
          <div class="flex-1 overflow-hidden">
            <p class="text-sm font-medium text-[var(--text-primary)] truncate">{{ conversation.title }}</p>
            <p class="text-[10px] text-[var(--text-muted)] mt-1">{{ new Date(conversation.last_message_at).toLocaleDateString() }}</p>
          </div>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.scrollbar-hide::-webkit-scrollbar { display: none; }
.scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }
</style>

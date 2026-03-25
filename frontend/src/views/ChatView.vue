<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'

import { api } from '../lib/api'
import { useSessionStore } from '../stores/session'
import type { Conversation, ConversationDetail } from '../types'

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
}

async function newConversation() {
  const conversation = await api.createConversation(`研究对话 ${new Date().toLocaleTimeString()}`)
  await loadConversations()
  await openConversation(conversation.id)
}

async function sendMessage() {
  if (!draft.value.trim()) {
    return
  }
  loading.value = true
  error.value = ''
  try {
    const payload = await api.sendChatStream(draft.value, activeConversation.value?.id)
    if (!activeConversation.value) {
      activeConversation.value = await api.getConversation(payload.conversation_id as number)
    } else {
      activeConversation.value.messages.push(payload.user_message, payload.assistant_message)
    }
    draft.value = ''
    await loadConversations()
    await nextTick()
    listRef.value?.scrollTo({ top: listRef.value.scrollHeight, behavior: 'smooth' })
  } catch (err) {
    error.value = err instanceof Error ? err.message : '发送失败'
  } finally {
    loading.value = false
  }
}

async function toggleFavorite(conversation: Conversation) {
  if (conversation.favorited) {
    await api.unfavoriteConversation(conversation.id)
    conversation.favorited = false
  } else {
    await api.favoriteConversation(conversation.id)
    conversation.favorited = true
  }
}

onMounted(() => {
  if (session.user) {
    void loadConversations()
  }
})
</script>

<template>
  <section class="split-layout chat-layout">
    <aside class="panel conversation-list">
      <div class="section-head">
        <div>
          <p class="eyebrow">Assistant</p>
          <h2>研究助手</h2>
        </div>
        <button class="primary-button" @click="newConversation">新建会话</button>
      </div>

      <button
        v-for="conversation in conversations"
        :key="conversation.id"
        class="history-row"
        :class="{ active: activeConversation?.id === conversation.id }"
        @click="openConversation(conversation.id)"
      >
        <div>
          <strong>{{ conversation.title }}</strong>
          <p>{{ new Date(conversation.last_message_at).toLocaleString() }}</p>
        </div>
        <span class="favorite-toggle" @click.stop="toggleFavorite(conversation)">
          {{ conversation.favorited ? '已藏' : '收藏' }}
        </span>
      </button>
    </aside>

    <section class="panel chat-panel">
      <div class="section-head">
        <div>
          <p class="eyebrow">Local First</p>
          <h2>{{ activeConversation?.title || '新对话' }}</h2>
        </div>
      </div>

      <p v-if="error" class="error-box">{{ error }}</p>

      <div ref="listRef" class="message-list">
        <article
          v-for="message in activeConversation?.messages ?? []"
          :key="message.id"
          class="message-bubble"
          :class="message.role"
        >
          <p class="message-role">{{ message.role === 'assistant' ? '助手' : '你' }}</p>
          <p>{{ message.content }}</p>
          <div v-if="message.citations?.length" class="citation-list">
            <a
              v-for="(citation, index) in message.citations"
              :key="index"
              :href="String(citation.url || '#')"
              target="_blank"
              rel="noreferrer"
            >
              {{ citation.label || citation.url }}
            </a>
          </div>
        </article>
      </div>

      <div class="composer">
        <textarea v-model="draft" placeholder="问日报、问来源、问某项设备或政策的影响。" />
        <button class="primary-button" :disabled="loading" @click="sendMessage">发送</button>
      </div>
    </section>
  </section>
</template>

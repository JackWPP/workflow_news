<script setup lang="ts">
import { computed } from 'vue'
import { Bot, User, Globe } from 'lucide-vue-next'
import type { Message } from '../types'

const props = defineProps<{ message: Message }>()

const isUser = computed(() => props.message.role === 'user')

</script>

<template>
  <div class="flex gap-4 w-full" :class="{ 'flex-row-reverse': isUser }">
    <div class="w-10 h-10 shrink-0 rounded-full flex flex-col items-center justify-center border transition-all duration-300 shadow-md"
         :class="isUser ? 'bg-[rgba(100,180,255,0.1)] border-[var(--accent-primary)] text-[var(--accent-primary)]' : 'bg-black/40 border-[var(--accent-policy)]/50 text-[var(--accent-policy)] glow-avatar'">
      <User v-if="isUser" class="w-5 h-5" />
      <Bot v-else class="w-5 h-5" />
    </div>

    <div class="flex flex-col gap-2 max-w-[85%] md:max-w-[75%]">
      <div class="p-4 rounded-2xl relative"
           :class="isUser ? 'bg-gradient-to-br from-[var(--accent-primary)] to-[#3a7bd5] text-white rounded-tr-sm shadow-[0_4px_15px_rgba(100,180,255,0.2)]' : 'glass-panel text-[var(--text-primary)] rounded-tl-sm prose prose-invert max-w-none'">
        <p class="whitespace-pre-wrap leading-relaxed">{{ message.content }}</p>

        <div v-if="message.citations?.length" class="mt-4 pt-3 border-t" :class="isUser ? 'border-white/20' : 'border-white/10'">
          <div class="flex flex-wrap gap-2">
            <a
              v-for="(citation, index) in message.citations"
              :key="index"
              :href="String(citation.url || '#')"
              target="_blank"
              rel="noreferrer"
              class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium bg-black/20 hover:bg-black/40 transition-colors border"
              :class="isUser ? 'border-white/30 text-white' : 'border-[var(--accent-policy)]/30 text-[var(--accent-policy)] hover:border-[var(--accent-policy)]/60'"
            >
              <Globe class="w-3 h-3" />
              <span class="truncate max-w-[150px]">{{ citation.label || citation.url }}</span>
            </a>
          </div>
        </div>
      </div>
      
      <div class="text-[10px] text-[var(--text-muted)] px-1" :class="isUser ? 'text-right' : 'text-left'">
        {{ new Date(message.created_at).toLocaleTimeString() }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.glow-avatar { box-shadow: 0 0 15px rgba(167, 139, 250, 0.3); }
</style>

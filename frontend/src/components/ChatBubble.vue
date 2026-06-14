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
         :class="isUser ? 'bg-blue-50 border-[var(--accent-primary)] text-[var(--accent-primary)]' : 'bg-gray-100 border-gray-200 text-[var(--accent-policy)]'">
      <User v-if="isUser" class="w-5 h-5" />
      <Bot v-else class="w-5 h-5" />
    </div>

    <div class="flex flex-col gap-2 max-w-[85%] md:max-w-[75%]">
      <div class="p-4 rounded-2xl relative"
           :class="isUser ? 'bg-[var(--accent-academic)] text-white rounded-tr-sm shadow-sm' : 'bg-white border border-gray-200 text-[var(--text-primary)] rounded-tl-sm prose max-w-none shadow-sm'">
        <p class="whitespace-pre-wrap leading-relaxed">{{ message.content }}</p>

        <div v-if="message.citations?.length" class="mt-4 pt-3 border-t" :class="isUser ? 'border-white/20' : 'border-white/10'">
          <div class="flex flex-wrap gap-2">
            <a
              v-for="(citation, index) in message.citations"
              :key="index"
              :href="String(citation.url || '#')"
              target="_blank"
              rel="noreferrer"
              class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium transition-colors border"
              :class="isUser ? 'border-white/30 text-white' : 'border-gray-200 text-[var(--accent-policy)] hover:border-gray-300'"
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



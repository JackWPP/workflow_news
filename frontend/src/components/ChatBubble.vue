<script setup lang="ts">
import { computed } from 'vue'
import { Bot, User, Globe } from 'lucide-vue-next'
import type { Message } from '../types'

const props = defineProps<{ message: Message }>()

const isUser = computed(() => props.message.role === 'user')

</script>

<template>
  <div class="flex gap-4 w-full" :class="{ 'flex-row-reverse': isUser }">
    <!-- Avatar -->
    <div class="w-10 h-10 shrink-0 rounded-full flex flex-col items-center justify-center border transition-all duration-300 shadow-md"
         :class="isUser ? 'bg-[rgba(100,180,255,0.1)] border-[var(--accent-primary)] text-[var(--accent-primary)]' : 'bg-black/40 border-[var(--accent-policy)]/50 text-[var(--accent-policy)] glow-avatar'">
      <User v-if="isUser" class="w-5 h-5" />
      <Bot v-else class="w-5 h-5" />
    </div>

    <!-- Bubble Wrapper -->
    <div class="flex flex-col gap-2 max-w-[85%] md:max-w-[75%]">
      <!-- Main Message Bubble -->
      <!-- Added prose for rich text rendering inside bubble -->
      <div class="p-4 rounded-2xl relative"
           :class="isUser ? 'bg-gradient-to-br from-[var(--accent-primary)] to-[#3a7bd5] text-white rounded-tr-sm shadow-[0_4px_15px_rgba(100,180,255,0.2)]' : 'glass-panel text-[var(--text-primary)] rounded-tl-sm prose prose-invert max-w-none'">
        <p class="whitespace-pre-wrap leading-relaxed">{{ message.content }}</p>

        <!-- Citations Row -->
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
      
      <!-- Timestamp inside / below bubble -->
      <div class="text-[10px] text-[var(--text-muted)] px-1" :class="isUser ? 'text-right' : 'text-left'">
        {{ new Date(message.created_at).toLocaleTimeString() }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.flex { display: flex; }
.flex-col { flex-direction: column; }
.flex-row-reverse { flex-direction: row-reverse; }
.flex-wrap { flex-wrap: wrap; }
.items-center { align-items: center; }
.justify-center { justify-content: center; }
.gap-1\.5 { gap: 0.375rem; }
.gap-2 { gap: 0.5rem; }
.gap-4 { gap: 1rem; }
.w-full { width: 100%; }
.shrink-0 { flex-shrink: 0; }
.w-3 { width: 0.75rem; }
.h-3 { height: 0.75rem; }
.w-4 { width: 1rem; }
.h-4 { height: 1rem; }
.w-5 { width: 1.25rem; }
.h-5 { height: 1.25rem; }
.w-10 { width: 2.5rem; }
.h-10 { height: 2.5rem; }
.max-w-\[85\%\] { max-width: 85%; }
.max-w-\[150px\] { max-width: 150px; }
.truncate { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.px-1 { padding-left: 0.25rem; padding-right: 0.25rem; }
.px-3 { padding-left: 0.75rem; padding-right: 0.75rem; }
.py-1\.5 { padding-top: 0.375rem; padding-bottom: 0.375rem; }
.p-3 { padding: 0.75rem; }
.p-4 { padding: 1rem; }
.pt-3 { padding-top: 0.75rem; }
.mt-4 { margin-top: 1rem; }
.mb-2 { margin-bottom: 0.5rem; }
.border { border-width: 1px; }
.border-t { border-top-width: 1px; }
.border-white\/5 { border-color: rgba(255, 255, 255, 0.05); }
.border-white\/10 { border-color: rgba(255, 255, 255, 0.1); }
.border-white\/20 { border-color: rgba(255, 255, 255, 0.2); }
.border-white\/30 { border-color: rgba(255, 255, 255, 0.3); }
.rounded-full { border-radius: 9999px; }
.rounded-lg { border-radius: 0.5rem; }
.rounded-2xl { border-radius: 1rem; }
.rounded-tl-sm { border-top-left-radius: 0.125rem; }
.rounded-tr-sm { border-top-right-radius: 0.125rem; }
.text-xs { font-size: 0.75rem; line-height: 1rem; }
.text-\[10px\] { font-size: 0.625rem; line-height: 1rem; }
.text-right { text-align: right; }
.text-left { text-align: left; }
.font-semibold { font-weight: 600; }
.font-medium { font-weight: 500; }
.tracking-wider { letter-spacing: 0.05em; }
.uppercase { text-transform: uppercase; }
.whitespace-pre-wrap { white-space: pre-wrap; }
.leading-relaxed { line-height: 1.625; }
.bg-black\/20 { background-color: rgba(0, 0, 0, 0.2); }
.bg-black\/30 { background-color: rgba(0, 0, 0, 0.3); }
.bg-black\/40 { background-color: rgba(0, 0, 0, 0.4); }
.hover\:bg-black\/40:hover { background-color: rgba(0, 0, 0, 0.4); }
.bg-gradient-to-br { background-image: linear-gradient(to bottom right, var(--tw-gradient-stops)); }
.from-\[var\(--accent-primary\)\] { --tw-gradient-from: var(--accent-primary); --tw-gradient-stops: var(--tw-gradient-from), var(--tw-gradient-to); }
.to-\[\#3a7bd5\] { --tw-gradient-to: #3a7bd5; }
.shadow-md { box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06); }
.shadow-inner { box-shadow: inset 0 2px 4px 0 rgba(0, 0, 0, 0.5); }
.shadow-\[0_4px_15px_rgba\(100\,180\,255\,0\.2\)\] { box-shadow: 0 4px 15px rgba(100, 180, 255, 0.2); }
.glow-avatar { box-shadow: 0 0 15px rgba(167, 139, 250, 0.3); }

/* Animation Utils */
@keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
.animate-pulse { animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; }

/* Responsive Grid */
@media (min-width: 768px) {
  .md\:max-w-\[75\%\] { max-width: 75%; }
}
</style>

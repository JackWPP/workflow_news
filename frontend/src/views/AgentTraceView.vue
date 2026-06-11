<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { Activity, Clock, TerminalSquare, AlertCircle, Database, Server } from 'lucide-vue-next'

import { api } from '../lib/api'
import StatusPill from '../components/StatusPill.vue'

const runs = ref<any[]>([])
const selectedRunId = ref<number | null>(null)
const traceData = ref<any>(null)
const loading = ref(false)
const error = ref('')

async function loadRuns() {
  loading.value = true
  error.value = ''
  try {
    const payload = await api.listAgentRuns(50)
    runs.value = payload.runs || payload || []
    if (runs.value.length > 0) {
      await inspectRun(runs.value[0].id)
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

async function inspectRun(runId: number) {
  selectedRunId.value = runId
  traceData.value = null
  try {
    const data = await api.getAgentRunTrace(runId)
    traceData.value = data
  } catch (err) {
    console.error('Failed to view trace', err)
  }
}

onMounted(() => {
  void loadRuns()
})
</script>

<template>
  <div class="flex h-[calc(100vh-80px)] gap-6 max-w-7xl mx-auto w-full relative">
    
    <aside class="w-80 flex-shrink-0 flex flex-col gap-4 border-r border-white/5 pr-4 relative z-10">
      <div class="flex items-center justify-between pb-4 border-b border-[var(--line)] shrink-0">
        <div>
          <h2 class="text-xl font-bold text-white flex items-center gap-2">
            <Activity class="w-5 h-5 text-[var(--accent-academic)]" />
            系统执行轨迹
          </h2>
          <p class="text-xs text-[var(--text-muted)] mt-1 tracking-wider uppercase">Agent Traces</p>
        </div>
      </div>

      <div class="flex-1 overflow-y-auto space-y-3 pr-2 scrollbar-hide">
        <div v-if="loading" class="text-center text-[var(--text-muted)] py-8 text-sm animate-pulse">
          读取运行记录中...
        </div>
        <div v-else-if="error" class="text-[var(--status-error)] p-3 rounded-lg bg-[var(--status-error)]/10 border border-[var(--status-error)]/20 text-sm">
          {{ error }}
        </div>
        
        <button
          v-for="run in runs"
          :key="run.id"
          class="w-full text-left p-4 rounded-xl border transition-all duration-300 group flex flex-col gap-2 relative overflow-hidden"
          :class="selectedRunId === run.id 
            ? 'bg-[var(--bg-surface)] border-[var(--accent-academic)]/40 shadow-[inset_0_0_20px_rgba(100,180,255,0.1)]' 
            : 'bg-black/20 border-white/5 hover:border-white/10 hover:bg-black/40'"
          @click="inspectRun(run.id)"
        >
          <div v-if="selectedRunId === run.id" class="absolute left-0 top-0 bottom-0 w-1 bg-[var(--accent-academic)] shadow-[0_0_10px_var(--accent-academic)]"></div>
          
          <div class="flex justify-between items-start gap-2">
            <strong class="text-white font-bold tracking-tight text-sm flex items-center gap-1">
              <TerminalSquare class="w-3.5 h-3.5 text-[var(--text-muted)]" />
              #{{ run.id }}
            </strong>
            <StatusPill :status="run.status" />
          </div>
          
          <p class="text-[10px] text-[var(--text-secondary)] font-mono">
            {{ new Date(run.started_at).toLocaleString() }}
          </p>
          
          <div class="flex items-center gap-4 mt-1 pt-2 border-t border-white/5 text-[10px] text-[var(--text-muted)] uppercase tracking-wider">
            <span>{{ run.total_steps || 0 }} Steps</span>
            <span v-if="run.harness_triggered" class="text-[var(--status-warn)] border border-[var(--status-warn)] px-1 rounded">Blocked</span>
          </div>
        </button>
      </div>
    </aside>

    <main class="flex-1 flex flex-col min-w-0 glass-panel border border-[var(--line)] rounded-2xl overflow-hidden relative z-10 shadow-2xl">
      <template v-if="traceData">
        <div class="shrink-0 border-b border-[var(--line)] bg-black/40 backdrop-blur-md px-6 py-4 flex items-center justify-between z-10">
          <div class="flex items-center gap-3">
            <Server class="w-5 h-5 text-[var(--accent-academic)]" />
            <div>
              <h2 class="text-lg font-bold text-white tracking-tight">Run #{{ selectedRunId }} Trace</h2>
              <p class="text-xs text-[var(--text-muted)] uppercase tracking-widest mt-0.5">{{ traceData.agent_name }} Mode</p>
            </div>
          </div>
          <div class="flex gap-4 items-center">
            <div class="flex flex-col text-right">
              <span class="text-[10px] text-[var(--text-muted)] uppercase">End Reason</span>
              <span class="text-xs text-[var(--text-secondary)] font-medium">{{ traceData.end_reason || 'Running' }}</span>
            </div>
            <div class="flex flex-col text-right">
              <span class="text-[10px] text-[var(--text-muted)] uppercase">Cost</span>
              <span class="text-xs text-white font-mono">${{ traceData.total_cost?.toFixed(4) || '0.00' }}</span>
            </div>
          </div>
        </div>

        <div class="flex-1 overflow-y-auto p-6 scroll-smooth z-0 bg-transparent flex flex-col gap-6">
          <div v-if="traceData.error_message" class="bg-[var(--status-error)]/10 border border-[var(--status-error)]/20 p-4 rounded-xl flex items-start gap-3">
            <AlertCircle class="w-5 h-5 text-[var(--status-error)] shrink-0 mt-0.5" />
            <div>
              <h4 class="text-sm font-bold text-[var(--status-error)] mb-1">Fatal Run Error</h4>
              <p class="text-xs text-[var(--status-error)]/80 font-mono whitespace-pre-wrap">{{ traceData.error_message }}</p>
            </div>
          </div>

          <div class="relative pl-6 border-l border-[var(--line)] space-y-8">
            <div v-for="step in traceData.steps" :key="step.id" class="relative group">
              <div class="absolute -left-[31px] top-6 w-4 h-4 rounded-full border-4 border-[#0a0e1a] bg-black ring-1 transition-colors"
                   :class="step.is_error ? 'ring-[var(--status-error)] bg-[var(--status-error)]/20' : step.harness_blocked ? 'ring-[var(--status-warn)] bg-[var(--status-warn)]/20' : 'ring-[var(--accent-academic)] group-hover:bg-[var(--accent-academic)]/20'">
              </div>
              
              <div class="glass-card p-5 transition-colors" :class="step.is_error ? 'border-[var(--status-error)]/50 shadow-[0_0_15px_rgba(248,113,113,0.1)]!' : ''">
                <div class="flex justify-between items-center mb-4 pb-3 border-b border-white/5">
                  <div class="flex items-center gap-3">
                    <span class="text-xs font-bold font-mono text-[var(--accent-academic)] bg-[var(--accent-academic)]/10 px-2 py-1 rounded">Step {{ step.step_number }}</span>
                    <span class="text-xs font-mono text-[var(--text-secondary)] flex items-center gap-1">
                      <Clock class="w-3 h-3" /> {{ new Date(step.created_at).toLocaleTimeString() }}
                    </span>
                  </div>
                  <div v-if="step.harness_blocked" class="text-xs font-bold text-[var(--status-warn)] flex items-center gap-1">
                    <AlertCircle class="w-3.5 h-3.5" /> HARNESS BLOCKED
                  </div>
                </div>

                <div class="space-y-4">
                  <div v-if="step.thought" class="flex flex-col gap-1.5">
                    <span class="text-[10px] text-[var(--text-muted)] uppercase tracking-widest font-bold">LLM Thought</span>
                    <p class="text-sm text-[var(--text-primary)] leading-relaxed italic bg-black/20 p-3 rounded-lg border border-white/5 border-l-[3px] border-l-[var(--accent-academic)]">
                      {{ step.thought }}
                    </p>
                  </div>

                  <div class="grid grid-cols-1 xl:grid-cols-2 gap-4">
                    <div v-if="step.tool_name" class="flex flex-col gap-1.5">
                      <span class="text-[10px] text-[var(--accent-industry)] uppercase tracking-widest font-bold flex items-center gap-1">
                        <TerminalSquare class="w-3 h-3" /> Tool Call
                      </span>
                      <div class="bg-black/40 p-3 rounded-lg border border-white/5 h-full overflow-hidden">
                        <strong class="text-xs text-white font-mono block mb-2">{{ step.tool_name }}()</strong>
                        <pre class="text-[11px] text-[var(--text-secondary)] font-mono overflow-auto whitespace-pre-wrap max-h-40 scrollbar-hide">{{ JSON.stringify(step.tool_args, null, 2) }}</pre>
                      </div>
                    </div>

                    <div v-if="step.tool_result" class="flex flex-col gap-1.5">
                      <span class="text-[10px] text-[var(--status-ok)] uppercase tracking-widest font-bold flex items-center gap-1">
                        <Database class="w-3 h-3" /> Tool Output
                      </span>
                      <div class="bg-black/40 p-3 rounded-lg border border-white/5 h-full overflow-hidden">
                        <pre class="text-[11px] text-[var(--text-secondary)] font-mono overflow-auto whitespace-pre-wrap max-h-48 scrollbar-hide" :class="{ 'text-[var(--status-error)]': step.is_error }">{{ step.tool_result }}</pre>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>
      
      <div v-else class="flex-1 flex items-center justify-center flex-col gap-4 opacity-50">
        <Activity class="w-16 h-16 text-[var(--text-muted)]" />
        <p class="text-sm font-medium text-[var(--text-secondary)]">选择左侧会话回放执行轨迹</p>
      </div>
    </main>
  </div>
</template>

<style scoped>
.scrollbar-hide::-webkit-scrollbar { display: none; }
.scrollbar-hide { -ms-overflow-style: none; scrollbar-width: none; }

.border-l-\[3px\] { border-left-width: 3px; }
.border-l-\[var\(--accent-academic\)\] { border-left-color: var(--accent-academic); }

.shadow-\[0_0_15px_rgba\(248\,113\,113\,0\.1\)\]\! { box-shadow: 0 0 15px rgba(248, 113, 113, 0.1) !important; }
.border-\[var\(--status-error\)\]\/50\! { border-color: rgba(248, 113, 113, 0.5) !important; }
</style>

<script setup lang="ts">
import { onMounted } from 'vue'

import AppShell from './components/AppShell.vue'
import { useSessionStore } from './stores/session'

const session = useSessionStore()

onMounted(() => {
  void session.ensureLoaded()
})
</script>

<template>
  <AppShell>
    <router-view />
  </AppShell>
</template>

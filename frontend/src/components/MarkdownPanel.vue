<script setup lang="ts">
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps<{ content: string }>()

const html = computed(() => DOMPurify.sanitize(marked.parse(props.content) as string))
</script>

<template>
  <article class="markdown-panel prose" v-html="html" />
</template>

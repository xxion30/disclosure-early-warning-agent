<script setup>
// 진행 단계 표시. MAIN_PATH 순서로 보여주고, 그 밖의 노드(save_and_summarize 등)는 온 순서대로 뒤에 붙인다.
import { computed } from 'vue'

import { MAIN_PATH, NODE_LABELS } from '../labels'

const props = defineProps({ state: { type: Object, required: true } })

const steps = computed(() => {
  const seen = props.state.order
  const extra = seen.filter((n) => !MAIN_PATH.includes(n))
  return [...MAIN_PATH, ...extra].map((node) => ({
    node,
    label: NODE_LABELS[node] || node,
    status: props.state.nodes[node] || 'todo',
  }))
})
</script>

<template>
  <ol class="progress">
    <li v-for="s in steps" :key="s.node" :class="`step-${s.status}`">
      <span class="dot" />
      <span>{{ s.label }}</span>
    </li>
  </ol>
  <p v-if="state.error" class="error">{{ state.error }}</p>
</template>

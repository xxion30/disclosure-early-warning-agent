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

// 막대: PB 승인까지를 100%로 본다 (반려·업무 생성은 승인 뒤 단계라 빼고 셈)
const percent = computed(() => {
  const upto = MAIN_PATH.indexOf('pb_approval') + 1
  const reached = steps.value.slice(0, upto).filter((s) => s.status !== 'todo').length
  return Math.round((reached / upto) * 100)
})
</script>

<template>
  <p v-if="state.queued" class="notice">큐에서 차례를 기다리는 중입니다…</p>
  <div class="progress-bar" role="progressbar" :aria-valuenow="percent" aria-valuemin="0" aria-valuemax="100">
    <span :style="{ width: `${percent}%` }" />
  </div>
  <ol class="progress">
    <li v-for="s in steps" :key="s.node" :class="`step-${s.status}`">
      <span class="dot" />
      <span>{{ s.label }}</span>
    </li>
  </ol>
  <p v-if="state.error" class="error">{{ state.error }}</p>
</template>

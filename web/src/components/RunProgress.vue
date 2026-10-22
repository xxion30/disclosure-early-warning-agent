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

// 끝난 모양: 수동 확인(주황) / 실패(빨강) / 그 밖(파랑)
const failed = computed(() => props.state.last === 'failed' && !props.state.manual)
const barTone = computed(() => (props.state.manual ? 'warn' : failed.value ? 'bad' : ''))
</script>

<template>
  <p v-if="state.queued" class="notice">큐에서 차례를 기다리는 중입니다…</p>
  <div class="progress-bar" :class="barTone" role="progressbar" :aria-valuenow="percent" aria-valuemin="0" aria-valuemax="100">
    <span :style="{ width: `${percent}%` }" />
  </div>
  <ol class="progress">
    <li v-for="s in steps" :key="s.node" :class="`step-${s.status}`">
      <span class="dot" />
      <span>{{ s.label }}</span>
    </li>
  </ol>
  <p v-if="state.manual" class="notice" role="status">
    <strong>사람의 확인이 필요해 멈췄어요.</strong>
    {{ state.error || 'AI가 근거를 충분히 찾지 못했어요.' }} 경보 상세에서 근거와 공시 원문을 확인해 주세요.
  </p>
  <p v-else-if="failed" class="error" role="alert">
    <strong>처리에 실패했어요.</strong> {{ state.error || '실패 이유를 받지 못했어요.' }}
  </p>
  <p v-else-if="state.disconnected" class="notice" role="status">
    진행 상황 연결이 끊겼어요. 처리는 서버에서 계속될 수 있으니 잠시 뒤 새로고침해 주세요.
  </p>
</template>

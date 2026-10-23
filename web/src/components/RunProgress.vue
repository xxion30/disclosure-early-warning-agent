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

const GROUPS = [
  { label: '공시 분석', nodes: ['receive', 'classify', 'extract_facts', 'score_risk', 'explain_risk', 'route_by_level', 'save_and_summarize'] },
  { label: '안내문 준비', nodes: ['find_customers', 'draft_messages', 'guardrail_check', 'prepare_approval'] },
  { label: 'PB 검토', nodes: ['pb_approval'] },
  { label: '업무 생성', nodes: ['save_rejection', 'create_tasks', 'finalize'] },
]
const statusLabels = { todo: '대기', running: '진행 중', waiting: '승인 대기', done: '완료', failed: '실패', manual: '확인 필요', skipped: '대상 아님' }
const groups = computed(() => GROUPS.map((group, index) => {
  const states = group.nodes.map((node) => props.state.nodes[node]).filter(Boolean)
  const laterStarted = GROUPS.slice(index + 1).some((g) => g.nodes.some((node) => props.state.nodes[node]))
  let status = 'todo'
  if (states.includes('failed')) status = 'failed'
  else if (states.includes('manual')) status = 'manual'
  else if (states.includes('waiting')) status = 'waiting'
  else if (props.state.last === 'done') status = states.length ? 'done' : 'skipped'
  else if (laterStarted) status = 'done'
  else if (states.length) status = 'running'
  return { ...group, status }
}))
// PB 검토 대기는 완료가 아니다. 서버의 완료 응답에서만 막대를 채운다.
const percent = computed(() => props.state.last === 'done' ? 100 : groups.value.filter((g) => g.status === 'done').length * 25)
const currentStage = computed(() => {
  if (props.state.queued) return '공시 분석 · 차례 대기'
  if (props.state.disconnected) return '진행 상황 연결 끊김 · 다시 확인 필요'
  if (props.state.manual) return '수동 확인 필요 · 처리가 멈췄어요'
  if (props.state.last === 'failed') return '처리 실패 · 다시 확인 필요'
  if (props.state.last === 'done') return props.state.nodes.pb_approval ? '결정 반영 완료 · 승인한 고객만 연락 업무 생성' : '공시 분석 완료 · 고객 안내 대상 아님'
  const current = groups.value.find((g) => ['failed', 'manual', 'waiting', 'running'].includes(g.status)) || groups.value.find((g) => g.status === 'todo')
  return current ? `${current.label} · ${statusLabels[current.status]}` : '처리 결과 확인 중'
})

// 실패 = 수동 확인이 아닌 failed 이벤트
const failed = computed(() => props.state.last === 'failed' && !props.state.manual)
// 끝난 모양: 수동 확인(주황) / 실패(빨강) / 그 밖(파랑)
const barTone = computed(() => (props.state.manual ? 'warn' : failed.value ? 'bad' : ''))
</script>

<template>
  <p v-if="state.queued" class="notice">큐에서 차례를 기다리는 중입니다…</p>
  <p class="muted" role="status">{{ currentStage }}</p>
  <div class="progress-bar" :class="barTone" role="progressbar" aria-label="공시 처리 진행" :aria-valuetext="currentStage" :aria-valuenow="percent" aria-valuemin="0" aria-valuemax="100">
    <span :style="{ width: `${percent}%` }" />
  </div>
  <ol class="progress">
    <li v-for="g in groups" :key="g.label" :class="`step-${g.status}`">
      <span class="dot" aria-hidden="true" />
      <span>{{ g.label }} · {{ statusLabels[g.status] }}</span>
    </li>
  </ol>
  <details>
    <summary>기술 단계 보기</summary>
    <ol class="progress">
      <li v-for="s in steps" :key="s.node" :class="`step-${s.status}`">
        <span class="dot" aria-hidden="true" />
        <span>{{ s.label }} · {{ statusLabels[s.status] }}</span>
      </li>
    </ol>
  </details>
  <p v-if="state.manual" class="notice warn" role="status">
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

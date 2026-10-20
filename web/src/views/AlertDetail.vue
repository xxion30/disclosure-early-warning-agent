<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import { api, errorMessage } from '../api/client'
import LevelBadge from '../components/LevelBadge.vue'
import RunProgress from '../components/RunProgress.vue'
import ScoreBreakdown from '../components/ScoreBreakdown.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useRunEvents } from '../composables/useRunEvents'
import { formatTime, MAIN_PATH } from '../labels'

const props = defineProps({ assessmentId: { type: String, required: true } })

const alert = ref(null)
const error = ref('')
const hasRunHistory = ref(false)
const { state: runState, connect, apply, reset } = useRunEvents()

async function load() {
  error.value = ''
  try {
    alert.value = await api.getAlert(props.assessmentId)
  } catch (err) {
    error.value = errorMessage(err)
    return
  }
  await loadProgress()
}

// 지난 진행 이벤트를 그리고, 아직 실행 중이면 SSE로 이어 받는다
async function loadProgress() {
  try {
    const run = await api.getRun(alert.value.rcept_no)
    hasRunHistory.value = true
    // 서버는 마지막 실행 구간만 기억한다. 재개 구간이면 pb_approval 앞 단계는 이미 끝난 것
    const resumed = run.events[0]?.data.node === 'pb_approval'
    const doneBefore = resumed ? MAIN_PATH.slice(0, MAIN_PATH.indexOf('pb_approval')) : []
    if (run.running) {
      connect(alert.value.rcept_no, { onEnd: load, doneBefore })
    } else {
      reset()
      doneBefore.forEach((n) => (runState.nodes[n] = 'done'))
      run.events.forEach((e) => apply(e.event, e.data))
    }
  } catch {
    hasRunHistory.value = false // 서버 재시작 등으로 실행 기록이 없으면 단계 표시는 생략
  }
}

onMounted(load)
watch(() => props.assessmentId, load)

// 같은 인용이 사실 추출·설명 단계에서 두 번 들어올 수 있어 출처+문장 기준으로 한 번만 보여준다
const evidence = computed(() => {
  const seen = new Set()
  return (alert.value?.evidence || []).filter((e) => {
    const key = `${e.source}|${e.rcept_no}|${e.quote}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
})

const myPending = computed(() => (alert.value?.customers || []).filter((c) => c.mine && c.decision === '승인대기'))
</script>

<template>
  <p><RouterLink to="/alerts">← 경보 목록</RouterLink></p>
  <p v-if="error" class="error">{{ error }}</p>

  <template v-if="alert">
    <div class="page-head">
      <div>
        <h1>
          {{ alert.company.name || alert.disclosure.title }}
          <LevelBadge :level="alert.level" />
          <StatusBadge :status="alert.status" />
        </h1>
        <p class="muted">
          {{ alert.disclosure.title }} · 접수번호 {{ alert.rcept_no }}
          <template v-if="alert.disclosure.filed_at"> · 공시일 {{ alert.disclosure.filed_at }}</template>
          <template v-if="alert.disclosure.url"> · <a :href="alert.disclosure.url" target="_blank" rel="noopener">DART 원문</a></template>
        </p>
      </div>
      <RouterLink
        v-if="myPending.length"
        :to="{ name: 'draft-review', params: { assessmentId } }"
      >
        <button>안내문 검토 ({{ myPending.length }}명)</button>
      </RouterLink>
    </div>

    <div v-if="hasRunHistory" class="card">
      <h2>진행 단계</h2>
      <RunProgress :state="runState" />
    </div>

    <div class="card">
      <h2>점수 내역 <small>항목별 점수의 합이 총점</small></h2>
      <div class="table-wrap">
        <ScoreBreakdown :reasons="alert.reasons" :total-score="alert.total_score" :combo-upgraded="alert.combo_upgraded" />
      </div>
    </div>

    <div class="card">
      <h2>AI 설명과 원문 근거</h2>
      <p style="white-space: pre-wrap">{{ alert.ai_explanation || '설명이 없습니다.' }}</p>
      <div v-for="(e, i) in evidence" :key="i">
        <blockquote>{{ e.quote }}</blockquote>
        <small>출처: {{ e.source }}<template v-if="e.rcept_no"> · {{ e.rcept_no }}</template></small>
      </div>
      <p v-if="!evidence.length" class="notice">원문 인용이 없습니다. 수동 확인이 필요합니다.</p>
    </div>

    <div class="card">
      <h2>영향 고객 <small>{{ alert.customers.length }}명 · 우선순위순</small></h2>
      <p v-if="alert.graph_error" class="notice">
        {{ alert.graph_error }} 점수 내역은 그대로 볼 수 있고, 영향 고객·안내문은 연결되면 다시 보입니다.
      </p>
      <p v-else-if="!alert.graph_state_available" class="notice">
        Graph 상태를 찾을 수 없어 영향 고객을 표시할 수 없습니다. (서버 재시작 후 가짜 Graph 사용 중일 수 있음)
      </p>
      <table v-else>
        <thead>
          <tr>
            <th>우선순위</th>
            <th>고객</th>
            <th>투자성향</th>
            <th class="num">영향 점수</th>
            <th>영향도</th>
            <th>담당</th>
            <th>결정</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="c in alert.customers" :key="c.customer_id">
            <td><span class="prio" :class="`prio-${c.priority}`">{{ c.priority }}</span></td>
            <td>{{ c.name }} <small>{{ c.customer_id }}</small></td>
            <td>{{ c.risk_profile || '-' }}</td>
            <td class="num">{{ c.impact_score }}</td>
            <td>{{ c.impact_level }}</td>
            <td>{{ c.mine ? '내 고객' : c.pb_id }}</td>
            <td><StatusBadge :status="c.decision" /></td>
          </tr>
        </tbody>
      </table>
      <p class="muted" style="margin-top: 8px"><small>생성 {{ formatTime(alert.created_at) }}</small></p>
    </div>
  </template>
</template>

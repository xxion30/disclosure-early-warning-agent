<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import { api, errorMessage } from '../api/client'
import LevelBadge from '../components/LevelBadge.vue'
import AlertSummary from '../components/AlertSummary.vue'
import RunProgress from '../components/RunProgress.vue'
import ScoreBreakdown from '../components/ScoreBreakdown.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useRunEvents } from '../composables/useRunEvents'
import { formatTime, isUnscored, MAIN_PATH, STATUS_GUIDE } from '../labels'
import { useAuthStore } from '../stores/auth'

const props = defineProps({ assessmentId: { type: String, required: true } })
const auth = useAuthStore()

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
const customers = computed(() => alert.value?.customers || [])
const fixedReason = computed(() => alert.value?.reasons?.find((r) => r.fixed_level))
const scoreTotals = computed(() => {
  const totals = { base: 0, signals: 0 }
  for (const reason of alert.value?.reasons || []) {
    totals[reason.group === 'BASE' ? 'base' : 'signals'] += Number(reason.points ?? 0)
  }
  return { base: Number(totals.base.toFixed(2)), signals: Number(totals.signals.toFixed(2)) }
})
const priorityNote = computed(() => ['P1', 'P2'].map((priority) => {
  const count = customers.value.filter((c) => c.priority === priority).length
  return count ? `${priority} ${count}명` : ''
}).filter(Boolean).join(' · ') || '영향 고객 없음')
const progressNote = computed(() => ({
  WAITING_APPROVAL: 'PB 승인에서 멈춰 있어요',
  DONE: '처리가 끝났어요',
  MANUAL_CHECK: '사람의 확인이 필요해 멈췄어요',
  FAILED: '처리 중간에 멈췄어요',
}[alert.value?.status] || ''))

// 실패·수동 확인·처리 중이면 맨 위에 안내를 띄운다. 이유(error)는 API가 주면 함께 보여준다
const guide = computed(() => STATUS_GUIDE[alert.value?.status] || null)
const unscored = computed(() => isUnscored(alert.value))
// 고객 영향도는 High·Critical 경보에서만 계산한다
const impactSkipped = computed(() => unscored.value || !['High', 'Critical'].includes(alert.value?.level))
</script>

<template>
  <RouterLink to="/alerts" class="back-link">
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6" /></svg>
    경보 목록
  </RouterLink>
  <p v-if="error" class="error detail-error" role="alert">{{ error }}</p>

  <template v-if="alert">
    <div v-if="guide" class="banner status-banner" :class="guide.tone" :role="guide.tone === 'bad' ? 'alert' : 'status'">
      <span class="banner-icon" aria-hidden="true">
        <svg v-if="guide.tone === 'bad'" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" /><path d="M15 9l-6 6M9 9l6 6" /></svg>
        <svg v-else-if="guide.tone === 'warn'" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01" /><path d="M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /></svg>
        <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></svg>
      </span>
      <span class="banner-text">
        <strong>{{ guide.title }}</strong>
        <span>{{ guide.text }}<template v-if="unscored"> 점수 계산 전에 멈춰서 등급과 총점이 없어요.</template></span>
        <span v-if="alert.error" class="status-reason">이유: {{ alert.error }}</span>
      </span>
    </div>

    <section class="card hero" aria-label="경보 요약">
      <div class="hero-head">
        <div class="hero-company">
          <span class="corp-mark" aria-hidden="true">{{ (alert.company.name || '-').slice(0, 1) }}</span>
          <div class="hero-copy">
            <div class="hero-title">
              <h1>{{ alert.company.name || alert.disclosure.title }}</h1>
              <span v-if="unscored" class="unscored">등급 미계산</span>
              <LevelBadge v-else :level="alert.level" />
              <StatusBadge :status="alert.status" />
            </div>
            <p class="hero-meta muted">
              <strong>{{ alert.disclosure.title }}</strong>
              <span>접수번호 {{ alert.rcept_no }}</span>
              <span v-if="alert.disclosure.filed_at">공시일 {{ alert.disclosure.filed_at }}</span>
              <a v-if="alert.disclosure.url" :href="alert.disclosure.url" target="_blank" rel="noopener">
                DART 원문
                <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17L17 7M9 7h8v8" /></svg>
              </a>
            </p>
          </div>
        </div>
        <RouterLink
          v-if="myPending.length"
          :to="{ name: 'draft-review', params: { assessmentId } }"
          class="review-link"
        >
          안내문 검토 <span class="count-pill">{{ myPending.length }}명</span>
          <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6" /></svg>
        </RouterLink>
      </div>
      <div class="mini-stats">
        <div v-if="unscored" class="mini-stat">
          <span class="mini-stat-label">등급 결정</span>
          <div class="mini-stat-value level-value"><strong class="muted">미계산</strong></div>
          <span class="mini-stat-note">점수 계산 전에 멈췄어요</span>
        </div>
        <div v-else class="mini-stat" :class="{ bad: fixedReason }">
          <span class="mini-stat-label">등급 결정</span>
          <div class="mini-stat-value level-value"><strong>{{ fixedReason ? `${fixedReason.fixed_level} 고정` : alert.level || '-' }}</strong></div>
          <span class="mini-stat-note">{{ fixedReason ? `확정 악재: ${fixedReason.item}` : `총점 기준 등급${alert.combo_upgraded ? ' · 조합 상향' : ''}` }}</span>
        </div>
        <div class="mini-stat">
          <span class="mini-stat-label">총점</span>
          <div class="mini-stat-value"><strong>{{ unscored ? '-' : alert.total_score ?? '-' }}</strong><span v-if="!unscored">점</span></div>
          <span class="mini-stat-note">{{ unscored ? '점수 내역 없음' : `기본 점수 ${scoreTotals.base} + 신호 ${scoreTotals.signals}` }}</span>
        </div>
        <div class="mini-stat">
          <span class="mini-stat-label">영향 고객</span>
          <div class="mini-stat-value"><strong>{{ customers.length }}</strong><span>명</span></div>
          <span class="mini-stat-note">{{ priorityNote }}</span>
        </div>
        <div class="mini-stat dark">
          <span class="mini-stat-label">내 승인 대기</span>
          <div class="mini-stat-value"><strong>{{ myPending.length }}</strong><span>명</span></div>
          <span class="mini-stat-note">{{ auth.pb?.pb_id || '-' }} 담당 고객</span>
        </div>
      </div>
    </section>

    <!-- 점수 계산 전에 멈춘 경보는 "추가된 신호 없음"으로 잘못 읽히므로 요약을 숨긴다 -->
    <AlertSummary v-if="alert.summary && !unscored" :summary="alert.summary" :level="alert.level" :total-score="alert.total_score" />

    <section v-if="hasRunHistory" class="card" aria-label="진행 단계">
      <div class="card-head">
        <h2>진행 단계</h2>
        <span v-if="progressNote" class="muted section-note">{{ progressNote }}</span>
      </div>
      <RunProgress :state="runState" />
    </section>

    <section class="card flush" aria-label="점수 내역">
      <div class="card-head">
        <h2>점수 내역</h2>
        <span class="muted section-note">점수는 규칙으로 계산해요. AI는 점수를 바꾸지 않아요.</span>
      </div>
      <div class="table-wrap">
        <ScoreBreakdown :reasons="alert.reasons" :total-score="alert.total_score" :combo-upgraded="alert.combo_upgraded" />
      </div>
    </section>

    <section class="card" aria-label="AI 설명과 원문 근거">
      <div class="card-head explanation-head">
        <h2>AI 설명과 원문 근거</h2>
        <span class="chip soft">AI가 쓴 설명 · 점수에는 영향 없음</span>
      </div>
      <p class="explanation">{{ alert.ai_explanation || '설명이 없습니다.' }}</p>
      <div v-if="evidence.length" class="quote-grid">
        <figure v-for="(e, i) in evidence" :key="i" class="quote">
          <blockquote>“{{ e.quote }}”</blockquote>
          <figcaption>
            <span class="chip strong">{{ e.source }}</span>
            <span v-if="e.rcept_no" class="chip">{{ e.rcept_no }}</span>
          </figcaption>
        </figure>
      </div>
      <p v-if="!evidence.length && alert.status === 'FAILED'" class="notice">처리가 중간에 멈춰 원문 인용이 없어요. 공시 원문을 직접 확인해 주세요.</p>
      <p v-else-if="!evidence.length" class="notice">원문 인용이 없어요. 추측하지 않고 수동 확인으로 남겼어요.</p>
    </section>

    <section class="card flush customers" aria-label="영향 고객">
      <div class="card-head">
        <h2>영향 고객 <small>{{ customers.length }}명 · 우선순위순</small></h2>
        <span class="muted section-note">다른 PB의 고객은 이름을 가려요</span>
      </div>
      <p v-if="impactSkipped && !customers.length" class="muted impact-skipped">
        고객 영향도는 High·Critical 경보에서만 계산해요.<template v-if="unscored"> 이 공시는 점수 계산 전에 멈췄어요.</template>
      </p>
      <p v-else-if="alert.graph_error" class="notice">
        {{ alert.graph_error }} 점수 내역은 그대로 볼 수 있고, 영향 고객·안내문은 연결되면 다시 보입니다.
      </p>
      <p v-else-if="!alert.graph_state_available" class="notice">
        Graph 상태를 찾을 수 없어 영향 고객을 표시할 수 없습니다. (서버 재시작 후 가짜 Graph 사용 중일 수 있음)
      </p>
      <div v-else class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>우선순위</th>
              <th>고객</th>
              <th>투자성향</th>
              <th>영향도</th>
              <th>담당</th>
              <th>결정</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="c in customers" :key="c.customer_id">
              <td><span class="prio-pill" :class="`prio-${c.priority}`">{{ c.priority }}</span></td>
              <td>
                <div class="customer">
                  <span class="avatar sm" :class="{ soft: !c.mine }" aria-hidden="true">{{ c.mine ? (c.name || '-').slice(0, 1) : 'C' }}</span>
                  <div class="customer-text"><strong>{{ c.name }}</strong><span v-if="c.mine" class="sub">{{ c.customer_id }}</span></div>
                </div>
              </td>
              <td>{{ c.risk_profile || '-' }}</td>
              <td>
                <div class="impact" :class="`impact-${c.impact_level}`">
                  <span class="impact-track" aria-hidden="true"><span :style="{ width: `${(c.impact_score ?? 0) / 12 * 100}%` }"></span></span>
                  <span class="impact-text">{{ c.impact_score }}점 · {{ c.impact_level }}</span>
                </div>
              </td>
              <td><span class="chip" :class="c.mine ? 'strong mine' : 'soft'">{{ c.mine ? '내 고객' : c.pb_id }}</span></td>
              <td><StatusBadge :status="c.decision" /></td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="muted created-at"><small>생성 {{ formatTime(alert.created_at) }}</small></p>
    </section>
  </template>
</template>

<style scoped>
.back-link { min-height: 44px; }
.detail-error { margin: 0 0 20px; }
.status-banner { margin-bottom: 20px; }
.status-reason { margin-top: 4px; font-weight: 600; overflow-wrap: anywhere; }
.impact-skipped { margin: 0 24px 16px; font-size: 14px; }
.hero { border-radius: 28px; padding: 28px; display: flex; flex-direction: column; gap: 24px; }
.hero-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 20px; flex-wrap: wrap; }
.hero-company { display: flex; align-items: flex-start; gap: 16px; flex: 1 1 560px; min-width: 0; }
.hero .corp-mark { width: 56px; height: 56px; border-radius: 18px; font-size: 22px; }
.hero-copy { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.hero-title { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.hero-title h1 { margin: 0; overflow-wrap: anywhere; }
.hero-meta { display: flex; gap: 6px 12px; flex-wrap: wrap; margin: 0; font-size: 14px; overflow-wrap: anywhere; }
.hero-meta strong { color: var(--text); font-weight: 600; }
.hero-meta a { display: inline-flex; align-items: center; gap: 4px; min-height: 44px; font-weight: 600; }
.hero-meta > span, .hero-meta > strong { align-self: center; }
.review-link { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 52px; padding: 0 24px; border-radius: var(--pill); background: var(--blue); color: var(--surface); font-size: 16px; font-weight: 700; white-space: nowrap; }
.review-link:hover { background: var(--blue-strong); text-decoration: none; }
.review-link:focus-visible, .back-link:focus-visible, .hero-meta a:focus-visible { outline: 3px solid var(--blue); outline-offset: 4px; }
.review-link .count-pill { background: color-mix(in srgb, var(--surface) 22%, transparent); font-size: 14px; }
.level-value strong { font-size: 20px; }
.mini-stat-note { overflow-wrap: anywhere; }
.section-note { font-size: 13px; }
.explanation-head { justify-content: flex-start; gap: 10px; }
.explanation { margin: 0 0 16px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 15px; line-height: 1.75; color: #2a3554; }
.quote { overflow-wrap: anywhere; }
.customers h2 small { margin-left: 6px; font-size: 14px; font-weight: 500; }
.customers > .notice { margin: 0 24px 16px; }
.customers table { min-width: 900px; }
.customers td { font-size: 14px; }
.customer { display: flex; align-items: center; gap: 10px; }
.customer-text { display: flex; flex-direction: column; line-height: 1.35; }
.chip.mine { background: var(--blue-soft); }
.created-at { margin: 8px 0; padding: 0 24px; }
@media (max-width: 540px) {
  .hero-company { flex-basis: 100%; }
  .hero-title h1 { font-size: 26px; }
  .hero .mini-stats { grid-template-columns: minmax(0, 1fr); }
  .review-link { width: 100%; }
}
</style>

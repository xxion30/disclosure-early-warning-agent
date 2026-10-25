<script setup>
import { computed, onMounted, ref, watch } from 'vue'

import { api, errorMessage } from '../api/client'
import { mockMode } from '../api/mock'
import AlertSummary from '../components/AlertSummary.vue'
import RunProgress from '../components/RunProgress.vue'
import StageBadge from '../components/StageBadge.vue'
import ScoreBreakdown from '../components/ScoreBreakdown.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useRunEvents } from '../composables/useRunEvents'
import { formatTime, isAlertRow, isUnscored, MAIN_PATH, stagesOf, stageText, STATUS_GUIDE } from '../labels'

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
  // 가짜 데이터 모드의 예시 경보는 실제 서버에 실행 기록이 없다 (불러도 404)
  if (alert.value?.mock) {
    hasRunHistory.value = false
    return
  }
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

// 대표 인용 순서: 등급을 고정한 확정 악재 → 이번 공시에서 뽑은 인용 → 이번 공시 항목 → 과거 신호.
// 서버는 항목을 점수 순으로 주므로 그대로 쓰면 과거 신호의 문장이 대표로 올라올 수 있다.
const reasonRank = (r) => (r.fixed_level ? 0 : r.rcept_no === alert.value?.rcept_no && !r.from_history ? 2 : 3)
// 같은 인용이 사실 추출·설명 단계에서 두 번 들어올 수 있어 출처+문장 기준으로 한 번만 보여준다
const evidence = computed(() => {
  const seen = new Set()
  const fromReasons = (alert.value?.reasons || []).flatMap((r) => (r.evidence || []).map((e) => ({ ...e, rcept_no: e.rcept_no || r.rcept_no, rank: reasonRank(r) })))
  const fromDisclosure = (alert.value?.evidence || []).map((e) => ({ ...e, rcept_no: e.rcept_no || alert.value?.rcept_no, rank: 1 }))
  const quotes = [...fromReasons, ...fromDisclosure].sort((a, b) => a.rank - b.rank) // 같은 순위 안에서는 서버 순서 유지
  return quotes.filter((e) => {
    if (!e.quote) return false
    const key = `${e.source}|${e.rcept_no}|${e.quote}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
})

const myPending = computed(() => (alert.value?.customers || []).filter((c) => c.mine && c.decision === '승인대기'))
const customers = computed(() => alert.value?.customers || [])
// 서버 문장에 단계 코드(RED 등)가 들어 있으면 한국어 이름으로 바꿔 보여 준다
const keyReason = computed(() => stageText(alert.value?.summary?.headline) || alert.value?.reasons?.[0]?.item || '핵심 사유를 확인할 수 없어요. 공시 원문을 확인해 주세요.')
const primaryEvidence = computed(() => evidence.value[0])
const dartUrl = (rcept) => `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${encodeURIComponent(rcept)}`
const evidenceUrl = (e) => (alert.value?.mock ? null : e?.rcept_no ? dartUrl(e.rcept_no) : alert.value?.disclosure?.url)
const disclosureUrl = computed(() => alert.value?.disclosure?.url || (alert.value?.rcept_no && !alert.value?.mock ? dartUrl(alert.value.rcept_no) : ''))
// 인용이 다른(과거) 공시에서 왔으면 링크 이름으로 알려 준다
const evidenceLinkLabel = (e) => (e?.rcept_no && e.rcept_no !== alert.value?.rcept_no ? '인용한 공시 원문' : 'DART 원문 확인')
const progressNote = computed(() => ({
  WAITING_APPROVAL: 'PB 승인에서 멈춰 있어요',
  DONE: '처리가 끝났어요',
  MANUAL_CHECK: '사람의 확인이 필요해 멈췄어요',
  FAILED: '처리 중간에 멈췄어요',
}[alert.value?.status] || ''))

// 실패·수동 확인·처리 중이면 맨 위에 안내를 띄운다. 이유(error)는 API가 주면 함께 보여준다
const guide = computed(() => STATUS_GUIDE[alert.value?.status] || null)
const unscored = computed(() => isUnscored(alert.value))
// 안내문 대상 고객은 경보(관찰 중·경보 없음 제외)에서만 정해진다
const impactSkipped = computed(() => unscored.value || !isAlertRow(alert.value))
const customerState = computed(() => {
  if (impactSkipped.value) return '조회 대상 아님'
  if (alert.value?.graph_error || !alert.value?.graph_state_available) return '확인 불가'
  return `${customers.value.length}명`
})
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
        <span>{{ guide.text }}<template v-if="unscored"> 위험 평가를 완료하지 못했어요.</template></span>
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
              <!-- 상세에서는 함께 난 단계를 모두 보여 준다 -->
              <StageBadge v-else all :stage="alert.stage" :stages="alert.stages" :level="alert.level" :legacy="alert.legacy" :available="alert.stage_available" />
              <StatusBadge :status="alert.status" />
            </div>
            <p class="hero-meta muted">
              <strong>{{ alert.disclosure.title }}</strong>
              <span>접수번호 {{ alert.rcept_no }}</span>
              <span v-if="alert.disclosure.filed_at">공시일 {{ alert.disclosure.filed_at }}</span>
              <a v-if="disclosureUrl" :href="disclosureUrl" target="_blank" rel="noopener">
                이번 공시 DART 원문
                <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M7 17L17 7M9 7h8v8" /></svg>
              </a>
              <!-- 종목 단위 위험 상태(새 단계)가 있을 때만 종목 상세로 보낸다 -->
              <RouterLink v-if="alert.company.stock_code && (stagesOf(alert).length || mockMode())" :to="{ name: 'risk-detail', params: { stockCode: alert.company.stock_code } }">
                종목 위험 상태 보기
              </RouterLink>
            </p>
          </div>
        </div>
      </div>
      <p class="explanation"><strong>핵심 사유</strong><br />{{ unscored ? '위험 평가를 완료하지 못했어요.' : keyReason }}</p>
      <figure v-if="primaryEvidence" class="quote">
        <blockquote>“{{ primaryEvidence.quote }}”</blockquote>
        <figcaption>
          <span v-if="primaryEvidence.source" class="chip strong">{{ primaryEvidence.source }}</span>
          <a v-if="evidenceUrl(primaryEvidence)" :href="evidenceUrl(primaryEvidence)" target="_blank" rel="noopener">{{ evidenceLinkLabel(primaryEvidence) }}</a>
        </figcaption>
      </figure>
      <p v-else class="notice warn">원문 인용을 확인할 수 없어요. 위의 "이번 공시 DART 원문"에서 직접 확인해 주세요.</p>
      <RouterLink
        v-if="myPending.length && !impactSkipped && !alert.graph_error && alert.graph_state_available"
        :to="{ name: 'draft-review', params: { assessmentId } }"
        class="review-link"
      >내 고객 안내문 검토 · {{ myPending.length }}명 승인 대기</RouterLink>
    </section>

    <!-- 점수 계산 전에 멈춘 경보는 "추가된 신호 없음"으로 잘못 읽히므로 요약을 숨긴다 -->
    <details v-if="alert.summary && !unscored" class="card">
      <summary>이번 공시의 영향 더 보기</summary>
      <AlertSummary :summary="alert.summary" :level="alert.level" :stage="alert.stage" :stages="alert.stages" />
    </details>

    <details v-if="hasRunHistory" class="card" :open="alert.status !== 'DONE'">
      <summary>진행 단계<template v-if="progressNote"> · {{ progressNote }}</template></summary>
      <RunProgress :state="runState" />
    </details>

    <section class="card flush" aria-label="위험 항목">
      <div class="card-head">
        <h2>위험 항목</h2>
        <span class="muted section-note">등급은 규칙으로 정해요. AI는 등급을 바꾸지 않아요.</span>
      </div>
      <div class="table-wrap">
        <ScoreBreakdown :reasons="alert.reasons" :combo-upgraded="alert.combo_upgraded" :linkable="!alert.mock" />
      </div>
    </section>

    <details class="card">
      <summary>AI 설명과 추가 인용<template v-if="evidence.length > 1"> · {{ evidence.length - 1 }}개</template></summary>
      <p class="explanation">{{ alert.ai_explanation || '설명이 없습니다.' }}</p>
      <p class="muted section-note">AI가 쓴 설명은 등급에 영향을 주지 않아요.</p>
      <div v-if="evidence.length > 1" class="quote-grid">
        <figure v-for="(e, i) in evidence.slice(1)" :key="i" class="quote">
          <blockquote>“{{ e.quote }}”</blockquote>
          <figcaption>
            <span v-if="e.source" class="chip strong">{{ e.source }}</span>
            <a v-if="evidenceUrl(e)" :href="evidenceUrl(e)" target="_blank" rel="noopener">{{ evidenceLinkLabel(e) }}</a>
          </figcaption>
        </figure>
      </div>
    </details>

    <section class="card flush customers" aria-label="영향 고객">
      <div class="card-head">
        <h2>보유 고객 <small>{{ customerState }}<template v-if="customerState.endsWith('명') && customers.length"> · 보유 평가금액 큰 순</template></small></h2>
        <span class="muted section-note">다른 PB의 고객은 이름을 가려요</span>
      </div>
      <p v-if="impactSkipped" class="muted impact-skipped">
        안내문 대상 고객은 경보 단계에서만 정해져요.<template v-if="unscored"> 이 공시는 위험 평가 전에 멈췄어요.</template>
      </p>
      <p v-else-if="alert.graph_error" class="notice">
        고객 정보를 불러오지 못했어요. 위험 항목은 볼 수 있어요. 잠시 뒤 다시 확인해 주세요.
      </p>
      <p v-else-if="!alert.graph_state_available" class="notice">
        이 경보의 고객 조회 기록을 확인할 수 없어요. 담당 운영자에게 확인을 요청해 주세요.
      </p>
      <div v-else class="table-wrap">
        <table class="stack-table">
          <thead>
            <tr>
              <th scope="col">고객 (보유 평가금액 순)</th>
              <th scope="col">담당</th>
              <th scope="col">결정</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!customers.length"><td colspan="3" class="empty">보유 고객이 없습니다.</td></tr>
            <tr v-for="c in customers" :key="c.customer_id">
              <td>
                <div class="customer">
                  <span class="avatar sm" :class="{ soft: !c.mine }" aria-hidden="true">{{ c.mine ? (c.name || '-').slice(0, 1) : 'C' }}</span>
                  <div class="customer-text"><strong>{{ c.name }}</strong><span class="sub"><template v-if="c.mine">{{ c.customer_id }} · </template>{{ c.risk_profile || '-' }}</span></div>
                </div>
              </td>
              <td><span class="chip" :class="c.mine ? 'strong mine' : 'soft'">{{ c.mine ? '내 고객' : c.pb_id }}</span></td>
              <td class="decision-cell"><StatusBadge :status="c.decision" /></td>
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
.detail-error { margin: 0 0 24px; }
.status-banner { margin-bottom: 24px; }
.status-reason { margin-top: 4px; font-weight: 600; overflow-wrap: anywhere; }
.impact-skipped { margin: 0 24px 16px; font-size: 14px; }
.hero { border-radius: var(--radius); padding: 24px; display: flex; flex-direction: column; gap: 24px; }
.hero-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; flex-wrap: wrap; }
.hero-company { display: flex; align-items: flex-start; gap: 16px; flex: 1 1 560px; min-width: 0; }
.hero .corp-mark { width: 56px; height: 56px; border-radius: var(--radius-md); font-size: 22px; }
.hero-copy { display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.hero-title { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.hero-title h1 { margin: 0; overflow-wrap: anywhere; }
.hero-meta { display: flex; gap: 8px 12px; flex-wrap: wrap; margin: 0; font-size: 14px; overflow-wrap: anywhere; }
.hero-meta strong { color: var(--text); font-weight: 600; }
.hero-meta > span, .hero-meta > strong { align-self: center; }
.hero-meta a { display: inline-flex; align-items: center; gap: 4px; min-height: 44px; font-weight: 600; }
.review-link { display: inline-flex; align-items: center; justify-content: center; gap: 8px; min-height: 52px; padding: 0 24px; border-radius: var(--pill); background: var(--blue-strong); color: var(--surface); font-size: 16px; font-weight: 700; text-align: center; align-self: flex-start; max-width: 100%; }
.review-link:hover { background: var(--blue-strong); text-decoration: none; }
.section-note { font-size: 13px; }
.explanation { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 15px; line-height: 1.75; color: #2a3554; }
.quote { overflow-wrap: anywhere; }
.quote .chip { white-space: normal; overflow-wrap: anywhere; }
.quote a { display: inline-flex; align-items: center; min-height: 44px; }
details > summary { margin-bottom: 16px; }
details:not([open]) > summary { margin-bottom: 0; }
details > .summary { margin: 0; padding: 16px; box-shadow: none; }
.customers h2 small { margin-left: 6px; font-size: 14px; font-weight: 500; }
.customers > .notice { margin: 0 24px 16px; }
.customers th:not(:first-child) { width: 1%; white-space: nowrap; }
@media (max-width: 640px) {
  .customers tr { display: flex !important; align-items: center; gap: 8px; flex-wrap: wrap; }
  .customers td:first-child { flex: 1 1 100%; }
}
.customers td { font-size: 14px; }
.customer { display: flex; align-items: center; gap: 12px; }
.customer-text { display: flex; flex-direction: column; line-height: 1.35; }
.chip.mine { background: var(--blue-soft); }
.created-at { margin: 8px 0; padding: 0 24px; }
@media (max-width: 540px) {
  .hero-company { flex-basis: 100%; }
  .hero-title h1 { font-size: 26px; }
  .review-link { width: 100%; }
}
</style>

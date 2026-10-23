<script setup>
// 안내문 검토: 고객마다 결정을 고른 뒤 한 번에 제출한다.
// 두 방식을 같은 화면으로 다룬다
// - 예전 방식 /alerts/:assessmentId/drafts: GET /alerts/{id}/drafts, POST decision (PB 1명의 결정 = Graph 재개 1회, SSE로 진행)
// - 새 조직별 검토 /reviews/:reviewId: GET /reviews/{id}, POST /reviews/{id}/decisions {expected_version}, 초안 생성은 POST /reviews/{id}/run
//   (R4 계약 "Pre-merge 실행·조직 검토"). 새 API는 고객 ID만 주므로 이름은 고객 현황(/customers)에서 붙인다 — AI에는 이름이 가지 않는다
import { computed, onMounted, reactive, ref, watch } from 'vue'

import { api, errorMessage } from '../api/client'
import StageBadge from '../components/StageBadge.vue'
import RunProgress from '../components/RunProgress.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useRunEvents } from '../composables/useRunEvents'
import { MAIN_PATH } from '../labels'

const props = defineProps({
  assessmentId: { type: String, default: null },
  reviewId: { type: String, default: null },
  stockCode: { type: String, default: null }, // 새 검토 화면에서 돌아갈 종목 상세
})
const isOrg = computed(() => !!props.reviewId)
const review = ref(null) // 새 조직별 검토 원본 (status, version)
const running = ref(false)
const REVIEW_STATUS = {
  PENDING: { tone: 'info', title: '안내문 초안이 아직 없어요', text: '"안내문 초안 만들기"를 누르면 고객별 초안을 만들어요. 다시 작성을 요청한 고객도 여기서 새 초안을 받아요.' },
  MANUAL_CHECK: { tone: 'warn', title: '초안을 만들지 못했어요', text: '사람의 확인이 필요해요. 다시 만들기를 눌러 보거나 공시 원문을 확인해 주세요.' },
  RUNNING: { tone: 'info', title: '안내문 초안을 만드는 중이에요', text: '잠시 뒤 새로고침해 주세요.' },
  DONE: { tone: 'ok', title: '모든 고객의 결정이 끝났어요', text: '승인한 고객은 연락 업무에서 확인할 수 있어요.' },
  SUPERSEDED: { tone: 'warn', title: '판정이 바뀌어 이 검토는 더 이상 승인할 수 없어요', text: '새 판정의 검토에서 다시 확인해 주세요.' },
  RESOLVED: { tone: 'ok', title: '위험이 해소돼 이 검토는 끝났어요', text: '승인할 안내문이 없어요.' },
}

const PLACEHOLDER = '{고객명}'
const CHOICES = {
  approve: '승인',
  edit: '수정 후 승인',
  rewrite: '다시 작성',
  reject: '반려',
}

const data = ref(null)
const alertInfo = ref(null)
const error = ref('')
const message = ref('')
const submitting = ref(false)
const approvedSubmitted = ref(false)
const form = reactive({}) // customer_id → { choice, text, reason }
const { state: runState, connect } = useRunEvents()
const showProgress = ref(false)

// 새 조직별 검토 응답 → 예전 화면이 쓰는 모양 {drafts: [{customer_id, name, risk_profile, draft, preview, manual, decision, ...}]}
async function loadOrgReview() {
  const [rv, customers] = await Promise.all([api.getReview(props.reviewId), api.listCustomers().catch(() => null)])
  const names = Object.fromEntries((customers?.customers || []).map((c) => [c.customer_id, c]))
  const extra = rv.customer_names || {} // 가짜 데이터 모드에서만 오는 예시 이름
  review.value = rv
  const drafts = Object.entries(rv.drafts || {}).map(([cid, text]) => {
    const who = names[cid] || extra[cid] || {}
    const name = who.name || cid
    const dec = rv.decisions?.[cid]
    return {
      customer_id: cid, name, risk_profile: who.risk_profile || '-', draft: text || '', preview: fillName(text, name),
      manual: !text, // 서버가 금지 표현 검사에 걸린 초안을 빈 문자열로 준다
      decision: dec ? (dec.action === 'reject' ? '반려' : '승인') : '승인대기',
      reject_reason: dec?.reject_reason, edited_draft: dec?.edited_draft,
    }
  })
  return { drafts, guardrail_flags: [] }
}

// keepError: 제출·초안 만들기 뒤 다시 불러올 때 방금 난 오류 안내를 지우지 않는다
async function load({ keepError = false } = {}) {
  if (!keepError) error.value = ''
  try {
    data.value = isOrg.value ? await loadOrgReview() : await api.getDrafts(props.assessmentId)
  } catch (err) {
    error.value = errorMessage(err)
    return
  }
  for (const d of data.value.drafts) {
    if (!form[d.customer_id] || d.decision !== '승인대기') {
      form[d.customer_id] = { choice: null, text: d.draft, reason: '' }
    } else {
      form[d.customer_id].text = form[d.customer_id].choice === 'edit' ? form[d.customer_id].text : d.draft
    }
  }
}
onMounted(load)
onMounted(async () => {
  try {
    if (!isOrg.value) alertInfo.value = await api.getAlert(props.assessmentId)
    else if (props.stockCode) {
      const st = await api.getState(props.stockCode)
      alertInfo.value = { company: { name: st.corp_name }, stage: st.stage, stages: st.active_stages, stage_available: true }
    }
  } catch {
    // 부가 정보가 없어도 안내문은 검토할 수 있다
  }
})
watch(() => props.reviewId, () => { if (isOrg.value) load() })

// 새 조직별 검토: PB가 눌러야 초안을 만든다 (R2 실행기가 연결되지 않은 서버는 503)
async function runDrafts() {
  if (running.value) return
  running.value = true
  error.value = ''
  message.value = ''
  approvedSubmitted.value = false
  try {
    await api.runReview(props.reviewId)
    message.value = '안내문 초안을 만들었어요.'
  } catch (err) {
    const st = err.response?.status
    error.value = st === 503 ? '안내문 초안 생성기가 아직 연결되지 않았어요. AI 흐름(R2) 연결 뒤에 사용할 수 있어요.' : errorMessage(err)
  } finally {
    running.value = false
    await load({ keepError: true })
  }
}
const reviewGuide = computed(() => (isOrg.value && review.value && review.value.status !== 'WAITING_APPROVAL' ? REVIEW_STATUS[review.value.status] || null : null))
const canRun = computed(() => isOrg.value && ['PENDING', 'MANUAL_CHECK'].includes(review.value?.status))

// 새 조직별 검토는 승인 대기 상태일 때만 결정할 수 있다 (다시 작성 요청 뒤에는 새 초안을 먼저 만든다)
const pending = computed(() => (isOrg.value && review.value?.status !== 'WAITING_APPROVAL') ? [] : (data.value?.drafts || []).filter((d) => d.decision === '승인대기'))
const decided = computed(() => (data.value?.drafts || []).filter((d) => d.decision !== '승인대기'))
const chosen = computed(() => pending.value.filter((d) => form[d.customer_id]?.choice))
const choiceCounts = computed(() => {
  const counts = { approve: 0, edit: 0, rewrite: 0, reject: 0, unselected: 0 }
  for (const d of pending.value) counts[form[d.customer_id]?.choice || 'unselected'] += 1
  return counts
})
const selectedPercent = computed(() => pending.value.length ? chosen.value.length / pending.value.length * 100 : 0)
const liveProblems = computed(() => problems())
const needsExpressionCheck = computed(() => !!data.value?.guardrail_flags?.length || pending.value.some((d) => d.manual))
const submitLabel = computed(() => {
  const counts = choiceCounts.value
  const parts = []
  if (counts.approve + counts.edit) parts.push(`승인 ${counts.approve + counts.edit}`)
  if (counts.reject) parts.push(`반려 ${counts.reject}`)
  if (counts.rewrite) parts.push(`다시 작성 ${counts.rewrite}`)
  return parts.length ? `${parts.join(' · ')} 제출` : '결정 제출'
})

function fillName(text, name) {
  return (text || '').replaceAll(PLACEHOLDER, name)
}

function highlightParts(text, name) {
  if (!name) return [{ text: text || '', highlight: false }]
  return (text || '').split(name).flatMap((part, index) => [
    ...(index ? [{ text: name, highlight: true }] : []),
    { text: part, highlight: false },
  ])
}

function problems() {
  const out = []
  for (const d of chosen.value) {
    const f = form[d.customer_id]
    if (f.choice === 'reject' && !f.reason.trim()) out.push(`${d.name}: 반려 사유를 입력하세요.`)
    if (f.choice === 'edit' && !f.text.trim()) out.push(`${d.name}: 수정한 안내문이 비어 있습니다.`)
    // 초안이 빈 고객을 그대로 승인하면 빈 안내문으로 연락 업무가 생긴다
    if (f.choice === 'approve' && d.manual) out.push(`${d.name}: 초안이 비어 있어 그대로 승인할 수 없어요. 직접 작성해 주세요.`)
  }
  return out
}

// 초안이 빈(직접 작성 필요) 고객은 한꺼번에 승인하지 않는다
function approveAll() {
  for (const d of pending.value) if (!form[d.customer_id].choice && !d.manual) form[d.customer_id].choice = 'approve'
}

async function submit() {
  if (submitting.value) return
  message.value = ''
  approvedSubmitted.value = false
  error.value = problems().join(' ')
  if (error.value || !chosen.value.length) return

  const decisions = chosen.value.map((d) => {
    const f = form[d.customer_id]
    if (f.choice === 'edit') return { customer_id: d.customer_id, action: 'approve', edited_draft: f.text }
    if (f.choice === 'reject') return { customer_id: d.customer_id, action: 'reject', reject_reason: f.reason }
    return { customer_id: d.customer_id, action: f.choice }
  })

  submitting.value = true
  if (isOrg.value) return submitOrg(decisions)
  try {
    const res = await api.decide(props.assessmentId, decisions)
    showProgress.value = true
    connect(res.rcept_no, {
      doneBefore: MAIN_PATH.slice(0, MAIN_PATH.indexOf('pb_approval')),
      onEnd: async (name, ev) => {
        submitting.value = false
        if (name === 'failed') error.value = ev.message || '처리에 실패했습니다.'
        else {
          message.value = `${decisions.length}건 결정을 반영했습니다.` + (name === 'done' ? ' 모든 고객 결정이 끝났습니다.' : '')
          approvedSubmitted.value = decisions.some((d) => d.action === 'approve')
        }
        for (const d of decisions) form[d.customer_id].choice = null
        await load()
      },
    })
  } catch (err) {
    submitting.value = false
    error.value = errorMessage(err)
  }
}

// 새 조직별 검토 제출: 진행 이벤트(SSE) 없이 바로 다시 불러온다. 버전이 바뀌었으면(다른 곳에서 먼저 결정) 새로 불러와 다시 고르게 한다
async function submitOrg(decisions) {
  try {
    await api.decideReview(props.reviewId, review.value.version, decisions)
    const rewrites = decisions.filter((d) => d.action === 'rewrite').length
    message.value = `${decisions.length}건 결정을 반영했어요.` + (rewrites ? ` 다시 작성 ${rewrites}건은 "안내문 초안 만들기"로 새 초안을 받아요.` : '')
    approvedSubmitted.value = decisions.some((d) => d.action === 'approve')
    for (const d of decisions) form[d.customer_id].choice = null
  } catch (err) {
    const st = err.response?.status
    if (st === 403) error.value = '내 담당 고객의 안내문만 결정할 수 있어요.'
    else if (st === 409 && /버전/.test(errorMessage(err))) error.value = '그사이 검토 내용이 바뀌었어요. 최신 내용으로 다시 불러왔으니 확인 후 다시 제출해 주세요.'
    else error.value = errorMessage(err)
  } finally {
    submitting.value = false
    await load({ keepError: true })
  }
}
</script>

<template>
  <RouterLink :to="isOrg ? (stockCode ? { name: 'risk-detail', params: { stockCode } } : '/alerts') : { name: 'alert-detail', params: { assessmentId } }" class="back-link">
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6" /></svg>
    {{ isOrg ? (stockCode ? '종목 상세' : '경보 목록') : '경보 상세' }}
  </RouterLink>
  <div class="page-head">
    <div>
      <h1>안내문 검토</h1>
      <div class="review-meta">
        <strong v-if="alertInfo?.company?.name">{{ alertInfo.company.name }}</strong>
        <StageBadge v-if="alertInfo?.stage || alertInfo?.stages?.length || alertInfo?.level" :stage="alertInfo.stage" :stages="alertInfo.stages" :level="alertInfo.level" :legacy="alertInfo.legacy" :available="alertInfo.stage_available" />
        <span class="muted"><template v-if="alertInfo?.disclosure?.title">{{ alertInfo.disclosure.title }} · </template>내 담당 고객 {{ data?.drafts.length ?? 0 }}명 · 승인 대기 {{ pending.length }}명</span>
      </div>
    </div>
  </div>

  <div class="review-layout" :class="{ 'has-pending': pending.length }">
    <div class="review-main">
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <p v-if="message" class="success" role="status">{{ message }} <RouterLink v-if="approvedSubmitted" to="/tasks">연락 업무 보기</RouterLink></p>
      <section v-if="showProgress" class="card" aria-label="처리 진행"><RunProgress :state="runState" /></section>

      <!-- 새 조직별 검토: 승인 대기가 아닐 때 지금 상태와 다음 할 일 -->
      <div v-if="reviewGuide" class="banner" :class="reviewGuide.tone" role="status">
        <span class="banner-text">
          <strong>{{ reviewGuide.title }}</strong>
          <span>{{ reviewGuide.text }}</span>
        </span>
        <span v-if="canRun" class="banner-action">
          <button type="button" class="small" :disabled="running" @click="runDrafts">{{ running ? '만드는 중…' : '안내문 초안 만들기' }}</button>
        </span>
      </div>

      <div v-if="data && data.drafts.length && !reviewGuide" class="banner" :class="needsExpressionCheck ? 'warn' : 'ok'" role="status">
        <span class="banner-icon">
          <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
            <template v-if="needsExpressionCheck"><path d="M12 8v5" /><path d="M12 16h.01" /></template>
            <path v-else d="M9 12l2 2 4-4" />
          </svg>
        </span>
        <div v-if="needsExpressionCheck" class="banner-text">
          <strong>금지 표현 검사 확인 필요</strong>
          <span v-if="pending.some((d) => d.manual)">직접 작성이 필요한 안내문이 있어요. 고객별 안내를 확인해 주세요.</span>
          <span v-for="(flag, i) in data.guardrail_flags" :key="i">{{ flag }}</span>
        </div>
        <div v-else class="banner-text">
          <strong>금지 표현 검사 통과</strong>
          <span>초안 {{ data.drafts.length }}개 모두 금지 표현(매수·매도 권유, 단정 표현 등)이 없어요</span>
        </div>
      </div>

      <article v-for="d in pending" :key="d.customer_id" class="card draft" :class="[form[d.customer_id].choice, { manual: d.manual && !form[d.customer_id].choice }]" :aria-label="`${d.name} 안내문 검토`">
        <div class="draft-head">
          <span class="avatar sm" aria-hidden="true">{{ (d.name || '-').slice(0, 1) }}</span>
          <div class="customer-text">
            <strong>{{ d.name }}</strong>
            <span class="sub">{{ d.customer_id }} · {{ d.risk_profile }}</span>
          </div>
          <span v-if="d.manual" class="badge tone-warn">직접 작성 필요</span>
        </div>

        <p v-if="form[d.customer_id].choice" class="chip soft" role="status">{{ CHOICES[form[d.customer_id].choice] }} 선택 · {{ submitting ? '제출 처리 중' : '아직 제출 안 함' }}</p>
        <p v-if="d.manual" class="notice warn">
          금지 표현 검사를 여러 번 통과하지 못해 AI 초안이 비어 있어요. "수정 후 승인"으로 직접 작성하거나 "다시 작성"을 눌러 주세요.
        </p>
        <div v-if="form[d.customer_id].choice === 'edit'" class="edit-body">
          <label :for="`edit-${d.customer_id}`">안내문 수정 <span class="edit-hint">· <code>{고객명}</code> 자리표시는 그대로 두세요</span></label>
          <textarea :id="`edit-${d.customer_id}`" v-model="form[d.customer_id].text" :disabled="submitting" rows="5" class="edit-text" />
          <span :id="`preview-${d.customer_id}`" class="preview-label">미리보기</span>
          <div class="message" :aria-labelledby="`preview-${d.customer_id}`"><template v-for="(part, i) in highlightParts(fillName(form[d.customer_id].text, d.name), d.name)" :key="i"><mark v-if="part.highlight">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></div>
        </div>
        <div v-else-if="!d.manual" class="message" :class="{ rejected: form[d.customer_id].choice === 'reject' }"><template v-for="(part, i) in highlightParts(d.preview, d.name)" :key="i"><mark v-if="part.highlight">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></div>

        <div v-if="form[d.customer_id].choice === 'reject'">
          <label :for="`reason-${d.customer_id}`">반려 사유 <span class="required">(필수)</span></label>
          <textarea :id="`reason-${d.customer_id}`" v-model="form[d.customer_id].reason" :disabled="submitting" rows="2" class="reject-reason" placeholder="예: 고객이 이미 공시 내용을 알고 있음" required />
        </div>

        <div class="choices" role="group" :aria-label="`${d.name} 결정`">
          <button
            v-for="(label, key) in CHOICES"
            :key="key"
            type="button"
            class="choice"
            :class="[key, { selected: form[d.customer_id].choice === key }]"
            :aria-pressed="form[d.customer_id].choice === key"
            :disabled="submitting || (key === 'approve' && d.manual)"
            :title="key === 'approve' && d.manual ? '초안이 비어 있어 그대로 승인할 수 없어요' : undefined"
            @click="form[d.customer_id].choice = form[d.customer_id].choice === key ? null : key"
          >
            <svg v-if="form[d.customer_id].choice === key" aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5 9-10" /></svg>
            {{ label }}
          </button>
        </div>
      </article>

      <section v-if="decided.length" class="card flush decided" aria-label="결정 완료">
        <div class="card-head"><h2>결정 완료 {{ decided.length }}명</h2></div>
        <div class="table-wrap">
          <table class="stack-table">
            <thead class="sr-only"><tr><th scope="col">고객</th><th scope="col">결정</th><th scope="col">결정 내용</th></tr></thead>
            <tbody>
              <tr v-for="d in decided" :key="d.customer_id">
                <td><strong>{{ d.name }}</strong><div class="sub">{{ d.customer_id }}</div></td>
                <td><StatusBadge :status="d.decision" /></td>
                <td>
                  <span v-if="d.reject_reason">사유: {{ d.reject_reason }}</span>
                  <span v-else-if="d.edited_draft" class="muted">수정본: {{ fillName(d.edited_draft, d.name) }}</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>
      <section v-else-if="pending.length" class="card decided-empty" aria-label="결정 완료">
        <h2>결정 완료 <span class="muted">0명</span></h2>
        <span class="muted">제출한 결정은 여기에 쌓여요 (승인·반려 사유·수정본)</span>
      </section>

      <p v-if="data && !data.drafts.length && !canRun" class="card empty">내 담당 고객의 안내문이 없습니다.</p>
    </div>

    <aside v-if="pending.length" class="card summary" aria-label="결정 요약">
      <h2>결정 요약</h2>
      <div class="selection-count"><strong>{{ chosen.length }}</strong><span class="muted">/ {{ pending.length }}명 선택함</span></div>
      <div class="progress-bar" role="progressbar" aria-label="결정 선택 현황" :aria-valuenow="chosen.length" :aria-valuemax="pending.length" :aria-valuemin="0"><span :style="{ width: `${selectedPercent}%` }"></span></div>
      <ul class="summary-list">
        <li v-for="(label, key) in CHOICES" :key="key">
          <span><span class="dot" :class="`dot-${key}`" aria-hidden="true"></span>{{ label }}</span><strong>{{ choiceCounts[key] }}</strong>
        </li>
        <li class="muted"><span><span class="dot dot-unselected" aria-hidden="true"></span>아직 고르지 않음</span><strong>{{ choiceCounts.unselected }}</strong></li>
      </ul>
      <div v-if="liveProblems.length" class="problem-list" aria-live="polite">
        <p v-for="problem in liveProblems" :key="problem" class="notice warn">{{ problem }}</p>
      </div>
      <button type="button" class="ghost block" :disabled="submitting || !pending.length" @click="approveAll">남은 고객 모두 승인 선택</button>
      <button type="button" class="block submit-decisions" :disabled="!chosen.length || submitting || liveProblems.length > 0" @click="submit">
        {{ submitting ? '반영 중…' : submitLabel }}
      </button>
      <p class="muted summary-note">승인한 고객만 연락 업무가 만들어져요. 반려한 고객은 업무 없이 사유만 기록돼요.</p>
    </aside>
    <div v-if="pending.length" class="mobile-submit" aria-label="선택한 결정 제출">
      <!-- 제출이 막혀 있으면 이유를 띠 안에서 바로 보여 준다 (요약 칸은 좁은 화면에서 한참 아래에 있다) -->
      <span v-if="liveProblems.length" class="mobile-problem" role="status">{{ liveProblems[0] }}<template v-if="liveProblems.length > 1"> 외 {{ liveProblems.length - 1 }}건</template></span>
      <span v-else>선택 {{ chosen.length }}건</span>
      <button type="button" :disabled="!chosen.length || submitting || liveProblems.length > 0" :aria-label="submitLabel" @click="submit">{{ submitting ? '반영 중…' : '결정 제출' }}</button>
    </div>
  </div>
</template>

<style scoped>
.back-link { min-height: 44px; }
.review-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; font-size: 14px; }
.review-layout { display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start; }
.review-main { flex: 999 1 600px; min-width: 0; display: flex; flex-direction: column; gap: 16px; }
.review-main > .card, .review-main > .banner, .review-main > .error, .review-main > .success { margin: 0; }
.banner-text { min-width: 0; overflow-wrap: anywhere; }
.draft { border-radius: var(--radius); display: flex; flex-direction: column; gap: 16px; }
.draft.edit { border: 1.5px solid var(--navy); }
.draft.approve, .draft.rewrite { border: 1.5px solid var(--blue); }
.draft.reject { border: 1.5px solid var(--muted); }
.draft.manual { border: 1.5px solid #f7c9a6; }
.choice:disabled { opacity: .45; }
.draft-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.customer-text { display: flex; flex-direction: column; line-height: 1.3; min-width: 0; overflow-wrap: anywhere; }
.customer-text strong { font-size: 16px; }
.draft > .notice { margin: 0; }
.edit-body { display: flex; flex-direction: column; gap: 8px; }
.edit-body label { margin: 0; }
.edit-hint { color: var(--muted); font-weight: 400; }
code { background: var(--neutral-soft); padding: 1px 6px; border-radius: 6px; font-size: 13px; }
.edit-text { min-height: 128px; padding: 16px; line-height: 1.65; }
.preview-label { margin-top: 4px; font-size: 13px; font-weight: 600; color: var(--muted); }
.message { color: #2a3554; font-size: 15px; line-height: 1.7; overflow-wrap: anywhere; }
.message.rejected, .message.rejected mark { color: var(--muted); }
.message.rejected mark { background: none; font-weight: inherit; padding: 0; }
.required { color: var(--blue-strong); }
.reject-reason { min-height: 72px; padding: 12px 16px; background: var(--surface-2); }
.decided td { font-size: 14px; }
.decided td:first-child { width: 140px; }
.decided td:nth-child(2) { width: 100px; }
.decided td:last-child { overflow-wrap: anywhere; }
@media (max-width: 640px) { .decided td:first-child, .decided td:nth-child(2) { width: auto; } }
.decided-empty { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; border-style: dashed; }
.decided-empty h2 { margin: 0; }
.decided-empty > span, .decided-empty h2 span { font-size: 14px; font-weight: 500; }
.summary { flex: 1 1 300px; min-width: 0; position: sticky; top: 96px; padding: 24px; box-shadow: var(--shadow-md); display: flex; flex-direction: column; gap: 16px; }
.summary h2, .summary .progress-bar { margin: 0; }
.selection-count { display: flex; align-items: baseline; gap: 8px; }
.selection-count strong { font-size: 34px; font-weight: 800; line-height: 1.2; font-variant-numeric: tabular-nums; }
.summary-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
.summary-list li { display: flex; justify-content: space-between; gap: 12px; font-size: 14px; }
.summary-list li > span { display: flex; align-items: center; gap: 8px; }
.dot-approve { background: var(--blue); }
.dot-edit { background: var(--navy); }
.dot-rewrite { border: 2px solid var(--blue); }
.dot-reject { background: var(--muted); }
.dot-unselected { background: var(--border-strong); }
.problem-list { display: flex; flex-direction: column; gap: 8px; }
.problem-list .notice { margin: 0; padding: 12px 16px; border-radius: var(--radius-md); font-size: 13px; }
.summary button { white-space: normal; }
.submit-decisions { min-height: 56px; font-size: 16px; }
.summary-note { margin: 0; font-size: 13px; line-height: 1.55; }
.mobile-submit { display: none; }
.mobile-problem { flex: 1; min-width: 0; color: var(--warn); font-size: 13px; font-weight: 600; line-height: 1.4; overflow-wrap: anywhere; }
.mobile-submit button { flex: none; }
.draft > .chip { margin: 0; align-self: flex-start; white-space: normal; }
@media (max-width: 900px) {
  .summary { position: static; }
  .summary .submit-decisions { display: none; }
  .has-pending :is(button, textarea) { scroll-margin-block: 180px 96px; }
  .has-pending { padding-bottom: calc(96px + env(safe-area-inset-bottom)); }
  .mobile-submit { display: flex; align-items: center; justify-content: space-between; gap: 12px; position: fixed; z-index: 20; inset: auto 0 0; padding: 12px 16px calc(12px + env(safe-area-inset-bottom)); background: var(--surface); color: var(--text); border-top: 1px solid var(--border-strong); box-shadow: var(--shadow-md); }
}
</style>

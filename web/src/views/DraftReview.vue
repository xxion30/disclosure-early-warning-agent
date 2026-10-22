<script setup>
// 안내문 검토: 고객마다 결정을 고른 뒤 한 번에 제출한다 (PB 1명의 결정 = Graph 재개 1회).
import { computed, onMounted, reactive, ref } from 'vue'

import { api, errorMessage } from '../api/client'
import LevelBadge from '../components/LevelBadge.vue'
import RunProgress from '../components/RunProgress.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useRunEvents } from '../composables/useRunEvents'
import { MAIN_PATH } from '../labels'

const props = defineProps({ assessmentId: { type: String, required: true } })

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
const form = reactive({}) // customer_id → { choice, text, reason }
const { state: runState, connect } = useRunEvents()
const showProgress = ref(false)

async function load() {
  error.value = ''
  try {
    data.value = await api.getDrafts(props.assessmentId)
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
    alertInfo.value = await api.getAlert(props.assessmentId)
  } catch {
    // 부가 정보가 없어도 안내문은 검토할 수 있다
  }
})

const pending = computed(() => (data.value?.drafts || []).filter((d) => d.decision === '승인대기'))
const decided = computed(() => (data.value?.drafts || []).filter((d) => d.decision !== '승인대기'))
const chosen = computed(() => pending.value.filter((d) => form[d.customer_id]?.choice))
const choiceCounts = computed(() => {
  const counts = { approve: 0, edit: 0, rewrite: 0, reject: 0, unselected: 0 }
  for (const d of pending.value) counts[form[d.customer_id]?.choice || 'unselected'] += 1
  return counts
})
const selectedPercent = computed(() => pending.value.length ? chosen.value.length / pending.value.length * 100 : 0)
const liveProblems = computed(() => problems())

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
  message.value = ''
  error.value = problems().join(' ')
  if (error.value || !chosen.value.length) return

  const decisions = chosen.value.map((d) => {
    const f = form[d.customer_id]
    if (f.choice === 'edit') return { customer_id: d.customer_id, action: 'approve', edited_draft: f.text }
    if (f.choice === 'reject') return { customer_id: d.customer_id, action: 'reject', reject_reason: f.reason }
    return { customer_id: d.customer_id, action: f.choice }
  })

  submitting.value = true
  try {
    const res = await api.decide(props.assessmentId, decisions)
    showProgress.value = true
    connect(res.rcept_no, {
      doneBefore: MAIN_PATH.slice(0, MAIN_PATH.indexOf('pb_approval')),
      onEnd: async (name, ev) => {
        submitting.value = false
        if (name === 'failed') error.value = ev.message || '처리에 실패했습니다.'
        else message.value = `${decisions.length}건 결정을 반영했습니다.` + (name === 'done' ? ' 모든 고객 결정이 끝났습니다.' : '')
        for (const d of decisions) form[d.customer_id].choice = null
        await load()
      },
    })
  } catch (err) {
    submitting.value = false
    error.value = errorMessage(err)
  }
}
</script>

<template>
  <RouterLink :to="{ name: 'alert-detail', params: { assessmentId } }" class="back-link">
    <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 18l-6-6 6-6" /></svg>
    경보 상세
  </RouterLink>
  <div class="page-head">
    <div>
      <h1>안내문 검토</h1>
      <div class="review-meta">
        <strong v-if="alertInfo?.company?.name">{{ alertInfo.company.name }}</strong>
        <LevelBadge v-if="alertInfo?.level" :level="alertInfo.level" />
        <span class="muted"><template v-if="alertInfo?.disclosure?.title">{{ alertInfo.disclosure.title }} · </template>내 담당 고객 {{ data?.drafts.length ?? 0 }}명 · 승인 대기 {{ pending.length }}명</span>
      </div>
    </div>
  </div>

  <div class="review-layout">
    <div class="review-main">
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <p v-if="message" class="success" role="status">{{ message }}</p>
      <section v-if="showProgress" class="card" aria-label="처리 진행"><RunProgress :state="runState" /></section>

      <div v-if="data" class="banner" :class="data.guardrail_flags?.length ? 'warn' : 'ok'" role="status">
        <span class="banner-icon">
          <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
            <template v-if="data.guardrail_flags?.length"><path d="M12 8v5" /><path d="M12 16h.01" /></template>
            <path v-else d="M9 12l2 2 4-4" />
          </svg>
        </span>
        <div v-if="data.guardrail_flags?.length" class="banner-text">
          <strong>가드레일 확인 필요</strong>
          <span v-for="(flag, i) in data.guardrail_flags" :key="i">{{ flag }}</span>
        </div>
        <div v-else class="banner-text">
          <strong>가드레일 통과</strong>
          <span>초안 {{ data.drafts.length }}개 모두 금지 표현(매수·매도 권유, 단정 표현 등)이 없어요</span>
        </div>
      </div>

      <article v-for="d in pending" :key="d.customer_id" class="card draft" :class="[form[d.customer_id].choice, { manual: d.manual && !form[d.customer_id].choice }]" :aria-label="`${d.name} 안내문 검토`">
        <div class="draft-head">
          <span class="prio-pill" :class="`prio-${d.priority}`">{{ d.priority }}</span>
          <span class="avatar sm" aria-hidden="true">{{ (d.name || '-').slice(0, 1) }}</span>
          <div class="customer-text">
            <strong>{{ d.name }}</strong>
            <span class="sub">{{ d.customer_id }} · {{ d.risk_profile }}</span>
          </div>
          <span v-if="d.manual" class="badge tone-warn">직접 작성 필요</span>
          <span class="impact-pill" :class="`impact-${d.impact_level}`">영향 {{ d.impact_level }} · {{ d.impact_score }}점</span>
        </div>

        <p v-if="d.manual" class="notice">
          가드레일 검사를 여러 번 통과하지 못해 AI 초안이 비어 있어요. "수정 후 승인"으로 직접 작성하거나 "다시 작성"을 눌러 주세요.
        </p>
        <div v-if="form[d.customer_id].choice === 'edit'" class="edit-body">
          <label :for="`edit-${d.customer_id}`">안내문 수정 <span class="edit-hint">· <code>{고객명}</code> 자리표시는 그대로 두세요</span></label>
          <textarea :id="`edit-${d.customer_id}`" v-model="form[d.customer_id].text" rows="5" class="edit-text" />
          <span :id="`preview-${d.customer_id}`" class="preview-label">미리보기</span>
          <div class="message" :aria-labelledby="`preview-${d.customer_id}`"><template v-for="(part, i) in highlightParts(fillName(form[d.customer_id].text, d.name), d.name)" :key="i"><mark v-if="part.highlight">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></div>
        </div>
        <div v-else-if="!d.manual" class="message" :class="{ rejected: form[d.customer_id].choice === 'reject' }"><template v-for="(part, i) in highlightParts(d.preview, d.name)" :key="i"><mark v-if="part.highlight">{{ part.text }}</mark><template v-else>{{ part.text }}</template></template></div>

        <div v-if="form[d.customer_id].choice === 'reject'">
          <label :for="`reason-${d.customer_id}`">반려 사유 <span class="required">(필수)</span></label>
          <textarea :id="`reason-${d.customer_id}`" v-model="form[d.customer_id].reason" rows="2" class="reject-reason" placeholder="예: 고객이 이미 공시 내용을 알고 있음" required />
        </div>

        <div class="choices" role="group" :aria-label="`${d.name} 결정`">
          <button
            v-for="(label, key) in CHOICES"
            :key="key"
            type="button"
            class="choice"
            :class="[key, { selected: form[d.customer_id].choice === key }]"
            :aria-pressed="form[d.customer_id].choice === key"
            :disabled="key === 'approve' && d.manual"
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
          <table>
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

      <p v-if="data && !data.drafts.length" class="card empty">내 담당 고객의 안내문이 없습니다.</p>
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
        <p v-for="problem in liveProblems" :key="problem" class="notice">{{ problem }}</p>
      </div>
      <button type="button" class="ghost block" :disabled="!pending.length" @click="approveAll">남은 고객 모두 승인 선택</button>
      <button type="button" class="block submit-decisions" :disabled="!chosen.length || submitting" @click="submit">
        {{ submitting ? '반영 중…' : `결정 제출 (${chosen.length}건)` }}
      </button>
      <p class="muted summary-note">승인한 고객만 연락 업무가 만들어져요. 반려한 고객은 업무 없이 사유만 기록돼요.</p>
    </aside>
  </div>
</template>

<style scoped>
.back-link { min-height: 44px; }
.back-link:focus-visible { outline: 3px solid var(--blue); outline-offset: 4px; }
.review-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-top: 8px; font-size: 14px; }
.review-layout { display: flex; flex-wrap: wrap; gap: 20px; align-items: flex-start; }
.review-main { flex: 999 1 600px; min-width: 0; display: flex; flex-direction: column; gap: 16px; }
.review-main > .card, .review-main > .banner, .review-main > .error, .review-main > .success { margin: 0; }
.banner-text { min-width: 0; overflow-wrap: anywhere; }
.draft { border-radius: var(--radius); display: flex; flex-direction: column; gap: 16px; }
.draft.edit { border: 1.5px solid var(--navy); }
.draft.approve, .draft.rewrite { border: 1.5px solid var(--blue); }
.draft.reject { border: 1.5px solid #e5a3a3; }
.draft.manual { border: 1.5px solid #f7c9a6; }
.choice:disabled { opacity: .45; }
.draft-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.customer-text { display: flex; flex-direction: column; line-height: 1.3; min-width: 0; overflow-wrap: anywhere; }
.customer-text strong { font-size: 16px; }
.impact-pill { margin-left: auto; padding: 5px 12px; border-radius: var(--pill); background: var(--neutral-soft); color: var(--muted); font-size: 13px; font-weight: 700; white-space: nowrap; }
.impact-pill.impact-높음 { background: var(--bad-soft); color: var(--bad); }
.impact-pill.impact-보통 { background: var(--warn-soft); color: var(--warn); }
.draft > .notice { margin: 0; }
.edit-body { display: flex; flex-direction: column; gap: 8px; }
.edit-body label { margin: 0; }
.edit-hint { color: var(--muted); font-weight: 400; }
code { background: var(--neutral-soft); padding: 1px 6px; border-radius: 6px; font-size: 13px; }
.edit-text { min-height: 128px; padding: 14px 16px; line-height: 1.65; }
.preview-label { margin-top: 4px; font-size: 13px; font-weight: 600; color: var(--muted); }
.message { color: #2a3554; font-size: 15px; line-height: 1.7; overflow-wrap: anywhere; }
.message.rejected, .message.rejected mark { color: var(--faint); text-decoration: line-through; text-decoration-color: var(--border-strong); }
.message.rejected mark { background: none; font-weight: inherit; padding: 0; }
.required { color: var(--bad); }
.reject-reason { min-height: 72px; padding: 12px 16px; background: var(--surface-2); }
.decided table { min-width: 560px; }
.decided td { font-size: 14px; }
.decided td:first-child { width: 140px; }
.decided td:nth-child(2) { width: 100px; }
.decided td:last-child { overflow-wrap: anywhere; }
.decided-empty { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; border-style: dashed; }
.decided-empty h2 { margin: 0; }
.decided-empty > span, .decided-empty h2 span { font-size: 14px; font-weight: 500; }
.summary { flex: 1 1 300px; min-width: 0; position: sticky; top: 96px; padding: 24px; box-shadow: var(--shadow-md); display: flex; flex-direction: column; gap: 18px; }
.summary h2, .summary .progress-bar { margin: 0; }
.selection-count { display: flex; align-items: baseline; gap: 6px; }
.selection-count strong { font-size: 34px; font-weight: 800; line-height: 1.2; font-variant-numeric: tabular-nums; }
.summary-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.summary-list li { display: flex; justify-content: space-between; gap: 12px; font-size: 14px; }
.summary-list li > span { display: flex; align-items: center; gap: 8px; }
.dot-approve { background: var(--blue); }
.dot-edit { background: var(--navy); }
.dot-rewrite { border: 2px solid var(--blue); }
.dot-reject { background: var(--critical); }
.dot-unselected { background: var(--border-strong); }
.problem-list { display: flex; flex-direction: column; gap: 8px; }
.problem-list .notice { margin: 0; padding: 12px 14px; border-radius: 14px; font-size: 13px; }
.summary button { white-space: normal; }
.submit-decisions { min-height: 56px; font-size: 16px; }
.summary-note { margin: 0; font-size: 13px; line-height: 1.55; }
@media (max-width: 900px) {
  .summary { position: static; }
}
@media (max-width: 540px) {
  .impact-pill { margin-left: 0; }
}
</style>

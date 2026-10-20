<script setup>
// 안내문 검토: 고객마다 결정을 고른 뒤 한 번에 제출한다 (PB 1명의 결정 = Graph 재개 1회).
import { computed, onMounted, reactive, ref } from 'vue'

import { api, errorMessage } from '../api/client'
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

const pending = computed(() => (data.value?.drafts || []).filter((d) => d.decision === '승인대기'))
const decided = computed(() => (data.value?.drafts || []).filter((d) => d.decision !== '승인대기'))
const chosen = computed(() => pending.value.filter((d) => form[d.customer_id]?.choice))

function fillName(text, name) {
  return (text || '').replaceAll(PLACEHOLDER, name)
}

function problems() {
  const out = []
  for (const d of chosen.value) {
    const f = form[d.customer_id]
    if (f.choice === 'reject' && !f.reason.trim()) out.push(`${d.name}: 반려 사유를 입력하세요.`)
    if (f.choice === 'edit' && !f.text.trim()) out.push(`${d.name}: 수정한 안내문이 비어 있습니다.`)
  }
  return out
}

function approveAll() {
  for (const d of pending.value) if (!form[d.customer_id].choice) form[d.customer_id].choice = 'approve'
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
  <p><RouterLink :to="{ name: 'alert-detail', params: { assessmentId } }">← 경보 상세</RouterLink></p>
  <div class="page-head">
    <div>
      <h1>안내문 검토</h1>
      <p class="muted">내 담당 고객 {{ data?.drafts.length ?? 0 }}명 · 승인 대기 {{ pending.length }}명</p>
    </div>
    <div class="row">
      <button class="ghost" :disabled="!pending.length" @click="approveAll">남은 고객 모두 승인 선택</button>
      <button :disabled="!chosen.length || submitting" @click="submit">
        {{ submitting ? '반영 중…' : `결정 제출 (${chosen.length}건)` }}
      </button>
    </div>
  </div>

  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="message" class="success">{{ message }}</p>
  <div v-if="showProgress" class="card"><RunProgress :state="runState" /></div>

  <div v-if="data?.guardrail_flags?.length" class="card">
    <h2>가드레일 결과</h2>
    <p class="notice" v-for="f in data.guardrail_flags" :key="f">{{ f }}</p>
  </div>
  <p v-else-if="data" class="success">가드레일: 금지 표현(매수·매도 권유 등) 없음</p>

  <div v-for="d in pending" :key="d.customer_id" class="card draft">
    <div class="row head">
      <span class="prio" :class="`prio-${d.priority}`">{{ d.priority }}</span>
      <strong>{{ d.name }}</strong>
      <small>{{ d.customer_id }} · {{ d.risk_profile }} · 영향 {{ d.impact_level }} ({{ d.impact_score }}점)</small>
    </div>

    <template v-if="form[d.customer_id].choice === 'edit'">
      <label>안내문 수정 — <code>{고객명}</code> 자리표시는 그대로 두세요</label>
      <textarea v-model="form[d.customer_id].text" rows="5" />
      <label style="margin-top: 8px">미리보기</label>
      <div class="message">{{ fillName(form[d.customer_id].text, d.name) }}</div>
    </template>
    <div v-else class="message">{{ d.preview }}</div>

    <div v-if="form[d.customer_id].choice === 'reject'" style="margin-top: 8px">
      <label>반려 사유 (필수)</label>
      <textarea v-model="form[d.customer_id].reason" rows="2" placeholder="예: 고객이 이미 공시 내용을 알고 있음" />
    </div>

    <div class="row choices">
      <button
        v-for="(label, key) in CHOICES"
        :key="key"
        class="small"
        :class="{ ghost: form[d.customer_id].choice !== key, danger: key === 'reject' && form[d.customer_id].choice === key }"
        @click="form[d.customer_id].choice = form[d.customer_id].choice === key ? null : key"
      >
        {{ label }}
      </button>
    </div>
  </div>

  <div v-if="decided.length" class="card">
    <h2>결정 완료</h2>
    <table>
      <tbody>
        <tr v-for="d in decided" :key="d.customer_id">
          <td style="width: 160px"><strong>{{ d.name }}</strong> <small>{{ d.customer_id }}</small></td>
          <td style="width: 90px"><StatusBadge :status="d.decision" /></td>
          <td>
            <span v-if="d.reject_reason">사유: {{ d.reject_reason }}</span>
            <span v-else-if="d.edited_draft" class="muted">수정본: {{ fillName(d.edited_draft, d.name) }}</span>
          </td>
        </tr>
      </tbody>
    </table>
  </div>

  <p v-if="data && !data.drafts.length" class="card empty">내 담당 고객의 안내문이 없습니다.</p>
</template>

<style scoped>
.draft .head { margin-bottom: 10px; }
.choices { margin-top: 12px; }
code { background: #eef0f3; padding: 0 4px; border-radius: 4px; }
</style>

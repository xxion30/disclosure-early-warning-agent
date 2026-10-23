<script setup>
import { computed, onMounted, reactive, ref } from 'vue'

import { api, errorMessage } from '../api/client'
import LevelBadge from '../components/LevelBadge.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { formatTime } from '../labels'

const STATUS_TABS = [
  { value: '', label: '전체' },
  { value: '승인', label: '연락 전' },
  { value: '보류', label: '보류' },
  { value: '연락완료', label: '연락완료' },
]
const CHANGEABLE = ['승인', '보류'] // 이 상태에서만 연락완료·보류로 바꿀 수 있다 (API와 같은 규칙)

const tasks = ref([])
const statusFilter = ref('')
const error = ref('')
const loading = ref(false)
const notes = reactive({}) // task_id → 입력 중인 메모
const open = reactive({}) // task_id → 안내문 펼침

const filteredTasks = computed(() => tasks.value.filter((t) => !statusFilter.value || t.status === statusFilter.value))
const statusCounts = computed(() => tasks.value.reduce((counts, t) => {
  counts[t.status] = (counts[t.status] || 0) + 1
  return counts
}, {}))
const remaining = computed(() => tasks.value.filter((t) => CHANGEABLE.includes(t.status)).length)
const done = computed(() => statusCounts.value['연락완료'] || 0)
const donePercent = computed(() => tasks.value.length ? Math.round(done.value / tasks.value.length * 100) : 0)

async function load() {
  error.value = ''
  loading.value = true
  try {
    tasks.value = await api.listTasks({})
    for (const t of tasks.value) if (notes[t.task_id] === undefined) notes[t.task_id] = t.result_note || ''
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}
onMounted(load)

async function change(task, status) {
  error.value = ''
  try {
    const updated = await api.updateTask(task.task_id, { status, note: notes[task.task_id] })
    Object.assign(task, updated)
  } catch (err) {
    error.value = errorMessage(err)
  }
}
</script>

<template>
  <div class="page-head">
    <div>
      <p class="eyebrow">PB 승인으로 만들어진 내 업무</p>
      <h1>연락 업무</h1>
    </div>
    <div class="row task-filters">
      <div class="tabs" role="group" aria-label="업무 상태">
        <button v-for="tab in STATUS_TABS" :key="tab.value" type="button" :class="{ active: statusFilter === tab.value }" :aria-pressed="statusFilter === tab.value" @click="statusFilter = tab.value">
          {{ tab.label }}<span class="count">{{ tab.value ? statusCounts[tab.value] || 0 : tasks.length }}</span>
        </button>
      </div>
      <button type="button" class="ghost" :disabled="loading" @click="load">
        <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 3v6h-6" /></svg>
        새로고침
      </button>
    </div>
  </div>
  <p v-if="error" class="error task-error" role="alert">{{ error }}</p>

  <div v-if="tasks.length" class="summary-band">
    <div class="remaining-summary">
      <span class="muted">아직 연락하지 않은 고객</span>
      <div v-if="remaining" class="remaining-count"><strong>{{ remaining }}</strong><span class="muted">명 남았어요</span></div>
      <strong v-else class="all-done">모든 연락을 마쳤어요</strong>
    </div>
    <div class="completion-summary">
      <div class="completion-label muted"><span>처리 {{ done }} / {{ tasks.length }}건</span><span>{{ donePercent }}%</span></div>
      <div class="progress-bar" role="progressbar" aria-label="연락 업무 완료 현황" :aria-valuenow="done" :aria-valuemin="0" :aria-valuemax="tasks.length"><span :style="{ width: `${done / tasks.length * 100}%` }"></span></div>
    </div>
  </div>

  <div class="task-list">
    <article v-for="t in filteredTasks" :key="t.task_id" class="card task-card" :class="{ held: t.status === '보류', completed: t.status === '연락완료' }" :aria-label="`${t.customer_name} 연락 업무`">
      <div class="task-head">
        <span class="avatar" :class="{ soft: t.status === '연락완료' }" aria-hidden="true">{{ (t.customer_name || '-').slice(0, 1) }}</span>
        <div class="customer-text"><strong>{{ t.customer_name }}</strong><span class="sub">{{ t.customer_id }}</span></div>
        <div class="task-corp">
          <span class="corp-mark" aria-hidden="true">{{ (t.corp_name || '-').slice(0, 1) }}</span>
          <strong>{{ t.corp_name || '-' }}</strong>
          <LevelBadge v-if="t.status !== '연락완료'" :level="t.level" />
          <RouterLink :to="{ name: 'alert-detail', params: { assessmentId: t.assessment_id } }">경보 #{{ t.assessment_id }}</RouterLink>
        </div>
        <div class="task-status"><StatusBadge :status="t.status" /><span class="sub">{{ formatTime(t.updated_at || t.created_at) }}</span></div>
      </div>

      <div v-if="open[t.task_id]" :id="`task-message-${t.task_id}`" class="message approved-message">
        <span class="message-label">승인한 안내문</span>
        <p>{{ t.message_preview }}</p>
      </div>

      <div v-if="CHANGEABLE.includes(t.status)" class="task-footer">
        <div class="memo-field">
          <label :for="`task-note-${t.task_id}`">결과 메모</label>
          <textarea :id="`task-note-${t.task_id}`" v-model="notes[t.task_id]" rows="2" placeholder="통화 결과 등" />
        </div>
        <div class="task-actions">
          <button type="button" class="message-toggle" :aria-expanded="!!open[t.task_id]" :aria-controls="`task-message-${t.task_id}`" @click="open[t.task_id] = !open[t.task_id]">
            {{ open[t.task_id] ? '안내문 닫기' : '안내문 보기' }}
            <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path :d="open[t.task_id] ? 'M6 15l6-6 6 6' : 'M6 9l6 6 6-6'" /></svg>
          </button>
          <button type="button" class="ghost" @click="change(t, '보류')">{{ t.status === '보류' ? '메모 저장' : '보류' }}</button>
          <button type="button" @click="change(t, '연락완료')">
            <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5 9-10" /></svg>
            연락완료
          </button>
        </div>
      </div>
      <template v-else>
        <p v-if="t.result_note" class="result-note">메모: {{ t.result_note }}</p>
        <button type="button" class="message-toggle compact-toggle" :aria-expanded="!!open[t.task_id]" :aria-controls="`task-message-${t.task_id}`" @click="open[t.task_id] = !open[t.task_id]">
          {{ open[t.task_id] ? '안내문 닫기' : '안내문 보기' }}
          <svg aria-hidden="true" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path :d="open[t.task_id] ? 'M6 15l6-6 6 6' : 'M6 9l6 6 6-6'" /></svg>
        </button>
      </template>
    </article>
    <div v-if="!filteredTasks.length" class="card"><div class="empty">{{ statusFilter ? '이 상태의 업무가 없습니다.' : '연락 업무가 없습니다.' }}</div></div>
  </div>
</template>

<style scoped>
.task-filters .tabs { margin-bottom: 0; }
.task-filters .tabs button { min-height: 44px; padding: 0 18px; font-size: 14px; }
.task-filters .tabs button:not(.active) .count { color: inherit; }
.task-error { margin: 0 0 20px; }
.remaining-summary { flex: 1; min-width: min(220px, 100%); display: flex; flex-direction: column; gap: 4px; }
.remaining-summary > .muted { font-size: 14px; }
.remaining-count { display: flex; align-items: baseline; gap: 6px; }
.remaining-count strong { font-size: 30px; font-weight: 800; font-variant-numeric: tabular-nums; }
.all-done { font-size: 24px; font-weight: 800; }
.completion-summary { flex: 2; min-width: min(260px, 100%); display: flex; flex-direction: column; gap: 8px; }
.completion-label { display: flex; justify-content: space-between; font-size: 13px; }
.task-list { display: flex; flex-direction: column; gap: 14px; }
.task-list > .card { margin-bottom: 0; }
.task-card { display: flex; flex-direction: column; gap: 16px; border-radius: 24px; padding: 22px 24px; }
.task-card.held { border: 1.5px solid #f7c9a6; }
.task-head { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.customer-text { display: flex; flex-direction: column; line-height: 1.3; min-width: 0; overflow-wrap: anywhere; }
.customer-text strong { font-size: 16px; }
.task-corp { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; padding: 6px 12px 6px 6px; border-radius: var(--pill); background: var(--surface-2); margin-left: 8px; min-width: 0; }
.task-corp .corp-mark { width: 28px; height: 28px; border-radius: 10px; background: var(--surface); font-size: 12px; }
.task-corp strong { font-size: 14px; overflow-wrap: anywhere; }
.task-corp a { display: inline-flex; align-items: center; min-height: 44px; font-size: 13px; font-weight: 600; white-space: nowrap; }
.task-corp a:focus-visible { outline: 2px solid var(--blue); outline-offset: 3px; border-radius: 6px; }
.task-status { margin-left: auto; display: flex; flex-direction: column; align-items: flex-end; gap: 4px; }
.task-status .sub { white-space: nowrap; }
.approved-message { white-space: normal; display: flex; flex-direction: column; gap: 8px; }
.message-label { color: var(--muted); font-size: 12px; font-weight: 700; }
.approved-message p { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; color: #2a3554; }
.task-footer { display: flex; align-items: flex-end; gap: 12px; flex-wrap: wrap; }
.memo-field { flex: 1; min-width: min(280px, 100%); display: flex; flex-direction: column; gap: 8px; }
.memo-field label { margin: 0; }
.memo-field textarea { min-height: 52px; padding: 12px 16px; background: var(--surface-2); }
.task-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.task-actions button { min-height: 48px; white-space: nowrap; }
.task-actions .ghost { padding: 0 18px; }
button.message-toggle { background: transparent; color: var(--blue-strong); border-color: transparent; padding: 0 8px; font-size: 14px; font-weight: 600; }
button.message-toggle:hover { background: var(--blue-soft); }
.compact-toggle { align-self: flex-start; min-height: 44px; font-size: 13px; }
.task-card.completed { background: #fafbfd; padding: 18px 24px; gap: 12px; }
.completed .customer-text strong, .completed .task-corp strong { color: var(--muted); }
.completed .task-corp { background: #f1f4f9; }
.result-note { margin: 0; padding: 10px 14px; border: 1px solid var(--border); border-radius: 14px; background: var(--surface); color: #2a3554; font-size: 14px; white-space: pre-wrap; overflow-wrap: anywhere; }
@media (max-width: 540px) {
  .remaining-summary, .completion-summary { min-width: 0; flex-basis: 100%; }
  .memo-field { min-width: 0; flex-basis: 100%; }
  .task-corp { margin-left: 0; }
  .task-actions { width: 100%; }
}
</style>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { api, errorMessage } from '../api/client'
import RunProgress from '../components/RunProgress.vue'
import StageBadge from '../components/StageBadge.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useRunEvents } from '../composables/useRunEvents'
import { ALERT_STAGES, ASSESSMENT_STATUS, formatTime, hasStage, isAlertRow, isUnscored, STAGES, stagesOf } from '../labels'

const router = useRouter()

// 탭: 경보 / 관찰 중 / 전체. stages는 새 단계, levels는 단계 값이 없는 예전 행에 쓰는 등급
const TABS = {
  alerts: { label: '경보', stages: ALERT_STAGES, levels: ['Critical', 'High'], chips: ALERT_STAGES },
  watch: { label: '관찰 중', stages: ['WATCH'], levels: ['Medium'], chips: [] },
  all: { label: '전체', stages: [], levels: [], chips: [...ALERT_STAGES, 'WATCH', 'NONE'] },
}
const tab = ref('alerts')
const statusFilter = ref('')
const stageChips = ref([]) // 고른 단계 (여러 개). 비어 있으면 탭 전체

const pendingOnly = ref(false) // 한 줄 바로 가기: 내 승인 대기가 있는 경보만

// 목록은 한 번에 받아 화면에서 나눈다. 그래야 탭마다 건수를 함께 보여 줄 수 있다 (서버 정렬 순서는 그대로)
const allRows = ref([])
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    allRows.value = await api.listAlerts({})
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

// 단계 값이 있으면 단계로, 없으면(예전 평가) 등급으로 탭을 나눈다
function inTab(r, key) {
  const t = TABS[key]
  const stages = stagesOf(r)
  if (stages.length) return !t.stages.length || stages.some((s) => t.stages.includes(s))
  return !t.levels.length || t.levels.includes(r.level)
}
// 단계 칩: 고른 단계 중 하나라도 있으면 보인다. 단계 값이 없는 행은 칩으로 거르지 않는다
const chipPass = (r) => !stageChips.value.length || !hasStage(r) || stagesOf(r).some((s) => stageChips.value.includes(s))
function toggleChip(stage) {
  stageChips.value = stageChips.value.includes(stage) ? stageChips.value.filter((s) => s !== stage) : [...stageChips.value, stage]
}
// 단계 값이 실제로 들어왔는지 (예전 평가만 있으면 칩을 누를 수 없다)
const stageReady = computed(() => allRows.value.some(hasStage))
function passes(r) {
  if (statusFilter.value && r.status !== statusFilter.value) return false
  if (pendingOnly.value && (r.status === 'DONE' || !(r.my_pending_count > 0))) return false
  return true
}
const rows = computed(() => allRows.value.filter((r) => inTab(r, tab.value) && passes(r) && chipPass(r)))
const tabCounts = computed(() => Object.fromEntries(Object.keys(TABS).map((key) => [key, allRows.value.filter((r) => inTab(r, key) && passes(r)).length])))

// 사람이 봐야 하는 공시: 위험 평가 전에 멈추면 경보 탭에 안 보이므로 따로 센다
const attention = computed(() => ({
  MANUAL_CHECK: allRows.value.filter((r) => r.status === 'MANUAL_CHECK').length,
  FAILED: allRows.value.filter((r) => r.status === 'FAILED').length,
}))
const showAttention = computed(() => !statusFilter.value && (attention.value.MANUAL_CHECK || attention.value.FAILED))
function showStatus(status) {
  tab.value = 'all'
  statusFilter.value = status
}

watch(tab, () => { stageChips.value = [] })
onMounted(load)

// 진짜 모드에서 MCP 서버가 꺼져 있으면 영향 고객 수를 못 센다. 목록은 DB 내용으로 계속 보여준다
const graphError = computed(() => allRows.value.find((r) => r.graph_error)?.graph_error || '')

const today = computed(() => new Date().toLocaleDateString('ko-KR', {
  year: 'numeric', month: 'long', day: 'numeric', weekday: 'long',
}))
const stats = computed(() => ({
  pending: allRows.value.reduce((sum, r) => sum + (r.status === 'DONE' ? 0 : Number(r.my_pending_count || 0)), 0),
  pendingAlerts: allRows.value.filter((r) => r.status !== 'DONE' && r.my_pending_count > 0).length,
  // 즉시 경보 / 그 밖의 경보(사건·재무·복합). 단계 값이 없는 예전 평가는 Critical / High로 센다
  critical: allRows.value.filter((r) => (hasStage(r) ? stagesOf(r)[0] === 'RED' : r.level === 'Critical')).length,
  high: allRows.value.filter((r) => isAlertRow(r) && (hasStage(r) ? stagesOf(r)[0] !== 'RED' : r.level === 'High')).length,
}))
function togglePending() {
  pendingOnly.value = !pendingOnly.value
  if (pendingOnly.value) {
    tab.value = 'alerts'
    statusFilter.value = ''
  }
}
const openAlert = (r) => router.push({ name: 'alert-detail', params: { assessmentId: r.assessment_id } })

// ---- 공시 1건 처리 시작 (데모용) ----
const rceptNo = ref('')
const runError = ref('')
const running = ref(false)
const { state: runState, connect } = useRunEvents()

async function startRun() {
  runError.value = ''
  try {
    await api.startRun(rceptNo.value.trim())
    running.value = true
    connect(rceptNo.value.trim(), {
      onEnd: (name, data) => {
        load()
        if (name === 'waiting_approval' && data.assessment_id) {
          router.push({ name: 'alert-detail', params: { assessmentId: data.assessment_id } })
        }
      },
    })
  } catch (err) {
    runError.value = errorMessage(err)
  }
}
</script>

<template>
  <div class="page-head">
    <div>
      <p class="eyebrow">{{ today }}</p>
      <h1>경보 목록</h1>
    </div>
  </div>

  <button type="button" class="shortcut-line pending-shortcut" :class="{ on: pendingOnly }" :disabled="!stats.pending && !pendingOnly" :aria-pressed="pendingOnly" @click="togglePending">
    <span class="shortcut-icon" aria-hidden="true">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3 8-8" /><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9" /></svg>
    </span>
    <span>
      <strong>{{ stats.pending ? `내 승인 대기 ${stats.pending}명` : '지금은 결정할 고객이 없어요' }}</strong>
      <span class="shortcut-sub"><template v-if="stats.pending"> · 경보 {{ stats.pendingAlerts }}건</template> · {{ STAGES.RED.label }} {{ stats.critical }} · 그 밖의 경보 {{ stats.high }}</span>
    </span>
    <span v-if="stats.pending || pendingOnly" class="shortcut-go">
      {{ pendingOnly ? '모든 경보 보기' : '승인 대기만 보기' }}
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path :d="pendingOnly ? 'M18 6L6 18M6 6l12 12' : 'M9 6l6 6-6 6'" /></svg>
    </span>
  </button>

  <section v-if="running || runError" class="card" aria-label="처리 진행">
    <div class="card-head">
      <h2>처리 진행 <small v-if="rceptNo" class="run-number">· {{ rceptNo }}</small></h2>
    </div>
    <RunProgress v-if="running" :state="runState" />
    <p v-if="runError" class="error">{{ runError }}</p>
    <RouterLink
      v-if="['failed', 'done'].includes(runState.last) && runState.lastData?.assessment_id"
      :to="{ name: 'alert-detail', params: { assessmentId: String(runState.lastData.assessment_id) } }"
      class="run-detail-link"
    >
      경보 상세 보기
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6" /></svg>
    </RouterLink>
  </section>

  <div v-if="showAttention" class="banner warn" role="status">
    <span class="banner-icon" aria-hidden="true">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01" /><path d="M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /></svg>
    </span>
    <span class="banner-text">
      <strong>사람이 확인해야 할 공시가 있어요</strong>
      <span>
        <template v-if="attention.MANUAL_CHECK">수동 확인 {{ attention.MANUAL_CHECK }}건</template>
        <template v-if="attention.MANUAL_CHECK && attention.FAILED"> · </template>
        <template v-if="attention.FAILED">실패 {{ attention.FAILED }}건</template>
        · 위험 평가 전에 멈춘 공시는 경보 탭에 나오지 않아요.
      </span>
    </span>
    <span class="banner-action">
      <button v-if="attention.MANUAL_CHECK" type="button" class="ghost small" @click="showStatus('MANUAL_CHECK')">수동 확인 보기</button>
      <button v-if="attention.FAILED" type="button" class="ghost small" @click="showStatus('FAILED')">실패 보기</button>
    </span>
  </div>

  <section class="card flush alert-list" aria-label="경보 목록">
    <div class="card-head">
      <div class="tabs" role="group" aria-label="보기">
        <button v-for="(t, key) in TABS" :key="key" type="button" :class="{ active: tab === key }" :aria-pressed="tab === key" @click="tab = key">
          {{ t.label }}<span class="count" :class="{ dim: tab !== key }">{{ tabCounts[key] }}</span>
        </button>
      </div>

      <div class="row list-filters">
        <label for="status-filter" class="sr-only">처리 상태</label>
        <select id="status-filter" v-model="statusFilter">
          <option value="">상태 전체</option>
          <option v-for="(label, key) in ASSESSMENT_STATUS" :key="key" :value="key">{{ label }}</option>
        </select>
        <button type="button" class="ghost small" @click="load">
          <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 3v6h-6" />
          </svg>
          새로고침
        </button>
      </div>
    </div>

    <!-- 단계 칩: 여러 개 골라 거른다. 단계 값이 아직 없으면(예전 평가만 있으면) 누를 수 없다 -->
    <div v-if="TABS[tab].chips.length" class="stage-filter">
      <div class="stage-chips" role="group" aria-label="단계로 거르기">
        <button v-for="s in TABS[tab].chips" :key="s" type="button" :aria-pressed="stageChips.includes(s)" :disabled="!stageReady" :title="STAGES[s].hint || undefined" @click="toggleChip(s)">
          <span class="chip-dot" :class="s.toLowerCase()" aria-hidden="true"></span>{{ STAGES[s].label }}
        </button>
        <button v-if="stageChips.length" type="button" class="chip-clear" @click="stageChips = []">전체 보기</button>
      </div>
      <span v-if="!stageReady" class="stage-filter-note">단계 정보가 있는 경보가 생기면 단계로 걸러 볼 수 있어요</span>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="graphError" class="notice">{{ graphError }} 영향 고객 수는 연결되면 다시 보입니다.</p>
    <div class="table-wrap">
      <table class="stack-table">
        <thead>
          <tr>
            <th scope="col">경보</th>
            <th scope="col">{{ stageReady ? '단계·상태' : '등급·상태' }}</th>
            <th scope="col" class="num">고객</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.assessment_id" class="clickable" :class="{ completed: r.status === 'DONE' }" @click="openAlert(r)">
            <td>
              <div class="corp">
                <span class="corp-mark" aria-hidden="true">{{ (r.corp_name || '-').slice(0, 1) }}</span>
                <div class="corp-text">
                  <strong><RouterLink :to="{ name: 'alert-detail', params: { assessmentId: r.assessment_id } }" @click.stop>{{ r.corp_name || '-' }}</RouterLink> <small v-if="r.stock_code" class="code">{{ r.stock_code }}</small></strong>
                  <span class="disclosure-title">{{ r.title || r.rcept_no }}</span>
                  <span class="sub">공시 {{ r.filed_at || '-' }} · 처리 {{ formatTime(r.created_at) }}</span>
                </div>
              </div>
            </td>
            <td>
              <div class="level-cell">
                <span v-if="isUnscored(r)" class="unscored" title="점수 계산 전에 멈췄어요">미계산</span>
                <StageBadge v-else :stage="r.stage" :stages="r.stages" :level="r.level" />
                <span v-if="r.new" class="new-pill">새 경보</span>
                <StatusBadge :status="r.status" />
                <span v-if="r.status === 'DONE'" class="sub">처리 완료 기록</span>
                <span v-if="r.combo_upgraded" class="sub">조합 상향</span>
              </div>
            </td>
            <td class="num">
              <div class="customer-cell">
                <span>보유 {{ r.affected_count ?? '-' }}명</span>
                <span v-if="r.status !== 'DONE' && r.my_pending_count > 0" class="count-pill" :title="`내 승인 대기 ${r.my_pending_count}명`">내 대기 {{ r.my_pending_count }}</span>
              </div>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="3" class="empty">{{ loading ? '불러오는 중…' : stageChips.length ? '고른 단계에 해당하는 경보가 없어요.' : '표시할 경보가 없습니다.' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
  <details class="card">
    <summary>공시 직접 처리</summary>
    <form class="row run-start" @submit.prevent="startRun">
      <label for="rcept" class="sr-only">접수번호</label>
      <input id="rcept" v-model="rceptNo" placeholder="접수번호 14자리" pattern="\d{14}" required />
      <button type="submit">공시 처리 시작</button>
    </form>
  </details>
</template>

<style scoped>
.run-start { margin-top: 16px; }
.run-start input { width: 200px; min-width: 0; padding: 0 18px; border: none; border-radius: var(--pill); background: var(--bg); }
.run-start button { white-space: nowrap; padding: 0 22px; }
.run-number { font-size: 14px; font-weight: 500; overflow-wrap: anywhere; }
.run-detail-link { display: inline-flex; align-items: center; gap: 4px; min-height: 44px; margin-top: 8px; font-weight: 600; }
.alert-list .tabs { margin-bottom: 0; }
.alert-list .tabs button, .list-filters button { min-height: 44px; }
.stage-filter { display: flex; align-items: center; gap: 8px 16px; flex-wrap: wrap; padding: 0 24px 16px; }
.stage-filter-note { font-size: 13px; color: var(--muted); }
.chip-clear { min-height: 36px; padding: 0 12px; background: transparent; color: var(--blue-strong); border: none; font-size: 13px; font-weight: 600; }
.chip-clear:hover:not(:disabled) { background: var(--blue-soft); }
@media (max-width: 640px) { .stage-filter { padding: 0 16px 16px; } }
.list-filters input, .list-filters select { font-size: 14px; }
.list-filters input { border-radius: var(--pill); }
.alert-list > .error, .alert-list > .notice { margin: 0 24px 16px; }
.alert-list th { padding-top: 12px; padding-bottom: 12px; }
.alert-list td { padding-top: 16px; padding-bottom: 16px; font-size: 14px; }
.corp-text { display: flex; flex-direction: column; gap: 2px; line-height: 1.4; min-width: 0; }
.corp-text .code { font-size: 12px; font-weight: 500; color: var(--muted); }
.disclosure-title { font-weight: 500; overflow-wrap: anywhere; }
.level-cell { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; }
.customer-cell { display: flex; flex-direction: column; align-items: flex-end; gap: 8px; white-space: nowrap; }
.pending-shortcut.on { box-shadow: 0 0 0 2px var(--blue) inset; }
.alert-list th:nth-child(2), .alert-list th:nth-child(3) { width: 1%; }
@media (max-width: 640px) {
  .level-cell, .customer-cell { flex-direction: row; flex-wrap: wrap; align-items: center; }
  .alert-list td { padding-top: 4px; padding-bottom: 4px; }
}
@media (max-width: 540px) {
  .run-start { width: 100%; border-radius: var(--radius); }
  .run-start input { flex: 1 1 160px; }
  .run-start button { flex: 1 1 auto; }
}
.completed { background: var(--surface-2); }
.completed .corp-text a { color: var(--muted); }
</style>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { api, errorMessage } from '../api/client'
import LevelBadge from '../components/LevelBadge.vue'
import RunProgress from '../components/RunProgress.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useRunEvents } from '../composables/useRunEvents'
import { ASSESSMENT_STATUS, formatTime, isUnscored } from '../labels'

const router = useRouter()

// 탭: 경보(High·Critical) / 일일 요약(Medium) / 전체
const TABS = {
  alerts: { label: '경보', levels: ['Critical', 'High'] },
  summary: { label: '일일 요약', levels: ['Medium'] },
  all: { label: '전체', levels: [] },
}
const tab = ref('alerts')
const statusFilter = ref('')
const levelFilter = ref('')
const summaryDate = ref(new Date().toLocaleDateString('sv-SE')) // 로컬 날짜 YYYY-MM-DD

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

// 일일 요약 날짜: 서버와 같은 기준(공시일 또는 생성일)
const onDate = (r, d) => String(r.filed_at || '').startsWith(d) || String(r.created_at || '').startsWith(d)
function inTab(r, key) {
  if (key === 'summary') return r.level === 'Medium' && onDate(r, summaryDate.value)
  const levels = TABS[key].levels
  return !levels.length || levels.includes(r.level)
}
function passes(r) {
  if (statusFilter.value && r.status !== statusFilter.value) return false
  if (pendingOnly.value && !(r.my_pending_count > 0)) return false
  return true
}
const rows = computed(() => allRows.value.filter((r) => inTab(r, tab.value) && passes(r) && (tab.value !== 'all' || !levelFilter.value || r.level === levelFilter.value)))
const tabCounts = computed(() => Object.fromEntries(Object.keys(TABS).map((key) => [key, allRows.value.filter((r) => inTab(r, key) && passes(r)).length])))

// 사람이 봐야 하는 공시: 점수 계산 전에 멈추면 Low로 저장돼 경보 탭(High·Critical)에 안 보이므로 따로 센다
const attention = computed(() => ({
  MANUAL_CHECK: allRows.value.filter((r) => r.status === 'MANUAL_CHECK').length,
  FAILED: allRows.value.filter((r) => r.status === 'FAILED').length,
}))
// 메인 대시보드 알림 = 경보 등급 + 처리 실패가 아닌 것. 서버가 이미 우선순위로 정렬해 준다
const notifications = computed(() => allRows.value.filter((r) => ['High', 'Critical'].includes(r.level) && r.status !== 'FAILED').slice(0, 8))
const showAttention = computed(() => !statusFilter.value && (attention.value.MANUAL_CHECK || attention.value.FAILED))
function showStatus(status) {
  tab.value = 'all'
  statusFilter.value = status
}

watch(tab, () => { levelFilter.value = '' })
onMounted(load)

// 진짜 모드에서 MCP 서버가 꺼져 있으면 영향 고객 수를 못 센다. 목록은 DB 내용으로 계속 보여준다
const graphError = computed(() => allRows.value.find((r) => r.graph_error)?.graph_error || '')

const today = computed(() => new Date().toLocaleDateString('ko-KR', {
  year: 'numeric', month: 'long', day: 'numeric', weekday: 'long',
}))
const stats = computed(() => ({
  pending: allRows.value.reduce((sum, r) => sum + Number(r.my_pending_count || 0), 0),
  pendingAlerts: allRows.value.filter((r) => r.my_pending_count > 0).length,
  critical: allRows.value.filter((r) => r.level === 'Critical').length,
  high: allRows.value.filter((r) => r.level === 'High').length,
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
    <form class="row run-start" @submit.prevent="startRun">
      <label for="rcept" class="sr-only">접수번호</label>
      <input id="rcept" v-model="rceptNo" placeholder="접수번호 14자리" pattern="\d{14}" required />
      <button type="submit">공시 처리 시작</button>
    </form>
  </div>

  <button type="button" class="shortcut-line pending-shortcut" :class="{ on: pendingOnly }" :disabled="!stats.pending && !pendingOnly" :aria-pressed="pendingOnly" @click="togglePending">
    <span class="shortcut-icon" aria-hidden="true">
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3 8-8" /><path d="M20 12v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h9" /></svg>
    </span>
    <span>
      <strong>{{ stats.pending ? `내 승인 대기 ${stats.pending}명` : '지금은 결정할 고객이 없어요' }}</strong>
      <span class="shortcut-sub"><template v-if="stats.pending"> · 경보 {{ stats.pendingAlerts }}건</template> · Critical {{ stats.critical }} · High {{ stats.high }}</span>
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
        — 점수 계산 전에 멈춘 공시는 경보 탭에 나오지 않아요.
      </span>
    </span>
    <span class="banner-action">
      <button v-if="attention.MANUAL_CHECK" type="button" class="ghost small" @click="showStatus('MANUAL_CHECK')">수동 확인 보기</button>
      <button v-if="attention.FAILED" type="button" class="ghost small" @click="showStatus('FAILED')">실패 보기</button>
    </span>
  </div>

  <!-- 메인 대시보드 알림: 경보 등급(High·Critical)을 우선순위(등급 → 점수 순)로. 점수 숫자는 보여 주지 않는다 -->
  <section v-if="notifications.length" class="card notifications" aria-label="새 경보 알림">
    <div class="card-head">
      <h2>경보 알림 <small>{{ notifications.length }}건 · 우선순위순</small></h2>
    </div>
    <ol class="notice-list">
      <li v-for="(r, n) in notifications" :key="r.assessment_id" class="clickable" @click="openAlert(r)">
        <span class="rank">{{ n + 1 }}</span>
        <LevelBadge :level="r.level" />
        <strong>{{ r.corp_name || '-' }}</strong>
        <span class="sub">{{ r.title || r.rcept_no }}</span>
        <span class="notice-meta">보유 고객 {{ r.affected_count }}명<template v-if="r.my_pending_count > 0"> · 내 승인 대기 {{ r.my_pending_count }}명</template></span>
      </li>
    </ol>
  </section>

  <section class="card flush alert-list" aria-label="경보 목록">
    <div class="card-head">
      <div class="tabs" role="group" aria-label="보기">
        <button v-for="(t, key) in TABS" :key="key" type="button" :class="{ active: tab === key }" :aria-pressed="tab === key" @click="tab = key">
          {{ t.label }}<span class="count" :class="{ dim: tab !== key }">{{ tabCounts[key] }}</span>
        </button>
      </div>

      <div class="row list-filters">
        <template v-if="tab === 'summary'">
          <label for="d" class="date-label">날짜</label>
          <input id="d" v-model="summaryDate" type="date" />
        </template>
        <template v-if="tab === 'all'">
          <label for="level-filter" class="sr-only">등급</label>
          <select id="level-filter" v-model="levelFilter">
            <option value="">등급 전체</option>
            <option v-for="l in ['Critical', 'High', 'Medium', 'Low']" :key="l" :value="l">{{ l }}</option>
          </select>
        </template>
        <label for="status-filter" class="sr-only">상태</label>
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

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="graphError" class="notice">{{ graphError }} 영향 고객 수는 연결되면 다시 보입니다.</p>
    <div class="table-wrap">
      <table class="stack-table">
        <thead>
          <tr>
            <th scope="col">경보</th>
            <th scope="col">등급·상태</th>
            <th scope="col" class="num">고객</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.assessment_id" class="clickable" @click="openAlert(r)">
            <td>
              <div class="corp">
                <span class="corp-mark" aria-hidden="true">{{ (r.corp_name || '-').slice(0, 1) }}</span>
                <div class="corp-text">
                  <strong>{{ r.corp_name || '-' }} <small v-if="r.stock_code" class="code">{{ r.stock_code }}</small></strong>
                  <span class="disclosure-title">{{ r.title || r.rcept_no }}</span>
                  <span class="sub">공시 {{ r.filed_at || '-' }} · 처리 {{ formatTime(r.created_at) }}</span>
                </div>
              </div>
            </td>
            <td>
              <div class="level-cell">
                <span v-if="isUnscored(r)" class="unscored" title="점수 계산 전에 멈췄어요">미계산</span>
                <LevelBadge v-else :level="r.level" />
                <StatusBadge :status="r.status" />
                <span v-if="r.combo_upgraded" class="sub">조합 상향</span>
              </div>
            </td>
            <td class="num">
              <div class="customer-cell">
                <span>보유 {{ r.affected_count ?? '-' }}명</span>
                <span v-if="r.my_pending_count > 0" class="count-pill" :title="`내 승인 대기 ${r.my_pending_count}명`">내 대기 {{ r.my_pending_count }}</span>
              </div>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td colspan="3" class="empty">{{ loading ? '불러오는 중…' : '표시할 경보가 없습니다.' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.run-start { padding: 8px; border: 1px solid var(--border); border-radius: var(--pill); background: var(--surface); box-shadow: var(--shadow-sm); }
.run-start input { width: 200px; min-width: 0; padding: 0 18px; border: none; border-radius: var(--pill); background: var(--bg); }
.run-start button { white-space: nowrap; padding: 0 22px; }
.run-number { font-size: 14px; font-weight: 500; overflow-wrap: anywhere; }
.run-detail-link { display: inline-flex; align-items: center; gap: 4px; min-height: 44px; margin-top: 8px; font-weight: 600; }
.alert-list .tabs { margin-bottom: 0; }
.alert-list .tabs button, .list-filters button { min-height: 44px; }
.date-label { margin: 0; }
.list-filters input, .list-filters select { font-size: 14px; }
.list-filters input { border-radius: var(--pill); }
.alert-list > .error, .alert-list > .notice { margin: 0 24px 16px; }
.alert-list th { padding-top: 12px; padding-bottom: 12px; }
.alert-list td { padding-top: 16px; padding-bottom: 16px; font-size: 14px; }
.corp-text { display: flex; flex-direction: column; gap: 2px; line-height: 1.4; min-width: 0; }
.corp-text .code { font-size: 12px; font-weight: 500; color: var(--muted); }
.disclosure-title { font-weight: 500; overflow-wrap: anywhere; }
.level-cell { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; }
.customer-cell { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; white-space: nowrap; }
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
.notice-list { list-style: none; margin: 0; padding: 0 24px 16px; display: flex; flex-direction: column; gap: 8px; }
.notice-list li { display: flex; align-items: center; gap: 12px; padding: 12px 16px; border: 1px solid var(--border); border-radius: var(--radius); flex-wrap: wrap; }
.notice-list li:hover { background: var(--bg); }
.notice-list .rank { width: 24px; height: 24px; border-radius: 50%; background: var(--neutral-soft); color: var(--muted); display: inline-flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; }
.notice-meta { margin-left: auto; font-size: 13px; color: var(--muted); }
</style>

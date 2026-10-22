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

const rows = ref([])
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  const levels = levelFilter.value ? [levelFilter.value] : TABS[tab.value].levels
  try {
    rows.value = await api.listAlerts({
      level: levels.length ? levels : undefined,
      status: statusFilter.value || undefined,
      date: tab.value === 'summary' ? summaryDate.value : undefined,
    })
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
  loadAttention()
}

// 사람이 봐야 하는 공시: 점수 계산 전에 멈추면 Low로 저장돼 경보 탭(High·Critical)에 안 보이므로 따로 센다
const attention = ref({ MANUAL_CHECK: 0, FAILED: 0 })
async function loadAttention() {
  try {
    const [manual, failed] = await Promise.all([
      api.listAlerts({ status: 'MANUAL_CHECK' }),
      api.listAlerts({ status: 'FAILED' }),
    ])
    attention.value = { MANUAL_CHECK: manual.length, FAILED: failed.length }
  } catch {
    // 안내용이라 실패해도 목록은 그대로 둔다
  }
}
const showAttention = computed(() => !statusFilter.value && (attention.value.MANUAL_CHECK || attention.value.FAILED))
function showStatus(status) {
  tab.value = 'all'
  statusFilter.value = status
}

watch(tab, () => {
  levelFilter.value = ''
  load()
})
watch([statusFilter, levelFilter, summaryDate], load)
onMounted(load)

const today = computed(() => new Date().toLocaleDateString('ko-KR', {
  year: 'numeric', month: 'long', day: 'numeric', weekday: 'long',
}))
const stats = computed(() => ({
  critical: rows.value.filter((r) => r.level === 'Critical').length,
  high: rows.value.filter((r) => r.level === 'High').length,
  medium: rows.value.filter((r) => r.level === 'Medium').length,
}))

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
        if (name === 'done' && data.assessment_id) {
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

  <div class="stat-grid">
    <div class="stat">
      <span class="stat-label"><span class="dot dot-critical" aria-hidden="true"></span>Critical</span>
      <div class="stat-value"><strong>{{ stats.critical }}</strong><span>건</span></div>
      <span class="stat-note">긴급 경보</span>
    </div>
    <div class="stat">
      <span class="stat-label"><span class="dot dot-high" aria-hidden="true"></span>High</span>
      <div class="stat-value"><strong>{{ stats.high }}</strong><span>건</span></div>
      <span class="stat-note">경보</span>
    </div>
    <div class="stat">
      <span class="stat-label"><span class="dot dot-blue" aria-hidden="true"></span>Medium</span>
      <div class="stat-value"><strong>{{ stats.medium }}</strong><span>건</span></div>
      <span class="stat-note">일일 요약 대상</span>
    </div>
  </div>

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

  <section class="card flush alert-list" aria-label="경보 목록">
    <div class="card-head">
      <div class="tabs" role="group" aria-label="보기">
        <button v-for="(t, key) in TABS" :key="key" type="button" :class="{ active: tab === key }" :aria-pressed="tab === key" @click="tab = key">
          {{ t.label }}<span v-if="tab === key" class="count">{{ rows.length }}</span>
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
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>회사</th>
            <th>공시</th>
            <th>등급</th>
            <th class="num">점수</th>
            <th>상태</th>
            <th>생성</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in rows"
            :key="r.assessment_id"
            class="clickable"
            @click="router.push({ name: 'alert-detail', params: { assessmentId: r.assessment_id } })"
          >
            <td>
              <div class="corp">
                <span class="corp-mark" aria-hidden="true">{{ (r.corp_name || '-').slice(0, 1) }}</span>
                <div class="corp-text">
                  <strong>{{ r.corp_name || '-' }}</strong>
                  <span v-if="r.stock_code" class="sub">{{ r.stock_code }}</span>
                </div>
              </div>
            </td>
            <td>
              <div class="disclosure">
                <span class="disclosure-title">{{ r.title || r.rcept_no }}</span>
                <span class="sub">{{ r.filed_at || r.rcept_no }}</span>
              </div>
            </td>
            <td>
              <div class="level-cell">
                <span v-if="isUnscored(r)" class="unscored" title="점수 계산 전에 멈췄어요">미계산</span>
                <LevelBadge v-else :level="r.level" />
                <span v-if="r.combo_upgraded" class="sub">조합 상향</span>
              </div>
            </td>
            <td class="num score">{{ isUnscored(r) ? '-' : r.total_score ?? '-' }}</td>
            <td><StatusBadge :status="r.status" /></td>
            <td class="created-at">{{ formatTime(r.created_at) }}</td>
          </tr>
          <tr v-if="!rows.length && !loading">
            <td colspan="6" class="empty">표시할 경보가 없습니다.</td>
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
.alert-list table { min-width: 980px; }
.alert-list th { padding-top: 12px; padding-bottom: 12px; }
.alert-list td { padding-top: 16px; padding-bottom: 16px; font-size: 14px; }
.disclosure { display: flex; flex-direction: column; line-height: 1.4; }
.disclosure-title { font-weight: 500; }
.level-cell { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }
.alert-list .score { font-size: 16px; font-weight: 700; }
.alert-list .created-at { font-size: 13px; color: var(--muted); white-space: nowrap; }
@media (max-width: 540px) {
  .run-start { width: 100%; border-radius: var(--radius); }
  .run-start input { flex: 1 1 160px; }
  .run-start button { flex: 1 1 auto; }
}
</style>

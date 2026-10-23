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

// 탭: 경보 / 관찰 중 / 전체. stages는 새 단계, levels는 API가 아직 예전 등급만 줄 때 쓰는 값
const TABS = {
  alerts: { label: '경보', stages: ALERT_STAGES, levels: ['Critical', 'High'], chips: ALERT_STAGES },
  watch: { label: '관찰 중', stages: ['WATCH'], levels: ['Medium'], chips: [] },
  all: { label: '전체', stages: [], levels: [], chips: [...ALERT_STAGES, 'WATCH', 'NONE'] },
}
const tab = ref('alerts')
const statusFilter = ref('')
const stageChips = ref([]) // 고른 단계 (여러 개). 비어 있으면 탭 전체

const rows = ref([])
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  const t = TABS[tab.value]
  try {
    rows.value = await api.listAlerts({
      // 새 단계를 아는 서버는 stage를, 아직 예전 등급인 서버는 level을 쓴다 (모르는 값은 서버가 무시)
      stage: t.stages.length ? t.stages : undefined,
      level: t.levels.length ? t.levels : undefined,
      status: statusFilter.value || undefined,
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
// 단계 정보가 실제로 들어왔는지 (R4가 규칙 엔진을 새 단계로 바꾸기 전에는 false)
const stageReady = computed(() => rows.value.some(hasStage))

// 서버 순서(단계 → 점수 → 최근)는 그대로 두고 걸러내기만 한다. 단계 정보가 없는 행은 서버가 level로 거른 결과를 믿는다
const visibleRows = computed(() => {
  const t = TABS[tab.value]
  return rows.value.filter((r) => {
    const stages = stagesOf(r)
    if (!stages.length) return true
    if (t.stages.length && !stages.some((s) => t.stages.includes(s))) return false
    if (stageChips.value.length && !stages.some((s) => stageChips.value.includes(s))) return false
    return true
  })
})
function toggleChip(stage) {
  stageChips.value = stageChips.value.includes(stage)
    ? stageChips.value.filter((s) => s !== stage)
    : [...stageChips.value, stage]
}

// 메인 대시보드 알림 = 경보(관찰 중 제외) + 처리 실패가 아닌 것. 서버가 이미 우선순위로 정렬해 준다
const notifications = computed(() => rows.value.filter((r) => isAlertRow(r) && r.status !== 'FAILED').slice(0, 8))
const showAttention = computed(() => !statusFilter.value && (attention.value.MANUAL_CHECK || attention.value.FAILED))
function showStatus(status) {
  tab.value = 'all'
  statusFilter.value = status
}

watch(tab, () => {
  stageChips.value = []
  load()
})
watch(statusFilter, load)
onMounted(load)

// 진짜 모드에서 MCP 서버가 꺼져 있으면 영향 고객 수를 못 센다. 목록은 DB 내용으로 계속 보여준다
const graphError = computed(() => rows.value.find((r) => r.graph_error)?.graph_error || '')

const waitingCount = computed(() => rows.value.filter((r) => r.status === 'WAITING_APPROVAL').length)

const today = computed(() => new Date().toLocaleDateString('ko-KR', {
  year: 'numeric', month: 'long', day: 'numeric', weekday: 'long',
}))
const stats = computed(() => ({
  pending: rows.value.reduce((sum, r) => sum + Number(r.my_pending_count || 0), 0),
  pendingAlerts: rows.value.filter((r) => r.my_pending_count > 0).length,
  // 새 단계가 있으면 즉시 경보 / 그 밖 경보, 없으면 예전 Critical / High
  critical: stageReady.value ? rows.value.filter((r) => stagesOf(r)[0] === 'RED').length : rows.value.filter((r) => r.level === 'Critical').length,
  high: stageReady.value
    ? rows.value.filter((r) => isAlertRow(r) && stagesOf(r)[0] !== 'RED').length
    : rows.value.filter((r) => r.level === 'High').length,
  // 끝난 경보까지 더하면 이미 결정한 고객이 다시 세진다. 승인 대기 경보만 센다
  holders: rows.value.filter((r) => r.status === 'WAITING_APPROVAL').reduce((sum, r) => sum + Number(r.affected_count || 0), 0),
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

  <div class="stat-grid">
    <div class="stat dark">
      <span class="stat-label">내 승인 대기</span>
      <div class="stat-value"><strong>{{ stats.pending }}</strong><span>명</span></div>
      <span class="stat-note">{{ stats.pendingAlerts ? `경보 ${stats.pendingAlerts}건에 걸쳐 있어요` : '지금은 결정할 고객이 없어요' }}</span>
    </div>
    <div class="stat">
      <span class="stat-label"><span class="dot dot-critical" aria-hidden="true"></span>{{ stageReady ? STAGES.RED.label : 'Critical' }}</span>
      <div class="stat-value"><strong>{{ stats.critical }}</strong><span>건</span></div>
      <span class="stat-note">{{ stageReady ? '확정 악재' : '긴급 경보' }}</span>
    </div>
    <div class="stat">
      <span class="stat-label"><span class="dot dot-high" aria-hidden="true"></span>{{ stageReady ? '사건·재무·복합 경보' : 'High' }}</span>
      <div class="stat-value"><strong>{{ stats.high }}</strong><span>건</span></div>
      <span class="stat-note">안내문 대상 경보</span>
    </div>
    <div class="stat">
      <span class="stat-label"><span class="dot dot-blue" aria-hidden="true"></span>경보 종목 보유 고객</span>
      <div class="stat-value"><strong>{{ stats.holders }}</strong><span>명</span></div>
      <span class="stat-note">승인 대기 경보 기준</span>
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

  <!-- 메인 대시보드 알림: 경보(관찰 중 제외)를 서버가 정한 우선순위(단계 → 점수 → 최근)로. 점수 숫자는 보여 주지 않는다 -->
  <section v-if="notifications.length" class="card notifications" aria-label="새 경보 알림">
    <div class="card-head">
      <h2>경보 알림 <small>{{ notifications.length }}건 · 우선순위순</small></h2>
    </div>
    <ol class="notice-list">
      <li v-for="(r, n) in notifications" :key="r.assessment_id" class="clickable" @click="router.push({ name: 'alert-detail', params: { assessmentId: r.assessment_id } })">
        <span class="rank">{{ n + 1 }}</span>
        <StageBadge :stage="r.stage" :stages="r.stages" :level="r.level" />
        <span v-if="r.new" class="new-pill">새 경보</span>
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
          {{ t.label }}<span v-if="tab === key" class="count">{{ visibleRows.length }}</span>
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

    <!-- 단계 칩: 여러 개 골라 거른다. 단계 정보가 아직 없으면(예전 등급 서버) 누를 수 없다 -->
    <div v-if="TABS[tab].chips.length" class="stage-filter">
      <div class="stage-chips" role="group" aria-label="단계로 거르기">
        <button
          v-for="s in TABS[tab].chips"
          :key="s"
          type="button"
          :aria-pressed="stageChips.includes(s)"
          :disabled="!stageReady"
          :title="STAGES[s].hint || undefined"
          @click="toggleChip(s)"
        >
          <span class="chip-dot" :class="s.toLowerCase()" aria-hidden="true"></span>{{ STAGES[s].label }}
        </button>
        <button v-if="stageChips.length" type="button" class="chip-clear" @click="stageChips = []">전체 보기</button>
      </div>
      <span v-if="!stageReady" class="stage-filter-note">단계 정보는 규칙 엔진이 새 단계로 바뀐 뒤에 걸러 볼 수 있어요</span>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="graphError" class="notice">{{ graphError }} 영향 고객 수는 연결되면 다시 보입니다.</p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>회사</th>
            <th>공시</th>
            <th>{{ stageReady ? '단계' : '등급' }}</th>
            <th class="num">보유 고객</th>
            <th class="num">내 승인 대기</th>
            <th>상태</th>
            <th>생성</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in visibleRows"
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
                <StageBadge v-else :stage="r.stage" :stages="r.stages" :level="r.level" />
                <span v-if="r.new" class="new-pill">새 경보</span>
                <span v-if="r.combo_upgraded" class="sub">조합 상향</span>
              </div>
            </td>
            <td class="num">{{ r.affected_count }}명</td>
            <td class="num"><span v-if="r.my_pending_count > 0" class="count-pill">{{ r.my_pending_count }}</span><span v-else class="muted">-</span></td>
            <td><StatusBadge :status="r.status" /></td>
            <td class="created-at">{{ formatTime(r.created_at) }}</td>
          </tr>
          <tr v-if="!visibleRows.length && !loading">
            <td colspan="7" class="empty">{{ stageChips.length ? '고른 단계에 해당하는 경보가 없어요.' : '표시할 경보가 없습니다.' }}</td>
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
.stage-filter { display: flex; align-items: center; gap: 8px 16px; flex-wrap: wrap; padding: 0 24px 16px; }
.stage-filter-note { font-size: 13px; color: var(--muted); }
.chip-clear { min-height: 36px; padding: 0 12px; background: transparent; color: var(--blue-strong); border: none; font-size: 13px; font-weight: 600; }
.chip-clear:hover:not(:disabled) { background: var(--blue-soft); }
.list-filters input, .list-filters select { font-size: 14px; }
.list-filters input { border-radius: var(--pill); }
.alert-list > .error, .alert-list > .notice { margin: 0 24px 16px; }
.alert-list table { min-width: 980px; }
.alert-list th { padding-top: 12px; padding-bottom: 12px; }
.alert-list td { padding-top: 16px; padding-bottom: 16px; font-size: 14px; }
.disclosure { display: flex; flex-direction: column; line-height: 1.4; }
.disclosure-title { font-weight: 500; }
.level-cell { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }
.alert-list .created-at { font-size: 13px; color: var(--muted); white-space: nowrap; }
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

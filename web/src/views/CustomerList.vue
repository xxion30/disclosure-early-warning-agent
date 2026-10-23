<script setup>
// 고객 현황: 내 담당 고객의 보유 종목, 경보 종목 노출, 연락 이력을 한 화면에서 본다 (읽기 전용).
import { computed, onMounted, reactive, ref } from 'vue'

import { api, errorMessage } from '../api/client'
import StageBadge from '../components/StageBadge.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { formatTime, formatWon, STAGES, stagesOf } from '../labels'

// 고객별 가장 심한 단계: API가 top_stage를 주면 그 값, 없으면 경보 종목들의 단계 중 가장 심한 것 (목록 순서는 바꾸지 않는다)
// 서버가 top_stage 칸을 주면 null이어도 그 값을 따른다(null = 경보 노출 없음 또는 예전 노출이라 단계끼리 비교 불가 → 예전 등급 배지)
const topStage = (c) => ('top_stage' in (c.exposure || {})
  ? c.exposure.top_stage
  : stagesOf({ stages: c.holdings.map((h) => h.alert?.stage).filter(Boolean) })[0] || null)
const stageReady = computed(() => data.value.customers.some((c) => topStage(c)))
// 단계 값이 들어오면 '즉시 경보 노출', 예전 등급만 있으면 'Critical 노출'
const customerTabs = computed(() => ({ all: '전체', exposed: '경보 종목 보유', critical: stageReady.value ? `${STAGES.RED.label} 노출` : 'Critical 노출' }))
const historyTone = (status) => ({ 연락완료: 'ok', 승인: 'ok', 보류: 'muted', 반려: 'outline' }[status] || '')

const data = ref({ summary: { customers: 0, exposed: 0, critical: 0, high: 0, tasks: {} }, customers: [] })
const error = ref('')
const loading = ref(false)
const filter = ref('all') // all | exposed | critical
const query = ref('')
const open = reactive({}) // customer_id → 펼침

async function load() {
  error.value = ''
  loading.value = true
  try {
    data.value = await api.listCustomers()
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}
onMounted(load)

// 탭(전체·경보 종목 보유·Critical 노출)과 검색어로 거른다. 탭 숫자도 같은 규칙으로 센다
function matches(c, tab) {
  const q = query.value.trim().toLowerCase()
  if (tab === 'exposed' && !c.exposure.alert_count) return false
  if (tab === 'critical' && !(topStage(c) ? topStage(c) === 'RED' : c.exposure.top_level === 'Critical')) return false
  return !q || c.name.toLowerCase().includes(q) || c.customer_id.toLowerCase().includes(q)
}
const rows = computed(() => data.value.customers.filter((c) => matches(c, filter.value)))
const tabCounts = computed(() =>
  Object.fromEntries(Object.keys(customerTabs.value).map((tab) => [tab, data.value.customers.filter((c) => matches(c, tab)).length])),
)

const taskDone = computed(() => {
  const t = data.value.summary.tasks || {}
  return { open: (t['승인'] || 0) + (t['보류'] || 0), done: t['연락완료'] || 0, rejected: t['반려'] || 0 }
})

const alertedHoldings = (c) => c.holdings.filter((h) => h.alert)
// 목록 칸에는 경보 종목만 2개까지 보여 주고, 나머지는 "외 N개"로 줄인다. 전체는 펼치면 보인다
const SHOWN_ALERTS = 2
const shownAlerts = (c) => alertedHoldings(c).slice(0, SHOWN_ALERTS)
const hiddenCount = (c) => c.holdings.length - shownAlerts(c).length
const exposurePct = (c) => (c.total_assets && c.exposure.amount ? Math.round((c.exposure.amount / c.total_assets) * 1000) / 10 : null)
const toggle = (id) => (open[id] = !open[id])
</script>

<template>
  <div class="page-head">
    <div>
      <p class="eyebrow">조회 전용 · 보유 종목, 경보 노출, 연락 이력</p>
      <h1>고객 현황</h1>
    </div>
    <div class="row">
      <label for="customer-search" class="sr-only">고객 검색</label>
      <div class="search-field">
        <svg aria-hidden="true" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="7" /><path d="M20 20l-3.5-3.5" /></svg>
        <input id="customer-search" v-model="query" placeholder="이름 또는 고객 ID" />
      </div>
      <button type="button" class="ghost" :disabled="loading" @click="load">
        <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12a9 9 0 1 1-2.64-6.36" /><path d="M21 3v6h-6" /></svg>
        새로고침
      </button>
    </div>
  </div>

  <!-- 숫자 카드 대신: 고객 수는 아래 탭에, 연락 업무는 한 줄 바로 가기로 -->
  <RouterLink to="/tasks" class="shortcut-line">
    <span class="shortcut-icon" aria-hidden="true">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 11l3 3 8-8" /><path d="M20 12v6a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h9" /></svg>
    </span>
    <span><strong>전체 연락 업무 · 진행 중 {{ taskDone.open }}건</strong><span class="shortcut-sub"> · 완료 {{ taskDone.done }}건</span></span>
    <span class="shortcut-go">연락 업무 보기
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 6l6 6-6 6" /></svg>
    </span>
  </RouterLink>

  <section class="card flush customer-list" aria-label="고객 목록">
    <div class="card-head">
      <div class="tabs" role="group" aria-label="고객 보기">
        <button v-for="(label, value) in customerTabs" :key="value" type="button" :class="{ active: filter === value }" :aria-pressed="filter === value" @click="filter = value">
          {{ label }}<span class="count" :class="{ dim: filter !== value }">{{ tabCounts[value] }}</span>
        </button>
      </div>
      <span v-if="query.trim()" class="chip soft" role="status">검색 결과 기준</span>
      <span class="muted list-hint">줄을 누르면 보유 종목과 연락 이력이 펼쳐져요</span>
    </div>
    <p v-if="taskDone.rejected" class="muted review-history">전체 검토 이력 · 반려 {{ taskDone.rejected }}건 (연락 업무 없음)</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div class="table-wrap">
      <table class="stack-table">
        <thead>
          <tr>
            <th scope="col">고객</th>
            <th scope="col">경보 노출</th>
            <th scope="col">최근 연락</th>
            <th scope="col"><span class="sr-only">펼치기</span></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="c in rows" :key="c.customer_id">
            <tr class="clickable" :class="{ expanded: open[c.customer_id] }" @click="toggle(c.customer_id)">
              <td>
                <div class="customer-cell">
                  <span class="avatar sm" aria-hidden="true">{{ (c.name || '-').slice(0, 1) }}</span>
                  <div class="customer-text">
                    <strong>{{ c.name }} <small class="code">{{ c.customer_id }}</small></strong>
                    <span class="sub">{{ c.risk_profile || '-' }} · 총자산 {{ formatWon(c.total_assets) }}</span>
                  </div>
                </div>
              </td>
              <td>
                <div class="exposure-cell">
                  <template v-if="topStage(c) || c.exposure.top_level">
                    <StageBadge :stage="topStage(c)" :level="c.exposure.top_level" />
                    <strong v-if="c.exposure.amount" class="exposure-amount">{{ formatWon(c.exposure.amount) }}</strong>
                  </template>
                  <span v-else class="muted">경보 종목 없음</span>
                  <span v-if="c.exposure.manual_check_count" class="notice warn">추가 확인 {{ c.exposure.manual_check_count }}종목</span>
                </div>
                <div class="chip-row holdings-line">
                  <span v-for="h in shownAlerts(c)" :key="h.stock_code" class="chip alert" :title="h.on_credit ? '신용거래' : ''">{{ h.stock_name }}</span>
                  <span v-if="hiddenCount(c) > 0" class="chip stock">{{ shownAlerts(c).length ? `외 ${hiddenCount(c)}개` : `보유 ${hiddenCount(c)}개` }}</span>
                  <span v-if="!c.holdings.length" class="muted">보유 종목 없음</span>
                </div>
              </td>
              <td>
                <div v-if="c.last_contact" class="last-contact">
                  <StatusBadge :status="c.last_contact.status" />
                  <span class="sub">{{ formatTime(c.last_contact.when) }}</span>
                </div>
                <span v-else class="muted">없음</span>
              </td>
              <td class="toggle-cell">
                <button type="button" class="icon-button" :class="{ active: open[c.customer_id] }" :aria-label="`${c.name} ${open[c.customer_id] ? '접기' : '펼치기'}`" :aria-expanded="!!open[c.customer_id]" :aria-controls="`customer-detail-${c.customer_id}`" @click.stop="toggle(c.customer_id)">
                  <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path :d="open[c.customer_id] ? 'M6 15l6-6 6 6' : 'M6 9l6 6 6-6'" /></svg>
                </button>
              </td>
            </tr>
            <tr v-if="open[c.customer_id]" :id="`customer-detail-${c.customer_id}`" class="detail expanded">
              <td colspan="4">
                <!-- 목록에서 줄인 정보를 여기서 모두 보여 준다 -->
                <dl class="facts">
                  <div><dt>투자성향</dt><dd>{{ c.risk_profile || '-' }}</dd></div>
                  <div><dt>총자산</dt><dd>{{ formatWon(c.total_assets) }}</dd></div>
                  <div><dt>경보 노출액</dt><dd :class="{ bad: c.exposure.amount }">{{ c.exposure.amount ? formatWon(c.exposure.amount) : '-' }}<small v-if="exposurePct(c) != null"> · 총자산의 {{ exposurePct(c) }}%</small></dd></div>
                  <div><dt>보유 종목</dt><dd>{{ c.holdings.length }}개<small v-if="alertedHoldings(c).length"> · 경보 {{ alertedHoldings(c).length }}개</small></dd></div>
                  <div><dt>신용거래</dt><dd>{{ c.holdings.some((h) => h.on_credit) ? '있음' : '없음' }}</dd></div>
                </dl>
                <div class="detail-grid">
                  <section class="detail-panel" :aria-labelledby="`holdings-${c.customer_id}`">
                    <h3 :id="`holdings-${c.customer_id}`">보유 종목</h3>
                    <div class="holding-list">
                      <div v-for="h in c.holdings" :key="h.stock_code" class="holding" :class="{ alerted: h.alert }">
                        <span class="corp-mark" aria-hidden="true">{{ (h.stock_name || '-').slice(0, 1) }}</span>
                        <div class="holding-text">
                          <strong>{{ h.stock_name }} <small>{{ h.stock_code }}</small></strong>
                          <span class="sub">{{ formatWon(h.eval_amount) }} · 비중 {{ h.ratio_pct ?? '-' }}% · 신용 {{ h.on_credit ? '있음' : '없음' }}</span>
                        </div>
                        <div class="holding-alert">
                          <template v-if="h.alert">
                            <StageBadge :stage="h.alert.stage" :stages="h.alert.stages" :level="h.alert.level" />
                            <RouterLink :to="{ name: 'alert-detail', params: { assessmentId: String(h.alert.assessment_id) } }" @click.stop>경보 보기</RouterLink>
                          </template>
                          <span v-else-if="h.manual_check" class="notice warn">판단 보류</span>
                          <span v-else class="muted">경보 없음</span>
                          <span v-if="h.alert && h.manual_check" class="notice warn">일부 항목 추가 확인</span>
                        </div>
                      </div>
                      <p v-if="!c.holdings.length" class="muted">보유 종목 없음</p>
                    </div>
                    <p v-if="alertedHoldings(c).length" class="muted rule-note">
                      경보 종목 {{ alertedHoldings(c).length }}개 · 우선순위는 경보 화면과 같은 영향도 규칙(보유 비중·평가금액·투자성향·신용거래)으로 계산합니다.
                    </p>
                  </section>
                  <section class="detail-panel" :aria-labelledby="`history-${c.customer_id}`">
                    <h3 :id="`history-${c.customer_id}`">연락 이력</h3>
                    <p v-if="!c.history.length" class="muted">아직 안내문 승인·반려 기록이 없습니다.</p>
                    <ol v-else class="timeline">
                      <li v-for="(x, i) in c.history" :key="i">
                        <div class="timeline-rail" aria-hidden="true"><span class="timeline-dot" :class="historyTone(x.status)"></span><span class="timeline-line"></span></div>
                        <div class="timeline-body">
                          <div class="row history-head">
                            <StatusBadge :status="x.status" />
                            <strong>{{ x.corp_name || '-' }}</strong>
                            <span class="sub">{{ formatTime(x.when) }}</span>
                          </div>
                          <p v-if="x.note" class="history-note">{{ x.kind === 'reject' ? '반려 사유: ' : '메모: ' }}{{ x.note }}</p>
                          <details v-if="x.message_preview">
                            <summary>승인한 안내문 보기</summary>
                            <div class="message">{{ x.message_preview }}</div>
                          </details>
                        </div>
                      </li>
                    </ol>
                  </section>
                </div>
              </td>
            </tr>
          </template>
          <tr v-if="!rows.length"><td colspan="4" class="empty">{{ loading ? '불러오는 중…' : '조건에 맞는 고객이 없습니다.' }}</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.customer-list .tabs { margin-bottom: 0; }
.customer-list .tabs button { min-height: 44px; }
.list-hint { font-size: 13px; }
.review-history { margin: 0 24px 16px; font-size: 14px; }
.customer-list > .error { margin: 0 24px 16px; }
.customer-list td { font-size: 14px; }
.customer-list th:last-child { width: 60px; }
.customer-cell { display: flex; align-items: center; gap: 12px; }
.customer-text { display: flex; flex-direction: column; gap: 2px; line-height: 1.35; min-width: 0; }
.customer-text .code { font-size: 12px; font-weight: 500; color: var(--muted); }
.exposure-cell { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.holdings-line { margin-top: 6px; }
.holdings-line:empty { display: none; }
.last-contact { display: flex; flex-direction: column; align-items: flex-start; gap: 4px; }
.exposure-amount { color: var(--bad); }
.facts { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px; margin: 0 0 16px; }
.facts > div { background: var(--surface); border: 1px solid var(--border); border-radius: 16px; padding: 12px 16px; min-width: 0; }
.facts dt { font-size: 12px; color: var(--muted); font-weight: 600; }
.facts dd { margin: 2px 0 0; font-size: 15px; font-weight: 700; overflow-wrap: anywhere; }
.facts dd.bad { color: var(--bad); }
.facts small { font-size: 12px; font-weight: 500; color: var(--muted); }
@media (max-width: 640px) {
  .customer-list tr { position: relative; }
  .customer-list .toggle-cell { position: absolute; top: 12px; right: 16px; }
  .customer-list td:first-child { padding-right: 48px !important; }
  .customer-list tr.expanded { background: #f8faff; }
  .customer-list td[data-label]::before { display: none; }
  .customer-list .detail > td { padding: 0 0 8px !important; }
  .customer-list .detail.expanded { padding-top: 0; border-top: none; }
}
.customer-list .expanded > td { background: #f8faff; }
.customer-list .detail > td { padding: 0 24px 20px; border-top: none; }
.detail-grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(0, 1fr); gap: 16px; }
.detail-panel { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius-md); padding: 16px; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.detail-panel h3 { margin: 0; font-size: 15px; font-weight: 700; }
.detail-panel p { margin: 0; }
.holding-list { display: flex; flex-direction: column; gap: 8px; }
.holding { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; background: var(--surface-2); border-radius: var(--radius-md); padding: 12px 16px; }
.holding.alerted { background: #fdf3f3; }
.holding .corp-mark { width: 36px; height: 36px; border-radius: 12px; background: var(--surface); font-size: 14px; }
.holding-text { flex: 1; min-width: 0; display: flex; flex-direction: column; line-height: 1.35; overflow-wrap: anywhere; }
.holding-text small { font-size: 12px; font-weight: 400; }
.holding-alert { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-left: auto; font-size: 13px; }
.holding-alert a { display: inline-flex; align-items: center; min-height: 44px; font-weight: 600; white-space: nowrap; }
.rule-note { font-size: 12px; }
.history-head { gap: 8px; }
.history-note { border-radius: 12px; background: var(--surface-2); padding: 8px 12px; font-size: 13px; color: #2a3554; white-space: pre-wrap; overflow-wrap: anywhere; }
.timeline-body { flex: 1; }
.timeline summary { min-height: 44px; align-content: center; cursor: pointer; color: var(--blue-strong); font-size: 13px; font-weight: 600; }
.timeline .message { font-size: 13px; overflow-wrap: anywhere; }
@media (max-width: 800px) { .detail-grid { grid-template-columns: 1fr; } }
</style>

<script setup>
// 고객 현황: 내 담당 고객의 보유 종목과 경보 종목 노출을 한 화면에서 본다 (읽기 전용). 고객 연락(우선순위·이력)은 삭제됐다.
import { computed, onMounted, reactive, ref } from 'vue'

import { api, errorMessage } from '../api/client'
import LevelBadge from '../components/LevelBadge.vue'
import { formatWon } from '../labels'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const customerTabs = { all: '전체', exposed: '경보 종목 보유', critical: 'Critical 노출' }

const data = ref({ summary: { customers: 0, exposed: 0, critical: 0, high: 0 }, customers: [] })
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

const rows = computed(() => {
  const q = query.value.trim().toLowerCase()
  return data.value.customers.filter((c) => {
    if (filter.value === 'exposed' && !c.exposure.alert_count) return false
    if (filter.value === 'critical' && c.exposure.top_level !== 'Critical') return false
    return !q || c.name.toLowerCase().includes(q) || c.customer_id.toLowerCase().includes(q)
  })
})

// 종목명 비중% · 경보 등급 (공백을 명시해 "KODEX 20037.5%"처럼 붙지 않게 한다)
const chipText = (h) => [h.stock_name, h.ratio_pct != null ? `${h.ratio_pct}%` : '', h.alert ? `· ${h.alert.level}` : ''].filter(Boolean).join(' ')
const alertedHoldings = (c) => c.holdings.filter((h) => h.alert)
const toggle = (id) => (open[id] = !open[id])
</script>

<template>
  <div class="page-head">
    <div>
      <p class="eyebrow">조회 전용 · 보유 종목과 경보 노출</p>
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

  <div class="stat-grid">
    <div class="stat">
      <span class="stat-label">담당 고객</span>
      <div class="stat-value"><strong>{{ data.summary.customers }}</strong><span>명</span></div>
      <span v-if="auth.pb?.pb_id && auth.pb?.name" class="stat-note">{{ auth.pb.pb_id }} {{ auth.pb.name }} PB</span>
    </div>
    <div class="stat">
      <span class="stat-label"><span class="dot dot-high" aria-hidden="true"></span>경보 종목 보유</span>
      <div class="stat-value"><strong>{{ data.summary.exposed }}</strong><span>명</span></div>
      <span class="stat-note">High·Critical 경보 종목</span>
    </div>
    <div class="stat">
      <span class="stat-label"><span class="dot dot-critical" aria-hidden="true"></span>Critical / High 노출</span>
      <div class="stat-value"><strong>{{ data.summary.critical }} / {{ data.summary.high }}</strong><span>명</span></div>
      <span class="stat-note">가장 높은 노출 등급 기준</span>
    </div>
  </div>

  <section class="card flush customer-list" aria-label="고객 목록">
    <div class="card-head">
      <div class="tabs" role="group" aria-label="고객 보기">
        <button v-for="(label, value) in customerTabs" :key="value" type="button" :class="{ active: filter === value }" :aria-pressed="filter === value" @click="filter = value">
          {{ label }}<span v-if="filter === value" class="count">{{ rows.length }}</span>
        </button>
      </div>
      <span class="muted list-hint">줄을 누르면 보유 종목이 펼쳐져요</span>
    </div>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th scope="col">노출 등급</th>
            <th scope="col">고객</th>
            <th scope="col">투자성향</th>
            <th scope="col" class="num">총자산</th>
            <th scope="col">보유 종목</th>
            <th scope="col" class="num">경보 노출액</th>
            <th scope="col"><span class="sr-only">펼치기</span></th>
          </tr>
        </thead>
        <tbody>
          <template v-for="c in rows" :key="c.customer_id">
            <tr class="clickable" :class="{ expanded: open[c.customer_id] }" @click="toggle(c.customer_id)">
              <td>
                <LevelBadge v-if="c.exposure.top_level" :level="c.exposure.top_level" />
                <span v-else class="muted">-</span>
              </td>
              <td>
                <div class="customer-cell">
                  <span class="avatar sm" aria-hidden="true">{{ (c.name || '-').slice(0, 1) }}</span>
                  <div class="customer-text"><strong>{{ c.name }}</strong><span class="sub">{{ c.customer_id }}</span></div>
                </div>
              </td>
              <td class="nowrap">{{ c.risk_profile || '-' }}</td>
              <td class="num nowrap">{{ formatWon(c.total_assets) }}</td>
              <td>
                <div class="chip-row">
                  <span v-for="h in c.holdings" :key="h.stock_code" class="chip" :class="h.alert ? 'alert' : 'stock'" :title="h.on_credit ? '신용거래' : ''">{{ chipText(h) }}</span>
                  <span v-if="!c.holdings.length" class="muted">보유 종목 없음</span>
                </div>
              </td>
              <td class="num nowrap">
                <strong v-if="c.exposure.amount" class="exposure-amount">{{ formatWon(c.exposure.amount) }}</strong>
                <span v-else class="muted">-</span>
              </td>
              <td>
                <button type="button" class="icon-button" :class="{ active: open[c.customer_id] }" :aria-label="`${c.name} ${open[c.customer_id] ? '접기' : '펼치기'}`" :aria-expanded="!!open[c.customer_id]" :aria-controls="`customer-detail-${c.customer_id}`" @click.stop="toggle(c.customer_id)">
                  <svg aria-hidden="true" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path :d="open[c.customer_id] ? 'M6 15l6-6 6 6' : 'M6 9l6 6 6-6'" /></svg>
                </button>
              </td>
            </tr>
            <tr v-if="open[c.customer_id]" :id="`customer-detail-${c.customer_id}`" class="detail expanded">
              <td colspan="7">
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
                            <LevelBadge :level="h.alert.level" />
                            <RouterLink :to="{ name: 'alert-detail', params: { assessmentId: String(h.alert.assessment_id) } }" @click.stop>경보 보기</RouterLink>
                          </template>
                          <span v-else class="muted">경보 없음</span>
                        </div>
                      </div>
                      <p v-if="!c.holdings.length" class="muted">보유 종목 없음</p>
                    </div>
                    <p v-if="alertedHoldings(c).length" class="muted rule-note">
                      경보 종목 {{ alertedHoldings(c).length }}개 · 종목별로 가장 최근의 High·Critical 평가를 보여줘요.
                    </p>
                  </section>
                </div>
              </td>
            </tr>
          </template>
          <tr v-if="!rows.length"><td colspan="7" class="empty">{{ loading ? '불러오는 중…' : '조건에 맞는 고객이 없습니다.' }}</td></tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.customer-list .tabs { margin-bottom: 0; }
.customer-list .tabs button { min-height: 44px; }
.list-hint { font-size: 13px; }
.customer-list > .error { margin: 0 24px 16px; }
.customer-list table { min-width: 860px; }
.customer-list td { font-size: 14px; }
.customer-list th:last-child { width: 60px; }
.nowrap { white-space: nowrap; }
.customer-cell { display: flex; align-items: center; gap: 10px; }
.customer-text { display: flex; flex-direction: column; line-height: 1.35; }
.exposure-amount { color: var(--bad); }
.customer-list .expanded > td { background: #f8faff; }
.customer-list .detail > td { padding: 0 24px 20px; border-top: none; }
.detail-grid { display: grid; grid-template-columns: minmax(0, 1fr); gap: 16px; }
.detail-panel { background: var(--surface); border: 1px solid var(--border); border-radius: 20px; padding: 18px 20px; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.detail-panel h3 { margin: 0; font-size: 15px; font-weight: 700; }
.detail-panel p { margin: 0; }
.holding-list { display: flex; flex-direction: column; gap: 8px; }
.holding { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; background: var(--surface-2); border-radius: var(--radius-md); padding: 12px 14px; }
.holding.alerted { background: #fdf3f3; }
.holding .corp-mark { width: 36px; height: 36px; border-radius: 12px; background: var(--surface); font-size: 14px; }
.holding-text { flex: 1; min-width: 140px; display: flex; flex-direction: column; line-height: 1.35; overflow-wrap: anywhere; }
.holding-text small { font-size: 12px; font-weight: 400; }
.holding-alert { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-left: auto; font-size: 13px; }
.holding-alert a { display: inline-flex; align-items: center; min-height: 44px; font-weight: 600; white-space: nowrap; }
.rule-note { font-size: 12px; }
</style>

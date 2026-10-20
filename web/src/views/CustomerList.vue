<script setup>
// 고객 현황: 내 담당 고객의 보유 종목, 경보 종목 노출, 연락 이력을 한 화면에서 본다 (읽기 전용).
import { computed, onMounted, reactive, ref } from 'vue'

import { api, errorMessage } from '../api/client'
import LevelBadge from '../components/LevelBadge.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { formatTime, formatWon } from '../labels'

const data = ref({ summary: { customers: 0, exposed: 0, p1: 0, p2: 0, tasks: {} }, customers: [] })
const error = ref('')
const loading = ref(false)
const filter = ref('all') // all | exposed | p1
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
    if (filter.value === 'p1' && c.exposure.top_priority !== 'P1') return false
    return !q || c.name.toLowerCase().includes(q) || c.customer_id.toLowerCase().includes(q)
  })
})

const taskDone = computed(() => {
  const t = data.value.summary.tasks || {}
  return { open: (t['승인'] || 0) + (t['보류'] || 0), done: t['연락완료'] || 0, rejected: t['반려'] || 0 }
})

// 종목명 비중% · 경보 등급 (공백을 명시해 "KODEX 20037.5%"처럼 붙지 않게 한다)
const chipText = (h) => [h.stock_name, h.ratio_pct != null ? `${h.ratio_pct}%` : '', h.alert ? `· ${h.alert.level}` : ''].filter(Boolean).join(' ')
const alertedHoldings = (c) => c.holdings.filter((h) => h.alert)
const toggle = (id) => (open[id] = !open[id])
</script>

<template>
  <div class="page-head">
    <div>
      <h1>고객 현황</h1>
      <p class="muted">내 담당 고객 {{ data.summary.customers }}명 · 보유 종목, 경보 노출, 연락 이력 (조회 전용)</p>
    </div>
    <div class="row">
      <input v-model="query" placeholder="이름 또는 고객 ID" />
      <select v-model="filter">
        <option value="all">전체 고객</option>
        <option value="exposed">경보 종목 보유 고객</option>
        <option value="p1">최우선(P1) 연락 대상</option>
      </select>
      <button class="ghost small" :disabled="loading" @click="load">새로고침</button>
    </div>
  </div>
  <p v-if="error" class="error">{{ error }}</p>

  <div class="tiles">
    <div class="tile"><small>담당 고객</small><strong>{{ data.summary.customers }}</strong><span class="muted">명</span></div>
    <div class="tile warn"><small>경보 종목 보유</small><strong>{{ data.summary.exposed }}</strong><span class="muted">명</span></div>
    <div class="tile bad"><small>최우선 P1 / P2</small><strong>{{ data.summary.p1 }} / {{ data.summary.p2 }}</strong><span class="muted">명</span></div>
    <div class="tile"><small>연락 업무</small><strong>{{ taskDone.open }}</strong><span class="muted">건 진행 · 완료 {{ taskDone.done }} · 반려 {{ taskDone.rejected }}</span></div>
  </div>

  <div class="card">
    <table>
      <thead>
        <tr>
          <th>우선순위</th>
          <th>고객</th>
          <th>투자성향</th>
          <th class="num">총자산</th>
          <th>보유 종목</th>
          <th class="num">경보 노출액</th>
          <th>최근 연락</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <template v-for="c in rows" :key="c.customer_id">
          <tr class="clickable" @click="toggle(c.customer_id)">
            <td>
              <span v-if="c.exposure.top_priority" class="prio" :class="`prio-${c.exposure.top_priority}`">{{ c.exposure.top_priority }}</span>
              <span v-else class="muted">-</span>
            </td>
            <td><strong>{{ c.name }}</strong> <small>{{ c.customer_id }}</small></td>
            <td class="nowrap">{{ c.risk_profile || '-' }}</td>
            <td class="num">{{ formatWon(c.total_assets) }}</td>
            <td>
              <span v-for="h in c.holdings" :key="h.stock_code" class="chip" :class="{ alert: h.alert }" :title="h.on_credit ? '신용거래' : ''">
                {{ chipText(h) }}
              </span>
              <span v-if="!c.holdings.length" class="muted">보유 종목 없음</span>
            </td>
            <td class="num">{{ c.exposure.amount ? formatWon(c.exposure.amount) : '-' }}</td>
            <td>
              <template v-if="c.last_contact">
                <StatusBadge :status="c.last_contact.status" />
                <div class="sub">{{ formatTime(c.last_contact.when) }}</div>
              </template>
              <span v-else class="muted">연락 이력 없음</span>
            </td>
            <td class="chev">{{ open[c.customer_id] ? '▲' : '▼' }}</td>
          </tr>

          <tr v-if="open[c.customer_id]" class="detail">
            <td colspan="8">
              <div class="detail-grid">
                <div>
                  <h3>보유 종목</h3>
                  <table class="inner">
                    <thead>
                      <tr><th>종목</th><th class="num">평가금액</th><th class="num">비중</th><th>신용</th><th>경보</th></tr>
                    </thead>
                    <tbody>
                      <tr v-for="h in c.holdings" :key="h.stock_code">
                        <td>{{ h.stock_name }} <small>{{ h.stock_code }}</small></td>
                        <td class="num">{{ formatWon(h.eval_amount) }}</td>
                        <td class="num">{{ h.ratio_pct ?? '-' }}%</td>
                        <td>{{ h.on_credit ? '있음' : '-' }}</td>
                        <td>
                          <template v-if="h.alert">
                            <LevelBadge :level="h.alert.level" />
                            <span v-if="h.alert.priority" class="prio" :class="`prio-${h.alert.priority}`"> {{ h.alert.priority }}</span>
                            <RouterLink :to="{ name: 'alert-detail', params: { assessmentId: String(h.alert.assessment_id) } }" @click.stop>
                              <small> 경보 보기</small>
                            </RouterLink>
                          </template>
                          <span v-else class="muted">-</span>
                        </td>
                      </tr>
                    </tbody>
                  </table>
                  <p v-if="alertedHoldings(c).length" class="muted note">
                    경보 종목 {{ alertedHoldings(c).length }}개 · 우선순위는 경보 화면과 같은 영향도 규칙(보유 비중·평가금액·투자성향·신용거래)으로 계산합니다.
                  </p>
                </div>

                <div>
                  <h3>연락 이력</h3>
                  <p v-if="!c.history.length" class="muted">아직 안내문 승인·반려 기록이 없습니다.</p>
                  <ul v-else class="history">
                    <li v-for="(x, i) in c.history" :key="i">
                      <div class="row">
                        <StatusBadge :status="x.status" />
                        <span v-if="x.priority" class="prio" :class="`prio-${x.priority}`">{{ x.priority }}</span>
                        <strong>{{ x.corp_name || '-' }}</strong>
                        <small>{{ formatTime(x.when) }}</small>
                      </div>
                      <p v-if="x.note" class="muted note">{{ x.kind === 'reject' ? '반려 사유: ' : '메모: ' }}{{ x.note }}</p>
                      <details v-if="x.message_preview">
                        <summary><small>승인한 안내문 보기</small></summary>
                        <blockquote>{{ x.message_preview }}</blockquote>
                      </details>
                    </li>
                  </ul>
                </div>
              </div>
            </td>
          </tr>
        </template>
        <tr v-if="!rows.length">
          <td colspan="8" class="empty">{{ loading ? '불러오는 중…' : '조건에 맞는 고객이 없습니다.' }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

<style scoped>
.tiles { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 16px; display: flex; flex-direction: column; gap: 2px; }
.tile strong { font-size: 24px; line-height: 1.2; }
.tile.warn strong { color: var(--warn); }
.tile.bad strong { color: var(--bad); }
.num { text-align: right; white-space: nowrap; }
.nowrap { white-space: nowrap; }
.sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
.clickable { cursor: pointer; }
.clickable:hover { background: var(--bg); }
.chev { color: var(--muted); width: 24px; text-align: center; }
.chip { display: inline-block; margin: 2px 4px 2px 0; padding: 2px 8px; border-radius: 999px; background: var(--bg); border: 1px solid var(--border); font-size: 12px; white-space: nowrap; }
.chip.alert { background: var(--bad-soft); border-color: transparent; color: var(--bad); font-weight: 600; }
.detail td { background: var(--bg); }
.detail-grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 24px; }
.detail h3 { margin: 0 0 8px; font-size: 14px; }
.inner th, .inner td { padding: 6px 8px; }
.note { margin: 6px 0 0; font-size: 12px; }
.history { list-style: none; margin: 0; padding: 0; }
.history li { padding: 8px 0; border-bottom: 1px solid var(--border); }
.history li:last-child { border-bottom: none; }
.history blockquote { margin: 6px 0 0; padding: 8px 10px; background: var(--surface); border-left: 3px solid var(--border); font-size: 13px; white-space: pre-wrap; }
@media (max-width: 800px) { .tiles { grid-template-columns: repeat(2, 1fr); } .detail-grid { grid-template-columns: 1fr; } }
</style>

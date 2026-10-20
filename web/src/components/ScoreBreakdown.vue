<script setup>
// 점수 내역 표. B의 calculate_risk_score breakdown 항목을 사람이 읽는 말로 바꿔 보여준다.
// 항목 모양: {group: BASE|A|B, item, points, weight, raw_points, base_points, extra_points, extras[],
//            filed_at, rcept_no, fixed_level, evidence[], note, from_history, reasons[]}
// 가짜 모드처럼 {item, points}만 있어도 깨지지 않는다.
import { computed } from 'vue'

const props = defineProps({
  reasons: { type: Array, default: () => [] },
  totalScore: { type: Number, default: null },
  comboUpgraded: { type: Boolean, default: false },
})

const GROUPS = {
  BASE: { label: '기본 점수', hint: '직전 사업보고서 재무제표', cls: 'g-base' },
  A: { label: '확정 악재', hint: '점수와 상관없이 등급 고정', cls: 'g-a' },
  B: { label: '숨은 신호', hint: '공시에서 찾은 위험 신호', cls: 'g-b' },
}

const num = (v) => (v == null || Number.isNaN(Number(v)) ? null : Number(Number(v).toFixed(2)))
const pct = (w) => `${Math.round(Number(w) * 100)}%`
const dartUrl = (rcept) => `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${rcept}`
const short = (s, n = 90) => (s && s.length > n ? `${s.slice(0, n)}…` : s)

const rows = computed(() =>
  (props.reasons || []).map((r) => {
    const g = GROUPS[r.group] || { label: '항목', hint: '', cls: 'g-b' }
    const evidence = Array.isArray(r.evidence) ? r.evidence : []
    const detail = []
    if (r.group === 'A') {
      detail.push('점수와 상관없이 등급을 고정하는 사건')
    } else if (r.group === 'BASE') {
      for (const x of r.reasons || []) {
        if (typeof x === 'string') detail.push(x)
        else if (x?.reason) detail.push(`${x.reason}${x.points != null ? ` (+${x.points}점)` : ''}`)
      }
    } else if (r.group === 'B' && r.raw_points != null) {
      const parts = [`기본 ${num(r.base_points ?? r.raw_points)}점`]
      if (r.extra_points) parts.push(`추가 ${num(r.extra_points)}점${r.extras?.length ? ` (${r.extras.join(', ')})` : ''}`)
      detail.push(parts.join(' + '))
      if (r.weight != null && Number(r.weight) !== 1) detail.push(`${r.from_history ? '6~12개월 전 신호' : '시간 반영'} × ${pct(r.weight)}`)
    }
    return {
      group: g,
      item: r.item || r.signal_type || '항목',
      filedAt: r.filed_at,
      history: !!r.from_history,
      kind: r.group === 'BASE' ? '직전 사업보고서 기준' : r.from_history ? '과거 신호' : '이번 공시',
      points: num(r.points),
      fixedLevel: r.fixed_level,
      detail,
      note: r.note,
      needsCheck: /수동 확인|근거 인용 없음/.test(r.note || ''),
      evidence,
      rcept: r.rcept_no || evidence[0]?.rcept_no,
    }
  }),
)
const fixedLevels = computed(() => rows.value.map((r) => r.fixedLevel).filter(Boolean))
const sum = computed(() => props.totalScore ?? rows.value.reduce((a, r) => a + (r.points || 0), 0))
</script>

<template>
  <table v-if="rows.length" class="score-table">
    <thead>
      <tr>
        <th>구분</th>
        <th>항목</th>
        <th class="num">점수</th>
        <th>계산</th>
        <th>근거 인용</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(r, i) in rows" :key="i">
        <td>
          <span class="group" :class="r.group.cls" :title="r.group.hint">{{ r.group.label }}</span>
        </td>
        <td>
          <strong>{{ r.item }}</strong>
          <div class="sub">
            <template v-if="r.filedAt">{{ r.filedAt }} · </template>{{ r.kind }}
          </div>
        </td>
        <td class="num">
          <template v-if="r.fixedLevel"><span class="fixed">등급 {{ r.fixedLevel }} 고정</span></template>
          <template v-else-if="r.points != null"><strong>{{ r.points }}</strong>점</template>
          <template v-else>-</template>
        </td>
        <td>
          <div v-for="(d, j) in r.detail" :key="j" class="calc">{{ d }}</div>
          <div v-if="r.note" class="calc" :class="{ warn: r.needsCheck }">{{ r.note }}</div>
          <span v-if="!r.detail.length && !r.note" class="muted">-</span>
        </td>
        <td class="evidence">
          <template v-if="r.evidence.length">
            <details v-for="(e, j) in r.evidence" :key="j">
              <summary>“{{ short(e.quote) }}”</summary>
              <blockquote>{{ e.quote }}</blockquote>
              <small>{{ e.source }} · {{ e.location }}</small>
            </details>
            <a v-if="r.rcept" :href="dartUrl(r.rcept)" target="_blank" rel="noopener"><small>DART 원문 {{ r.rcept }}</small></a>
          </template>
          <span v-else class="muted">근거 인용 없음</span>
        </td>
      </tr>
    </tbody>
    <tfoot>
      <tr>
        <td colspan="2"><strong>합계</strong></td>
        <td class="num"><strong>{{ num(sum) }}</strong>점</td>
        <td colspan="2">
          <span v-if="fixedLevels.length" class="calc">확정 악재가 있어 총점과 상관없이 등급이 {{ fixedLevels[0] }}(으)로 고정됨</span>
          <span v-else-if="comboUpgraded" class="calc">서로 다른 신호 3종류 이상 → 조합 규칙으로 등급 한 단계 상향</span>
          <span v-else class="muted">등급 기준: 0~3 Low · 4~6 Medium · 7~9 High · 10 이상 Critical</span>
        </td>
      </tr>
    </tfoot>
  </table>
  <p v-else class="muted">점수 내역이 없습니다.</p>
</template>

<style scoped>
.score-table td, .score-table th { vertical-align: top; }
.num { text-align: right; white-space: nowrap; }
.sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
.group { display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; white-space: nowrap; }
.g-base { background: var(--info-soft); color: var(--info); }
.g-a { background: var(--bad-soft); color: var(--bad); }
.g-b { background: var(--warn-soft); color: var(--warn); }
.fixed { color: var(--bad); font-weight: 600; }
.calc { font-size: 13px; line-height: 1.5; }
.calc.warn { color: var(--warn); font-weight: 600; }
.evidence { max-width: 420px; }
.evidence details { margin-bottom: 4px; }
.evidence summary { cursor: pointer; font-size: 13px; color: var(--text); }
.evidence blockquote { margin: 6px 0 4px; padding: 8px 10px; background: var(--bg); border-left: 3px solid var(--border); font-size: 12px; white-space: pre-wrap; word-break: break-all; }
tfoot td { border-top: 2px solid var(--border); border-bottom: none; }
</style>

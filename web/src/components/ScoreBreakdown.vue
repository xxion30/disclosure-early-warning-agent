<script setup>
import EvidenceCitation from './EvidenceCitation.vue'
// 위험 항목 표. 점수 숫자는 보여 주지 않는다(서버가 응답에서 뺀다). 항목은 점수 높은 순으로 이미 정렬돼 온다.
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
  RED: { label: '즉시 경보', hint: '즉시 확인이 필요한 위험 사건', cls: 'g-a' },
  ORANGE: { label: '사건 경보', hint: '공시에서 확인한 사건 위험', cls: 'g-b' },
  YELLOW: { label: '재무 경보', hint: '공시 재무 지표의 위험', cls: 'g-base' },
  BASE: { label: '재무 상태', hint: '직전 사업보고서 재무제표', cls: 'g-base' },
  A: { label: '확정 악재', hint: '점수와 상관없이 등급 고정', cls: 'g-a' },
  B: { label: '숨은 신호', hint: '공시에서 찾은 위험 신호', cls: 'g-b' },
}

const rows = computed(() =>
  (props.reasons || []).map((r) => {
    const g = GROUPS[r.group] || { label: '항목', hint: '', cls: 'g-b' }
    const evidence = Array.isArray(r.evidence) ? r.evidence : []
    const detail = []
    if (r.group === 'A') {
      detail.push('확정 악재: 점수와 상관없이 경보 등급이 정해지는 사건')
    } else if (r.group === 'BASE') {
      for (const x of r.reasons || []) {
        if (typeof x === 'string') detail.push(x)
        else if (x?.reason) detail.push(x.reason)
      }
    } else if (r.group === 'B' && r.extras?.length) {
      detail.push(`추가 사유: ${r.extras.join(', ')}`)
    }
    return {
      group: g,
      item: r.item || r.signal_type || '항목',
      filedAt: r.filed_at,
      history: !!r.from_history,
      kind: r.group === 'BASE' ? '직전 사업보고서 기준' : r.from_history ? '과거 신호' : '이번 공시',
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
</script>

<template>
  <table v-if="rows.length" class="score-table stack-table">
    <thead>
      <tr>
        <th scope="col">위험 항목</th>
        <th scope="col">근거 인용</th>
      </tr>
    </thead>
    <tbody>
      <tr v-for="(r, i) in rows" :key="i">
        <td class="item-cell">
          <div class="tags">
            <span class="group" :class="r.group.cls" :title="r.group.hint">{{ r.group.label }}</span>
            <span v-if="r.fixedLevel" class="fixed" :class="`fixed-${r.fixedLevel.toLowerCase()}`">{{ r.fixedLevel }} 고정</span>
          </div>
          <strong>{{ r.item }}</strong>
          <div class="sub">
            <template v-if="r.filedAt">{{ r.filedAt }} · </template>{{ r.kind }}
          </div>
          <div v-for="(d, j) in r.detail" :key="j" class="calc">{{ d }}</div>
          <div v-if="r.note" class="calc" :class="{ warn: r.needsCheck }">{{ r.note }}</div>
        </td>
        <td class="evidence">
          <!-- 발표 때 근거가 바로 보이도록 첫 인용은 펼쳐 두고, 나머지는 접는다 -->
          <div v-if="r.evidence.length"><EvidenceCitation v-for="(e,j) in r.evidence" :key="j" :evidence="e" /></div>
          <span v-else class="muted">근거 인용 없음</span>
        </td>
      </tr>
    </tbody>
    <tfoot v-if="fixedLevels.length || comboUpgraded">
      <tr>
        <td colspan="2">
          <span v-if="fixedLevels.length" class="calc fixed-note">확정 악재가 있어 등급이 {{ fixedLevels[0] }}(으)로 고정됨</span>
          <span v-else class="calc">여러 위험 조건이 함께 확인됐습니다.</span>
        </td>
      </tr>
    </tfoot>
  </table>
  <p v-else class="muted empty-note">위험 항목이 없습니다.</p>
</template>

<style scoped>
.score-table td, .score-table th { vertical-align: top; }
.num { text-align: right; white-space: nowrap; }
.sub { font-size: 12px; color: var(--muted); margin-top: 2px; }
.pts { font-size: 16px; }
.group { display: inline-block; padding: 5px 12px; border-radius: 999px; font-size: 12px; font-weight: 700; white-space: nowrap; }
.g-base { background: var(--blue-soft); color: var(--blue-strong); }
.g-a { background: var(--bad-soft); color: var(--bad); }
.g-b { background: var(--warn-soft); color: var(--warn); }
.fixed { display: inline-block; padding: 5px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; white-space: nowrap; background: var(--warn-soft); color: var(--warn); }
.fixed-critical { background: var(--critical); color: #fff; }
.calc { font-size: 14px; line-height: 1.55; color: #2a3554; }
.calc.warn { color: var(--warn); font-weight: 600; }
.fixed-note { color: var(--bad); font-weight: 600; }
.item-cell { display: flex; flex-direction: column; gap: 4px; }
.item-cell .tags { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 2px; }
.item-cell .calc { margin-top: 2px; overflow-wrap: anywhere; }
.evidence { width: 48%; }
.evidence figcaption { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-top: 6px; }
@media (max-width: 640px) {
  .evidence { width: auto; }
  .score-table td.evidence { padding-top: 8px !important; }
}
.evidence .src { font-size: 12px; font-weight: 600; overflow-wrap: anywhere; }
.evidence .chip { white-space: normal; overflow-wrap: anywhere; }
.evidence .quote blockquote { font-size: 15px; line-height: 1.55; word-break: keep-all; overflow-wrap: anywhere; }
.evidence summary { cursor: pointer; font-size: 12px; color: var(--muted); }
.evidence details blockquote { margin-top: 6px; }
.empty-note { margin: 0; padding: 0 24px 16px; }
@media (max-width: 640px) { .empty-note { padding: 0 16px 16px; } }
tfoot td { border-top: 2px solid var(--border); }
</style>

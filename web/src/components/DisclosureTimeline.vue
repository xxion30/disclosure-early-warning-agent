<script setup>
// 종목 공시 타임라인: 위에는 최근 12개월 띠(점 위치 = 공시일, 색 = 종류), 아래에는 최신순 세로 목록.
// 점수는 쓰지 않는다. 단계가 바뀐 공시는 큰 점과 "관찰 중 → 사건 경보" 표시로 보여준다.
import { computed } from 'vue'

import { STAGES, TIMELINE_KINDS } from '../labels'

const props = defineProps({
  timeline: { type: Array, default: () => [] },
  // 띠의 오른쪽 끝(기준일). 기본은 오늘
  endDate: { type: String, default: null },
})

const DAY = 86400000
const W = 720
const H = 64
const PAD = 16
const end = computed(() => (props.endDate ? Date.parse(props.endDate) : Date.now()))
const start = computed(() => end.value - 365 * DAY)
const x = (date) => PAD + ((Date.parse(date) - start.value) / (end.value - start.value)) * (W - PAD * 2)

// 띠에 그릴 점 (12개월 안의 것만). 같은 자리에 겹치면 살짝 위아래로 비켜 놓는다
const dots = computed(() => {
  const inRange = props.timeline.filter((t) => t.filed_at && Date.parse(t.filed_at) >= start.value)
  const used = []
  return inRange.map((t) => {
    const cx = x(t.filed_at)
    const near = used.filter((u) => Math.abs(u - cx) < 10).length
    used.push(cx)
    return { ...t, cx, cy: 30 + (near % 2 ? -10 : near ? 10 : 0), big: !!t.stage_after }
  })
})
// 월 눈금 (2달마다 글자)
const ticks = computed(() => {
  const out = []
  const d = new Date(start.value)
  d.setDate(1)
  d.setMonth(d.getMonth() + 1)
  let i = 0
  while (d.getTime() <= end.value) {
    out.push({ x: x(d.toISOString()), label: i % 2 === 0 ? `${String(d.getFullYear()).slice(2)}.${String(d.getMonth() + 1).padStart(2, '0')}` : '' })
    d.setMonth(d.getMonth() + 1)
    i += 1
  }
  return out
})
const half = computed(() => x(new Date(end.value - 182 * DAY).toISOString()))

// 목록: 최근 12개월은 펼쳐 두고, 그 이전 공시는 접어 둔다 (회사의 모든 공시가 오면 길어진다)
const inBand = (t) => !t.filed_at || Date.parse(t.filed_at) >= start.value
const recent = computed(() => {
  let others = 0
  return props.timeline.filter((t) => inBand(t) && (t.kind !== 'other' || others++ < 10))
})
const older = computed(() => props.timeline.filter((t) => !recent.value.includes(t)))

const tone = (t) => (t.kind === 'risk' ? (t.color || 'ORANGE').toLowerCase() : t.kind)
const summary = computed(() => `기준일까지 12개월 공시 ${dots.value.length}건, 위험 공시 ${dots.value.filter((d) => d.kind === 'risk').length}건`)
</script>

<template>
  <div class="dtl">
    <figure class="dtl-band">
      <svg :viewBox="`0 0 ${W} ${H + 18}`" role="img" :aria-label="summary">
        <rect :x="PAD" y="10" :width="half - PAD" height="40" rx="10" class="zone-old" />
        <rect :x="half" y="10" :width="W - PAD - half" height="40" rx="10" class="zone-new" />
        <text :x="PAD + 8" y="22" class="zone-label">6~12개월 전 · 절반 반영</text>
        <text :x="half + 8" y="22" class="zone-label">최근 6개월</text>
        <line :x1="PAD" :x2="W - PAD" y1="50" y2="50" class="axis" />
        <g v-for="t in ticks" :key="t.x">
          <line :x1="t.x" :x2="t.x" y1="50" y2="54" class="axis" />
          <text v-if="t.label" :x="t.x" :y="H + 12" text-anchor="middle" class="tick">{{ t.label }}</text>
        </g>
        <g v-for="d in dots" :key="d.rcept_no">
          <circle :cx="d.cx" :cy="d.cy" :r="d.big ? 8 : 5.5" class="dot" :class="tone(d)">
            <title>{{ d.filed_at }} · {{ TIMELINE_KINDS[d.kind] }} · {{ d.title }}</title>
          </circle>
        </g>
      </svg>
      <figcaption class="dtl-legend">
        <span><i class="dot-key red"></i>즉시</span>
        <span><i class="dot-key orange"></i>사건</span>
        <span><i class="dot-key yellow"></i>재무</span>
        <span><i class="dot-key correction"></i>정정</span>
        <span><i class="dot-key resolution"></i>해소</span>
        <span><i class="dot-key other"></i>그 밖 공시</span>
      </figcaption>
    </figure>

    <ol class="timeline dtl-list">
      <li v-for="t in recent" :key="t.rcept_no">
        <span class="timeline-rail">
          <span class="timeline-dot" :class="`dtl-${tone(t)}`"></span>
          <span class="timeline-line"></span>
        </span>
        <div class="timeline-body">
          <div class="dtl-row">
            <span class="dtl-date">{{ t.filed_at }}</span>
            <span class="dtl-kind" :class="`dtl-kind-${t.kind}`">{{ TIMELINE_KINDS[t.kind] || '공시' }}</span>
            <span v-if="t.reference_only" class="dtl-ref" title="자회사·종속회사 공시는 단계에 반영하지 않고 참고로만 보여 줘요">참고 · 자회사</span>
            <strong class="dtl-title">{{ t.title }}</strong>
          </div>
          <div v-if="t.stage_after" class="dtl-change">
            단계 변화: {{ STAGES[t.stage_before]?.label || '-' }} → <strong>{{ STAGES[t.stage_after]?.label }}</strong>
          </div>
          <a v-if="t.url" :href="t.url" target="_blank" rel="noopener" class="dtl-link">DART 원문 {{ t.rcept_no }}</a>
          <span v-else class="sub">접수번호 {{ t.rcept_no }}</span>
        </div>
      </li>
      <li v-if="!timeline.length" class="dtl-empty">아직 쌓인 공시가 없어요.</li>
    </ol>
    <details v-if="older.length" class="dtl-older">
      <summary>나머지 공시 {{ older.length }}건 보기</summary>
      <ul class="dtl-older-list">
        <li v-for="t in older" :key="t.rcept_no">
          <span class="dtl-date">{{ t.filed_at }}</span>
          <span v-if="t.kind !== 'other'" class="dtl-kind" :class="`dtl-kind-${t.kind}`">{{ TIMELINE_KINDS[t.kind] }}</span>
          <span v-if="t.reference_only" class="dtl-ref">참고 · 자회사</span>
          <a v-if="t.url" :href="t.url" target="_blank" rel="noopener" class="dtl-title">{{ t.title }}</a>
          <span v-else class="dtl-title">{{ t.title }}</span>
        </li>
      </ul>
    </details>
  </div>
</template>

<style scoped>
.dtl { display: flex; flex-direction: column; gap: 18px; }
.dtl-band { margin: 0; display: flex; flex-direction: column; gap: 8px; }
.dtl-band svg { width: 100%; height: auto; display: block; }
.zone-old { fill: var(--surface-2); }
.zone-new { fill: #f4f7fe; }
.zone-label { font-size: 11px; fill: var(--muted); }
.axis { stroke: var(--border); stroke-width: 1; }
.tick { font-size: 11px; fill: var(--muted); }
.dot { stroke: var(--surface); stroke-width: 2; }
.dot.red { fill: var(--critical); }
.dot.orange { fill: var(--warn-dot); }
.dot.yellow { fill: var(--caution-dot); }
.dot.correction { fill: var(--surface); stroke: var(--faint); stroke-width: 2; }
.dot.resolution { fill: var(--ok-dot); }
.dot.other { fill: #c5cad3; }
.dtl-legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--muted); }
.dtl-legend span { display: inline-flex; align-items: center; gap: 6px; }
.dot-key { width: 10px; height: 10px; border-radius: 999px; display: inline-block; }
.dot-key.red { background: var(--critical); }
.dot-key.orange { background: var(--warn-dot); }
.dot-key.yellow { background: var(--caution-dot); }
.dot-key.correction { background: var(--surface); box-shadow: inset 0 0 0 2px var(--faint); }
.dot-key.resolution { background: var(--ok-dot); }
.dot-key.other { background: #c5cad3; }
.dtl-list { margin: 0; }
.timeline-dot.dtl-red { background: var(--critical); }
.timeline-dot.dtl-orange { background: var(--warn-dot); }
.timeline-dot.dtl-yellow { background: var(--caution-dot); }
.timeline-dot.dtl-correction { background: var(--surface); box-shadow: inset 0 0 0 2px var(--faint); }
.timeline-dot.dtl-resolution { background: var(--ok-dot); }
.timeline-dot.dtl-other { background: #c5cad3; }
.dtl-row { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.dtl-date { font-size: 13px; color: var(--muted); font-variant-numeric: tabular-nums; }
.dtl-kind { padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 700; background: var(--neutral-soft); color: var(--muted); }
.dtl-kind-risk { background: var(--warn-soft); color: var(--warn); }
.dtl-kind-resolution { background: var(--ok-soft); color: var(--ok); }
.dtl-ref { padding: 2px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; color: var(--muted); box-shadow: inset 0 0 0 1px var(--border-strong); }
.dtl-title { font-size: 14px; overflow-wrap: anywhere; }
.dtl-change { font-size: 13px; color: var(--text); background: var(--surface-2); border-radius: 10px; padding: 6px 10px; align-self: flex-start; }
.dtl-link { font-size: 13px; font-weight: 600; }
.dtl-empty { color: var(--muted); font-size: 14px; }
.dtl-older-list { list-style: none; margin: 12px 0 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.dtl-older-list li { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; font-size: 14px; }
</style>

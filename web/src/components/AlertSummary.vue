<script setup>
// 경보 상단 요약: 이번 공시가 위험도에 준 영향을 한눈에 본다. 내용은 서버가 위험 항목에서 규칙으로 만든 값이다(AI 문장 아님). 점수 숫자는 보여 주지 않는다.
// 여기서는 "직전 평가 대비 무엇이 바뀌었나"와 이번 공시 항목만 보여준다.
import { computed } from 'vue'

import { stagesOf, stageText } from '../labels'
import StageBadge from './StageBadge.vue'

const props = defineProps({
  summary: { type: Object, required: true },
  level: { type: String, default: '' },
  stage: { type: String, default: null },
  stages: { type: Array, default: null },
})

const prev = computed(() => props.summary.previous)
// 비교는 새 단계가 있으면 가장 심한 단계로, 없으면 예전 등급으로
const nowKey = computed(() => stagesOf({ stage: props.stage, stages: props.stages })[0] || props.level)
const prevKey = computed(() => (prev.value ? stagesOf(prev.value)[0] || prev.value.level : null))
const changed = computed(() => prev.value && prevKey.value !== nowKey.value)
const filing = computed(() => props.summary.this_filing)

// 확정 악재: 빨강 / 이번 공시로 점수가 올라감: 주황 / 그 밖: 파랑
const tone = computed(() => (filing.value.fixed_level ? 'bad' : filing.value.items.length ? 'warn' : 'info'))
</script>

<template>
  <section class="card summary" :class="tone" aria-label="이번 공시의 영향">
    <div class="summary-head">
      <span class="summary-icon" aria-hidden="true">
        <svg v-if="tone === 'bad'" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 9v4M12 17h.01" /><path d="M10.3 3.9L2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z" /></svg>
        <svg v-else-if="tone === 'warn'" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17l6-6 4 4 8-8" /><path d="M15 7h6v6" /></svg>
        <svg v-else width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9" /><path d="M12 8h.01M11 12h1v4h1" /></svg>
      </span>
      <div class="summary-copy">
        <h2>이번 공시의 영향</h2>
        <p class="headline">{{ stageText(summary.headline) }}</p>
      </div>
    </div>

    <div class="changes">
      <div class="change">
        <span class="change-label">{{ stagesOf({ stage, stages }).length ? '단계' : '등급' }}</span>
        <div class="change-value">
          <template v-if="changed"><StageBadge :stage="prev.stage" :stages="prev.stages" :level="prev.level" /><span class="arrow" aria-hidden="true">→</span></template>
          <StageBadge :stage="stage" :stages="stages" :level="level" />
        </div>
        <span class="change-note">{{ !prev ? '직전 평가 없음' : changed ? '직전보다 바뀌었어요' : '직전과 같음' }}</span>
      </div>
      <div class="change">
        <span class="change-label">이번 공시의 위험 항목</span>
        <div class="change-value">
          <strong>{{ filing.fixed_level && !filing.items.length ? '등급 고정' : `${filing.items.length}건` }}</strong>
        </div>
        <span class="change-note">{{ filing.fixed_level ? '확정 악재라 등급이 고정돼요' : '이번 공시에서 찾은 항목' }}</span>
      </div>
    </div>

    <ul v-if="filing.items.length" class="items">
      <li v-for="(i, n) in filing.items" :key="n">
        <div class="item-head">
          <strong>{{ i.item }}</strong>
          <span v-if="i.fixed_level" class="tag fixed">확정 악재 · {{ i.fixed_level }}</span>
        </div>
        <blockquote v-if="i.quote">“{{ i.quote }}”</blockquote>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.summary { display: flex; flex-direction: column; gap: 16px; }
.summary.bad { border-color: #f3c4c4; }
.summary-head { display: flex; align-items: flex-start; gap: 16px; }
.summary-icon { width: 40px; height: 40px; border-radius: 14px; display: inline-flex; align-items: center; justify-content: center; flex: none; color: #fff; background: var(--blue); }
.bad .summary-icon { background: var(--critical); }
.warn .summary-icon { background: var(--warn-dot); }
.summary-copy { display: flex; flex-direction: column; gap: 4px; min-width: 0; }
.summary-copy h2 { margin: 0; font-size: 13px; font-weight: 600; color: var(--muted); }
.headline { margin: 0; font-size: 17px; font-weight: 700; line-height: 1.5; overflow-wrap: anywhere; }
.changes { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
@media (max-width: 800px) { .changes { grid-template-columns: minmax(0, 1fr); } }
.change { background: var(--surface-2); border-radius: var(--radius-md); padding: 16px; display: flex; flex-direction: column; gap: 8px; min-width: 0; }
.change-label { font-size: 13px; font-weight: 600; color: var(--muted); }
.change-value { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-height: 32px; }
.change-value strong { font-size: 22px; font-weight: 800; font-variant-numeric: tabular-nums; }
.from { font-size: 15px; color: var(--muted); font-variant-numeric: tabular-nums; }
.arrow { color: var(--muted); }
.delta { padding: 3px 10px; border-radius: var(--pill); background: var(--neutral-soft); color: var(--muted); font-size: 13px; font-weight: 700; }
.delta.up { background: var(--bad-soft); color: var(--bad); }
.change-note { font-size: 13px; color: var(--muted); }
.items { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 12px; }
.items li { display: flex; flex-direction: column; gap: 8px; }
.item-head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.item-head strong { font-size: 15px; }
.tag { padding: 4px 10px; border-radius: var(--pill); background: var(--warn-soft); color: var(--warn); font-size: 12px; font-weight: 700; white-space: nowrap; }
.tag.fixed { background: var(--critical); color: #fff; }
.items blockquote { margin: 0; font-size: 15px; overflow-wrap: anywhere; }
</style>

<script setup>
// 경보 상단 요약: 이번 공시가 위험도에 준 영향을 한눈에 본다. 숫자는 서버가 점수 내역·영향 고객에서 규칙으로 계산한 값이다(AI 문장 아님).
import { computed } from 'vue'

import LevelBadge from './LevelBadge.vue'

const props = defineProps({ summary: { type: Object, required: true }, level: { type: String, default: '' }, totalScore: { type: Number, default: null } })

const change = computed(() => props.summary.score_change)
const changeText = computed(() => (change.value == null ? '' : `${change.value > 0 ? '+' : ''}${change.value}점`))
const prev = computed(() => props.summary.previous)
const filing = computed(() => props.summary.this_filing)
const cust = computed(() => props.summary.customers)
</script>

<template>
  <div class="card summary" :class="{ fixed: filing.fixed_level }">
    <h2>이번 공시의 영향</h2>
    <p class="headline">{{ summary.headline }}</p>

    <div class="stats">
      <div class="stat">
        <small>총점</small>
        <strong>{{ totalScore ?? '-' }}점</strong>
        <span v-if="filing.fixed_level && prev" class="muted">직전 {{ prev.total_score }}점 · 확정 악재라 점수와 상관없이 등급 고정</span>
        <span v-else-if="changeText" class="muted" :class="{ up: change > 0 }">직전 {{ prev.total_score }}점 → {{ changeText }}</span>
        <span v-else class="muted">직전 평가 없음</span>
      </div>
      <div class="stat">
        <small>등급</small>
        <strong><LevelBadge :level="level" /></strong>
        <span v-if="prev && prev.level !== level" class="muted">직전 {{ prev.level }}</span>
        <span v-else-if="prev" class="muted">직전과 같음</span>
      </div>
      <div class="stat">
        <small>이번 공시 +점수</small>
        <strong>{{ filing.fixed_level && !filing.points ? '등급 고정' : `+${filing.points}점` }}</strong>
        <span class="muted">누적(기본·과거) {{ summary.carried_score ?? '-' }}점</span>
      </div>
      <div class="stat">
        <small>영향 고객</small>
        <template v-if="cust">
          <strong>{{ cust.total }}명</strong>
          <span class="muted">P1 {{ cust.p1 }} · P2 {{ cust.p2 }} · 내 결정 대기 {{ cust.my_pending }}</span>
        </template>
        <template v-else>
          <strong>-</strong>
          <span class="muted">엔진 연결 후 표시</span>
        </template>
      </div>
    </div>

    <ul v-if="filing.items.length" class="items">
      <li v-for="(i, n) in filing.items" :key="n">
        <strong>{{ i.item }}</strong>
        <span v-if="i.fixed_level" class="tag">확정 악재 · {{ i.fixed_level }}</span>
        <span v-else class="tag">+{{ i.points }}점</span>
        <div v-if="i.quote" class="quote">“{{ i.quote }}”</div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.summary { border-left: 4px solid var(--border); }
.summary.fixed { border-left-color: var(--bad); }
.headline { margin: 4px 0 12px; font-size: 15px; font-weight: 600; }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.stat { display: flex; flex-direction: column; gap: 2px; }
.stat strong { font-size: 20px; line-height: 1.3; }
.up { color: var(--bad); }
.items { list-style: none; margin: 12px 0 0; padding: 0; border-top: 1px solid var(--border); }
.items li { padding: 8px 0; border-bottom: 1px solid var(--border); }
.items li:last-child { border-bottom: none; }
.tag { margin-left: 8px; font-size: 12px; padding: 1px 8px; border-radius: 999px; background: var(--bg); border: 1px solid var(--border); }
.quote { margin-top: 4px; font-size: 13px; color: var(--muted); }
@media (max-width: 800px) { .stats { grid-template-columns: repeat(2, 1fr); } }
</style>

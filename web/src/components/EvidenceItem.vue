<script setup>
// 근거 하나를 그린다. 문서 근거는 원문 인용으로, API 근거(kind:'api')는 "어느 DART API의 어떤 값인지"로 보여 준다.
// API 근거에는 인용 문장이 없으므로 따옴표만 남는 일이 없게 한다. 값은 실제 금액·비율이고 점수가 아니다.
import { computed } from 'vue'

import { apiLabel, basisLabel, factValue, fieldLabel, isApiEvidence } from '../labels'

const props = defineProps({
  evidence: { type: Object, required: true },
  rceptNo: { type: String, default: null }, // 근거에 접수번호가 없을 때 쓸 항목의 접수번호
  linkable: { type: Boolean, default: true }, // 가짜 데이터 모드처럼 접수번호가 가짜면 DART 링크를 만들지 않는다
  clamp: { type: Number, default: 0 }, // 인용이 이보다 길면 줄이고 "전체 문장 보기"로 펼친다 (0이면 그대로)
  dense: { type: Boolean, default: false }, // 표 안처럼 좁은 곳
})

const api = computed(() => isApiEvidence(props.evidence))
const quote = computed(() => (typeof props.evidence.quote === 'string' ? props.evidence.quote.trim() : ''))
const long = computed(() => props.clamp > 0 && quote.value.length > props.clamp)
const rcept = computed(() => props.evidence.rcept_no || props.rceptNo)
const url = computed(() => (props.linkable && rcept.value ? `https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${encodeURIComponent(rcept.value)}` : null))
</script>

<template>
  <figure v-if="api" class="quote api-fact" :class="{ dense }">
    <span class="fact-kind">공시 항목 값</span>
    <p class="fact-line"><span class="fact-name">{{ fieldLabel(evidence.field) }}</span> <strong class="fact-value">{{ factValue(evidence.value, evidence.unit) }}</strong></p>
    <figcaption>
      <span class="chip strong">DART {{ apiLabel(evidence.api) }}</span>
      <span v-if="evidence.period" class="chip">{{ evidence.period }}</span>
      <span v-if="basisLabel(evidence.basis)" class="chip">{{ basisLabel(evidence.basis) }} 기준</span>
      <a v-if="url" :href="url" target="_blank" rel="noopener">DART 원문 확인</a>
    </figcaption>
  </figure>
  <figure v-else-if="quote" class="quote" :class="{ dense }">
    <blockquote :title="long ? quote : undefined">“{{ long ? `${quote.slice(0, clamp)}…` : quote }}”</blockquote>
    <figcaption>
      <span v-if="evidence.source" class="chip strong">{{ evidence.source }}</span>
      <a v-if="url" :href="url" target="_blank" rel="noopener">DART 원문 확인</a>
    </figcaption>
    <details v-if="long">
      <summary>전체 문장 보기</summary>
      <blockquote>“{{ quote }}”</blockquote>
    </details>
  </figure>
  <p v-else class="notice warn evidence-unknown">근거 형식을 읽지 못했어요. 공시 원문을 직접 확인해 주세요.</p>
</template>

<style scoped>
.quote { overflow-wrap: anywhere; }
.quote .chip { white-space: normal; overflow-wrap: anywhere; }
.quote a { display: inline-flex; align-items: center; min-height: 44px; font-weight: 600; }
.dense blockquote { font-size: 15px; line-height: 1.55; word-break: keep-all; }
.dense a { min-height: 36px; font-size: 13px; }
.api-fact { background: var(--surface-2); border: 1px dashed var(--border-strong); }
.fact-kind { font-size: 12px; font-weight: 700; color: var(--muted); }
.fact-line { margin: 2px 0 0; font-size: 15px; color: #2a3554; }
.fact-name { color: var(--muted); }
.fact-value { font-size: 16px; font-variant-numeric: tabular-nums; }
details summary { cursor: pointer; font-size: 12px; color: var(--muted); }
details blockquote { margin-top: 6px; }
.evidence-unknown { margin: 0; }
</style>

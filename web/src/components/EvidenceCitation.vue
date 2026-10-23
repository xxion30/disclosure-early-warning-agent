<script setup>
import { computed } from 'vue'
const props = defineProps({ evidence: {type:Object, required:true} })
const names = {impairment_pct:'자본잠식률', paid_in_capital:'자본금', total_equity:'자기자본', negative_years:'연속 적자 연수', current:'당기 영업현금흐름', previous:'전기 영업현금흐름', interest_coverage:'금융비용 기준 보상배율', operating_income:'영업이익', finance_costs:'금융비용', debt_to_cash_pct:'현금 대비 단기채무 비율', short_term_debt:'단기채무', cash:'현금성 자산', opinion:'감사의견', going_concern:'계속기업 불확실성'}
const value = (v) => typeof v === 'number' ? new Intl.NumberFormat('ko-KR', {maximumFractionDigits:4}).format(v) : String(v ?? '확인 불가')
const financialValues = computed(() => Object.entries(props.evidence.values || {}).map(([key,v]) => `${names[key] || key}: ${value(v)}${key.endsWith('_pct') ? '%' : ''}`))
</script>

<template>
  <figure class="quote evidence-citation">
    <template v-if="evidence.kind === 'financial'">
      <p><strong>{{ evidence.period }} · {{ evidence.basis === 'CONSOLIDATED' ? '연결 재무제표' : '개별 재무제표' }}</strong></p>
      <ul><li v-for="line in financialValues" :key="line">{{ line }}</li></ul>
      <p class="muted">공시 재무 지표 · 정정 버전 {{ evidence.revision }}</p>
    </template>
    <p v-else-if="evidence.kind === 'api'"><strong>공시 API 사실</strong> · {{ evidence.field }}: {{ value(evidence.value) }} {{ evidence.unit || '' }}</p>
    <blockquote v-else>“{{ evidence.quote }}”</blockquote>
    <figcaption>
      <span v-if="evidence.source" class="chip">{{ evidence.source }}</span>
      <a v-if="evidence.rcept_no" :href="`https://dart.fss.or.kr/dsaf001/main.do?rcpNo=${evidence.rcept_no}`" target="_blank" rel="noopener">DART 원문 {{ evidence.rcept_no }}</a>
    </figcaption>
  </figure>
</template>

<style scoped>
.evidence-citation { overflow-wrap:anywhere; }
.evidence-citation ul { padding-left:20px; margin:8px 0; }
.evidence-citation figcaption { display:flex; flex-wrap:wrap; gap:8px; }
.evidence-citation a { min-height:32px; display:inline-flex; align-items:center; }
</style>

<script setup>
import { computed } from 'vue'

import { ASSESSMENT_STATUS } from '../labels'

const props = defineProps({ status: { type: String, default: null } })
const label = computed(() => ASSESSMENT_STATUS[props.status] || props.status || '-')
// 업무 상태(승인, 반려, 연락완료, 보류, 승인대기)도 같은 배지를 쓴다
// 승인 대기 = 파랑(할 일), 완료 = 초록, 사람이 볼 것 = 주황, 실패·반려 = 빨강
const tone = computed(() => ({
  WAITING_APPROVAL: 'info', 승인대기: 'info',
  DONE: 'ok', 승인: 'ok', 연락완료: 'ok',
  MANUAL_CHECK: 'warn', 보류: 'warn',
  FAILED: 'bad', 반려: 'bad',
  RUNNING: 'muted',
}[props.status] || 'muted'))
</script>

<template>
  <span class="badge" :class="`tone-${tone}`">{{ label }}</span>
</template>

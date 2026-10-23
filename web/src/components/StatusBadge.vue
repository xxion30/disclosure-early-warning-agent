<script setup>
import { computed } from 'vue'

import { ASSESSMENT_STATUS } from '../labels'

const props = defineProps({ status: { type: String, default: null } })
const label = computed(() => ASSESSMENT_STATUS[props.status] || props.status || '-')
// 업무 상태(승인, 반려, 연락완료, 보류, 승인대기)도 같은 배지를 쓴다
// 승인 대기 = 파랑(할 일), 완료 = 초록, 사람이 볼 것(수동 확인) = 주황, 실패 = 빨강.
// 업무 상태 중 보류는 회색, 반려는 테두리 배지로 서로 구분한다 (위험색은 쓰지 않는다).
const tone = computed(() => ({
  WAITING_APPROVAL: 'info', 승인대기: 'info',
  DONE: 'ok', 승인: 'ok', 연락완료: 'ok',
  MANUAL_CHECK: 'warn', 보류: 'muted',
  FAILED: 'bad', 반려: 'outline',
  RUNNING: 'muted',
}[props.status] || 'muted'))
</script>

<template>
  <span class="badge" :class="`tone-${tone}`">{{ label }}</span>
</template>

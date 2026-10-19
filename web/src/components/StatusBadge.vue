<script setup>
import { computed } from 'vue'

import { ASSESSMENT_STATUS } from '../labels'

const props = defineProps({ status: { type: String, default: null } })
const label = computed(() => ASSESSMENT_STATUS[props.status] || props.status || '-')
// 업무 상태(승인, 반려, 연락완료, 보류, 승인대기)도 같은 배지를 쓴다
const tone = computed(() => ({
  WAITING_APPROVAL: 'warn', 승인대기: 'warn', 보류: 'warn',
  DONE: 'ok', 승인: 'ok', 연락완료: 'ok',
  FAILED: 'bad', 반려: 'bad', MANUAL_CHECK: 'bad',
  RUNNING: 'info',
}[props.status] || 'muted'))
</script>

<template>
  <span class="badge" :class="`tone-${tone}`">{{ label }}</span>
</template>

<script setup>
// 위험 단계 배지. stage/stages가 있으면 새 단계(즉시·사건·재무·복합·관찰)를, 없으면 예전 등급(level) 배지를 그린다.
// 목록에서는 가장 심한 단계 하나 + "+N", all을 주면(종목 상세) 단계를 모두 보여준다.
import { computed } from 'vue'

import { STAGES, stagesOf } from '../labels'
import LevelBadge from './LevelBadge.vue'

const props = defineProps({
  stage: { type: String, default: null },
  stages: { type: Array, default: null },
  level: { type: String, default: null },
  all: { type: Boolean, default: false },
})

const list = computed(() => stagesOf({ stage: props.stage, stages: props.stages }))
const shown = computed(() => (props.all ? list.value : list.value.slice(0, 1)))
const extra = computed(() => (props.all ? [] : list.value.slice(1)))
const extraText = computed(() => extra.value.map((s) => STAGES[s].label).join(', '))
</script>

<template>
  <span v-if="list.length" class="stage-group">
    <span
      v-for="s in shown"
      :key="s"
      class="badge stage"
      :class="`stage-${s.toLowerCase()}`"
      :title="STAGES[s].hint || undefined"
    >
      <span class="stage-dot" aria-hidden="true"></span>{{ STAGES[s].label }}
    </span>
    <span v-if="extra.length" class="badge stage-more" :title="`함께 난 단계: ${extraText}`">
      <span aria-hidden="true">+{{ extra.length }}</span>
      <span class="sr-only">함께 난 단계: {{ extraText }}</span>
    </span>
  </span>
  <LevelBadge v-else :level="level" />
</template>

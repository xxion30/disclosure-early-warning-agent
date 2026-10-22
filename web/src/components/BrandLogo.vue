<script setup>
// 다치 로고 (레이더 + 글자). variant="dark"는 네이비 바탕 위에 쓰는 흰 글자 버전.
// mark-only면 레이더 아이콘만 그린다.
import { computed } from 'vue'

const props = defineProps({
  variant: { type: String, default: 'light' }, // light | dark
  markOnly: { type: Boolean, default: false },
  height: { type: Number, default: 36 },
})

const c = computed(() =>
  props.variant === 'dark'
    ? { disc: '#1e2a5c', ring: '#34457f', word: '#ffffff' }
    : { disc: '#111b3a', ring: '#2e3d72', word: '#111b3a' },
)
const viewBox = computed(() => (props.markOnly ? '30 52 236 236' : '30 52 680 236'))
const width = computed(() => Math.round(props.height * (props.markOnly ? 1 : 680 / 236)))
</script>

<template>
  <svg :viewBox="viewBox" :width="width" :height="height" role="img" aria-label="다치">
    <circle cx="147" cy="170" r="112" :fill="c.disc" />
    <path d="M147 170 L147 58 A112 112 0 0 1 244 114 Z" fill="#3d6bf5" />
    <circle cx="147" cy="170" r="78" fill="none" :stroke="c.ring" stroke-width="6" />
    <circle cx="147" cy="170" r="42" fill="none" :stroke="c.ring" stroke-width="6" />
    <path d="M147 170 L236 120" stroke="#8ea5f5" stroke-width="13" stroke-linecap="round" />
    <circle cx="188" cy="104" r="17" fill="#ffffff" />
    <circle cx="188" cy="104" r="8" fill="#3d6bf5" />
    <circle cx="147" cy="170" r="14" fill="#ffffff" />
    <template v-if="!markOnly">
      <g fill="none" :stroke="c.word" stroke-width="36" stroke-linecap="round" stroke-linejoin="round">
        <path d="M420 110 H352 V222 H430" />
        <path d="M466 88 V244 M466 166 H500" />
        <path d="M552 82 H598 M532 128 H622 M578 142 L542 232 M586 162 L628 228" />
        <path d="M668 156 V242" />
      </g>
      <circle cx="668" cy="102" r="22" fill="#3d6bf5" />
    </template>
  </svg>
</template>

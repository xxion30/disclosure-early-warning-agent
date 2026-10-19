<script setup>
defineProps({ level: { type: String, default: null } })
</script>

<template>
  <span class="badge" :class="`level-${(level || 'none').toLowerCase()}`">{{ level || '-' }}</span>
</template>

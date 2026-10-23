<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { mockMode } from './api/mock'
import BrandLogo from './components/BrandLogo.vue'
import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
// 가짜 데이터 모드 표시 (화면을 옮길 때마다 다시 확인)
const isMock = computed(() => route.fullPath && mockMode())

const initial = computed(() => auth.pb?.name?.slice(0, 1) || 'P')

function logout() {
  auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <header v-if="auth.isLoggedIn" class="topbar">
    <div class="topbar-inner">
      <RouterLink to="/alerts" class="brand" aria-label="다치 홈">
        <BrandLogo :height="36" />
      </RouterLink>
      <nav aria-label="주 메뉴">
        <RouterLink to="/alerts">경보</RouterLink>
        <RouterLink to="/customers">고객 현황</RouterLink>
        <RouterLink to="/tasks">연락 업무</RouterLink>
      </nav>
      <span v-if="isMock" class="mock-pill" role="status">가짜 데이터 보는 중 <a href="?mock=off">끄기</a></span>
      <div v-if="auth.pb" class="who">
        <span class="avatar" aria-hidden="true">{{ initial }}</span>
        <span class="who-text">
          <strong>{{ auth.pb.name }} PB</strong>
          <small>{{ auth.pb.pb_id }}</small>
        </span>
        <button class="ghost small" @click="logout">로그아웃</button>
      </div>
    </div>
  </header>
  <main :class="auth.isLoggedIn ? 'container' : ''">
    <RouterView />
  </main>
</template>

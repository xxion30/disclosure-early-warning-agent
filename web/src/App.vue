<script setup>
import { computed } from 'vue'
import { useRouter } from 'vue-router'

import BrandLogo from './components/BrandLogo.vue'
import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
const router = useRouter()

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

<script setup>
import { useRouter } from 'vue-router'

import { useAuthStore } from './stores/auth'

const auth = useAuthStore()
const router = useRouter()

function logout() {
  auth.logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <header v-if="auth.isLoggedIn" class="topbar">
    <div class="brand">공시 조기경보</div>
    <nav>
      <RouterLink to="/alerts">경보</RouterLink>
      <RouterLink to="/customers">고객 현황</RouterLink>
      <RouterLink to="/tasks">연락 업무</RouterLink>
    </nav>
    <div class="who">
      <span v-if="auth.pb">{{ auth.pb.name }} PB <small>({{ auth.pb.pb_id }})</small></span>
      <button class="ghost" @click="logout">로그아웃</button>
    </div>
  </header>
  <main class="container">
    <RouterView />
  </main>
</template>

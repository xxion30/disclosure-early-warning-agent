<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { errorMessage } from '../api/client'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const email = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function submit() {
  error.value = ''
  loading.value = true
  try {
    await auth.login(email.value, password.value)
    router.push(route.query.redirect || '/alerts')
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

// 데모용 계정 채우기 (data/customers.json의 가짜 PB)
function fill(e) {
  email.value = e
  password.value = 'demo1234'
}
</script>

<template>
  <div class="login-wrap">
    <form class="card login" @submit.prevent="submit">
      <h1>공시 조기경보</h1>
      <p class="muted">PB 로그인</p>
      <div class="field">
        <label for="email">이메일</label>
        <input id="email" v-model="email" type="email" autocomplete="username" required />
      </div>
      <div class="field">
        <label for="pw">비밀번호</label>
        <input id="pw" v-model="password" type="password" autocomplete="current-password" required />
      </div>
      <button type="submit" :disabled="loading">{{ loading ? '로그인 중…' : '로그인' }}</button>
      <p v-if="error" class="error">{{ error }}</p>
      <div class="demo muted">
        데모 계정:
        <a href="#" @click.prevent="fill('p01@demo.local')">김하늘 (P01)</a> ·
        <a href="#" @click.prevent="fill('p02@demo.local')">박서준 (P02)</a>
      </div>
    </form>
  </div>
</template>

<style scoped>
.login-wrap { display: flex; justify-content: center; padding-top: 12vh; }
.login { width: 360px; padding: 28px; }
.field { margin: 14px 0; }
.field input { width: 100%; }
button[type='submit'] { width: 100%; margin-top: 8px; padding: 10px; }
.demo { margin-top: 16px; font-size: 13px; }
</style>

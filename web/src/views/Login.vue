<script setup>
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { errorMessage } from '../api/client'
import BrandLogo from '../components/BrandLogo.vue'
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
    <section class="intro" aria-label="서비스 소개">
      <BrandLogo variant="dark" :height="64" />

      <div class="intro-copy">
        <span class="intro-label">PB를 위한 공시 조기경보</span>
        <h1>공시 속 숨은 위험 신호,<br />터지기 전에 먼저 알려 드려요</h1>
        <p>신호를 점수로 쌓아 경보를 띄우고, 보유 고객의 연락 우선순위와 안내문 초안까지 준비합니다. 연락은 PB가 승인한 뒤에만 만들어져요.</p>
      </div>

      <div class="features">
        <div class="feature">
          <div class="feature-icon">
            <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M3 3v18h18" /><path d="M7 15l4-4 3 3 5-6" />
            </svg>
          </div>
          <strong>규칙으로 점수 계산</strong>
          <span>AI는 점수를 바꾸지 않아요</span>
        </div>
        <div class="feature">
          <div class="feature-icon">
            <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M4 4h12l4 4v12H4z" /><path d="M8 12h8M8 16h5" />
            </svg>
          </div>
          <strong>원문 근거 인용</strong>
          <span>공시 문장을 그대로 보여줘요</span>
        </div>
        <div class="feature">
          <div class="feature-icon">
            <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" /><path d="M9 12l2 2 4-4" />
            </svg>
          </div>
          <strong>PB 승인 후 연락</strong>
          <span>매수·매도 권유는 넣지 않아요</span>
        </div>
      </div>
    </section>

    <section class="login-panel" aria-label="로그인">
      <form class="card login" @submit.prevent="submit">
        <div class="login-heading">
          <h2>PB 로그인</h2>
          <p class="muted">담당 고객의 경보와 안내문을 확인하세요</p>
        </div>
        <div class="field">
          <label for="email">이메일</label>
          <input id="email" v-model="email" type="email" autocomplete="username" required />
        </div>
        <div class="field">
          <label for="pw">비밀번호</label>
          <input id="pw" v-model="password" type="password" autocomplete="current-password" required />
        </div>
        <button class="block login-submit" type="submit" :disabled="loading">{{ loading ? '로그인 중…' : '로그인' }}</button>
        <p v-if="error" class="error" role="alert">{{ error }}</p>
        <div class="demo-divider">데모 계정으로 채우기</div>
        <div class="demo-accounts">
          <button class="ghost demo-account" :class="{ selected: email === 'p01@demo.local' }" type="button" :aria-pressed="email === 'p01@demo.local'" @click="fill('p01@demo.local')">
            <span class="avatar sm" :class="{ soft: email !== 'p01@demo.local' }" aria-hidden="true">김</span>
            <span class="demo-name"><strong>김하늘</strong><span>P01</span></span>
          </button>
          <button class="ghost demo-account" :class="{ selected: email === 'p02@demo.local' }" type="button" :aria-pressed="email === 'p02@demo.local'" @click="fill('p02@demo.local')">
            <span class="avatar sm" :class="{ soft: email !== 'p02@demo.local' }" aria-hidden="true">박</span>
            <span class="demo-name"><strong>박서준</strong><span>P02</span></span>
          </button>
        </div>
      </form>
    </section>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: 100vh;
  min-height: 100dvh;
  display: grid;
  grid-template-columns: minmax(0, 1.16fr) minmax(0, 1fr);
  gap: 24px;
  padding: 24px;
  background: var(--bg);
}
.intro {
  min-width: 0;
  padding: 56px;
  border-radius: 32px;
  background: var(--navy);
  color: var(--surface);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 48px;
}
.intro-copy { display: flex; flex-direction: column; gap: 20px; }
.intro-label {
  align-self: flex-start;
  padding: 8px 14px;
  border-radius: var(--pill);
  background: rgba(61, 107, 245, 0.18);
  color: #c9d3ee;
  font-size: 14px;
  font-weight: 600;
}
.intro h1 { margin: 0; font-size: 44px; line-height: 1.25; font-weight: 800; letter-spacing: -1px; word-break: keep-all; }
.intro-copy p { margin: 0; max-width: 520px; font-size: 17px; line-height: 1.6; color: #c9d3ee; }
.features { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.feature { padding: 20px; border-radius: 20px; background: var(--navy-2); display: flex; flex-direction: column; gap: 10px; }
.feature-icon { width: 40px; height: 40px; border-radius: 14px; background: var(--blue); display: flex; align-items: center; justify-content: center; }
.feature strong { font-size: 15px; }
.feature > span { font-size: 13px; line-height: 1.5; color: #a9b4d0; }
.login-panel { min-width: 0; display: flex; align-items: center; justify-content: center; padding: 24px; }
.login { width: 100%; max-width: 420px; margin: 0; padding: 44px 40px; border-radius: 32px; box-shadow: var(--shadow-md); display: flex; flex-direction: column; gap: 20px; }
.login-heading { display: flex; flex-direction: column; gap: 6px; }
.login-heading h2 { margin: 0; font-size: 26px; font-weight: 800; letter-spacing: -0.5px; }
.login-heading p { margin: 0; }
.field input { width: 100%; min-height: 52px; padding: 0 18px; background: var(--surface-2); font-size: 16px; }
.field input:focus { background: var(--surface); }
.login-submit { min-height: 56px; margin-top: 4px; font-size: 17px; }
.demo-divider { display: flex; align-items: center; gap: 12px; color: var(--muted); font-size: 13px; }
.demo-divider::before, .demo-divider::after { content: ''; flex: 1; height: 1px; background: var(--border); }
.demo-accounts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.demo-account { min-height: 56px; min-width: 0; padding: 0 14px; border-width: 1.5px; border-radius: 18px; justify-content: flex-start; gap: 10px; text-align: left; }
.demo-account.selected, .demo-account.selected:hover { border-color: var(--blue); background: var(--blue-soft); }
.demo-name { display: flex; flex-direction: column; line-height: 1.25; }
.demo-name strong { color: var(--text); font-size: 14px; }
.demo-name > span { color: var(--muted); font-size: 12px; }
.selected .demo-name > span { color: var(--blue-strong); }
@media (min-width: 901px) and (max-width: 1200px) {
  .intro { padding: 36px; }
  .intro h1 { font-size: 34px; }
  .features { grid-template-columns: 1fr; }
  .feature { padding: 16px; }
  .login-panel { padding: 12px; }
  .login { padding: 36px 24px; }
}
@media (max-width: 900px) {
  .login-wrap { grid-template-columns: minmax(0, 1fr); }
  .intro { padding: 40px; }
  .intro h1 { font-size: 36px; }
  .login-panel { padding: 24px 0; }
}
@media (max-width: 540px) {
  .intro { padding: 28px 24px; gap: 32px; }
  .intro h1 { font-size: 28px; }
  .features { grid-template-columns: 1fr; }
  .login { padding: 32px 24px; }
  .demo-account { padding: 0 10px; gap: 8px; }
}
</style>

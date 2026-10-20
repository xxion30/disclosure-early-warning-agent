import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { api, tokenStorage } from '../api/client'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(tokenStorage.get())
  const pb = ref(null) // { pb_id, name }
  const checked = ref(false) // 새로고침 후 /auth/me로 토큰을 확인했는지

  const isLoggedIn = computed(() => !!token.value)

  async function login(email, password) {
    const res = await api.login(email, password)
    tokenStorage.set(res.access_token)
    token.value = res.access_token
    pb.value = res.pb
    checked.value = true
  }

  async function restore() {
    if (checked.value || !token.value) return
    try {
      pb.value = await api.me()
    } catch {
      logout()
    } finally {
      checked.value = true
    }
  }

  function logout() {
    tokenStorage.clear()
    token.value = null
    pb.value = null
  }

  return { token, pb, checked, isLoggedIn, login, restore, logout }
})

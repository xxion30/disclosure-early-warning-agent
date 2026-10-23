// FastAPI 호출. Vite 프록시가 /api → http://localhost:8000 으로 넘긴다.
import axios from 'axios'

import { mockApi, mockMode } from './mock'

export const API_BASE = '/api'
const TOKEN_KEY = 'pb_token'

export const tokenStorage = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (t) => localStorage.setItem(TOKEN_KEY, t),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

const http = axios.create({ baseURL: API_BASE })

http.interceptors.request.use((config) => {
  const token = tokenStorage.get()
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// 401이면 토큰을 지우고 로그인 화면으로 (router는 순환 import를 피하려고 나중에 등록)
let onUnauthorized = () => {}
export function setUnauthorizedHandler(fn) {
  onUnauthorized = fn
}
http.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401 && !err.config.url.startsWith('/auth/login')) {
      tokenStorage.clear()
      onUnauthorized()
    }
    return Promise.reject(err)
  },
)

/** FastAPI 오류 응답을 화면에 보여줄 한 줄로 */
export function errorMessage(err) {
  const detail = err.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((d) => d.msg?.replace(/^Value error, /, '')).join(' / ')
  return err.message || '알 수 없는 오류'
}

export const api = {
  login: (email, password) => http.post('/auth/login', { email, password }).then((r) => r.data),
  me: () => http.get('/auth/me').then((r) => r.data),

  startRun: (rceptNo) => http.post('/runs', { rcept_no: rceptNo }).then((r) => r.data),
  getRun: (rceptNo) => http.get(`/runs/${rceptNo}`).then((r) => r.data),

  // ?mock=stages면 화면 확인용 가짜 데이터(api/mock.js)를 쓴다. 실제 서버는 부르지 않는다
  listAlerts: (params) => (mockMode() ? mockApi.listAlerts(params) : http.get('/alerts', { params }).then((r) => r.data)),
  getAlert: (id) => (mockMode() && mockApi.isMockId(id) ? mockApi.getAlert(id) : http.get(`/alerts/${id}`).then((r) => r.data)),
  getDrafts: (id) => http.get(`/alerts/${id}/drafts`).then((r) => r.data),
  decide: (id, decisions) => http.post(`/alerts/${id}/decision`, { decisions }).then((r) => r.data),

  listCustomers: () => http.get('/customers').then((r) => r.data),

  listTasks: (params) => http.get('/tasks', { params }).then((r) => r.data),
  updateTask: (id, body) => http.patch(`/tasks/${id}`, body).then((r) => r.data),
}

// axios의 params 배열을 ?level=High&level=Critical 형태로 (FastAPI 방식)
http.defaults.paramsSerializer = { indexes: null }

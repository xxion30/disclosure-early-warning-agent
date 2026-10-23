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
  // 대시보드 종목 경보 (GET /states?alert=true, 서버가 우선순위로 정렬)와 수집 상태
  listStates: (params) => (mockMode() ? mockApi.listStates(params) : http.get('/states', { params }).then((r) => r.data)),
  collectStatus: () => (mockMode() ? mockApi.collectStatus() : http.get('/collect/status').then((r) => r.data)),
  // 종목 상세 (R4 /states). 가짜 데이터 모드에서는 예시 종목(MOCK01…)을 돌려준다
  getState: (code) => (mockMode() && mockApi.isMockStock(code) ? mockApi.getState(code) : http.get(`/states/${code}`).then((r) => r.data)),
  // 읽음 표시: 지금 본 알림 버전(version)까지만 읽음으로 남긴다 (R4 계약)
  ackState: (code, version) => (mockMode() && mockApi.isMockStock(code) ? mockApi.ackState(code, version) : http.post(`/states/${code}/ack`, { version }).then((r) => r.data)),
  getDrafts: (id) => http.get(`/alerts/${id}/drafts`).then((r) => r.data),
  decide: (id, decisions) => http.post(`/alerts/${id}/decision`, { decisions }).then((r) => r.data),

  // 새 조직별 검토 (R4 계약 "Pre-merge 실행·조직 검토"). 초안 생성은 R2 실행기가 연결돼야 동작하고, 없으면 서버가 503을 준다
  createReview: (publicAssessmentId) => (mockMode() && mockApi.isMockReview(publicAssessmentId) ? mockApi.createReview(publicAssessmentId) : http.post(`/public-assessments/${publicAssessmentId}/reviews`).then((r) => r.data)),
  getReview: (id) => (mockMode() && mockApi.isMockReview(id) ? mockApi.getReview(id) : http.get(`/reviews/${id}`).then((r) => r.data)),
  runReview: (id) => (mockMode() && mockApi.isMockReview(id) ? mockApi.runReview(id) : http.post(`/reviews/${id}/run`).then((r) => r.data)),
  decideReview: (id, expectedVersion, decisions) => (mockMode() && mockApi.isMockReview(id)
    ? mockApi.decideReview(id, expectedVersion, decisions)
    : http.post(`/reviews/${id}/decisions`, { expected_version: expectedVersion, decisions }).then((r) => r.data)),

  listCustomers: () => http.get('/customers').then((r) => r.data),

  listTasks: (params) => http.get('/tasks', { params }).then((r) => r.data),
  updateTask: (id, body) => http.patch(`/tasks/${id}`, body).then((r) => r.data),
}

// axios의 params 배열을 ?level=High&level=Critical 형태로 (FastAPI 방식)
http.defaults.paramsSerializer = { indexes: null }

import { createRouter, createWebHistory } from 'vue-router'

import { setUnauthorizedHandler } from '../api/client'
import { useAuthStore } from '../stores/auth'
import AlertDetail from '../views/AlertDetail.vue'
import AlertList from '../views/AlertList.vue'
import CustomerList from '../views/CustomerList.vue'
import DraftReview from '../views/DraftReview.vue'
import Login from '../views/Login.vue'
import RiskDetail from '../views/RiskDetail.vue'
import TaskList from '../views/TaskList.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/alerts' },
    { path: '/login', name: 'login', component: Login, meta: { public: true } },
    { path: '/alerts', name: 'alerts', component: AlertList },
    { path: '/alerts/:assessmentId', name: 'alert-detail', component: AlertDetail, props: true },
    { path: '/alerts/:assessmentId/drafts', name: 'draft-review', component: DraftReview, props: true },
    { path: '/stocks/:stockCode', name: 'risk-detail', component: RiskDetail, props: true },
    // 새 조직별 검토 (같은 안내문 검토 화면, ?stock=종목코드면 종목 상세로 돌아간다)
    { path: '/reviews/:reviewId', name: 'org-review', component: DraftReview, props: (route) => ({ reviewId: route.params.reviewId, stockCode: route.query.stock || null }) },
    { path: '/customers', name: 'customers', component: CustomerList },
    { path: '/tasks', name: 'tasks', component: TaskList },
    { path: '/:pathMatch(.*)*', redirect: '/alerts' },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore()
  await auth.restore()
  if (!to.meta.public && !auth.isLoggedIn) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.name === 'login' && auth.isLoggedIn) return { name: 'alerts' }
})

setUnauthorizedHandler(() => {
  useAuthStore().logout()
  if (router.currentRoute.value.name !== 'login') {
    router.push({ name: 'login', query: { redirect: router.currentRoute.value.fullPath } })
  }
})

export default router

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'

import { api, errorMessage } from '../api/client'
import LevelBadge from '../components/LevelBadge.vue'
import RunProgress from '../components/RunProgress.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { useRunEvents } from '../composables/useRunEvents'
import { ASSESSMENT_STATUS, formatTime } from '../labels'

const router = useRouter()

// 탭: 경보(High·Critical) / 일일 요약(Medium) / 전체
const TABS = {
  alerts: { label: '경보', levels: ['Critical', 'High'] },
  summary: { label: '일일 요약', levels: ['Medium'] },
  all: { label: '전체', levels: [] },
}
const tab = ref('alerts')
const statusFilter = ref('')
const levelFilter = ref('')
const summaryDate = ref(new Date().toLocaleDateString('sv-SE')) // 로컬 날짜 YYYY-MM-DD

const rows = ref([])
const loading = ref(false)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  const levels = levelFilter.value ? [levelFilter.value] : TABS[tab.value].levels
  try {
    rows.value = await api.listAlerts({
      level: levels.length ? levels : undefined,
      status: statusFilter.value || undefined,
      date: tab.value === 'summary' ? summaryDate.value : undefined,
    })
  } catch (err) {
    error.value = errorMessage(err)
  } finally {
    loading.value = false
  }
}

watch(tab, () => {
  levelFilter.value = ''
  load()
})
watch([statusFilter, levelFilter, summaryDate], load)
onMounted(load)

// 진짜 모드에서 MCP 서버가 꺼져 있으면 영향 고객 수를 못 센다. 목록은 DB 내용으로 계속 보여준다
const graphError = computed(() => rows.value.find((r) => r.graph_error)?.graph_error || '')

const waitingCount = computed(() => rows.value.filter((r) => r.status === 'WAITING_APPROVAL').length)

// ---- 공시 1건 처리 시작 (데모용) ----
const rceptNo = ref('')
const runError = ref('')
const running = ref(false)
const { state: runState, connect } = useRunEvents()

async function startRun() {
  runError.value = ''
  try {
    await api.startRun(rceptNo.value.trim())
    running.value = true
    connect(rceptNo.value.trim(), {
      onEnd: (name, data) => {
        load()
        if (name === 'waiting_approval' && data.assessment_id) {
          router.push({ name: 'alert-detail', params: { assessmentId: data.assessment_id } })
        }
      },
    })
  } catch (err) {
    runError.value = errorMessage(err)
  }
}
</script>

<template>
  <div class="page-head">
    <div>
      <h1>경보 목록</h1>
      <p class="muted">승인 대기 {{ waitingCount }}건</p>
    </div>
    <form class="row" @submit.prevent="startRun">
      <input v-model="rceptNo" placeholder="접수번호 14자리" pattern="\d{14}" required style="width: 170px" />
      <button type="submit">공시 처리 시작</button>
    </form>
  </div>

  <div v-if="running || runError" class="card">
    <h2>처리 진행 <small v-if="rceptNo">· {{ rceptNo }}</small></h2>
    <RunProgress v-if="running" :state="runState" />
    <p v-if="runError" class="error">{{ runError }}</p>
  </div>

  <div class="card">
    <div class="tabs">
      <button v-for="(t, key) in TABS" :key="key" :class="{ active: tab === key }" @click="tab = key">
        {{ t.label }}
      </button>
    </div>

    <div class="row" style="margin-bottom: 12px">
      <template v-if="tab === 'summary'">
        <label for="d" style="margin: 0">날짜</label>
        <input id="d" v-model="summaryDate" type="date" />
      </template>
      <select v-if="tab === 'all'" v-model="levelFilter">
        <option value="">등급 전체</option>
        <option v-for="l in ['Critical', 'High', 'Medium', 'Low']" :key="l" :value="l">{{ l }}</option>
      </select>
      <select v-model="statusFilter">
        <option value="">상태 전체</option>
        <option v-for="(label, key) in ASSESSMENT_STATUS" :key="key" :value="key">{{ label }}</option>
      </select>
      <button class="ghost small" @click="load">새로고침</button>
    </div>

    <p v-if="error" class="error">{{ error }}</p>
    <p v-if="graphError" class="notice">{{ graphError }} 영향 고객 수는 연결되면 다시 보입니다.</p>
    <table>
      <thead>
        <tr>
          <th>회사</th>
          <th>공시</th>
          <th>등급</th>
          <th class="num">점수</th>
          <th class="num">영향 고객</th>
          <th class="num">P1</th>
          <th class="num">내 승인 대기</th>
          <th>상태</th>
          <th>생성</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="r in rows"
          :key="r.assessment_id"
          class="clickable"
          @click="router.push({ name: 'alert-detail', params: { assessmentId: r.assessment_id } })"
        >
          <td><strong>{{ r.corp_name || '-' }}</strong></td>
          <td>{{ r.title || r.rcept_no }}<br /><small>{{ r.filed_at || r.rcept_no }}</small></td>
          <td><LevelBadge :level="r.level" /> <small v-if="r.combo_upgraded">조합 상향</small></td>
          <td class="num">{{ r.total_score ?? '-' }}</td>
          <td class="num">{{ r.affected_count }}</td>
          <td class="num">{{ r.p1_count }}</td>
          <td class="num"><strong v-if="r.my_pending_count">{{ r.my_pending_count }}</strong><span v-else>-</span></td>
          <td><StatusBadge :status="r.status" /></td>
          <td><small>{{ formatTime(r.created_at) }}</small></td>
        </tr>
        <tr v-if="!rows.length && !loading">
          <td colspan="9" class="empty">표시할 경보가 없습니다.</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

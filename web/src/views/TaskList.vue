<script setup>
import { onMounted, reactive, ref, watch } from 'vue'

import { api, errorMessage } from '../api/client'
import LevelBadge from '../components/LevelBadge.vue'
import StatusBadge from '../components/StatusBadge.vue'
import { formatTime } from '../labels'

const STATUSES = ['승인', '보류', '연락완료']
const CHANGEABLE = ['승인', '보류'] // 이 상태에서만 연락완료·보류로 바꿀 수 있다 (API와 같은 규칙)

const tasks = ref([])
const statusFilter = ref('')
const error = ref('')
const notes = reactive({}) // task_id → 입력 중인 메모
const open = reactive({}) // task_id → 안내문 펼침

async function load() {
  error.value = ''
  try {
    tasks.value = await api.listTasks({ status: statusFilter.value || undefined })
    for (const t of tasks.value) if (notes[t.task_id] === undefined) notes[t.task_id] = t.result_note || ''
  } catch (err) {
    error.value = errorMessage(err)
  }
}
onMounted(load)
watch(statusFilter, load)

async function change(task, status) {
  error.value = ''
  try {
    const updated = await api.updateTask(task.task_id, { status, note: notes[task.task_id] })
    Object.assign(task, updated)
    if (statusFilter.value && statusFilter.value !== status) await load()
  } catch (err) {
    error.value = errorMessage(err)
  }
}
</script>

<template>
  <div class="page-head">
    <div>
      <h1>연락 업무</h1>
      <p class="muted">PB 승인으로 만들어진 내 업무 {{ tasks.length }}건</p>
    </div>
    <div class="row">
      <select v-model="statusFilter">
        <option value="">상태 전체</option>
        <option v-for="s in STATUSES" :key="s" :value="s">{{ s }}</option>
      </select>
      <button class="ghost small" @click="load">새로고침</button>
    </div>
  </div>
  <p v-if="error" class="error">{{ error }}</p>

  <div class="card">
    <table>
      <thead>
        <tr>
          <th>우선순위</th>
          <th>고객</th>
          <th>종목·경보</th>
          <th>상태</th>
          <th style="width: 34%">결과 메모</th>
          <th>처리</th>
        </tr>
      </thead>
      <tbody>
        <template v-for="t in tasks" :key="t.task_id">
          <tr>
            <td><span class="prio" :class="`prio-${t.priority}`">{{ t.priority }}</span></td>
            <td>
              <strong>{{ t.customer_name }}</strong> <small>{{ t.customer_id }}</small><br />
              <a href="#" @click.prevent="open[t.task_id] = !open[t.task_id]">
                <small>{{ open[t.task_id] ? '안내문 닫기' : '안내문 보기' }}</small>
              </a>
            </td>
            <td>
              {{ t.corp_name || '-' }} <LevelBadge :level="t.level" /><br />
              <small>
                <RouterLink :to="{ name: 'alert-detail', params: { assessmentId: t.assessment_id } }">경보 #{{ t.assessment_id }}</RouterLink>
                · {{ formatTime(t.created_at) }}
              </small>
            </td>
            <td><StatusBadge :status="t.status" /><br /><small>{{ formatTime(t.updated_at) }}</small></td>
            <td>
              <textarea
                v-if="CHANGEABLE.includes(t.status)"
                v-model="notes[t.task_id]"
                rows="2"
                placeholder="통화 결과 등"
                style="min-height: 0"
              />
              <span v-else>{{ t.result_note || '-' }}</span>
            </td>
            <td>
              <div v-if="CHANGEABLE.includes(t.status)" class="row">
                <button class="small" @click="change(t, '연락완료')">연락완료</button>
                <button class="small ghost" @click="change(t, '보류')">보류</button>
              </div>
            </td>
          </tr>
          <tr v-if="open[t.task_id]">
            <td colspan="6"><div class="message">{{ t.message_preview }}</div></td>
          </tr>
        </template>
        <tr v-if="!tasks.length">
          <td colspan="6" class="empty">연락 업무가 없습니다.</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>

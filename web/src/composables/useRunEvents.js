// SSE 진행 이벤트 구독. 서버는 done / failed 뒤 스트림을 닫는다.
import { onBeforeUnmount, reactive } from 'vue'

import { API_BASE, tokenStorage } from '../api/client'

const TERMINAL = ['done', 'failed']
const EVENTS = ['queued', 'node_started', 'node_finished', ...TERMINAL]

export function useRunEvents() {
  const state = reactive({
    connected: false,
    queued: false, // 큐 모드: 실행 전 차례를 기다리는 중
    nodes: {}, // node → 'running' | 'done'
    order: [], // 이벤트가 온 노드 순서
    last: null, // 마지막 이벤트 이름
    lastData: null,
    error: null, // 실패·수동 확인 이유 (없으면 null)
    manual: false, // 수동 확인으로 끝남 (진짜 Graph는 failed 이벤트에 status: MANUAL_CHECK를 싣는다)
    disconnected: false, // 끝나기 전에 SSE 연결이 끊김 (처리 실패와는 다르다)
  })
  let es = null

  function reset() {
    state.nodes = {}
    state.order = []
    state.last = null
    state.lastData = null
    state.error = null
    state.manual = false
    state.disconnected = false
    state.queued = false
  }

  function apply(name, data) {
    state.queued = name === 'queued'
    if (name === 'queued') return
    if (data.node && !state.order.includes(data.node)) state.order.push(data.node)
    if (name === 'node_started') state.nodes[data.node] = 'running'
    if (name === 'node_finished') state.nodes[data.node] = 'done'
    if (name === 'failed') {
      state.manual = data.status === 'MANUAL_CHECK'
      // 이유가 없으면 서버가 상태 코드(MANUAL_CHECK 등)를 message로 보낸다. 코드가 그대로 보이지 않게 거른다
      const message = data.message && data.message !== data.status ? data.message : null
      state.error = message
      if (data.node) state.nodes[data.node] = state.manual ? 'manual' : 'failed'
    }
    state.last = name
    state.lastData = data
  }

  /**
   * onEvent(name, data)는 이벤트마다, 끝나면 onEnd(name, data).
   * doneBefore: 이벤트가 중간 노드부터 오는 경우, 그 앞 단계를 이미 끝난 것으로 표시한다.
   */
  function connect(rceptNo, { onEvent, onEnd, doneBefore = [] } = {}) {
    close()
    reset()
    for (const node of doneBefore) state.nodes[node] = 'done'
    // EventSource는 헤더를 못 보내서 토큰을 쿼리로 넘긴다
    es = new EventSource(`${API_BASE}/runs/${rceptNo}/events?token=${encodeURIComponent(tokenStorage.get() || '')}`)
    state.connected = true
    for (const name of EVENTS) {
      es.addEventListener(name, (e) => {
        const data = JSON.parse(e.data)
        apply(name, data)
        onEvent?.(name, data)
        if (TERMINAL.includes(name)) {
          close() // 닫지 않으면 EventSource가 자동 재연결해서 같은 이벤트를 다시 받는다
          onEnd?.(name, data)
        }
      })
    }
    es.onerror = () => {
      // 서버가 스트림을 닫았거나 연결 실패. 자동 재연결로 무한 반복하지 않게 닫는다
      if (!TERMINAL.includes(state.last)) state.disconnected = true
      close()
    }
  }

  function close() {
    if (es) es.close()
    es = null
    state.connected = false
  }

  onBeforeUnmount(close)
  return { state, connect, close, apply, reset }
}

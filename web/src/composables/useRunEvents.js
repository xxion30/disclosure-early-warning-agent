// SSE 진행 이벤트 구독. 서버는 waiting_approval / done / failed 뒤 스트림을 닫는다.
import { onBeforeUnmount, reactive } from 'vue'

import { API_BASE, tokenStorage } from '../api/client'

const TERMINAL = ['waiting_approval', 'done', 'failed']
const EVENTS = ['queued', 'node_started', 'node_finished', ...TERMINAL]

export function useRunEvents() {
  const state = reactive({
    connected: false,
    queued: false, // 큐 모드: 실행 전 차례를 기다리는 중
    nodes: {}, // node → 'running' | 'done'
    order: [], // 이벤트가 온 노드 순서
    last: null, // 마지막 이벤트 이름
    lastData: null,
    error: null,
  })
  let es = null

  function reset() {
    state.nodes = {}
    state.order = []
    state.last = null
    state.lastData = null
    state.error = null
    state.queued = false
  }

  function apply(name, data) {
    state.queued = name === 'queued'
    if (name === 'queued') return
    if (data.node && !state.order.includes(data.node)) state.order.push(data.node)
    if (name === 'node_started') state.nodes[data.node] = 'running'
    if (name === 'node_finished') state.nodes[data.node] = 'done'
    if (name === 'waiting_approval') state.nodes.pb_approval = 'waiting'
    if (name === 'failed') {
      state.error = data.message || '처리 실패'
      if (data.node) state.nodes[data.node] = 'failed'
    }
    state.last = name
    state.lastData = data
  }

  /**
   * onEvent(name, data)는 이벤트마다, 끝나면 onEnd(name, data).
   * doneBefore: 재개 구간은 pb_approval부터 이벤트가 오므로, 그 앞 단계는 이미 끝난 것으로 표시한다.
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
      if (!TERMINAL.includes(state.last)) state.error = state.error || '진행 이벤트 연결이 끊겼습니다.'
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

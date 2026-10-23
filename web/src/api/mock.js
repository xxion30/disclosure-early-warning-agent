// 화면 확인용 가짜 데이터 (R5 전용). 실제 API에 새 단계(stage)가 들어오기 전에 배지·필터를 보기 위한 것이다.
// 켜기: 주소에 ?mock=stages / 끄기: ?mock=off. 탭을 닫기 전까지(sessionStorage) 유지된다.
// 공시 내용·접수번호는 지어내지 않는다: 회사 이름은 "예시○○", 제목에 [예시], 접수번호는 0으로 채운 가짜 번호다.
// 점수 필드는 넣지 않는다(PB 화면 응답에는 점수가 없다는 계약, docs/contracts/api.md).

const KEY = 'dachi_mock'

function readMode() {
  try {
    const param = new URLSearchParams(window.location.search).get('mock')
    if (param === 'off') sessionStorage.removeItem(KEY)
    else if (param) sessionStorage.setItem(KEY, param)
    return sessionStorage.getItem(KEY)
  } catch {
    return null
  }
}

export const mockMode = () => readMode() === 'stages'

let seq = 0
const STAGE_LABELS = { RED: '즉시 경보', ORANGE: '사건 경보', YELLOW: '재무 경보', COMPOSITE: '복합 위험', WATCH: '관찰 중', NONE: '경보 없음' }
// 날짜는 오늘 기준 며칠 전으로 만든다 (예시 화면용)
const daysAgo = (n) => new Date(Date.now() - n * 86400000).toLocaleDateString('sv-SE')
const hoursAgo = (n) => new Date(Date.now() - n * 3600000).toISOString().slice(0, 16)
// 서버가 정렬해 주는 순서(단계 → 점수 → 최근)를 흉내 내어 이미 정렬된 순서로 둔다
const ROWS = [
  { corp: '예시바이오', title: '[예시] 횡령·배임 혐의 발생', stages: ['RED'], status: 'WAITING_APPROVAL', affected: 5, pending: 2, isNew: true },
  { corp: '예시전자', title: '[예시] 전환사채권 발행결정', stages: ['ORANGE', 'YELLOW'], status: 'WAITING_APPROVAL', affected: 3, pending: 1, isNew: true },
  { corp: '예시화학', title: '[예시] 유상증자 결정', stages: ['COMPOSITE'], status: 'DONE', affected: 2, pending: 0, isNew: false },
  // 해소된 종목: 지금 단계는 경보 없음, 이력(위험 항목)에는 사건 경보가 남는다
  { corp: '예시건설', title: '[예시] 소송 등의 제기', stages: ['NONE'], itemStages: ['ORANGE'], status: 'DONE', affected: 4, pending: 0, isNew: false, resolved: true },
  { corp: '예시식품', title: '[예시] 사업보고서', stages: ['YELLOW'], status: 'DONE', affected: 1, pending: 0, isNew: false },
  { corp: '예시물산', title: '[예시] 타법인 주식 양수 결정', stages: ['WATCH'], status: 'DONE', affected: 2, pending: 0, isNew: false },
  { corp: '예시통신', title: '[예시] 주주총회 소집결의', stages: ['NONE'], status: 'DONE', affected: 0, pending: 0, isNew: false },
  { corp: '예시제약', title: '[예시] 주요사항보고서', stages: ['NONE'], status: 'MANUAL_CHECK', affected: 0, pending: 0, isNew: false, unscored: true },
].map((r) => {
  seq += 1
  return {
    assessment_id: 900000 + seq,
    rcept_no: String(seq).padStart(14, '0'),
    // 가짜 종목코드: 실제 종목과 절대 겹치지 않게 MOCK + 번호
    stock_code: `MOCK${String(seq).padStart(2, '0')}`,
    resolved: !!r.resolved,
    item_stages: r.itemStages || r.stages,
    corp_name: r.corp,
    title: r.title,
    filed_at: daysAgo(seq * 2),
    stage: r.stages[0],
    stages: r.stages,
    stage_label: STAGE_LABELS[r.stages[0]],
    stage_available: true, // 서버(R4)가 새 단계 평가라고 알려 주는 값
    legacy: false,
    level: null,
    status: r.status,
    created_at: hoursAgo(seq),
    affected_count: r.affected,
    my_pending_count: r.pending,
    new: r.isNew,
    scored: !r.unscored,
    graph_error: null,
  }
})

const ALERT = ['RED', 'ORANGE', 'YELLOW', 'COMPOSITE']
const clone = (x) => JSON.parse(JSON.stringify(x))
const asList = (v) => (v == null ? [] : Array.isArray(v) ? v : [v])

export const mockApi = {
  async listAlerts(params = {}) {
    const stages = asList(params.stage)
    return clone(ROWS.filter((r) => {
      if (params.alert && (!r.stages.some((s) => ALERT.includes(s)) || r.status === 'FAILED')) return false
      if (stages.length && !r.stages.some((s) => stages.includes(s))) return false
      if (params.status && r.status !== params.status) return false
      return true
    }))
  },
  isMockId: (id) => Number(id) > 900000 && Number(id) <= 900000 + ROWS.length,
  async getAlert(id) {
    const r = ROWS.find((x) => x.assessment_id === Number(id))
    return clone({
      assessment_id: r.assessment_id,
      rcept_no: r.rcept_no,
      status: r.status,
      stage: r.stage,
      stages: r.stages,
      level: null,
      scored: r.scored,
      combo_upgraded: false,
      summary: null,
      reasons: [],
      ai_explanation: '[예시] 가짜 데이터 모드라 설명이 없어요.',
      created_at: null,
      company: { corp_code: null, name: r.corp_name, stock_code: r.stock_code, market: null },
      mock: true, // 가짜 접수번호라 DART 링크를 만들지 않는다
      disclosure: { rcept_no: r.rcept_no, title: r.title, filed_at: r.filed_at, url: null },
      evidence: [],
      guardrail_flags: [],
      customers: [],
      pending: [],
      graph_state_available: false,
      graph_error: null,
    })
  },

  async listStates(params = {}) {
    // 실제 API처럼 경보 단계만, 서버 순서(ROWS 순서) 그대로. 상세에서 쓰는 필드만 뺀다
    const items = ROWS.map(buildState)
      .filter((st) => !params.alert || (ALERT.includes(st.stage) && !st.resolved))
      .map(({ items: _i, timeline: _t, resolution_evidence: _r, ai_explanation: _a, ...rest }) => rest)
    return clone({ items, total: items.length, limit: 50, offset: 0 })
  },
  async collectStatus() {
    return { runs: [{ run_id: 'mock-run', status: 'DONE', started_at: hoursAgo(3), finished_at: hoursAgo(2), fetched_count: 8, failed_count: 0, error: null }] }
  },
  // ---- 새 조직별 검토 (예시전자 MOCK02) ----
  isMockReview: (id) => String(id).startsWith('mock-'),
  async createReview(publicId) {
    const rv = readReview()
    return { review_id: rv.review_id, status: rv.status, version: rv.version, public_assessment_id: publicId }
  },
  async getReview() {
    const rv = readReview()
    return clone({ ...rv, customer_names: MOCK_CUSTOMERS })
  },
  // 실제로는 R2 실행기가 초안을 만든다. 가짜 모드에서는 예시 문안을 채운다 (다시 작성 요청된 고객만 새로)
  async runReview() {
    const rv = readReview()
    if (!['PENDING', 'MANUAL_CHECK'].includes(rv.status)) throw mockError(409, '초안 또는 최신 판정을 확인해야 합니다.')
    const targets = rv.rewrite_customers.length ? rv.rewrite_customers : Object.keys(MOCK_CUSTOMERS)
    for (const cid of targets) rv.drafts[cid] = draftFor(cid, rv.rewrite_customers.includes(cid))
    rv.rewrite_customers = []
    rv.status = 'WAITING_APPROVAL'
    rv.version += 1
    saveReview(rv)
    return { review_id: rv.review_id, status: rv.status }
  },
  async decideReview(id, expectedVersion, decisions) {
    const rv = readReview()
    if (rv.version !== expectedVersion) throw mockError(409, '검토 버전이 변경됐습니다.')
    if (rv.status !== 'WAITING_APPROVAL') throw mockError(409, '승인 대기 상태가 아닙니다.')
    for (const d of decisions) {
      if (!(d.customer_id in rv.drafts)) throw mockError(403, '담당 고객만 결정할 수 있습니다.')
      if (rv.decisions[d.customer_id]) throw mockError(409, '이미 처리된 결정입니다.')
      if (d.action === 'reject' && !(d.reject_reason || '').trim()) throw mockError(409, '반려 사유가 필요합니다.')
      if (d.action === 'approve' && !(d.edited_draft || rv.drafts[d.customer_id] || '').trim()) throw mockError(409, '검사 실패 문안은 승인할 수 없습니다.')
    }
    for (const d of decisions) {
      if (d.action === 'rewrite') {
        rv.rewrite_customers = [...new Set([...rv.rewrite_customers, d.customer_id])]
        rv.status = 'PENDING'
      } else rv.decisions[d.customer_id] = { ...d, pb_id: 'P01' }
    }
    rv.version += 1
    if (Object.keys(rv.decisions).length === Object.keys(rv.drafts).length) rv.status = 'DONE'
    saveReview(rv)
    return { review_id: rv.review_id, status: rv.status, version: rv.version }
  },

  isMockStock: (code) => ROWS.some((r) => r.stock_code === code),
  async getState(code) {
    return clone(buildState(ROWS.find((r) => r.stock_code === code)))
  },
  // 실제 API와 같이 {version}까지만 읽음 처리한다
  async ackState(code, version) {
    const acked = readAcks()
    acked[code] = Math.max(acked[code] || 0, Number(version) || 0)
    try {
      sessionStorage.setItem(ACK_KEY, JSON.stringify(acked))
    } catch {
      // 저장이 막혀도 화면에는 이번에만 표시한다
    }
    const state = buildState(ROWS.find((r) => r.stock_code === code))
    return { stock_code: code, read_version: acked[code], new: state.new }
  },
}

// ---- 종목 상세 (GET /states/{stock_code}) 가짜 응답 ----
// 실제 R4 응답 모양(api/routes/states.py)을 그대로 따른다. 점수·정렬값은 없다.
const ACK_KEY = 'dachi_mock_acks'
function readAcks() {
  try {
    return JSON.parse(sessionStorage.getItem(ACK_KEY) || '{}')
  } catch {
    return {}
  }
}

const QUOTE = '[예시] 이 자리에 공시 원문에서 인용한 문장이 들어가요.'

// 단계별 위험 항목 예시. group은 즉시(RED)·사건(ORANGE)·재무(YELLOW) 분류, active=false는 기간이 지나 반영하지 않는 항목
const ITEMS = {
  RED: [{ group: 'RED', item: '횡령·배임', signal_type: 'EMBEZZLEMENT', explanation: '[예시] 임원의 횡령 혐의가 발생했다는 공시가 있어요. 이 항목 하나만으로 즉시 경보예요.', ago: 2 }],
  ORANGE: [
    { group: 'ORANGE', item: '전환사채(CB) 발행', signal_type: 'CB_BW', explanation: '[예시] 전환가액을 다시 낮출 수 있는 조항(리픽싱)이 붙은 전환사채를 발행했어요.', ago: 3 },
    { group: 'ORANGE', item: '소송 제기', signal_type: 'LAWSUIT', explanation: '[예시] 회사를 상대로 한 손해배상 소송이 제기됐어요.', ago: 220 },
    { group: 'ORANGE', item: '자기주식 처분', signal_type: 'TREASURY_DISPOSAL', explanation: '[예시] 1년도 더 지난 공시라 지금 판정에는 반영하지 않아요.', ago: 400, inactive: true },
  ],
  YELLOW: [{ group: 'YELLOW', item: '영업현금흐름 적자', signal_type: 'OPERATING_CASH_FLOW', explanation: '[예시] 최근 정기보고서에서 영업활동 현금흐름이 2년 연속 적자예요.', ago: 60 }],
  COMPOSITE: [
    { group: 'ORANGE', item: '유상증자', signal_type: 'RIGHTS_OFFERING', explanation: '[예시] 제3자배정 유상증자를 결정했어요.', ago: 200 },
    { group: 'YELLOW', item: '이자 부담', signal_type: 'INTEREST_BURDEN', explanation: '[예시] 영업이익이 이자비용보다 적어요. 사건·재무 신호를 합치면 기준을 넘어 복합 위험이에요.', ago: 40, noEvidence: true, missing: ['interest_coverage'] },
  ],
  WATCH: [{ group: 'ORANGE', item: '타법인 주식 양수', signal_type: 'ASSET_TRANSFER', explanation: '[예시] 다른 회사 지분을 사들이기로 했어요. 아직 경보 기준 아래라 지켜보는 중이에요.', ago: 10 }],
  NONE: [],
}

function buildState(r) {
  const base = r.rcept_no.slice(0, 10)
  const rcept = (n) => base + String(n).padStart(4, '0')
  let n = 0
  const items = r.item_stages.flatMap((s) => ITEMS[s] || []).map((it) => {
    n += 1
    return {
      group: it.group,
      item: it.item,
      signal_type: it.signal_type,
      event_id: `${r.stock_code}-${n}`,
      rcept_no: rcept(n),
      filed_at: daysAgo(it.ago),
      active: !it.inactive && !r.resolved,
      resolved: !!r.resolved,
      missing: it.missing || [],
      missing_labels: (it.missing || []).map((code) => ({ code, label: { interest_coverage: '금융비용 기준 보상배율' }[code] || code })),
      explanation: it.explanation,
      evidence: it.noEvidence ? [] : [{ source: '[예시] 공시 원문', quote: QUOTE, rcept_no: rcept(n), location: null }],
    }
  })
  const resolutionNo = rcept(90)
  const timeline = [
    ...items.map((it) => ({ rcept_no: it.rcept_no, title: `[예시] ${it.item} 공시`, filed_at: it.filed_at, url: null, event_id: it.event_id })),
    { rcept_no: rcept(80), title: '[예시] 분기보고서', filed_at: daysAgo(120), url: null, event_id: null },
    ...(items.length ? [{ rcept_no: rcept(81), title: '[기재정정] [예시] 정정 공시 (같은 사건의 정정, 반복으로 세지 않음)', filed_at: daysAgo(90), url: null, event_id: items[0].event_id }] : []),
    ...(r.resolved ? [{ rcept_no: resolutionNo, title: '[예시] 소송 취하 공시', filed_at: daysAgo(1), url: null, event_id: items[0]?.event_id }] : []),
  ].sort((a, b) => b.filed_at.localeCompare(a.filed_at))

  const version = r.new ? 2 : 1
  const readVersion = readAcks()[r.stock_code] || (r.new ? 1 : 1)
  const alertStage = ALERT.some((s) => r.stages.includes(s))
  return {
    stock_code: r.stock_code,
    corp_code: null,
    corp_name: r.corp_name,
    stage: r.stage,
    active_stages: r.stages,
    manual_check: items.some((it) => it.missing.length),
    resolved: !!r.resolved,
    version,
    new: alertStage && version > readVersion,
    updated_at: new Date(Date.now() - 3600000).toISOString(),
    latest_filed_at: timeline[0]?.filed_at || null,
    last_collection_at: new Date(Date.now() - 2 * 3600000).toISOString(),
    risk_types: [...new Set(items.filter((it) => it.active).map((it) => it.signal_type))].sort(),
    ai_explanation: '[예시] 가짜 데이터 모드라 AI 설명 대신 이 문장이 보여요. 등급에는 영향이 없어요.',
    items,
    resolution_evidence: r.resolved ? [{ source: '[예시] 소송 취하 공시', quote: '[예시] 원고가 소를 취하했다는 문장이 들어가요.', rcept_no: resolutionNo, location: null }] : [],
    timeline,
    ...mockChanges(r, items),
    ...mockReview(r),
    mock: true, // 가짜 접수번호라 DART 링크를 만들지 않는다
  }
}

// 이번에 무엇이 바뀌었나 (직전 공개 판정 → 지금). 예시물산(관찰 중)은 직전 판정이 없어 비교할 수 없는 경우
function mockChanges(r, items) {
  const base = { change_version: 2, changed_at: new Date(Date.now() - 3600000).toISOString() }
  if (r.stages[0] === 'WATCH') return { ...base, comparison_available: false, changes: [] }
  if (r.resolved) return { ...base, comparison_available: true, changes: [{ kind: 'stage_down', from: 'ORANGE', to: 'NONE' }, { kind: 'resolved', scope: 'stock' }] }
  if (r.stages[0] === 'NONE') return { ...base, comparison_available: true, changes: [] }
  const latest = items.find((it) => it.active)
  return { ...base, comparison_available: true, changes: [
    { kind: 'stage_up', from: 'WATCH', to: r.stages[0] },
    ...(latest ? [{ kind: 'new_type', signal_type: latest.signal_type }] : []),
    ...(r.stages.length > 1 ? [{ kind: 'evidence_added', signal_type: 'OPERATING_CASH_FLOW' }] : []),
  ] }
}

// 안내문 검토 연결: 예시바이오는 예전 방식(지금 화면으로 검토), 예시전자는 새 조직별 검토(화면 준비 중)
function mockReview(r) {
  const none = { assessment_id: null, public_assessment_id: null, review_id: null, review_status: null, review_backend: null, my_approval_pending: false, can_review: false }
  if (r.stock_code === 'MOCK01') return { ...none, assessment_id: r.assessment_id, review_backend: 'legacy', review_status: 'WAITING_APPROVAL', my_approval_pending: true, can_review: true }
  if (r.stock_code === 'MOCK02') return { ...none, assessment_id: r.assessment_id, public_assessment_id: 'mock-public-2', review_id: 'mock-review-2', review_backend: 'organization', review_status: 'WAITING_APPROVAL', my_approval_pending: true, can_review: true }
  if (r.stock_code === 'MOCK03') return { ...none, public_assessment_id: 'mock-public-2', review_backend: 'organization' } // 검토가 아직 없음 → 초안 만들기
  if (ALERT.includes(r.stages[0])) return { ...none, assessment_id: r.assessment_id, review_backend: 'legacy', review_status: 'DONE' }
  return none
}

// ---- 새 조직별 검토 예시 상태 (탭을 닫기 전까지 sessionStorage에 유지) ----
// 고객은 실제 고객이 아닌 예시 이름이다. 안내문에는 매수·매도 권유를 넣지 않는다
const REVIEW_KEY = 'dachi_mock_review'
const MOCK_CUSTOMERS = { 'MOCK-C1': { name: '예시고객 가', risk_profile: '안정형' }, 'MOCK-C2': { name: '예시고객 나', risk_profile: '적극투자형' }, 'MOCK-C3': { name: '예시고객 다', risk_profile: '위험중립형' } }
function draftFor(cid, rewritten) {
  if (cid === 'MOCK-C3' && !rewritten) return '' // 금지 표현 검사를 통과하지 못해 비어 있는 초안 (직접 작성 필요)
  return `{고객명}님, 보유하신 예시전자에 [예시] 전환사채 발행 공시가 있어 알려 드립니다. 공시 원문과 내용은 DART에서 확인하실 수 있습니다.${rewritten ? ' (다시 작성한 문안)' : ''}`
}
function readReview() {
  try {
    const saved = JSON.parse(sessionStorage.getItem(REVIEW_KEY) || 'null')
    if (saved) return saved
  } catch {
    // 저장이 막혀 있으면 처음 상태로 시작한다
  }
  return { review_id: 'mock-review-2', assessment_id: 'mock-public-2', status: 'WAITING_APPROVAL', version: 1, stage: 'ORANGE',
    ai_explanation: '[예시] 가짜 데이터 모드라 AI 설명 대신 이 문장이 보여요.',
    drafts: Object.fromEntries(Object.keys(MOCK_CUSTOMERS).map((cid) => [cid, draftFor(cid, false)])), decisions: {}, rewrite_customers: [] }
}
function saveReview(rv) {
  try {
    sessionStorage.setItem(REVIEW_KEY, JSON.stringify(rv))
  } catch {
    // 저장이 막혀도 이번 화면에서는 동작한다
  }
}
function mockError(status, detail) {
  const err = new Error(detail)
  err.response = { status, data: { detail } }
  return err
}


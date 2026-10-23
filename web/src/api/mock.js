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
// 날짜는 오늘 기준 며칠 전으로 만든다 (예시 화면용)
const daysAgo = (n) => new Date(Date.now() - n * 86400000).toLocaleDateString('sv-SE')
const hoursAgo = (n) => new Date(Date.now() - n * 3600000).toISOString().slice(0, 16)
// 서버가 정렬해 주는 순서(단계 → 점수 → 최근)를 흉내 내어 이미 정렬된 순서로 둔다
const ROWS = [
  { corp: '예시바이오', title: '[예시] 감사보고서 제출', stages: ['RED'], status: 'WAITING_APPROVAL', affected: 5, pending: 2, isNew: true },
  { corp: '예시전자', title: '[예시] 전환사채권 발행결정', stages: ['ORANGE', 'YELLOW'], status: 'WAITING_APPROVAL', affected: 3, pending: 1, isNew: true },
  { corp: '예시화학', title: '[예시] 유상증자 결정', stages: ['COMPOSITE'], status: 'DONE', affected: 2, pending: 0, isNew: false },
  { corp: '예시건설', title: '[예시] 소송 등의 제기', stages: ['ORANGE'], status: 'DONE', affected: 4, pending: 0, isNew: false },
  { corp: '예시식품', title: '[예시] 사업보고서', stages: ['YELLOW'], status: 'DONE', affected: 1, pending: 0, isNew: false },
  { corp: '예시물산', title: '[예시] 타법인 주식 양수 결정', stages: ['WATCH'], status: 'DONE', affected: 2, pending: 0, isNew: false },
  { corp: '예시통신', title: '[예시] 주주총회 소집결의', stages: ['NONE'], status: 'DONE', affected: 0, pending: 0, isNew: false },
  { corp: '예시제약', title: '[예시] 주요사항보고서', stages: ['NONE'], status: 'MANUAL_CHECK', affected: 0, pending: 0, isNew: false, unscored: true },
].map((r) => {
  seq += 1
  return {
    assessment_id: 900000 + seq,
    rcept_no: String(seq).padStart(14, '0'),
    corp_name: r.corp,
    title: r.title,
    filed_at: daysAgo(seq * 2),
    stage: r.stages[0],
    stages: r.stages,
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
      company: { corp_code: null, name: r.corp_name, stock_code: null, market: null },
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
}

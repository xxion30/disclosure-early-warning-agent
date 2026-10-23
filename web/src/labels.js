// 화면 표시용 이름 (API 값 → 한국어)

export const NODE_LABELS = {
  receive: '공시 수신',
  classify: '공시 분류',
  extract_facts: '원문 분석',
  score_risk: '위험 평가',
  explain_risk: '근거 설명',
  route_by_level: '등급 분기',
  save_and_summarize: '저장·요약',
  find_customers: '영향 고객',
  draft_messages: '안내문 초안',
  guardrail_check: '금지 표현 검사',
  pb_approval: 'PB 승인',
  create_tasks: '업무 생성',
  save_rejection: '반려 저장',
  prepare_approval: '승인 준비',
  finalize: '결정 반영',
  completed: '완료 처리',
  handle_failure: '실패 기록',
}

// High·Critical 경로의 기본 순서 (진행 단계 표시)
export const MAIN_PATH = [
  'receive', 'classify', 'extract_facts', 'score_risk', 'explain_risk',
  'find_customers', 'draft_messages', 'guardrail_check', 'prepare_approval', 'pb_approval',
  'save_rejection', 'create_tasks', 'finalize',
]

export const ASSESSMENT_STATUS = {
  RUNNING: '처리 중',
  WAITING_APPROVAL: '승인 대기',
  DONE: '완료',
  FAILED: '실패',
  MANUAL_CHECK: '수동 확인 필요',
}

export const LEVELS = ['Critical', 'High', 'Medium', 'Low']

// ---- 새 위험 단계 (DARTCH, docs/contracts/db.md: RED/ORANGE/YELLOW/COMPOSITE/WATCH/NONE) ----
// API가 stage(또는 stages)를 주면 이걸로 배지를 그리고, 아직 level(Low~Critical)만 오면 예전 등급 배지를 그대로 쓴다.
// 점수 숫자는 어디에도 보이지 않는다. 설명에도 기준 숫자를 넣지 않는다.
export const STAGES = {
  RED: { label: '즉시 경보', order: 0, alert: true, hint: '확정 악재가 한 가지라도 있어요' },
  ORANGE: { label: '사건 경보', order: 1, alert: true, hint: '소송·증자·사채 발행 같은 사건성 공시가 기준을 넘었어요' },
  COMPOSITE: { label: '복합 위험', order: 1, alert: true, hint: '사건·재무 신호가 각각은 기준 아래지만 합치면 기준을 넘었어요' },
  YELLOW: { label: '재무 경보', order: 2, alert: true, hint: '감사의견·자본잠식·현금흐름 같은 재무 지표가 기준을 넘었어요' },
  WATCH: { label: '관찰 중', order: 3, alert: false, hint: '기준에 가까워 지켜보는 중이에요. 알림은 보내지 않아요' },
  NONE: { label: '경보 없음', order: 4, alert: false, hint: '' },
}
export const ALERT_STAGES = ['RED', 'ORANGE', 'YELLOW', 'COMPOSITE']

/** 행(경보·종목·업무)의 단계 목록. 심한 순으로 정렬. 단계 정보가 없으면 빈 배열 */
export function stagesOf(row) {
  if (!row) return []
  const list = Array.isArray(row.stages) && row.stages.length ? row.stages : row.stage ? [row.stage] : []
  // 서버가 예전 평가인지 알려 주면 그 값을 따른다 (/alerts의 legacy·stage_available, R4 계약 "R5 화면 연결 보완")
  if (row.legacy === true || row.stage_available === false) return []
  // 서버가 그 값을 주지 않을 때만(예전 서버·가짜 모드) 추측한다: 새 단계 칸이 생기기 전 평가는 stage가 기본값 NONE이고
  // 예전 등급(level)만 의미가 있으므로, NONE인데 예전 경보 등급이 있으면 단계가 없는 것으로 본다
  if (row.stage_available !== true && list.length === 1 && list[0] === 'NONE' && ['Critical', 'High', 'Medium'].includes(row.level)) return []
  return [...new Set(list.filter((s) => STAGES[s]))].sort((a, b) => STAGES[a].order - STAGES[b].order)
}
export const hasStage = (row) => stagesOf(row).length > 0

/** 서버 문장 속 단계 코드(RED 등)를 한국어 이름으로 바꾼다. 예: "현재 단계는 RED입니다" → "현재 단계는 즉시 경보입니다" */
export function stageText(text) {
  if (!text) return text
  return text.replace(/\b(RED|ORANGE|YELLOW|COMPOSITE|WATCH|NONE)\b/g, (code) => STAGES[code].label)
}

// 종목 상세의 위험 항목 분류 (API items[].group). 점수는 보여주지 않는다
export const ITEM_GROUPS = {
  RED: { label: '즉시', name: '확정 악재' },
  ORANGE: { label: '사건', name: '사건성 공시' },
  YELLOW: { label: '재무', name: '재무 지표' },
}
// 판정에 필요한데 원문에서 찾지 못한 값(API items[].missing)의 이름. 규칙 파일(risk_engine/rules.yaml)의 키를 PB가 읽는 말로
export const MISSING_LABELS = {
  opinion: '감사의견', going_concern: '계속기업 불확실성 여부', impairment_pct: '자본잠식률',
  negative_years: '영업현금흐름 적자 연수', interest_coverage: '이자보상배율', debt_to_cash_pct: '현금 대비 단기채무 비율',
  rate_increased: '금리 인상 여부', maturity_shortened: '만기 단축 여부', purpose_mismatch: '자금 용도 불일치 여부',
  changed_within_window: '최근 최대주주 변경 여부', ratio_to_equity_pct: '자기자본 대비 비율', core_business: '핵심 사업 관련 여부',
  loss_compensation: '결손 보전 목적 여부', reduction_ratio_pct: '감자 비율', ratio_pct: '발행 주식 대비 비율',
  third_party: '제3자배정 여부', payment_delayed_or_failed: '납입 지연·실패 여부', refixing: '전환가액 조정(리픽싱) 조항 여부',
  early_redemption: '조기상환 조항 여부', core_asset_sale: '핵심 자산 매각 여부', obligation_to_equity_pct: '자기자본 대비 의무 금액',
  unfavorable_ratio: '불리한 교환 비율 여부', distressed_transfer: '부실 이전 여부', urgent_funding: '긴급 자금 조달 여부',
  negotiation_failed: '협상 결렬 여부', normalized: '정상화 여부', distress_or_violation: '부실·위반 사유 여부',
}
export const missingLabel = (key) => MISSING_LABELS[key] || key
/** 위험 항목의 빠진 값 이름 목록. 서버가 missing_labels([{code,label}])를 주면 그것을, 없으면 화면 이름표를 쓴다 */
export const missingNames = (item) => (Array.isArray(item?.missing_labels) && item.missing_labels.length
  ? item.missing_labels.map((m) => m.label || missingLabel(m.code))
  : (item?.missing || []).map(missingLabel))

// 위험 종류 이름 (API risk_types의 signal_type). risk_engine/rules.yaml의 label과 같게 둔다
export const SIGNAL_LABELS = {
  DEFAULT: '부도발생', REHABILITATION: '회생절차 개시신청', BANK_MANAGEMENT: '채권은행 관리절차 개시', DISSOLUTION: '해산사유 발생',
  BUSINESS_SUSPENSION: '영업정지', TRADING_HALT: '거래정지', ADMIN_ISSUE: '관리종목 지정', DELISTING: '상장폐지 사유',
  EMBEZZLEMENT: '횡령·배임', PAYMENT_DEFAULT: '원리금 미지급', LAWSUIT: '소송 제기', CAPITAL_REDUCTION: '감자 결정',
  RIGHTS_OFFERING: '유상증자', CB_BW: 'CB·BW·EB 발행', ASSET_TRANSFER: '영업·자산·타법인 양수도', PUTBACK_OPTION: '자산양수도·풋백옵션',
  MERGER: '합병·분할·주식교환', TREASURY_DISPOSAL: '자기주식 처분·신탁해지', BANK_MANAGEMENT_END: '채권은행 관리절차 중단',
  OVERSEAS_DELISTING: '해외 상장폐지', AUDIT_OPINION: '감사의견', CAPITAL_IMPAIRMENT: '자본잠식', OPERATING_CASH_FLOW: '영업현금흐름',
  INTEREST_BURDEN: '이자 부담', SHORT_TERM_DEBT: '단기채무', DEBT_ISSUANCE: '채무증권 발행', FUND_USAGE: '자금 사용내역',
  LARGEST_SHAREHOLDER_CHANGE: '최대주주 변경', EQUITY_INVESTMENT: '타법인 출자',
}
export const signalLabel = (code) => SIGNAL_LABELS[code] || code

// 수집 실행 상태 (GET /collect/status runs[].status)
export const COLLECT_STATUS = {
  PENDING: { text: '수집 대기', tone: 'muted' },
  RUNNING: { text: '수집 중', tone: 'info' },
  DONE: { text: '수집 완료', tone: 'ok' },
  PARTIAL: { text: '일부 종목 수집 실패', tone: 'warn' },
  FAILED: { text: '수집 실패', tone: 'bad' },
}

/** 위험 항목의 지금 상태 (API items[].active·resolved). 숫자 대신 말로 */
export function itemStatus(it) {
  if (it.resolved) return { text: '해소됨', tone: 'ok' }
  if (it.active) return { text: '지금 판정에 반영', tone: 'info' }
  return { text: '반영 안 함 (기간 지남 등)', tone: 'muted' }
}
// 공시 타임라인 종류
export const TIMELINE_KINDS = {
  risk: '위험 공시',
  correction: '정정',
  resolution: '해소',
  other: '공시',
}

/** 경보(알림 대상)인가. 단계가 있으면 단계로, 없으면 예전 등급(High·Critical)으로 판단 */
export function isAlertRow(row) {
  const stages = stagesOf(row)
  if (stages.length) return stages.some((s) => ALERT_STAGES.includes(s))
  return ['High', 'Critical'].includes(row?.level)
}

// 점수 계산 전에 멈춘 평가는 저장 기본값(총점 0 · Low)이 들어 있어 "안전한 Low"처럼 보인다.
// API가 scored 값을 주면 그것을 따르고, 없으면 상태·점수·점수 내역으로 판단한다.
const STOPPED_STATUSES = ['RUNNING', 'FAILED', 'MANUAL_CHECK']
export function isUnscored(a) {
  if (!a) return false
  if (typeof a.scored === 'boolean') return !a.scored
  if (!STOPPED_STATUSES.includes(a.status)) return false
  const noReasons = !Array.isArray(a.reasons) || a.reasons.length === 0
  // 새 단계에서는 저장 기본값이 NONE, 예전 등급에서는 Low·총점 0
  if (hasStage(a)) return stagesOf(a)[0] === 'NONE' && noReasons
  return !a.total_score && a.level === 'Low' && noReasons
}

// 사람이 봐야 하는 상태의 안내 (경보 상세 맨 위 배너)
export const STATUS_GUIDE = {
  FAILED: {
    tone: 'bad',
    title: '자동 처리에 실패했어요',
    text: '위험 평가·안내문 준비를 끝내지 못했어요. 공시 원문을 직접 확인하고, 필요하면 공시 처리를 다시 시작해 주세요.',
  },
  MANUAL_CHECK: {
    tone: 'warn',
    title: '사람의 확인이 필요해요',
    text: 'AI가 근거를 충분히 찾지 못해 추측하지 않고 멈췄어요. 위험 항목의 "수동 확인 필요" 안내와 공시 원문을 확인해 주세요.',
  },
  RUNNING: {
    tone: 'info',
    title: '아직 처리 중이에요',
    text: '분석이 끝나면 위험 평가와 대상 고객을 확인할 수 있어요.',
  },
}

export function formatWon(n) {
  if (n == null) return '-'
  if (n >= 1e8) return `${(n / 1e8).toFixed(n % 1e8 ? 1 : 0)}억 원`
  if (n >= 1e4) return `${Math.round(n / 1e4).toLocaleString()}만 원`
  return `${n.toLocaleString()}원`
}

export function formatTime(iso) {
  if (!iso) return '-'
  const text = String(iso)
  // 시간대가 붙은 시각(…Z, …+00:00 등)은 한국 시간으로 바꿔 보여 준다. 서버는 UTC로 줄 때가 있다
  if (/T\d{2}:\d{2}/.test(text) && /(Z|[+-]\d{2}:?\d{2})$/.test(text)) {
    const d = new Date(text)
    if (!Number.isNaN(d.getTime())) {
      const kst = new Date(d.getTime() + 9 * 3600000)
      return kst.toISOString().slice(0, 16).replace('T', ' ')
    }
  }
  return text.replace('T', ' ').slice(0, 16)
}

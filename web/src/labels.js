// 화면 표시용 이름 (API 값 → 한국어)

export const NODE_LABELS = {
  receive: '공시 수신',
  classify: '공시 분류',
  extract_facts: '원문 분석',
  score_risk: '위험 점수',
  explain_risk: '근거 설명',
  route_by_level: '등급 분기',
  save_and_summarize: '저장·요약',
  find_customers: '영향 고객',
  draft_messages: '안내문 초안',
  guardrail_check: '가드레일',
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
  return [...new Set(list.filter((s) => STAGES[s]))].sort((a, b) => STAGES[a].order - STAGES[b].order)
}
export const hasStage = (row) => stagesOf(row).length > 0

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
    text: '점수·안내문이 끝까지 만들어지지 않았어요. 공시 원문을 직접 확인하고, 필요하면 공시 처리를 다시 시작해 주세요.',
  },
  MANUAL_CHECK: {
    tone: 'warn',
    title: '사람의 확인이 필요해요',
    text: 'AI가 근거를 충분히 찾지 못해 추측하지 않고 멈췄어요. 점수 내역의 "수동 확인 필요" 항목과 공시 원문을 확인해 주세요.',
  },
  RUNNING: {
    tone: 'info',
    title: '아직 처리 중이에요',
    text: '단계가 끝나면 점수와 영향 고객이 채워져요.',
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
  return iso.replace('T', ' ').replace(/\+09:00$/, '').slice(0, 16)
}

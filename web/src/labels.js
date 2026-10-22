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

// 점수 계산 전에 멈춘 평가는 저장 기본값(총점 0 · Low)이 들어 있어 "안전한 Low"처럼 보인다.
// API가 scored 값을 주면 그것을 따르고, 없으면 상태·점수·점수 내역으로 판단한다.
const STOPPED_STATUSES = ['RUNNING', 'FAILED', 'MANUAL_CHECK']
export function isUnscored(a) {
  if (!a) return false
  if (typeof a.scored === 'boolean') return !a.scored
  if (!STOPPED_STATUSES.includes(a.status)) return false
  const noReasons = !Array.isArray(a.reasons) || a.reasons.length === 0
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

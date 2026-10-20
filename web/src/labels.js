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
}

// High·Critical 경로의 기본 순서 (진행 단계 표시)
export const MAIN_PATH = [
  'receive', 'classify', 'extract_facts', 'score_risk', 'explain_risk', 'route_by_level',
  'find_customers', 'draft_messages', 'guardrail_check', 'pb_approval', 'create_tasks',
]

export const ASSESSMENT_STATUS = {
  RUNNING: '처리 중',
  WAITING_APPROVAL: '승인 대기',
  DONE: '완료',
  FAILED: '실패',
  MANUAL_CHECK: '수동 확인 필요',
}

export const LEVELS = ['Critical', 'High', 'Medium', 'Low']

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

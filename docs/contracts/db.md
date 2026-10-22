# DB 테이블 계약

> 담당: **R4** · 상태: 이전 프로젝트(Capstone) 계약을 그대로 옮긴 것이다. `contact_tasks`와 `level`·`priority`는 연락 기능 삭제와 함께 사라지고, `tenants`·`risk_states`·`financial_snapshots`·`collect_runs`가 추가된다.
> DARTCH Phase 1에서 바뀌는 부분은 [설계서](../dartch_phase1_mvp.md)와 [파일 구성 계획](../dartch_file_plan.md)을 따르고, 바뀔 때 이 문서를 먼저 고친다.

## 4-1. DB 테이블 (SQLite)

| 테이블 | 컬럼 |
| --- | --- |
| companies | corp_code(PK), stock_code, name, market, base_score, base_reasons(JSON) |
| disclosures | rcept_no(PK), corp_code, title, disclosure_type, filed_at, url, body_text, processed(bool) |
| signals | signal_id(PK), corp_code, rcept_no, signal_type, points, detail(JSON), filed_at |
| risk_assessments | assessment_id(PK), corp_code, rcept_no, total_score, level, combo_upgraded, reasons(JSON), ai_explanation, status, created_at |
| pbs | pb_id(PK), name, email, password_hash |
| customers | customer_id(PK), pb_id, name, risk_profile, total_assets |
| holdings | customer_id, stock_code, eval_amount, on_credit(bool) — PK(customer_id, stock_code) |
| contact_tasks | task_id(PK), assessment_id, customer_id, pb_id, priority, impact_score, draft_message, status, reject_reason, result_note, created_at, updated_at |
| audit_logs | log_id(PK), actor, action, target, payload(JSON), trace_id, created_at |

- `risk_assessments.status`: RUNNING, WAITING_APPROVAL, DONE, FAILED, MANUAL_CHECK
- `contact_tasks.status`: 승인대기, 승인, 반려, 연락완료, 보류
- `level`: Low, Medium, High, Critical / `priority`: P1, P2, P3
- 날짜는 ISO 문자열(`2025-04-01`, `2025-04-01T09:00:00+09:00`)
- LangGraph 체크포인트는 `data/checkpoints.db`에 따로 저장

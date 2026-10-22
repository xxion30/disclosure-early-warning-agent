# DB 테이블 계약

> 담당: **R4** · 상태: 이전 프로젝트(Capstone) 계약을 그대로 옮긴 것이다. `contact_tasks`는 오후 회의로 되살아나고(`priority`·`impact_score` 제외), `level`은 `stage`로 바뀌며, `tenants`·`risk_states`·`financial_snapshots`·`collect_runs`가 추가된다.
> DARTCH Phase 1에서 바뀌는 부분은 [설계서](../dartch_phase1_mvp.md)와 [파일 구성 계획](../dartch_file_plan.md)을 따르고, 바뀔 때 이 문서를 먼저 고친다.


## R4 구현 계약 (2026-10-22)

- 공개 데이터: companies, disclosures, signals, risk_assessments, risk_states, financial_snapshots. 고객 실행은 tenant_runs로 분리한다.
- tenants(tenant_id PK, name), pbs/customers/holdings/contact_tasks/audit_logs에 tenant_id 추가. 기존 행은 default 테넌트로 보존한다. PB에는 role(PB/ADMIN)을 둔다.
- disclosures: event_id, event_started_at, original_rcept_no 추가. signals에도 event_id와 최초 발생일 추가. 기존 컬럼은 삭제하지 않는다.
- risk_states: corp_code PK, stock_code, stage, orange_score, yellow_score, composite_ratio, sort_value, active_stages(JSON), items(JSON), manual_check, resolved, resolution_evidence(JSON), alert_version, updated_at, latest_filed_at.
- state_reads: (pb_id, corp_code) PK, tenant_id, read_version. 확인함은 해당 PB가 본 버전만 기록한다.
- financial_snapshots: id PK, corp_code, rcept_no, period, period_kind(ANNUAL/QUARTER/YTD), basis(CONSOLIDATED/SEPARATE), indicators(JSON), sources(JSON), revision, created_at. 지표에는 value/status와 비교 기간을 기록한다. R3는 이 계약을 확인하고 수집기에 연결한다.
- collect_runs: run_id PK, tenant_id(nullable=전체 정기 수집), requested_by, status, stock_codes(JSON), started_at, finished_at, fetched_count, failed_count, error. collect_cursors는 종목별 마지막 성공 위치를 저장한다.
- tenant_runs: (tenant_id, alert_key) 유일, corp_code, assessment_id, thread_id(회사와 사건 포함), status. 공개 분석과 회사별 승인 상태를 분리한다.
- PostgreSQL과 Alembic을 운영 기준으로 사용한다. SQLite는 로컬 테스트용이다. 초기 마이그레이션은 기존 표를 보존하고 누락 컬럼만 추가한다.
- R3 소유 shared/models/collect.py는 수정하지 않는다. R4의 저장 계약 모델은 별도 모듈에 둔다. R3와 합의 후 위치를 정리한다.


## 오후 회의(10/22) 반영 — 이 절이 아래 본문보다 우선한다

- `contact_tasks` 표를 **되살린다** (구조 정리 때 모델을 지웠으므로 `shared/models/`에 다시 만든다). 컬럼: task_id(PK), assessment_id, customer_id, pb_id, draft_message, status, reject_reason, result_note, created_at, updated_at. **`priority`, `impact_score`는 만들지 않는다** (영향도 폐지, 대상은 보유 고객 전원).
- `contact_tasks.status`: 승인대기, 승인, 반려, 연락완료, 보류 (이전과 같음).
- `risk_assessments.status`에 `WAITING_APPROVAL`을 다시 허용한다 (RUNNING, WAITING_APPROVAL, DONE, FAILED, MANUAL_CHECK).
- `level`(Low~Critical)은 `stage`(RED/ORANGE/YELLOW/COMPOSITE/WATCH/NONE)로 바뀐다 (설계서 STEP 04).
- 점수 컬럼(`risk_states`의 🟠·🟡 점수, 합산 비율)은 **서버 안 정렬·판정 전용**이다. PB가 보는 응답에는 내보내지 않는다 (api.md 참고).
- 고객 순서(안내문 검토 화면): `holdings.eval_amount` 큰 순.

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

### 재무 스냅샷 지표 형식 (R4 초안, R3 확인 필요)

`indicators`는 `{signal_type: {status: "OK"|"MISSING"|"MANUAL_CHECK", detail: {규칙 입력 필드}}}`이다. `sources`는 `{source,quote,rcept_no,location}` 목록이다. `period`는 `YYYY` 또는 `YYYY-Qn`, `period_kind`는 ANNUAL/QUARTER/YTD다. 연속 적자 연수는 ANNUAL 자료에서만 사용하고, 분기 비교는 같은 누적 기준과 기간 슬롯을 사용한다. 분모가 0·음수·누락된 비율을 자동 0으로 대체하지 않는다. 수집기는 `shared.collection.reserve_stocks`/`finish_stock`을 사용해 종목별 중복 수집과 커서 누락을 방지한다.

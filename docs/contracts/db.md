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

## R4 검증 후 보완 계약 (2026-10-22)

- `risk_states`에 `as_of`, `rules_digest`, `state_version`을 추가한다. `state_version`은 공개 판정 내용이 바뀔 때 증가하고 `alert_version`과 구분한다. 계산 도구가 읽은 `expected_state_version`으로 저장 시 경합을 검사한다.
- `risk_assessments.risk_state_version`은 초안이 참조한 공개 판정 버전이다. 초기 판정 저장 뒤 승인 재개 시 동일 결과를 다시 공개 상태에 쓰지 않는다. 최신 버전과 다른 초안, 해소·비경보 상태의 초안은 승인과 연락 업무 생성에서 거부한다.
- `collect_runs.lease_expires_at`을 추가한다. 전달 전 장애로 남은 작업은 만료 후 FAILED로 전환하고 예약을 해제한다. 실제 작업자는 heartbeat로 임대를 갱신해야 한다. 만료 작업을 소생시키지 않고 새 run_id로 재실행한다.
- financial_snapshots/collect_runs/collect_cursors의 단일 모델 정의는 `shared/models/operations.py`다. R3는 모델을 import하여 사용하고 collect.py에 같은 테이블을 재선언하지 않는다.

## Pre-merge R4 계약 (2026-10-23)

- disclosures.structured_facts: nullable JSON. 구조화 사실 v1은 PRESENT/MISSING/NOT_APPLICABLE/NEEDS_CHECK를 사용한다. NULL은 빈 사실이 아니며 조회 도구는 UNAVAILABLE을 반환한다. 사실 hash는 정규화 원문 hash와 다르다.
- disclosures.analysis_ready_at/input_version: R3가 공시별 필수 저장을 마친 동일 트랜잭션에서 shared.analysis.mark_ready로 표시한다. 목록 삽입만으로 분석을 시작하지 않는다. 변경된 입력은 새 버전으로 표시한다.
- analysis_jobs: 입력/규칙/기준시점별 멱등 작업. PENDING/RUNNING/RETRY_WAIT/DONE/MANUAL_CHECK/FAILED. 원자적 선점·임대·토큰으로 늦은 결과를 차단한다. 최초 포함 최대 3회, 일시 오류만 재시도한다.
- public_assessments: 종목·기준시점·입력 manifest hash·규칙 digest의 불변 판정. analysis_jobs와 별개이며 현재 risk_states는 최신 판정 ID를 참조한다. source_chunks/원문 전문은 보존하지 않는다.
- organization_reviews: tenant_id+public_assessment_id당 하나. public/thread:<job_id>, review/thread:<review_id>로 분리한다. 초안·승인 상태가 공개 판정 완료를 변경하지 않는다. 기존 tenant_runs/구형 assessment는 호환 데이터로 보존한다.
- 기존 원공시 후보 original_rcept_no는 확정 event_id와 구분한다. 후보만으로 자동 확정하지 않는다.

### R3 저장 인계

R3는 `shared.facts.store_facts(session,rcept_no,payload)`로 구조화 사실을 검증·저장한다. 공시별 필수 스냅샷/조회 결과 저장이 모두 끝난 같은 트랜잭션에서 `shared.analysis.mark_ready(session,rcept_no)`를 호출한다. 이 함수는 모델·API·Graph를 호출하지 않는다. 목록만 삽입한 공시는 실행기가 가져가지 않는다. NULL을 확정 정상으로 바꾸기 위해 준비 표시를 해서는 안 되며, 알려진 결측은 MISSING으로 표현한다.

명시적 재분석은 관리자 요청의 UUID `reanalysis_request_id`를 입력 manifest의 요청 버전으로 포함해 새 작업을 만든다. 같은 UUID 재요청은 멱등이다. 기존 MANUAL_CHECK/FAILED의 시도 횟수·오류 이력은 덮어쓰지 않는다.

## R5 변경 요약 이력 (2026-10-23)

risk_state_changes는 corp_code/state_version 유일 키로 변경 시각, 이전·현재 단계, 점수 없는 changes를 저장한다. 현재 상태 저장과 같은 트랜잭션이다. 최초 저장은 이전 상태를 추정하지 않는다. 기존 risk_states를 과거 평가와 임의 조합해 이력을 역산하지 않는다. 같은 상태를 다시 저장하면 변경 이력을 늘리지 않는다.
# 실행 예산 장부 (2026-10-23, R4)

`analysis_jobs.execution_budget`, `organization_reviews.execution_budget` JSON 컬럼을 추가한다(마이그레이션 `20261023_06`). 모델 요청 수, 실제 실행 초, 동일 도구 입력별 호출 수만 저장한다. 고객 정보·원문·프롬프트·API 키는 저장하지 않는다. 작업 선점 토큰·만료 시각을 검사하고 단조 증가하도록 저장한다. 공개 재시도와 조직별 재선점 모두 같은 행의 누적 한도를 사용한다.

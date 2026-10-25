# DARTCH API 명세

README의 실행 방법과 같은 `Pre-merge-P01-v01` 구현을 기준으로 한다. Endpoint는 실제 백엔드 경로이며, 웹 개발 서버는 앞에 `/api`를 붙여 호출한다.

- README의 API 주소: `http://127.0.0.1:8010`
- Swagger: `http://127.0.0.1:8010/docs`
- OpenAPI: `http://127.0.0.1:8010/openapi.json`
- 웹 요청 예: `/api/auth/login` → Vite 프록시 → 백엔드 `/auth/login`
- 로그인·health를 제외한 업무 API는 JWT 인증이 필요하다. 헤더는 `Authorization: Bearer <access_token>`, 토큰은 HS256·유효기간 12시간이다.
- 성공 응답은 아래 객체·배열을 직접 반환한다. MCP 도구 응답의 `{ok,data,error}`와 구분한다.

## 엔드포인트

응답은 성공 HTTP 상태 코드와 주요 필드를 요약한다. 담당은 팀의 모듈 소유·연결 역할 기준이다.

| Method | Endpoint | 요청 | 응답 | 담당 |
| --- | --- | --- | --- | --- |
| GET | `/health` | 없음 | 200: `ok` | R4 |
| POST | `/auth/login` | `email`, `password` | 200: `access_token`, `token_type`, `pb` | R4 |
| GET | `/auth/me` | JWT | 200: `pb_id`, `name`, `tenant_id`, `role` | R4 |
| POST | `/runs` | `rcept_no`: 숫자 14자리 | 202: `rcept_no`, `status`, `events_url` | R4 · 분석 R2 |
| POST | `/runs/batch` | `rcept_nos`: 1~500개, 중복 제거 | 202: `queued`, `skipped` | R4 |
| GET | `/runs/queue` | 없음 | 200: `enabled`; 활성 시 `queues`, `recent` | R4 |
| GET | `/runs/{rcept_no}` | 공시 접수번호 | 200: `rcept_no`, `running`, `last_event`, `events` | R4 |
| GET | `/runs/{rcept_no}/events` | 접수번호, JWT 헤더 또는 `token` 쿼리 | 200: `text/event-stream` 진행 이벤트 | R4 |
| GET | `/alerts` | 선택 쿼리: `level`, `status`, `alert`, `date` | 200: 경보 배열 — ID·기업·공시·등급·단계·상태·고객 수 등 | R4 |
| GET | `/alerts/{assessment_id}` | 기존 평가 ID: 정수 | 200: 경보·설명·근거·회사·공시·고객·검토 상태 | R4 |
| GET | `/alerts/{assessment_id}/drafts` | 기존 평가 ID | 200: `assessment_id`, `rcept_no`, `status`, `guardrail_flags`, `drafts[]` | R4 · 초안 R2 |
| POST | `/alerts/{assessment_id}/decision` | `decisions[]`: 고객 ID·결정·선택 수정 문안·반려 사유 | 202: `assessment_id`, `rcept_no`, `accepted`, `events_url` | R4 · 재개 R2 |
| GET | `/states` | 선택 쿼리: `stage`, `alert`, `manual_check`, `resolved`, `limit`, `offset` | 200: `items`, `total`, `limit`, `offset` | R4 |
| GET | `/states/{stock_code}` | 종목코드 | 200: 위험 상태·항목별 근거·설명·타임라인·변경 내역·검토 연결 | R4 |
| POST | `/states/{stock_code}/ack` | `version`: 0 이상 정수 | 200: `stock_code`, `read_version`, `new` | R4 |
| GET | `/collect/status` | 없음 | 200: `runs[]` — 최근 수집 작업 최대 20개 | R4 · 수집 R3 |
| POST | `/collect/run` | ADMIN 인증, 본문 없음 | 202: `run_id`, `status`, `started_at`, `finished_at`, `fetched_count`, `failed_count`, `error` | R4 · 수집 R3 |
| POST | `/analysis/jobs` | ADMIN 인증, `rcept_no`, `as_of`, 선택 `reanalysis_request_id` | 202: `job_id`, `status` | R4 · 분석 R2 |
| GET | `/analysis/jobs/{job_id}` | 분석 작업 ID | 200: `job_id`, `status`, `attempts`, `error_code`, `assessment_id` | R4 |
| POST | `/public-assessments/{assessment_id}/reviews` | 공개 판정 ID: 문자열, 본문 없음 | 201: `review_id`, `status`, `version` | R4 |
| GET | `/reviews/{review_id}` | 검토 ID | 200: `review_id`, `assessment_id`, `status`, `version`, `stage`, `ai_explanation`, `drafts`, `decisions` | R4 |
| POST | `/reviews/{review_id}/run` | 검토 ID, 본문 없음 | 200: `review_id`, `status: WAITING_APPROVAL` | R4 · 초안 R2 |
| POST | `/reviews/{review_id}/decisions` | `expected_version`, `decisions[]` | 200: `review_id`, `status`, `version` | R4 |
| GET | `/customers` | 없음 | 200: `summary`, `customers[]` — 담당 고객·보유· 경보 노출·연락 이력 | R4 |
| GET | `/tasks` | 선택 쿼리: `status` | 200: 담당 연락 업무 배열 | R4 |
| PATCH | `/tasks/{task_id}` | `status`: 연락완료 또는 보류, 선택 `note` | 200: 갱신된 연락 업무 | R4 |

## 요청·사용 조건

| 항목 | 조건 |
| --- | --- |
| 기존 직접 처리 | `/runs`·SSE·기존 승인 경로는 default 조직용이며 다른 조직은 기존 실행 경계에서 503으로 차단 |
| 일괄 처리 | `/runs/batch`는 RabbitMQ 큐 활성화 필요. 비활성 상태는 409 |
| 새 분석 생성 | ADMIN·조직의 접근 가능한 보유 종목·공시 수집 완료 인계 필요 |
| 분석 기준일 | `as_of`는 YYYY-MM-DD. 공시일 이전 기준일은 거절 |
| 재분석 식별 | `reanalysis_request_id`는 선택 UUID. 동일 분석 식별이면 기존 작업 반환 가능 |
| 새 분석 실행 | API 내부 worker는 `PHASE1_RUNTIME_FACTORY` 설정 시 시작. 작업 등록 성공과 분석 완료는 별개 |
| 수동 수집 | ADMIN과 수집 dispatcher 연결 필요. 미연결은 503 |
| 조직별 초안 실행 | review executor 연결 필요. 미연결은 503 |
| 고객 결정 | `action`: approve/reject/rewrite. reject는 `reject_reason` 필수, 수정 승인은 `edited_draft` 사용 |
| 새 검토 결정 | 조회한 검토 version을 `expected_version`으로 전달. 중복 고객·담당 외 고객·최신 판정 여부 검사 |
| 읽음 표시 | 조회한 알림 version 전달. 미래 버전은 409. 읽음 버전은 후퇴시키지 않음 |
| 업무 변경 | 현재 상태 승인/보류일 때만 연락완료/보류로 변경 가능 |

`/states`의 `stage`는 반복 쿼리로 복수 전달 가능하다. `limit`은 기본 50·범위 1~100, `offset`은 기본 0·최솟값 0이다. 조직·담당 고객은 요청 JSON이 아닌 인증 정보와 DB로 검사한다. 새 검토의 초안·결정은 요청 PB의 담당 고객에 한해 조회한다. seed PB 계정은 기본 PB이며 관리자 승격 API는 없다.

## SSE

기존 `/runs/{rcept_no}/events`는 `queued`, `node_started`, `node_finished`, `waiting_approval`, `done`, `failed` 이벤트를 전송한다. `waiting_approval`, `done`, `failed`는 종료 이벤트다. 연결 유지는 `: keep-alive` 주석으로 표시한다.

`events_url`은 백엔드 상대 경로다. 웹은 `/api` 프록시를 통해 연결한다. EventSource는 `?token=<JWT>` 인증도 지원하므로 토큰이 포함된 URL을 문서·캡처에 노출하지 않는다. 새 분석 작업은 `/analysis/jobs/{job_id}` 조회 경로를 사용하며 기존 SSE가 자동 적용되지 않는다.

## AI 출력 규격

모델이 생성하는 내부 출력·근거 스키마이며 HTTP 응답과 구분한다. 위험 점수·최종 단계는 규칙 엔진이 계산한다.

| 출력 | 실제 스키마 | 주요 필드 | 검증 기준 |
| --- | --- | --- | --- |
| 문서 근거 | `Evidence` | `source`, `quote`, `rcept_no`, `location` | 실제 원문·인용·접수번호·위치 대조 |
| API 근거 | `APIEvidence` | `kind`, `api`, `field`, `rcept_no`, `row_key`, `value`, `unit`, `period`, `basis`, `data_version` | `kind=api`, 원천 사실·값·기간·버전 대조 |
| 기존 사실 추출 | `FactsOutput` | `facts[]`: `signal_type`, `values`, `evidence` | 공시 유형·허용 신호·근거 검사 |
| 새 텍스트 조건 | `FlagsOutput` | `flags[]`: `item`, `flag`, `value`, `evidence` | 조건 중복·근거 검사, 수치 계산과 구분 |
| 위험 설명 | `ExplanationOutput` | `items[]`: `breakdown_index`, `explanation`, `evidence` | 규칙 계산 항목과 설명·근거 연결 |
| 고객별 안내문 | `DraftsOutput` | `drafts: {customer_id: 안내문}` | 대상 고객 집합·고객명 자리표시·금지 표현·점수 노출 검사 |

기존 초안 조회는 `drafts[]` 배열로 가공하고, 새 검토 조회는 `drafts: {고객ID: 문안}` 객체로 반환한다. 안내문 단일 message 모델이 파일에 존재하더라도 실제 고객별 생성 노드는 DraftsOutput을 사용한다.

| 상태 대상 | 값 |
| --- | --- |
| 위험 단계 | RED, ORANGE, YELLOW, COMPOSITE, WATCH, NONE |
| 경보 단계 | RED, ORANGE, YELLOW, COMPOSITE |
| 기존 호환 등급 | Critical, High, Medium, Low |
| 기존 평가 | RUNNING, WAITING_APPROVAL, DONE, FAILED, MANUAL_CHECK |
| 새 분석 작업 | PENDING, RUNNING, RETRY_WAIT, DONE, MANUAL_CHECK, FAILED |
| 연락 업무 | 승인대기, 승인, 반려, 연락완료, 보류 |

## 예외 처리 경로

| 실패 | 어디서 알 수 있나 | 처리 경로 | status |
| --- | --- | --- | --- |
| 권한 부족 | API 오류·화면 메시지 | JWT·ADMIN·조직·담당 고객 검사, 접근 불가 자원 존재를 숨기는 경우 404 | HTTP 401 인증 실패 / 403 권한 부족 / 일부 조회 404 |
| 검색 결과 없음 | MCP NOT_FOUND·실행 상태·SSE·수동 확인 표시 | 필요한 근거를 확보하지 못하면 내용을 만들어 채우지 않고 수동 확인 | 도구 NOT_FOUND → 분석 MANUAL_CHECK |
| 근거 부족 | 사실·인용 검증·분석 상태·종목 상세 | 인용·접수번호·위치·버전·API 사실 검사, 실패·수치 누락은 추측하지 않음 | 분석 MANUAL_CHECK / 종목 manual_check=true |
| 외부 시스템 오류 | 서버·감사 로그·SSE failed·작업 error_code | 모델 429·503은 1초·3초 대기 후 2회 재시도→fallback, MCP TIMEOUT 1회 재시도 | 분석 RETRY_WAIT 또는 FAILED / 실행기·연결 미준비 API는 HTTP 503 |

요청 검증 오류는 HTTP 422, 실행·상태·버전 충돌은 409로 반환한다. 새 검토 결정의 문안·행위 의미 검사는 409, 기존 결정의 요청 검증·수정 문안 검사는 422일 수 있다. HTTP 상태·MCP 오류·작업 상태는 다른 층위이며 검색 실패가 곧 HTTP 404를 뜻하지 않는다.

HTTP 오류는 `api/main.py`에서 `{ok:false,error:{code,message},detail}`로 반환한다. 401=UNAUTHORIZED, 403=FORBIDDEN, 404=NOT_FOUND, 409=CONFLICT, 503=UNAVAILABLE이며 요청 검증은 INVALID_INPUT이다. 명시적 HTTPException 422의 code는 문자열 "422"일 수 있다.

## 구현 근거

- [라우터 등록·오류 처리](../api/main.py), [인증](../api/auth.py)
- [직접 처리](../api/routes/runs.py), [SSE](../api/routes/events.py), [기존 경보](../api/routes/alerts.py), [기존 승인](../api/routes/approvals.py)
- [종목 상태](../api/routes/states.py), [수집](../api/routes/collect.py), [고객](../api/routes/customers.py), [업무](../api/routes/tasks.py)
- [새 분석·검토](../api/routes/execution.py), [조직 검토 저장](../mcp_servers/business/reviews.py)
- [출력 스키마](../agent/state.py), [텍스트 조건](../agent/phase1.py), [안내문 생성](../agent/nodes/draft_messages.py)

등록·스키마·사용 조건의 코드 대조와 실제 DART·모델·PostgreSQL 전체 실행 검증은 구분한다. 서비스 연결 한계는 [README](../README.md)의 알려진 한계를 따른다.

# API·화면 계약

> 담당: **R4 (화면은 R5)** · 상태: 이전 프로젝트(Capstone) 계약을 그대로 옮긴 것이다. 승인·업무·안내문 API와 화면은 오후 회의로 되살아나고(점수는 응답에서 뺀다), `/states`·`/collect`가 추가된다.
> DARTCH Phase 1에서 바뀌는 부분은 [설계서](../dartch_phase1_mvp.md)와 [파일 구성 계획](../dartch_file_plan.md)을 따르고, 바뀔 때 이 문서를 먼저 고친다.


## R4 구현 계약 (2026-10-22)

- 모든 PB 조회는 JWT의 PB·tenant와 DB 소속을 확인한다. states는 본인 담당 고객의 보유 종목만 조회한다. 회사 관리자는 수동 수집만 회사 전체 범위로 요청한다.
- GET /states: stage(반복 가능), alert, manual_check, resolved, limit(1~100), offset. 응답 {items,total,limit,offset}. 서버가 전체 우선순위 정렬 후 페이지를 나눈다.
- GET /states/{stock_code}: 목록 필드 + items(항목·인용·출처), timeline(접수번호·제목·일자·링크), resolution_evidence. 점수·weight·sort_value·composite_ratio는 어떤 중첩에도 내보내지 않는다.
- POST /states/{stock_code}/ack: {version: int}. 요청 버전까지만 읽음 처리하며 새 버전을 읽은 것으로 취급하지 않는다. 응답 {stock_code,read_version,new}.
- GET /collect/status: 본인 회사 작업과 공개 정기 작업의 최근 상태. POST /collect/run: 관리자 전용, 회사 보유 종목으로 제한, 202 {run_id,status}. 중복 실행은 기존 실행을 반환한다. 수집기 미연결은 503이며 성공을 가장하지 않는다.
- /customers는 현재 stage와 연락 이력을 반환하고 평가금액 합계 큰 순으로 정렬한다. 기존 응답 모양은 호환을 위해 보존한다.
- 오류: HTTP 상태 유지, {ok:false,error:{code,message}}. detail은 구형 클라이언트 호환용이다. 승인·초안·연락 업무 경로는 유지한다.
- SSE/승인 런타임의 회사별 thread 전환은 R2와 함께 연결한다. 기존 그래프를 새 그래프로 간주하지 않는다.


## 오후 회의(10/22) 반영 — 이 절이 아래 본문보다 우선한다

- 되살리는 경로: `GET /alerts/{id}/drafts`, `POST /alerts/{id}/decision`(승인 / 수정 후 승인 / 다시 작성 / 반려, 반려 사유 필수, 다른 PB 고객 403), `GET /tasks`, `PATCH /tasks/{task_id}`. 화면 `/alerts/:id/drafts`, `/tasks`와 SSE `waiting_approval`도 돌아온다.
- **점수 비노출**: PB 화면용 응답(`/states`, `/states/{stock_code}`, `/alerts*`, `/customers`)에는 **총점·항목별 점수 필드를 넣지 않는다.** 보여 주는 것은 `stage`(단계 배지), 항목 이름, 설명 문장, 근거 인용, DART 링크.
- **정렬은 서버가 한다**: 목록·대시보드 알림은 ① 단계(RED → ORANGE·COMPOSITE → YELLOW) ② 같은 단계에서 점수 높은 순 ③ 최근 공시 순으로 이미 정렬해 돌려준다. 클라이언트는 순서를 그대로 쓴다.
- **대시보드 알림**: 경보(RED/ORANGE/YELLOW/COMPOSITE)가 생기면 담당 PB의 메인 대시보드 알림 목록에 오른다. WATCH(관찰)는 알림이 아니다. 목록은 `GET /states?alert=true`(정렬됨). 새 경보 표시는 `new: true`. `[R4·R5 합의]`: 별도 경로를 둘지는 구현 때 정한다.
- `/customers`: 영향도(P1~P3) 없음. 고객별 보유 종목 + 종목의 현재 `stage` 배지 + 연락 이력. 고객 순서는 보유 평가금액 큰 순.
- 직접 발송 API는 없다. 연락 업무는 PB가 직접 연락하는 일을 기록하는 용도다.

## 8. PB 화면 (Vue)

| 화면 | 경로 | 내용 |
| --- | --- | --- |
| 로그인 | /login | JWT 발급 |
| 경보 목록 | /alerts | 회사명, 등급 배지, 점수, 영향 고객 수, P1 수, 상태 / 등급·상태 필터 / 일일 요약 탭 |
| 경보 상세 | /alerts/:assessmentId | 진행 단계 표시(SSE), 점수 내역 표, AI 설명과 원문 인용, 영향 고객 목록(우선순위순) |
| 안내문 검토 | /alerts/:assessmentId/drafts | 고객별 초안, 가드레일 결과, 승인 / 수정 후 승인 / 다시 작성 / 반려(사유 필수) |
| 고객 현황 | /customers | 내 담당 고객의 보유 종목, 경보 종목 노출(우선순위 P1·P2·P3), 연락 이력 (조회 전용) |
| 연락 업무 | /tasks | 내 업무 목록, 연락완료·보류 처리, 결과 메모 |

- PB는 자기 담당 고객의 안내문만 승인할 수 있다 (토큰의 pb_id와 customers.pb_id 비교).
- SSE 이벤트 이름: `node_started`, `node_finished`, `waiting_approval`, `done`, `failed`.

API 주요 경로 (C 확정). `/auth/login` 외에는 모두 `Authorization: Bearer <JWT>` 필요:

| 메서드 | 경로 | 하는 일 |
| --- | --- | --- |
| POST | /auth/login | 로그인 (body: email, password) → access_token, pb |
| GET | /auth/me | 토큰 확인, 현재 PB |
| POST | /runs | 공시 1건 처리 시작 (body: rcept_no 14자리) → 202. 이미 실행·처리된 공시는 409 |
| GET | /runs/{rcept_no} | 현재 실행 구간의 진행 이벤트 기록 (SSE가 끊겼을 때) |
| POST | /runs/batch | (큐 모드) 여러 공시를 한꺼번에 큐에 넣기 (body: rcept_nos). 이미 처리된 공시는 skipped |
| GET | /runs/queue | (큐 모드) 큐 길이(disclosures, .retry, .dlq)와 최근 처리 결과 |
| GET | /runs/{rcept_no}/events | SSE 진행 이벤트. EventSource용으로 `?token=<JWT>`도 받음. waiting_approval·done·failed 뒤 닫힘 |
| GET | /alerts | 경보 목록. `?level=High&level=Critical`, `?status=`, `?date=YYYY-MM-DD`(일일 요약) |
| GET | /alerts/{assessment_id} | 경보 상세. 다른 PB 고객 이름은 `고객 C002`로 가림 |
| GET | /alerts/{assessment_id}/drafts | 내 담당 고객 안내문 초안 (`{고객명}` 자리표시 + 실명 미리보기) |
| POST | /alerts/{assessment_id}/decision | PB 결정 1묶음 → Graph 재개. 다른 PB 고객 403, 반려 사유 필수 422 |
| GET | /customers | 내 담당 고객 현황: 고객별 보유 종목, 가장 최근의 High·Critical 경보 노출과 우선순위(영향도 규칙), 연락 이력(업무·반려). 다른 PB의 고객은 나오지 않는다. 조회 전용 |
| GET | /tasks | 내 연락 업무 목록 (`?status=`) |
| PATCH | /tasks/{task_id} | 연락완료·보류 처리 (body: status, note). 승인·보류 상태에서만 |

결정 body: `{"decisions": [{"customer_id", "action": "approve"|"reject"|"rewrite", "edited_draft"?, "reject_reason"?}]}`
→ API가 `ApprovalBatch`·`PBContext`로 바꿔 `runtime.resume`에 넘긴다 (4-7절).

Graph 선택: `GRAPH_ADAPTER=fake`(기본, MCP·키 없이 시나리오 A 흉내, 발표 비상용) | `real`(agent.AgentRuntime).
큐(가산점): `docker compose up -d rabbitmq` + `.env`에 `QUEUE_ENABLED=true` → `POST /runs`가 바로 실행하지 않고 큐에 넣고(SSE에 `queued` 이벤트), API 안의 소비자(`api/worker/`)가 한 건씩 실행한다. 체크포인트가 생기기 전 실패(MCP 서버 꺼짐 등)는 3초·10초 뒤 최대 3회 재시도, 그 밖의 실패(MANUAL_CHECK)와 재시도 소진은 `disclosures.dlq`. 관리 화면 http://localhost:15672 (guest/guest).
점검: 서버를 켠 뒤 `uv run python scripts/demo/smoke.py --rcept-no <접수번호>` (9장 5~12단계 확인, 연락 업무를 실제로 만듦).

---

## R4 검증 후 보완 계약 (2026-10-22)

- 승인·재작성 전 최신 공개 상태와 평가의 risk_state_version을 비교하고, Graph 초안의 판정 fingerprint와 저장된 판정도 대조한다. 해소·비경보·버전 불일치는 409이며 감사 결정과 Graph 재개를 만들지 않는다. 변경 후 명시적 새 분석이 필요하다. 전원 반려만 요청하면 오래된 검토도 종료할 수 있다.
- 읽음은 DB의 원자적 최댓값 upsert로 처리하며 감소하지 않는다.
- 설명문에 내부 점수·반영률 표현이 있으면 PB 응답에서 설명을 수동 확인 문구로 대체한다. 원문 인용은 별도 evidence에 그대로 보존한다. R4 adapter의 규칙 설명 fallback 자체도 점수를 포함하지 않는다.
- 기존 /alerts도 새 stage 우선순위와 기준 대비 정렬값을 사용한다. 과거 level만 있는 행에 한해 기존 순위를 호환한다.
- 수집 실행자는 heartbeat로 임대를 갱신하고 finish_stock으로 완료한다. 만료 실행은 다음 요청 또는 운영 복구 함수에서 실패 처리하며 같은 run_id로 재사용하지 않는다.

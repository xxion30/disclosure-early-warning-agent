# API·화면 계약

> 담당: **R4 (화면은 R5)** · 상태: 이전 프로젝트(Capstone) 계약을 그대로 옮긴 것이다. 승인·업무·안내문 API와 화면은 삭제 대상이고, `/states`·`/collect`가 추가된다.
> DARTCH Phase 1에서 바뀌는 부분은 [설계서](../dartch_phase1_mvp.md)와 [파일 구성 계획](../dartch_file_plan.md)을 따르고, 바뀔 때 이 문서를 먼저 고친다.

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

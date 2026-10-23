# R4 승인·반려 SQLite 잠금 수정

## 기준

- 기준: `Pre-merge-P01-v01`의 `fc97061`.
- 브랜치: `codex/r4-approval-sqlite-lock`.
- 요청: 안내문 검토에서 반려 4건 제출 시 database is locked가 발생하고 재시도하면 승인 대기가 아니라는 오류가 나는 문제를 수정한다.
- 담당 범위: 구형 승인 API, RunManager, FakeGraphAdapter, API 계약·회귀 테스트. R2 Graph·새 조직별 검토 서비스·수집·위험 규칙·프론트는 변경하지 않는다.

## 원인과 수정 전 재현

1. 결정 API가 audit_logs를 추가하고, 요청 세션의 yield 의존성 종료 시점에 커밋하도록 둔다.
2. 여러 감사 기록을 추가하는 동안 ORM 자동 flush로 SQLite 쓰기 트랜잭션이 시작된다.
3. API가 커밋 전에 manager.resume을 호출하면 Graph의 별도 연결이 risk_assessments를 RUNNING으로 UPDATE한다.
4. 요청 트랜잭션과 쓰기가 겹쳐 SQLite 잠금 오류가 발생한다. FakeGraphAdapter는 이미 메모리를 RUNNING으로 바꾼 뒤여서 이후 요청은 409로 거절된다.

임시 파일 DB와 독립 연결로 재현했다: WAITING_APPROVAL → POST 202 → failed(database is locked) → 메모리 RUNNING → 재시도 409. 실제 사용자 DB는 변경하지 않았다. 기존 API 테스트의 메모리 SQLite/StaticPool은 연결을 공유하므로 이 경합을 드러내지 못했다.

## 변경

### 커밋 이후에만 Graph 실행

- RunManager.reserve_resume은 해당 공시에 task를 먼저 예약한다. task는 시작 gate를 기다리며 그 전에는 adapter에 진입하지 않는다.
- 예약 task가 있으므로 다른 시작·재개·큐 등록은 실행 중으로 판단한다.
- 요청의 권한·상태·판정·문안 검증을 통과한 뒤 예약하고, 감사 기록을 명시적으로 commit한 다음 gate를 연다.
- commit 실패 시 rollback하고 gate를 열지 않은 task를 취소한다. 이전 이벤트·완료 task를 복구하고 새로 만든 채널은 제거한다. 사용자에게 SQL 내용을 포함하지 않는 503을 반환한다.
- 커밋 후 응답에서 ORM 객체를 다시 읽지 않도록 접수번호를 미리 보존한다.
- 기존 RunManager.resume 직접 호출은 같은 예약 경로로 즉시 gate를 열어 호환한다.

### 실패 단계별 상태 구분

- FakeGraphAdapter는 최초 DB RUNNING 갱신 성공 뒤 메모리를 RUNNING으로 바꾼다. 초기 갱신이 실패하면 승인 대기·미결정 상태가 그대로 남아 재시도할 수 있다.
- 결정/업무 반영이 시작된 뒤 예외가 나면 메모리를 MANUAL_CHECK로 전환하고 DB 상태도 같은 값으로 갱신을 시도한다. 이미 생성한 업무와 결정 내역을 되돌려 미결정으로 위장하지 않으며 재제출은 409다. DB 복구 갱신도 실패하면 원래 오류를 유지한다.
- 202는 결정 요청 저장 및 인계 수락이지 Graph 성공 완료가 아니다. 커밋 뒤 Graph가 실패한 요청의 감사 기록은 보존한다. 재시도는 새 요청이므로 두 번 수락했다면 감사 기록도 두 시도를 남기지만 업무는 한 번만 생성한다.
- gate 대기는 _pump 밖에서 한다. 취소된 예약이 뒤늦게 실패 이벤트를 발행하거나 다음 실행의 SSE 구독자를 닫지 않도록 한다.

## 검증

- 기존 시나리오·실행 API·RealGraphAdapter 연결 시험: **20 passed**.
- 파일 SQLite 회귀 시험: **8 passed**. timeout을 짧게 둔 실제 파일 DB와 서로 다른 연결을 사용한다. DART/MCP/모델은 호출하지 않는다.
- 승인·수정 승인·반려·재작성에서 Graph의 첫 DB 쓰기 전에 다른 연결로 감사 기록 4건이 보이는지 확인한다.
- 커밋 실패 시 503·감사/업무 미생성·Graph 미실행 및 재시도를 검증한다.
- 최초 Graph 쓰기 실패 시 WAITING_APPROVAL 유지·다시 제출 후 업무 4건만 생성됨을 검증한다.
- 업무 commit 후 finalize 실패 시 MANUAL_CHECK와 기존 업무 보존, 재제출 거절을 검증한다.
- 예약 중 중복 시작·재개·큐 등록 차단, 취소 후 이전 이벤트 보존, 실행 중 이중 제출의 추가 감사/업무 미생성을 확인한다.
- 전체 테스트: **825 passed, 6 skipped, 1 failed** (73.42초). 실패는 수정 전에도 확인했던 `tests/demo/test_r5_eval.py::test_r1_gold_answers_for_r5_cases` 한 건으로, 이자보상배율 변경 이후의 R1·R5 정답 충돌이다. 승인·잠금 테스트의 신규 실패는 없다.
- 실행 명령: `UV_CACHE_DIR=/tmp/r4-facts-uv-cache uv run --offline pytest -q`. PostgreSQL opt-in 등 건너뛴 검증을 실운영 DB 검증으로 간주하지 않는다.
- `git diff --check` 통과.

## 범위·운영 참고

- 이 예약은 구형 단일 API 프로세스의 메모리 경계다. 새 조직별 DB 실행기 및 다중 프로세스의 영속 선점을 대체하지 않는다. 이번 수정으로 실제 수집→새 Graph의 미연결 부분이 연결된 것은 아니다.
- DB timeout 증가나 사용자 DB 초기화로 우회하지 않는다. 라이브러리·환경 변수·마이그레이션은 변경하지 않는다.
- 이미 오류가 난 기존 FakeGraph 실행의 메모리 상태를 이 패치가 자동 복원하지는 않는다. FakeGraph 상태는 서버 재시작 시 사라지므로 수정 적용 후 새 데모 실행으로 확인해야 한다. 사용자 DB·기존 평가·업무는 삭제하지 않았다.
- 알려진 별도 실패: 이자보상배율 활성화 후 금양의 첫 경보 정답과 충돌하는 R5 평가 시험은 R1·R5 확인 대상이다. 이 수정에서 정답을 변경하거나 xfail 처리하지 않는다.
- 커밋·푸시·PR 생성·main 머지는 수행하지 않았다.

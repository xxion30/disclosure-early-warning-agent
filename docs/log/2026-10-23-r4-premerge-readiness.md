# 통합 PR의 R4 잔여 항목 확인

## 기준과 범위

- 작업 브랜치: `codex/r4-facts-contract-fix`.
- 원격 통합 브랜치 확인 결과: `b256771` (#50·#51 반영). 기존 미커밋 수정은 보존하고 작업 브랜치를 fast-forward했다. 통합 브랜치를 대상으로 merge하거나 main에 merge하지 않았다.
- 앞선 구조화 사실 연결 수정에 이어 사용자가 제공한 main 머지 전 목록을 점검했다. 운영 API·DART·모델 호출, 실제 수집 활성화, 커밋·푸시·PR 머지는 수행하지 않았다.

## 항목별 결과

| 항목 | 결과 및 근거 |
| --- | --- |
| 구조화 사실 컬럼·마이그레이션 | 이미 구현. Disclosure.structured_facts와 migration 20261023_03 존재. 기존 DB 데이터 보존 및 반복 upgrade 테스트 통과. |
| 조회 도구·R2 입력 연결 | 앞선 R4 수정 적용. 최상위 facts/data_version, 조회 시 number→decimal 투영, 작업 스냅샷 facts_version 제공. R3 저장 데이터는 유지. |
| #50 strict xfail 3건 | 정상 회귀 테스트로 전환. 저장 형식을 수동으로 풀던 테스트를 실제 R2 조회 DTO 기준으로 변경. 기존 4건 모두 통과. |
| 자본잠식 OFS | 이미 구현. 최신 기간 OFS 선택, 동일 기간 OFS 없으면 CFS·이전 기간으로 대체하지 않는 테스트 통과. |
| 이자보상배율 | INTEREST_BURDEN enabled=false 유지 및 가점 미반영 테스트 통과. 경계값은 R1 확정 필요. 임의로 활성화하지 않음. |
| 수동 수집 연결 | 활성화 플래그가 true일 때 실제 R3 dispatch_collection으로 202 요청과 예약 종목을 전달하는 테스트 추가·통과. 기본 false 유지. |
| 전체 공시 인계·선별 | 기존 전체 mark_ready/enqueue_ready 유지. 제외 기준은 R1 합의 필요. 사실 NULL만으로 제외하면 원문 분석이 필요한 공시를 놓칠 수 있음. |
| 체크포인터 R4 생성·주입 | 이미 구현. 추가 테스트에서 open_executors → factory → 실제 R2 AgentRuntime.graph에 동일 saver 주입, 체크포인트 조회, 종료 후 연결 해제를 확인. |
| 운영 Graph factory | R4 생성·주입 경계와 실행 문맥 제공은 구현. 실제 factory 및 unified bindings 연결은 R2와 통합 확인 필요. 레거시 runtime으로 대신 실행하지 않음. |
| R5 추가 PR·R3 정리 작업 | 각각 담당자 합의 사항. R4가 화면·평가·수집·RAG 구현을 변경하지 않음. |

## 이번 추가 변경

- `tests/collector/test_facts_contract_r2.py`: 사용자가 요청한 #50 연결 문제 해결 범위에서 xfail 3건 제거. R3 수집기 소스와 저장 형식은 수정하지 않았다.
- 단순 xfail 제거만으로는 두 번째 테스트가 실패한다. 이 테스트가 호환용 structured_facts의 저장 dtype number를 직접 R2에 주입하기 때문이다. 최상위 facts 응답을 검증하고 각 저장 number 항목의 값·출처가 그대로이며 dtype만 decimal로 투영됐는지 확인하도록 변경했다. 첫 번째 테스트도 최상위 facts를 대상으로 한다. 세 번째 테스트는 data_version과 기존 해시의 일치를 검사한다.
- `tests/api/test_collect_dispatch_wiring.py`: 실제 R3 dispatcher를 사용하되 run_collection만 가짜 실행으로 바꾼다. API의 종목 예약 커밋과 dispatcher 인계를 확인하며 DART는 호출하지 않는다.
- `tests/wiring/test_premerge_ports.py`: R4가 초기화한 SQLite saver의 실제 R2 Graph 주입·조회·종료를 확인한다. Graph 분석·고객 업무는 실행하지 않는다.
- `docs/run.md`: #49 저장 인계 구현 상태와 수집/분석 활성화의 구분, 사실 버전, OFS·보류 규칙·주입 확인 내용을 반영했다.

## 검증

- 최신 #50을 반영한 직후 `--runxfail`: 3 passed, 1 failed. 남은 실패는 저장 DTO를 수동으로 풀어 주입하는 위 테스트였다.
- 수정 후 #50 4건, 수집 연결, 체크포인터 경계, 마이그레이션, 재무 선택 테스트: **15 passed**.
- 최종 전체 테스트: **767 passed, 6 skipped, xfailed/XPASS 없음** (68.23초). `UV_CACHE_DIR=/tmp/r4-facts-uv-cache uv run --offline pytest -q` 실행. 기존 유형의 JWT 테스트 키 길이·라이브러리 폐기 예정 경고 139건이 있다.
- `git diff --check` 통과.
- PostgreSQL 테스트는 R4_POSTGRES_TEST_URL이 필요한 opt-in 검증이다. SQLite 통과를 PostgreSQL 실검증으로 간주하지 않는다.

## 통합 전 남는 확인

1. R1: 이자보상배율 경계값, 자동 분석 제외 기준 결정.
2. R2·R4: 실제 운영 factory·unified bindings 연결 및 미수집 구조화 사실의 원문 분석 분기 확인.
3. 운영 담당: 수동 수집 사용 환경의 COLLECT_DISPATCH_ENABLED 설정 확인. 이 플래그만으로 분석 Graph가 활성화되는 것은 아니다.
4. R3: #50 테스트 변경과 저장용/조회용 dtype 구분 검토. 기간·재무 기준별 다중 사실이 생길 때 R2 중복 키 기준도 별도 협의.
5. R1: 전체 통합 검증 후 main 머지 여부 결정.

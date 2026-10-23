# R4 구조화 사실 number·facts_hash 계약 정리

## 기준

- 브랜치: `codex/r4-facts-number-contract`.
- 기준 통합 커밋: `58d3e74` (#56 반영). 원격 통합 브랜치를 확인한 뒤 새 브랜치를 생성했다.
- 요청: #53 R2 동작에 맞춰 R4 도구·wiring 기대값·계약을 정리한다. R2 소스는 변경하지 않는다.
- 수정 전 wiring 결과: **9 failed, 3 passed**. dtype/data_version 기대값 및 UNAVAILABLE 오류 문구가 현재 R2 동작과 달랐다.

## 구현과 결정

1. 정식 도구 입력은 `ToolResponse.data.structured_facts`이며 사실 목록은 그 안의 facts다. 자료형은 저장본과 동일한 string/number/integer/boolean이다.
2. `get_disclosure_facts`의 number→decimal 투영을 제거했다. 값·출처·단위·결측 상태 및 저장 스키마는 그대로 유지한다.
3. 정식 조회 버전 이름은 **facts_hash**다. R2 validator가 저장 payload의 canonical JSON 해시를 확인하고, receive는 이를 작업 문맥의 **facts_version**과 비교한다. 전체 input_version·원문 content_hash와 구분한다. worker 버전 계산은 변경하지 않았다.
4. 최상위 schema_version/collected_at/facts/data_version은 호환용으로 유지한다. R3 #56 테스트가 아직 최상위 facts를 참조하므로 이번에 제거하지 않는다. 호환 facts도 number를 사용하고 data_version은 facts_hash와 값이 같은 별칭이다. R2는 이 필드를 읽지 않는다. 신규 호출자는 중첩 저장본과 facts_hash를 사용한다.
5. mcp_business.md, agent.md의 상충하는 앞부분, run.md를 위 기준으로 정리했다. R2가 추가한 v1 number 규약과 일치한다. 과거 작업 로그는 당시 기록으로 보존한다.

## 테스트 보완

- wiring의 dtype 기대값을 저장본 그대로로 변경했다. R2 결과의 버전은 data_version이 아닌 facts_hash로 확인한다.
- UNAVAILABLE의 최신 오류와 NULL/정상 빈 목록 구분을 검증한다.
- 정상·결측·0·false·출처 보존, 해시 일치 및 작업 스냅샷 버전 변경 감지는 유지한다.
- R2의 API 근거 구조 안의 data_version은 도구 응답 버전 키와 별개의 기존 선택 필드다. 이 테스트 저장본의 출처에는 해당 값이 없어 API 근거에서는 None으로 검증한다. 이를 facts_hash로 개명하거나 출처에 임의로 주입하지 않았다. 도구 payload 무결성은 facts_hash로 별도 확인한다.
- 호환 최상위 facts/data_version이 바뀌어도 R2가 중첩 저장본과 facts_hash를 사용함을 검증한다. 중첩 값을 해시 변경 없이 변조하면 거절하는 시험을 추가했다.
- 기존 worker 시험은 executor의 KeyError도 MANUAL_CHECK로 처리되는 경우 assertion까지 도달하지 못한 사실을 놓칠 수 있었다. 검증 종료 표식을 외부에서 확인하도록 추가해, receive 결과 검증이 실제 완료됐는지 확인한다. 운영 worker의 오류 처리 로직은 수정하지 않았다.

## 담당 경계

- R2 `agent/structured_facts.py`, `agent/nodes/receive.py` 등 소스 변경 없음.
- R3 수집기·저장기·테스트 변경 없음. #56 연결 시험을 그대로 실행한다. 해당 시험의 과거 decimal 투영 관련 설명은 R3가 후속 정리할 수 있다.
- DB 모델·마이그레이션·위험 정책·수집/분석 실행 설정 변경 없음.
- API/DART/모델 실호출 없음. 테스트는 임시 DB와 로컬 도구를 사용한다.

## 검증 결과

- 관련 wiring·R2 계약·R3 #50 연결·R4 도구 시험: **44 passed**.
- 전체 테스트: **791 passed, 6 skipped** (67.95초). `UV_CACHE_DIR=/tmp/r4-facts-uv-cache uv run --offline pytest -q` 실행. PostgreSQL 등 opt-in 검증은 설정이 없어 건너뛴 항목에 포함되며 실제 DB 검증 완료로 간주하지 않는다. JWT 테스트 키 길이·라이브러리 폐기 예정 경고 139건이 있다.
- `git diff --check` 통과.
- 커밋·푸시·PR 생성·머지는 수행하지 않았다.

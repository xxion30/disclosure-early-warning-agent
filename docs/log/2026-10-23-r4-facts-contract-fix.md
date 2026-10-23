# R4 구조화 사실 연결 수정

- 기준: `Pre-merge-P01-v01`의 `386d676` (#49 머지 완료).
- 작업 브랜치: `codex/r4-facts-contract-fix`.
- 요청: R3가 보고한 MCP 응답 구조·자료형·자료 버전 불일치 해결.
- 범위: R4 도구·실행 adapter·계약·연결 테스트. R2 그래프/검증기 및 R3 수집·저장 코드는 수정하지 않는다.

## 결정 및 구현

1. **조회 응답을 R2 형식으로 맞춘다(b안).** ToolResponse.data에 schema_version/rcept_no/corp_code/collected_at/facts/data_version을 제공한다. invoke_tool이나 R2 validator에 별도 변환을 추가하지 않는다.
2. **저장 스키마를 유지한다.** 조회에서만 dtype number를 decimal로 투영하고 raw/value/unit/status/reason/source는 보존한다. 저장은 기존 StructuredFacts/store_facts 검증을 그대로 통과한다.
3. **사실 버전을 구분한다.** data_version은 변환 전 검증·정규화된 저장 payload의 facts_hash이다. 수집·조회 시각도 포함한다. 전체 분석 입력 해시 또는 원문 해시와 다르다.
4. **작업 스냅샷 버전을 전달한다.** AnalysisContext.facts_version은 input_manifest의 해당 접수번호 사실을 기준으로 계산한다. execution_context 속성은 R4 adapter에서 R2 ExecutionContext 매핑을 만들어 제공한다. 생성 이후 사실이 바뀌어도 작업 버전을 최신 값으로 덮지 않는다.
5. **미수집과 빈 결과를 구분한다.** UNAVAILABLE은 facts/data_version=null, 저장된 정상 빈 목록은 AVAILABLE + facts=[]이다. 결측을 0/false로 바꾸지 않는다.
6. **기존 R3 소비자 호환을 유지한다.** R3의 현재 테스트가 structured_facts/facts_hash를 확인하므로 이 필드는 전환용으로 함께 제공한다. 신규 R2 입력은 평탄화된 facts/data_version을 사용한다. 별칭 제거는 R3 소비자 전환 후 별도 변경한다.

## 문서·담당 경계

- 코드 전에 mcp_business.md와 agent.md에 위 응답·버전 규약을 반영했다. agent.md 변경은 이번 요청의 연결 계약 문구만 해당하며 R2 소스는 변경하지 않았다.
- R2 factory는 `runtime.start(context.rcept_no, context=context.execution_context)`로 공개 실행 문맥을 전달해야 한다. 이 변경은 factory를 신설하거나 unified 실행을 임의 활성화하지 않는다. 운영 factory 연결과 상대 담당자의 계약 검토는 통합 시 확인한다.
- 현재 R2 receive는 구조화 사실이 NULL이면 중단한다. 구조화 API가 없는 공시를 원문 분석으로 넘기는 분기는 R2와 별도로 협의한다. 이번 수정이 이를 자동 해결한 것은 아니다.
- 수집기의 전체 mark_ready와 enqueue_ready 동작은 유지한다. 분석 선별은 R1 기준 합의 후 별도 구현한다. 사실이 없다는 이유만으로 제외하거나 미분석 공시를 성공 처리하지 않는다. 선별 구현 시 앞쪽 제외 대상 때문에 뒤 공시가 계속 대기하는 문제도 방지해야 한다.
- R2 validator의 중복 키는 현재 period/basis를 포함하지 않지만 저장 검증기는 포함한다. 동일 항목·행의 여러 기간/재무 기준을 전달할 경우 키 규칙을 R2와 맞춰야 한다. 이번 세 가지 응답 불일치 수정에 맞춰 임의로 출처나 행 식별자를 변경하지 않았다.
- 최초 검증 시점에는 #50 재현 테스트가 로컬에 없어 수정하지 않았다. 이후 사용자의 통합 잔여 항목 처리 요청으로 #50을 반영하고 연결 테스트를 수정했다. 후속 내역은 `2026-10-23-r4-premerge-readiness.md`에 기록한다.

## 검증

- 수정 전 전체 테스트: **741 passed, 6 skipped**.
- 관련 테스트 1차 실행: **37 passed**.
- 추가한 R4 wiring 테스트는 실제 store_facts → get_disclosure_facts → MCPTools/invoke_tool → R2 validate_facts/receive를 호출한다. 모델·외부 네트워크는 사용하지 않는다. 숫자는 프로토콜 검증용이며 실제 기업 재무 사실을 주장하지 않는다.
- number의 decimal 투영, 0/false·결측 상태·출처 보존, API 근거의 data_version 검증, NULL/정상 빈 목록 구분, 전체 입력/사실 버전 분리, 작업 이후 변경 감지를 확인한다.
- worker는 executor 예외를 내부에서 처리하므로 테스트는 실행 후 DB 작업 상태도 확인한다. executor 내부 assertion 실패가 테스트 성공으로 숨겨지지 않게 했다.
- 최종 전체 테스트: **753 passed, 6 skipped** (64.81초). 추가한 연결 테스트 12건 포함. 기존 JWT 테스트 키 길이·라이브러리 폐기 예정 경고 137건은 수정 전과 동일하다.
- `git diff --check` 통과.

## 기타

- DB 마이그레이션·환경 변수·의존성·위험 점수 규칙 변경 없음.
- 기존 uv 캐시 경로 쓰기가 제한되어 `UV_CACHE_DIR=/tmp/r4-facts-uv-cache uv run --offline pytest -q`로 로컬 의존성을 사용했다.
- 최초 작업에서는 커밋·푸시·PR 생성·머지를 수행하지 않았다. 후속 검증을 위해 작업 브랜치를 최신 통합 참조로 fast-forward했다. 통합 브랜치를 대상으로 merge하거나 main에 merge하지 않았다.

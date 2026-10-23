# Agent 계약 — R2 평가 반영 구현안

기준: 최종 PM ZIP의 CLAUDE.md와 10/22 오후 회의. R3/R4 연동 승인 전의 R2 구현 계약안이다.
이전의 연락 기능 삭제안은 적용하지 않는다. 연락 대상은 보유 고객 전원이며 직접 발송과 영향도/P1~P3는 없다.

## 책임과 실행 경계

R2는 Graph·모델·검증·승인 중단과 재개를 담당한다. 점수·임계값·단계 계산은 R4, 원문·재무 지표는 R3다.
Dependencies.phase1에 Phase1Bindings를 주입하면 새 그래프를 선택한다. None이면 기존 그래프를 유지한다.
R4 소유 adapters/assembly.py, API, DB, risk_engine와 의존성은 수정하지 않는다.
새 흐름은 주입된 분류표와 async 함수만 사용하며 R2가 계산 규칙을 복사하지 않는다.

기존 make_classifier()/KnownSignalsModel()의 무인자 호출은 기존 조립 호환용으로 설정된 YAML을 읽는다.
risk_engine Python import는 제거했지만 이 파일 읽기 호환 경로는 남는다.
신규 조립에서는 make_classifier(document=...), KnownSignalsModel(..., catalog=...)에 R4 카탈로그를 주입한다.
기존 R4 조립의 무인자 호출 교체는 인계 사항이다. 이 제한을 숨겨 완전한 의존성 분리가 끝났다고 주장하지 않는다.

## 새 그래프

receive → classify → mark_red → extract_flags → score_orange → compute_indicators
→ score_yellow → judge_stage → explain_risk → save_state.
관련 없는 갈래는 계산 없이 통과한다.
RED/ORANGE/YELLOW/COMPOSITE만 고객 조회 → 초안 → 검사 → PB 승인 interrupt → 승인된 연락 업무 생성.
WATCH/NONE은 저장하고 종료한다. MANUAL_CHECK는 위험 단계가 아닌 처리 상태다.

## 주입할 도구

Phase1Bindings.classify(title)는 bucket/item/text_flags 경로 배열을 반환한다.
async mark_red, score_orange, financial_snapshot, score_yellow, judge_stage는 dict를 주고받는다.
모두 실행 시간·동일 입력 한도·도구 오류 복구를 적용한다.
financial_snapshot은 R3가 계산·저장한 지표와 출처를 R4가 제공한다. 누락을 0이나 정상으로 대체하지 않는다.

judge_stage 응답:
- stage: RED | ORANGE | YELLOW | COMPOSITE | WATCH | NONE
- orange_score/yellow_score/combined_ratio: 유한한 음수 아닌 내부 수치
- breakdown: item/evidence, 선택적으로 검증 가능한 source_chunks
- evidence: source/quote/rcept_no/location
- needs_manual_check: true면 수동 확인 종료

R2는 점수를 그대로 보존한다. 새 단계 MCP·저장·API는 R4가 연결해야 한다.
기존 ToolAssessmentStore에 새 StageResult를 그대로 연결하면 안 된다.

## 내부 상태와 공개 데이터

score_result와 점수 필드는 내부 전용이다. 설명·안내문 모델 입력은 public_risk_context의 허용 필드만 전달한다.
기존 score_result 입력 키는 유지하되 값은 단계·항목·인용만 있는 공개용 투영이다.
points/weight/note/source_chunks와 내부 점수를 모델 입력에 넣지 않는다.
설명·초안·PB 수정·업무 생성 직전 문안을 검사한다. 금액·날짜·재무 비율을 일괄 삭제하지 않는다.
get_state/start/resume은 서버 내부 반환이다. get_review는 점수 없는 뷰와 PB 소유 고객만 반환한다.
HTTP/SSE가 이 경계를 사용하고 원시 interrupt를 노출하지 않도록 하는 수정은 R4 담당이다.
위험 단계(stage), 처리 상태(status), PB 결정(decisions)은 분리한다.
인용의 실제 위치·연속 부분 문자열 검증은 해석의 재무적 타당성까지 보증하지 않는다.

## 승인·영속성

PBContext는 인증 API가 DB 소유권으로 만든다. 클라이언트 값을 복사하지 않는다.
부분 승인·반려·재작성, 재시작 후 재개, 승인 전 업무 금지, 동일 결정 재전송을 유지한다.
thread_id는 rcept_no. 정책별 재분석·tenant 키·분산 잠금은 R4와 별도 계약이 필요하다.
SQLite는 개발용. Postgres DSN은 AsyncPostgresSaver를 사용하고 의존성 누락 시 명시적으로 실패한다.
Postgres 패키지·실접속·DB 유일 제약은 R4가 준비한다.

## 인계와 평가

- R4: 새 도구/저장/공개 API·SSE, 카탈로그 주입, Postgres, 평가 실행기 등록.
- R3: 실제 수집·지표·원문, 수집 이후 분석 호출 연결, 검색 품질 측정.
- R5: PB 화면의 점수 비노출·승인 동선 실제 확인.
- R1: README/설계서의 오래된 연락 삭제 문구와 CODEOWNERS 정리.
- tests/agent/test_phase1.py: ZIP의 실제 DART 원문 + 가짜 규칙 응답으로 제어 흐름 검증.
- tests/agent/test_reviewed.py: 점수 노출·출처·예산·PB 수정·승인 회귀 검증.
- eval/experiments/{model,guardrail,context}.py, eval/capture_signals.py: 주입된 동일 사례 비교.

테스트는 AI의 실제 금융 예측력이나 실제 모델/API/DB 성능을 입증하지 않는다.

```bash
uv sync --locked
uv run --locked pytest tests/agent -q
uv run --locked pytest -q
```

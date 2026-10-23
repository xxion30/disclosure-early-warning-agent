# R4 → R5 API 후속 보완 — 2026-10-23

## 기존 작업 보존

사용자 요청에 따라 먼저 기존 R4 구현을 `b8b329d` (`feat(r4): add pre-merge facts and isolated execution backends`)로 커밋하고 `origin/codex/r4-premerge-integration`에 푸시했다. 원격 추적 연결과 성공 결과를 확인한 뒤 이 후속 변경을 시작했다. PR 생성·main 머지는 하지 않았다.

## R5 요청 반영

1. **단계 문장:** 새 판정의 headline에 즉시 경보/사건 경보/재무 경보/복합 위험/관찰 중/경보 없음을 사용한다. API stage 코드는 유지한다. stage_label을 상태·평가 응답에 제공한다.
2. **고객 대표 단계:** exposure.top_stage/top_stage_label을 추가했다. 현재 경보 노출만 대상으로 서버의 단계·내부 정렬값·최근 공시 기준을 적용한다. 경보 없음 또는 구형 노출 혼합으로 단계 비교가 불가능하면 null, 기존 top_level은 유지한다. WATCH를 경보 노출에 포함하지 않는다.
3. **변경 요약:** risk_state_changes를 추가하고 현재 상태 저장과 같은 트랜잭션에서 기록한다. 최초 기록은 comparison_available=false다. 단계 상승/하락/동일 우선순위 대표 변경, 새 위험 종류, 같은 종류의 근거 추가, 사건별/종목 전체 해소를 구분한다. 시간 감쇠는 해소로 표시하지 않는다. 같은 상태 재저장은 이력을 중복 생성하지 않는다.
4. **상세 링크:** 현재 상태 버전과 일치하는 assessment_id, public_assessment_id와 요청 조직의 review_id/status/backend를 제공한다. 승인 대기는 담당 고객의 미결정 초안으로 판별한다. 해소/비경보는 can_review=false이며 구형 Graph 미연결·미지원은 pending/can_review=null로 표시한다. Graph를 시작/재개하지 않는다.
5. **구형 판정:** stage_available/legacy를 추가했다. 구형 기본값 NONE을 새 판정의 실제 NONE으로 해석하거나 level을 새 stage로 변환하지 않는다. 정상 새 NONE에는 경보 없음 표시명을 제공한다.
6. **결측 표시:** missing 코드는 보존하고 missing_labels:[{code,label}]를 추가했다. 공통 표시명은 shared/display.py에 모았다.
7. **읽음:** ack 동작은 변경하지 않았다. version은 알림 버전이며 change_version과 검토 버전은 별개다.

## R5 연결 규약

- `GET /states/{code}`: changes/comparison_available/change_version/changed_at 및 검토 링크 필드가 추가됐다.
- changes.kind: stage_up, stage_down, stage_changed, new_type, evidence_added, resolved.
- resolved.scope=event는 signal_type/event_id를 포함하며 scope=stock은 종목 전체 해소다. 개별 사건 해소가 종목 전체 해소를 의미하지 않는다.
- 최초 저장 또는 기존 상태에 대응하는 이력이 없으면 comparison_available=false이며 이전 판정을 추측하지 않는다. 점수·내부 정렬값은 changes에 없다.
- review_backend=organization이면 review_id 기반 새 조직별 검토 경로를 사용해야 한다. 기존 정수 assessment_id는 경보 상세 호환 ID, 문자열 public_assessment_id는 공개 판정 ID다. 서로 바꿔 쓰지 않는다.
- 구형 진행 상태를 확인하지 못한 null은 승인 대기 없음(false)과 다르다. UI는 확인 불가로 표시할 수 있다.

## 변경 범위와 DB

R4 소유 API·공통 표시·위험 상태 저장·모델·계약·테스트만 수정했다. R5 화면, R2 Graph, R3 수집/RAG는 수정하지 않았다. 마이그레이션 `20261023_05`를 추가했고 이전 마이그레이션은 변경하지 않았다. 기존 데이터의 변경 이력을 임의로 역산하지 않는다. 배포/검증 DB에는 `alembic upgrade head`가 필요하다. 사용자 DB에는 적용하지 않았다.

## 검증

- 기존 관련 API/마이그레이션: 21 passed.
- 새 R5 API 계약 테스트: 11 passed (최종 전체 실행에도 포함).
- 최종 전체 `verify_all.py`: **494 passed, 6 skipped**, compileall 통과.
- 임시 PostgreSQL: **5 passed**. 05까지 데이터 보존 업그레이드, 동시 상태/작업/읽음 처리와 체크포인터 복원을 확인했다. 테스트별 독립 스키마만 정리했고 임시 서버는 종료했다.
- `git diff --check` 통과. 실제 DART·유료 모델·운영 DB·전체 UI 시연은 실행하지 않았다.

## 현재 Git 상태와 한계

기존 구현은 b8b329d로 커밋·푸시되어 있다. 이 R5 후속 변경은 별도 로컬 변경이며 아직 커밋·푸시하지 않았다. 새 조직별 Graph/R3 저장 인계/R5 화면의 최종 연결은 앞선 Pre-merge 로그의 과제로 남는다. 이번 변경은 그 전체 연결 완료를 뜻하지 않는다.

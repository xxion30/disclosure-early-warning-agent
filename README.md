# DARTCH (임시 이름)

증권사 PB가 고객 보유 종목의 위험을 공시로 판단하던 일을 AI가 대신하고, 경보와 그 근거를 웹 화면으로 알려 주는 B2B2E 서비스.
**위험 점수와 단계는 규칙이 계산하고, AI는 공시에서 사실을 뽑아 설명만 한다.** 근거를 못 찾으면 추측하지 않고 수동 확인으로 남긴다.

> 상태: **Phase 1 MVP 준비 중.** 지금 저장소는 이전 프로젝트(공시 조기경보 에이전트)를 DARTCH 구조로 정리한 상태이며, 수집기·새 판정 규칙은 아직 없다.

## 문서
| 문서 | 내용 | 담당 |
| --- | --- | --- |
| [CLAUDE.md](CLAUDE.md) | 팀 약속: 담당, 공동 작업 규칙 | R1 |
| [docs/dartch_phase1_mvp.md](docs/dartch_phase1_mvp.md) | Phase 1 MVP 설계서 | R1 |
| [docs/dartch_file_plan.md](docs/dartch_file_plan.md) | 파일 구성 계획 | R1 |
| [docs/architecture.md](docs/architecture.md) | 아키텍처와 품질 목표 | R1 |
| [docs/contracts/](docs/contracts/) | 담당이 다른 코드가 만나는 곳의 계약 (DB·API·MCP·State·규칙·데이터) | 각 담당 |
| [docs/run.md](docs/run.md) | 실행 방법·환경 변수 | R4 |
| [docs/verification.md](docs/verification.md) | 검증 결과·시연 절차 | R5 |

## 역할
| 역할 | 담당자 | 맡은 곳 |
| --- | --- | --- |
| R1 PM·PO | 양시온 | 문제·범위, 점수 정책 확정, 일정, 문서·발표 |
| R2 AI 흐름 | 윤찬웅 | `agent/`, 모델 쪽 Harness, AI 평가 |
| R3 RAG·데이터 | 한예빈 | `collector/`, `mcp_servers/rag/`, `rag_docs/`, `data/` |
| R4 백엔드·도구 | 양경환 | `shared/`, `risk_engine/`, `mcp_servers/business/`, `api/`, 실행 환경 |
| R5 프론트·데모 | 박세호 | `web/`, `scripts/demo/`, 화면 검증·시연 |

## 빠른 시작
실행 방법은 [docs/run.md](docs/run.md)를 본다.
```bash
uv sync                       # 의존성 설치
uv run pytest -q              # 단위 테스트
```

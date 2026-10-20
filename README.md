# 공시 조기경보 에이전트 (SKALA AI Agent Capstone)

공시 속 숨은 위험 신호를 점수로 쌓아 악재가 터지기 전에 경보를 띄우고, 영향받는 고객의 연락 우선순위와 안내문 초안을 만든 뒤 **PB 승인** 후 연락 업무를 만든다.
설계와 팀 약속은 [CLAUDE.md](CLAUDE.md)를 본다.

## 준비
```bash
uv sync                      # Python 3.11 의존성 (uv.lock으로 고정)
cp .env.example .env         # 키를 채운다: ANTHROPIC_API_KEY, OPENAI_API_KEY, LANGSMITH_API_KEY, DART_API_KEY
```
`.env`는 git에 올리지 않는다.

## 데이터 준비 (D)
```bash
uv run python scripts/fetch_dart.py              # OpenDART → data/disclosures/<종목코드>.json (+ demo_companies.json의 corp_code)
uv run python scripts/fetch_dart.py --with-text  # 원문 텍스트까지 (느림)
uv run python scripts/seed_db.py --reset         # data/app.db 생성: PB 2, 고객 20, 보유, 회사 5, 공시
```
- 공시 내용과 접수번호는 OpenDART에서 받은 것만 쓴다. 직접 만들지 않는다.
- 회사별 원문 md는 `rag_docs/companies/<종목코드>_<회사명>/<YYYYMMDD>_<공시종류>.md` (머리말 형식은 CLAUDE.md 7장).
- `corp_code`가 비어 있으면 seed_db가 종목코드를 임시 키로 쓴다. fetch_dart 실행 후 `--reset`으로 다시 적재한다.

## 실행 순서
```bash
uv run python -m mcp_servers.business.server   # 8001
uv run python -m mcp_servers.rag.server        # 8002 (먼저 mcp_servers/rag/ingest.py로 적재)
uv run uvicorn api.main:app --port 8000
cd web && npm install && npm run dev           # 5173
uv run python scripts/replay_demo.py --stock 001570   # 시나리오 A 재생
```
데모 로그인: `p01@demo.local` / `p02@demo.local`, 비밀번호 `demo1234` (가짜 계정).

## Harness (`harness/`)
| 파일 | 구성 요소 | 하는 일 |
| --- | --- | --- |
| `loop_limits.py` | 5 Agent Loop | 8턴 · 120초 · 같은 도구 3회. 넘으면 `LoopLimitExceeded` → MANUAL_CHECK |
| `guardrails.py` | 6 Guardrails | 안내문 금지 표현 검사, 재작성 2회 한도 |
| `masking.py` | 6 Guardrails | 고객 이름 `고객 C001`, 전화·계좌 `***` |
| `recovery.py` | 7 Recovery | LLM 429/503 → 재시도(1초, 3초) → 대체 모델 → FAILED, 도구 TIMEOUT 1회 재시도 |
| `context.py` | 3 Context | 도구 결과 4,000자 자르기, 입력 6,000토큰 예산, 오래된 메시지 요약, RAG 상위 5개 |
| `tracing.py` | 8 Observability | LangSmith 메타데이터(rcept_no, corp_name, risk_level, prompt_version, model), audit_logs 기록 |

Agent(A) 쪽 연결 예:
```python
from harness.tracing import graph_config
from harness.recovery import call_llm_with_recovery, call_tool_with_retry
from harness.loop_limits import count_tool_call, LoopLimitExceeded
from harness.guardrails import check_drafts, to_flags
from harness.masking import mask_obj
from harness.context import truncate_tool_result

config = graph_config(rcept_no, corp_name="금양", model="claude-haiku-5-5")
graph.invoke(state, config=config)
```
- 실패 시나리오: `FAKE_LLM_ERROR=503`이면 기본 모델 호출만 실패해 재시도 → 대체 모델로 넘어간다.

## 평가
```bash
uv run python eval/run_eval.py            # eval/results/*.md 에 전/후 표 저장
uv run python eval/run_eval.py --only guardrail
```
`guardrail`, `context`는 지금 돈다. `combo`, `rag_k`, `model`은 business-mcp·rag-mcp·Agent가 준비되면 `eval/run_eval.py`의 `EXPERIMENTS`에 함수를 채운다.

## 테스트
```bash
uv run pytest tests/harness
```

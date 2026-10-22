# Harness 8요소 계약

> 담당: **R2 (마스킹·tracing·도구 복구는 R4)** · 상태: 이전 프로젝트(Capstone) 계약을 그대로 옮긴 것이다. 안내문 금지 표현 검사는 설명문 검사로 바뀐다.
> DARTCH Phase 1에서 바뀌는 부분은 [설계서](../dartch_phase1_mvp.md)와 [파일 구성 계획](../dartch_file_plan.md)을 따르고, 바뀔 때 이 문서를 먼저 고친다.

## 오후 회의(10/22) 반영 — 이 절이 아래 본문보다 우선한다

- 되살림: `agent/prompts/draft_message.md`, `rewrite_message.md`. `create_contact_task`는 실행 전에 interrupt로 멈추고 PB 승인을 받는다 (PB 승인 단계 유지).
- Guardrails: **안내문 금지 표현 검사**(매도하세요·매수하세요·비중을 줄이·반드시·확실히·손실을 피하 등)를 되살리고, **설명문 검사**도 같은 목록으로 건다. 걸리면 `rewrite_message.md`로 다시 쓰고 2회 실패하면 PB에게 "직접 작성 필요"로 넘긴다.
- LLM은 실제 고객 이름을 보지 않는다 (마스킹 유지). 사람의 결정(승인·반려·수정)은 audit_logs에 남긴다.

## 6. Harness (수업 자료의 8개 구성 요소)

> Agent = 모델 + Harness. 모델은 "다음에 뭘 할지" 제안만 하고, 실행·제한·기록·복구는 Harness가 맡는다.
> 같은 모델을 쓰면 팀마다 차이가 없고, **Harness에서 팀마다 차이가 난다.**

## 모델에 들어가는 입력을 다루는 4개

**1. Instructions — 시스템 프롬프트와 규칙**
- 역할, 도구 사용 규칙, 금지 사항, 출력 형식을 `agent/prompts/*.md`로 따로 두고 버전 관리한다. **코드 안에 프롬프트를 직접 쓰지 않는다.**
- 파일: `system.md`, `extract_facts.md`, `explain_risk.md`, `draft_message.md`, `rewrite_message.md`
- 각 파일 맨 위에 `version: v1` 같은 줄을 두고, 바꿀 때마다 올린다. trace 메타데이터에 버전을 남긴다.
- 출력은 JSON 형식으로 받고 Pydantic으로 검사한다 (`with_structured_output`).

**2. Tools — MCP Client로 가져온 도구**
- `langchain-mcp-adapters`로 business-mcp, rag-mcp 도구를 가져온다.
- 도구 결과가 4,000자를 넘으면 잘라서 넘기고 "(이하 생략, 전체 N자)"를 붙인다 (`harness/context.py`).
- 도구 오류(`ok: false`)는 예외로 멈추지 말고 ToolMessage로 모델에게 돌려준다.

**3. Context — 모델에 넣는 정보의 양 관리**
- LLM 호출 1회당 입력 토큰 예산 6,000. 넘으면 오래된 메시지를 요약해서 줄인다.
- RAG 검색 결과는 상위 5개 조각만 넣는다. 공시 원문 전체를 통째로 넣지 않는다.

**4. State·Memory — 상태 저장**
- `SqliteSaver` 체크포인터, `thread_id = rcept_no`.
- PB 승인 대기에서 멈췄다가, 서버를 재시작해도 같은 thread_id로 그대로 이어서 실행할 수 있어야 한다.

## 실행을 통제하고 기록하는 4개

**5. Agent Loop — 반복 한도**
- 최대 8턴(LangGraph `recursion_limit` + AI 노드 내부 도구 호출 루프), 작업당 120초, 같은 도구는 3회까지.
- 한도를 넘으면 status를 MANUAL_CHECK로 저장하고 종료한다.

**6. Guardrails — 안전장치**
- 데이터를 바꾸는 도구(`create_contact_task`)는 실행 전에 멈추고(interrupt) PB 승인을 받는다. `save_assessment`는 모델이 아니라 코드만 호출한다. `update_task_status`는 PB가 화면에서만 호출한다.
- 도구 결과의 개인정보를 가린다: 고객 이름은 `고객 C001` 형태로, 전화번호·계좌번호는 `***`로. **LLM은 실제 고객 이름을 보지 않는다.** 이름은 화면에 보여줄 때만 채운다.
- 안내문 금지 표현 검사: "매도하세요", "매수하세요", "비중을 줄이", "반드시", "확실히", "손실을 피하" 등 (`harness/guardrails.py`의 목록). 걸리면 `rewrite_message.md`로 다시 쓰고, 2회 실패하면 PB에게 "직접 작성 필요"로 넘긴다.

**7. Recovery — 실패 대응**
- LLM 429(요청 과다)·503(서버 오류): 2회 재시도(1초, 3초 대기) → 대체 모델(`LLM_FALLBACK`)로 교체 → 그래도 실패하면 status FAILED.
- MCP 도구 TIMEOUT: 1회 재시도. 나머지 오류는 State.error에 남기고 MANUAL_CHECK.
- 모든 실패는 audit_logs에 trace_id와 함께 남긴다.

**8. Observability — 기록과 관측**
- LangSmith로 trace_id 하나에 노드, 도구, 토큰, 지연, 비용이 모이게 한다.
- trace 메타데이터: rcept_no, corp_name, risk_level, prompt_version, model.
- 사람의 결정(승인·반려·수정)은 audit_logs에 남긴다.

## 증빙 (발표 장표용)
- **관측:** 금양 시나리오 1회 실행의 LangSmith trace 화면 캡처 (노드별 토큰·지연·비용이 보이게).
- **평가:** `eval/run_eval.py`로 설정을 바꾸기 전/후 결과표를 만든다. 예:
  - 조합 규칙 켬/끔 → 첫 경보가 사건보다 며칠 먼저 울렸는지
  - 가드레일 켬/끔 → 금지 표현이 들어간 안내문 수
  - RAG k=3 / k=5 → 근거 인용 정확도, 토큰 수
  - 기본 모델 / 대체 모델 → 비용, 지연
- 결과는 `eval/results/*.md` 표로 저장한다.

---

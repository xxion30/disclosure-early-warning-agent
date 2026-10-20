# 발표 증빙 — LangSmith trace 3종

LangSmith 프로젝트 `disclosure-early-warning`에서 아래 trace ID로 열어 화면을 캡처한다 (프로젝트 → Runs 검색창에 ID 입력, 또는 접수번호 `20250321802001`로 검색).
화면 캡처는 LangSmith 로그인이 필요해 사람이 직접 한다. 아래 표는 같은 trace를 API로 읽어 만든 수치다 (LLM은 모두 `gpt-5.4-mini`).
trace는 시연할 때마다 새로 생기므로, 발표 직전에 새로 만들려면 아래 "다시 만드는 법"을 쓴다.

## 1. 금양 사건 공시 — 정상 흐름 (노드·도구·토큰·지연·비용이 한 trace에)
trace_id `4625cf07-bc79-4e6b-9f06-a40c9a365cad` · 접수번호 20250321802001 (감사보고서제출, 감사의견 거절)

| 노드 | 시간 | LLM 호출 | 토큰 | 비용 |
| --- | --- | --- | --- | --- |
| extract_facts | 3.5초 | 2 | 4,058 | $0.0021 |
| explain_risk | 9.2초 | 2 | 7,560 | $0.0114 |
| draft_messages | 5.6초 | 1 | 6,796 | $0.0092 |
| 그 밖의 노드 (receive, classify, score_risk, find_customers, guardrail_check …) | 0.1초 미만 | 0 | 0 | 0 |

- LLM 호출은 `select_tools`(도구 선택) 2회를 포함해 5회, 합계 약 $0.0227이다. 규칙 계산 노드(score_risk, find_customers, guardrail_check)는 LLM을 부르지 않는다.
- 같은 trace에 PB 승인 뒤 재개(pb_approval → create_tasks → finalize)가 이어서 붙어 있다 (같은 thread_id).

## 2. 가드레일 — 금지 표현을 잡고 다시 쓰는 흐름
trace_id `f0327be1-4fa8-474b-9895-bfd5896a0bfe` · `FAKE_UNSAFE_DRAFT=true`로 첫 초안에 "지금 바로 매도하세요."를 섞은 실행

노드 순서: `draft_messages`(6.3초) → `guardrail_check`(걸림) → `draft_messages`(다시 쓰기, 6.4초) → `guardrail_check`(통과) → `prepare_approval` → `pb_approval`(승인 대기)

- 같은 trace 안에서 `draft_messages`와 `guardrail_check`가 각각 2번 나온다.
- 실행 결과: 최종 안내문 4개 모두 금지 표현이 없고, 상태는 WAITING_APPROVAL이다.

## 3. LLM 503 — 재시도 → 대체 모델
trace_id `b0b519c6-9a78-4899-9aa1-56f78d1e9a45` · `FAKE_LLM_ERROR=503`

- 기본 모델 호출을 1초·3초 간격으로 재시도한 뒤 대체 모델로 전환해 정상 완료한다 (상태 WAITING_APPROVAL).
- 재시도·전환 기록은 `audit_logs`에 trace_id와 함께 남는다: 이 실행에서 `llm_retry` 15건, `llm_fallback` 5건.
- 한계: 대체 모델도 같은 회사(OpenAI) 모델이라 "다른 회사 모델로 넘어간다"는 설계 의도는 시연하지 못한다 (Anthropic 키 없음).
- 한계: 503은 모델을 부르기 전에 주입하므로, LangSmith에는 실패한 LLM 호출이 따로 남지 않고 성공한 호출만 보인다. 재시도 증거는 `audit_logs`와 위 건수다.

## 다시 만드는 법
```bash
# 1. 정상 흐름: 서버를 띄운 뒤 금양 사건 공시를 처리
uv run python scripts/run_stack.py
uv run python scripts/replay_demo.py --stock 001570
# 2. 가드레일: .env에 FAKE_UNSAFE_DRAFT=true 를 넣고 api 서버를 다시 시작한 뒤 같은 공시를 새 DB에서 처리
# 3. 503: .env에 FAKE_LLM_ERROR=503 을 넣고 api 서버를 다시 시작한 뒤 같은 공시를 새 DB에서 처리
```
(확인 후 `.env`의 두 값은 비운다. 같은 접수번호는 한 번만 처리되므로 DB를 초기화하거나 다른 공시를 쓴다.)

import pytest

from harness import context, guardrails, loop_limits, masking, recovery


# ---- loop_limits ----
def test_same_tool_limit():
    counts = {}
    for _ in range(3):
        counts = loop_limits.count_tool_call(counts, "get_disclosure")
    with pytest.raises(loop_limits.LoopLimitExceeded):
        loop_limits.count_tool_call(counts, "get_disclosure")


def test_turn_limit():
    guard = loop_limits.LoopGuard(max_turns=2)
    guard.check()
    guard.check()
    with pytest.raises(loop_limits.LoopLimitExceeded):
        guard.check()


def test_time_limit():
    guard = loop_limits.LoopGuard(max_seconds=-1)
    with pytest.raises(loop_limits.LoopLimitExceeded):
        guard.check()


# ---- guardrails ----
def test_forbidden_phrases_caught_even_with_spaces():
    assert guardrails.find_forbidden("지금 매도 하세요") == ["매도하세요"]
    assert "반드시" in guardrails.find_forbidden("반드시 확인")


def test_clean_message_passes():
    msg = "{고객명}님, 보유하신 종목에 감사의견 거절 공시가 접수되어 안내드립니다. 편하신 시간에 연락 주세요."
    assert guardrails.check_message(msg) == []


def test_missing_placeholder_flagged():
    assert guardrails.check_message("안녕하세요") == ["{고객명} 자리표시 없음"]


def test_check_drafts_toggle():
    drafts = {"C001": "{고객명}님 매수하세요"}
    assert "C001" in guardrails.check_drafts(drafts)
    assert guardrails.check_drafts(drafts, enabled=False) == {}


def test_manual_flag_after_max_rewrites():
    flagged = {"C001": ["금지 표현: 반드시"]}
    assert guardrails.MANUAL_WRITE_FLAG not in " ".join(guardrails.to_flags(flagged, attempts=1))
    assert guardrails.MANUAL_WRITE_FLAG in " ".join(guardrails.to_flags(flagged, attempts=2))


# ---- masking ----
def test_mask_obj():
    data = [{"customer_id": "C001", "name": "한지우", "phone": "010-1234-5678", "note": "계좌 110-123-456789"}]
    out = masking.mask_obj(data)
    assert out[0]["name"] == "고객 C001"
    assert out[0]["phone"] == "***"
    assert "456789" not in out[0]["note"]
    assert data[0]["name"] == "한지우"  # 원본은 그대로


def test_mask_text_phone():
    assert masking.mask_text("연락처 010 1234 5678") == "연락처 ***"


def test_unmask_draft():
    assert masking.unmask_draft("{고객명}님 안녕하세요", "한지우") == "한지우님 안녕하세요"


# ---- context ----
def test_truncate_tool_result():
    assert context.truncate_tool_result("a" * 100) == "a" * 100
    out = context.truncate_tool_result("a" * 5000)
    assert out.endswith("(이하 생략, 전체 5000자)")
    assert len(out) < 4100


def test_trim_messages_summarizes_middle():
    msgs = [{"role": "system", "content": "지침"}] + [{"role": "user", "content": "가" * 3000} for _ in range(6)]
    out = context.trim_messages(msgs, lambda m: f"{len(m)}개 요약")
    assert out[0]["content"] == "지침"
    assert "이전 대화 요약" in out[1]["content"] and "4개 요약" in out[1]["content"]
    assert len(out) == 4


def test_trim_messages_noop_under_budget():
    msgs = [{"role": "user", "content": "짧음"}] * 5
    assert context.trim_messages(msgs, lambda m: "x") == msgs


# ---- recovery ----
class Err(Exception):
    def __init__(self, code):
        self.status_code = code


@pytest.fixture(autouse=True)
def no_audit(monkeypatch):
    monkeypatch.setattr(recovery, "write_audit_log", lambda *a, **k: None)


def test_retry_then_success():
    calls, sleeps = [], []

    def primary():
        calls.append(1)
        if len(calls) < 3:
            raise Err(503)
        return "ok"

    assert recovery.call_llm_with_recovery(primary, sleep=sleeps.append) == "ok"
    assert sleeps == [1, 3]


def test_fallback_after_retries():
    def primary():
        raise Err(429)

    assert recovery.call_llm_with_recovery(primary, lambda: "fb", sleep=lambda s: None) == "fb"


def test_unavailable_when_all_fail():
    def bad():
        raise Err(503)

    with pytest.raises(recovery.LLMUnavailable):
        recovery.call_llm_with_recovery(bad, bad, sleep=lambda s: None)


def test_non_retryable_raises_immediately():
    def primary():
        raise Err(400)

    with pytest.raises(Err):
        recovery.call_llm_with_recovery(primary, lambda: "fb", sleep=lambda s: None)


def test_fake_error_env(monkeypatch):
    monkeypatch.setenv("FAKE_LLM_ERROR", "503")
    assert recovery.call_llm_with_recovery(lambda: "primary", lambda: "fallback", sleep=lambda s: None) == "fallback"


def test_tool_timeout_retried_once():
    results = iter([{"ok": False, "error": {"code": "TIMEOUT", "message": "t"}}, {"ok": True, "data": {}}])
    assert recovery.call_tool_with_retry(lambda: next(results))["ok"] is True


def test_mask_text_keeps_iso_dates():
    # 날짜(2025-03-21)는 계좌번호 모양과 같아서 가려지면 안 된다
    assert masking.mask_text("접수일 2025-03-21") == "접수일 2025-03-21"
    assert masking.mask_text("계좌 110-123-456789") == "계좌 ***"
    assert masking.mask_obj({"filed_at": "2025-03-21"}) == {"filed_at": "2025-03-21"}

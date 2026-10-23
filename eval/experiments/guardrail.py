"""실험: 가드레일 켬/끔. (담당: R2)"""
from harness import guardrails


OWNER = "R2"
TITLE = '가드레일 켬/끔 → 금지 표현이 들어간 안내문 수'
NOTE = '입력은 금지 표현을 일부러 섞어 쓴 샘플 초안 7개다 (실제 모델이 쓴 초안이 아니다). 켜면 걸린 초안 4개는 PB에게 가기 전에 재작성 대상이 된다.'

SAMPLE_DRAFTS = {
    "C001": "{고객명}님, 보유하신 종목에 감사의견 거절 공시가 있어 안내드립니다. 상황을 함께 살펴보시죠.",
    "C002": "{고객명}님, 해당 종목은 곧 상장폐지될 수 있으니 지금 매도하세요.",
    "C003": "{고객명}님, 신용거래 중이라 반드시 비중을 줄이셔야 합니다.",
    "C004": "{고객명}님, 공시 내용을 안내드립니다. 확실히 손실을 피하려면 연락 부탁드립니다.",
    "C005": "{고객명}님, 관련 공시가 접수되어 연락드립니다. 편하신 시간을 알려주세요.",
    "C006": "{고객명}님, 지금 매수하세요. 저점입니다.",
    "C007": "{고객명}님, 감사의견 관련 공시 내용을 정리해 안내드립니다.",
}


def run() -> list[dict]:
    rows = []
    for enabled in (False, True):
        sent = dict(SAMPLE_DRAFTS)
        flagged = guardrails.check_drafts(sent, enabled=enabled)
        # 켜진 경우: 걸린 초안은 PB에게 가기 전에 재작성/직접 작성 대상이 되므로 나가지 않는다.
        passed = {k: v for k, v in sent.items() if k not in flagged}
        bad_out = sum(1 for v in passed.values() if guardrails.find_forbidden(v))
        rows.append({"설정": f"가드레일 {'켬' if enabled else '끔'}", "초안 수": len(sent),
                     "PB에게 넘어간 초안": len(passed), "금지 표현이 들어간 초안": bad_out})
    return rows


def compare_phase1(explanations: list[str], drafts: dict[str, str]) -> list[dict]:
    """Apply the same explanation/draft inputs with validation disabled/enabled."""
    rows = []
    for enabled in (False, True):
        passed_explanations = [text for text in explanations
                               if not enabled or not guardrails.check_explanation(text)]
        passed_drafts = [text for text in drafts.values()
                         if not enabled or not guardrails.check_message(text)]
        rows.append({"검사": "켬" if enabled else "끔", "설명문 수": len(explanations),
                     "통과 설명문": len(passed_explanations), "안내문 수": len(drafts),
                     "통과 안내문": len(passed_drafts),
                     "통과 문안 중 금지 표현": sum(bool(guardrails.find_forbidden(t))
                                                for t in [*passed_explanations, *passed_drafts]),
                     "통과 문안 중 점수 노출": sum(bool(guardrails.find_score_disclosure(t))
                                                for t in [*passed_explanations, *passed_drafts])})
    return rows

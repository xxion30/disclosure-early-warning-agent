"""PB narrative guard. Financial values and separately supplied citations stay unchanged."""
import re

_INTERNAL_SCORE = re.compile(r'(?:\d+(?:\.\d+)?\s*점|반영률(?:은|는|이|가)?\s*[:：]?\s*\d|(?:총점|위험\s*점수|점수|가중치|orange_score|yellow_score|composite_ratio)(?:은|는|이|가)?\s*[:：=]?\s*\d)', re.I)
MANUAL_EXPLANATION = '설명에 내부 판정값이 포함되어 수동 확인이 필요합니다. 항목별 근거를 확인해 주세요.'
INCOMPLETE_FINANCIAL_NOTICE = '일부 재무 항목은 자료가 부족해 추가 확인이 필요합니다. 표시된 위험은 확인된 근거를 기준으로 하며, 전체 위험 여부가 확정된 것은 아닙니다.'


def public_explanation(text):
    if text and _INTERNAL_SCORE.search(text):
        return MANUAL_EXPLANATION
    return text

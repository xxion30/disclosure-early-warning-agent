"""Harness 6 — Guardrails: 안내문 금지 표현 검사.

걸리면 rewrite_message.md로 다시 쓰고(최대 MAX_REWRITES회), 그래도 실패하면 PB에게 "직접 작성 필요"로 넘긴다.
목록은 팀이 같이 고친다. 매수·매도 권유와 단정·과장 표현이 대상이다.
"""
import re
import unicodedata

MAX_REWRITES = 2
MANUAL_WRITE_FLAG = "직접 작성 필요"
NAME_PLACEHOLDER = "{고객명}"

# 공백이 끼어도 잡도록 검사 전에 공백을 모두 지운다.
FORBIDDEN_PHRASES: list[str] = [
    "매도하세요", "매도하십시오", "매도를권", "팔으세요", "파세요",
    "매수하세요", "매수하십시오", "매수를권", "사세요",
    "비중을줄이", "비중을늘리", "비중을축소", "비중을확대",
    "반드시", "확실히", "무조건", "틀림없", "100%",
    "손실을피하", "손실을막", "손해를피하", "수익을보장", "원금보장",
    "추천드립니다", "권유드립니다",
]


def find_forbidden(text: str) -> list[str]:
    """text에서 발견된 금지 표현 목록 (원래 표기)."""
    squeezed = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))
    return [p for p in FORBIDDEN_PHRASES if p in squeezed]


def find_score_disclosure(text: str) -> list[str]:
    """Detect explicit internal score/weight expressions, not all numerals.

    Normalize width and invisible separators. This deterministic guard is a
    regression barrier, not a semantic proof against every paraphrase.
    """
    normalized = unicodedata.normalize("NFKC", text)
    compact = re.sub(r"[\s\u200b-\u200f\ufeff]+", "", normalized).lower()
    patterns = (
        r"\d+(?:\.\d+)?점(?!유|포)",
        r"(?:총점|배점|위험점수|내부점수|가중치|반영률|복합비율|combined_ratio|orange_score|yellow_score)",
        r"(?:risk|total|internal)[_ -]?score",
        r"(?:score|points?|weight)[:=]",
        r"(?:영|일|이|삼|사|오|육|칠|팔|구|십|한|두|세|네)점(?:입니다|으로|이상|이하|이므로|을|이고|에|$)",
        r"\d+(?:\.\d+)?(?:points?\b|/\d+점)",
    )
    return ["내부 점수·배점·반영률 노출"] if any(re.search(p, compact) for p in patterns) else []


def check_message(text: str) -> list[str]:
    """한 건의 안내문에 대한 문제점 목록. 비어 있으면 통과."""
    problems = [f"금지 표현: {p}" for p in find_forbidden(text)] + find_score_disclosure(text)
    if NAME_PLACEHOLDER not in text:
        problems.append(f"{NAME_PLACEHOLDER} 자리표시 없음")
    return problems


def check_explanation(text: str) -> list[str]:
    """PB 설명문에는 고객명 자리표시를 요구하지 않는다."""
    if not text.strip():
        return ["빈 설명문"]
    return [f"금지 표현: {p}" for p in find_forbidden(text)] + find_score_disclosure(text)


def check_drafts(drafts: dict[str, str], enabled: bool = True) -> dict[str, list[str]]:
    """고객별 초안 검사. 문제가 있는 고객만 {customer_id: [문제...]}로 돌려준다.

    enabled=False는 eval 전/후 비교용(가드레일 끔)이다. 운영에서는 항상 켠다.
    """
    if not enabled:
        return {}
    flagged = {}
    for customer_id, text in drafts.items():
        problems = check_message(text)
        if problems:
            flagged[customer_id] = problems
    return flagged


def to_flags(flagged: dict[str, list[str]], attempts: int) -> list[str]:
    """State.guardrail_flags 형식. 재작성 한도를 넘긴 고객은 '직접 작성 필요'로 표시."""
    flags = []
    for customer_id, problems in flagged.items():
        flags.append(f"{customer_id}: {', '.join(problems)}")
        if attempts >= MAX_REWRITES:
            flags.append(f"{customer_id}: {MANUAL_WRITE_FLAG}")
    return flags

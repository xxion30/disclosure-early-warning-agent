"""실험: 도구 결과 자르기 켬/끔. (담당: R2)"""
from harness import context


OWNER = "R2"
TITLE = '도구 결과 자르기 켬/끔 → 입력 토큰 수'
NOTE = '합성 텍스트(약 6,400자)로 잰 값이다. 토큰은 글자 수 기반 추정이다.'


def run() -> list[dict]:
    raw = "공시 원문 조각. " * 800  # 약 6,400자
    rows = []
    for label, text in (("자르기 끔", raw), ("자르기 켬(4,000자)", context.truncate_tool_result(raw))):
        rows.append({"설정": label, "글자 수": len(text), "추정 토큰": context.estimate_tokens(text)})
    return rows


async def measure_phase1(payloads: list[dict]) -> list[dict]:
    """Measure actual supplied model payloads before/after the R2 input harness."""
    import json
    from agent.harness_llm import prepare_input

    rows = []
    for index, payload in enumerate(payloads):
        prepared = await prepare_input(payload)
        rows.append({"사례": index + 1,
                     "적용 전 추정 토큰": context.estimate_tokens(json.dumps(payload, ensure_ascii=False)),
                     "적용 후 추정 토큰": context.estimate_tokens(json.dumps(prepared, ensure_ascii=False))})
    return rows

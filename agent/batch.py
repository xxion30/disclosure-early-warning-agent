"""R2 batch entry point for an R3/R4 scheduler; no collection or DB policy here."""
import asyncio
import re


async def analyze_receipts(runtime, receipts, *, concurrency=1, contexts=None):
    """Analyze unique receipts and return status summaries without private scores.

    The caller owns durable queue/tenant identity, retry policy and collection
    cursors. Runtime.start keeps its persisted same-receipt/idempotent behavior.
    Default serialization avoids untested parallel mutations in the old DB.
    """
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1:
        raise ValueError("동시 실행 수는 양의 정수여야 합니다")
    unique = list(dict.fromkeys(receipts))
    if any(not isinstance(r, str) or not re.fullmatch(r"\d{14}", r) for r in unique):
        raise ValueError("실제 DART 접수번호 형식을 확인하세요")
    if getattr(getattr(runtime, 'deps', None), 'unified', None) is not None:
        if contexts is None or any(r not in contexts for r in unique):
            raise ValueError('R4 선점 후 생성한 공시별 실행 context가 필요합니다')
    semaphore = asyncio.Semaphore(concurrency)

    async def one(receipt):
        async with semaphore:
            try:
                state = await runtime.start(receipt, **({'context': contexts[receipt]} if contexts else {}))
                return {"rcept_no": receipt, "status": state["status"],
                        "stage": state.get("stage"), "assessment_id": state.get("assessment_id")}
            except Exception as exc:
                # Do not expose provider exception messages, credentials or PII.
                return {"rcept_no": receipt, "status": "FAILED", "error": type(exc).__name__}

    return await asyncio.gather(*(one(r) for r in unique))

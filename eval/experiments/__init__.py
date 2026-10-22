"""평가 실험 모음. 실험마다 파일 하나(담당 표시는 각 파일 위 OWNER). 새 실험은 여기에 등록한다."""
from experiments import combo, context, guardrail, model, rag_k

EXPERIMENTS = {
    name: (module.TITLE, module.run, module.NOTE)
    for name, module in (("guardrail", guardrail), ("context", context), ("combo", combo),
                         ("rag_k", rag_k), ("model", model))
}

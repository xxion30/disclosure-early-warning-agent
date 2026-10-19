"""Harness 8 — Observability: LangSmith 메타데이터와 audit_logs 기록."""
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "agent" / "prompts"
KST = timezone(timedelta(hours=9))


def prompt_version(name: str) -> str:
    """agent/prompts/<name>.md 맨 위의 `version: v1` 줄을 읽는다. 없으면 'unknown'."""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        return "unknown"
    for line in path.read_text(encoding="utf-8").splitlines()[:5]:
        m = re.match(r"\s*version:\s*(\S+)", line)
        if m:
            return m.group(1)
    return "unknown"


def trace_metadata(
    rcept_no: str,
    corp_name: str | None = None,
    risk_level: str | None = None,
    prompt: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """trace 메타데이터: rcept_no, corp_name, risk_level, prompt_version, model.

    graph.invoke(config={"metadata": trace_metadata(...), "tags": [...]}) 로 넘긴다.
    """
    return {
        "rcept_no": rcept_no,
        "corp_name": corp_name,
        "risk_level": risk_level,
        "prompt_version": prompt_version(prompt) if prompt else None,
        "model": model,
    }


def graph_config(rcept_no: str, trace_id: str | None = None, **meta: Any) -> dict:
    """LangGraph config. thread_id = rcept_no, 한 공시의 모든 호출이 같은 trace_id를 쓴다."""
    return {
        "configurable": {"thread_id": rcept_no},
        "metadata": {**trace_metadata(rcept_no, **meta), "trace_id": trace_id or new_trace_id()},
        "tags": ["disclosure-early-warning"],
    }


def new_trace_id() -> str:
    return uuid.uuid4().hex


def write_audit_log(
    actor: str,
    action: str,
    target: str | None = None,
    payload: dict | None = None,
    trace_id: str | None = None,
) -> None:
    """audit_logs에 한 줄 남긴다. 실패·사람의 결정(승인·반려·수정) 모두 여기로."""
    from shared.db import get_session, init_db
    from shared.models import AuditLog

    init_db()
    with get_session() as s:
        s.add(
            AuditLog(
                actor=actor,
                action=action,
                target=target,
                payload=payload or {},
                trace_id=trace_id,
                created_at=datetime.now(KST).isoformat(timespec="seconds"),
            )
        )

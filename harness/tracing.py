"""Harness 8 — Observability: LangSmith 메타데이터와 audit_logs 기록."""
import asyncio
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("harness.tracing")
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


def current_trace_id() -> str | None:
    """진행 중인 LangSmith trace의 root id (없으면 None)."""
    try:
        from langsmith.run_helpers import get_current_run_tree

        run = get_current_run_tree()
        return str(run.trace_id) if run is not None else None
    except Exception:  # noqa: BLE001 — 관측 실패가 본 작업을 막으면 안 된다
        return None


async def audit_system(action: str, target: str | None, error: Any, **extra: Any) -> None:
    try:
        await asyncio.to_thread(
            write_audit_log, "system", action, target, {"error": str(error), **extra}, current_trace_id()
        )
    except Exception:  # noqa: BLE001
        log.exception("audit_logs 기록 실패: %s", action)


def enable_langsmith(settings) -> bool:
    """`.env`의 LangSmith 설정을 환경 변수로 내보낸다. LangChain·LangGraph는 환경 변수만 읽기 때문에
    이걸 하지 않으면 키가 있어도 trace가 전송되지 않는다. 테스트(pytest)에서는 전송하지 않는다."""
    if os.environ.get("PYTEST_CURRENT_TEST") or not (settings.LANGSMITH_TRACING and settings.LANGSMITH_API_KEY):
        return False
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", settings.LANGSMITH_API_KEY)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.LANGSMITH_PROJECT)
    return True

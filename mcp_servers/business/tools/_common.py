"""도구 공통: 오류를 {"ok": false, ...}로 바꾸는 데코레이터와 작은 도우미 (CLAUDE.md 4-4장)."""
from __future__ import annotations

import functools
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.exc import SQLAlchemyError

from risk_engine.risk_rules import RuleInputError
from shared.schemas import ErrorCode, fail

log = logging.getLogger("business-mcp")
KST = timezone(timedelta(hours=9))


class ToolFail(Exception):
    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def not_found(message: str) -> ToolFail:
    return ToolFail(ErrorCode.NOT_FOUND, message)


def invalid(message: str) -> ToolFail:
    return ToolFail(ErrorCode.INVALID_INPUT, message)


def now_iso() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


def guarded(fn):
    """도구 안에서 난 예외를 밖으로 던지지 않고 공통 오류 형식으로 돌려준다."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ToolFail as e:
            return fail(e.code, e.message)
        except (RuleInputError, ValueError) as e:
            return fail(ErrorCode.INVALID_INPUT, str(e))
        except TimeoutError as e:
            return fail(ErrorCode.TIMEOUT, str(e) or "시간 초과")
        except SQLAlchemyError as e:
            log.exception("DB 오류")
            return fail(ErrorCode.DB_ERROR, f"DB 오류: {type(e).__name__}")
        except Exception as e:  # noqa: BLE001 — 도구는 예외를 던지지 않는다
            log.exception("예기치 못한 오류")
            return fail(ErrorCode.DB_ERROR, f"처리 중 오류: {type(e).__name__}: {e}")

    return wrapper


def require_company(s, corp_code: str):
    from shared.models import Company

    if not corp_code or not isinstance(corp_code, str):
        raise invalid("corp_code가 필요합니다.")
    company = s.get(Company, corp_code)
    if not company:
        raise not_found(f"corp_code {corp_code} 회사를 찾을 수 없습니다.")
    return company


def task_dict(t) -> dict:
    return {
        "task_id": t.task_id,
        "assessment_id": t.assessment_id,
        "customer_id": t.customer_id,
        "pb_id": t.pb_id,
        "draft_message": t.draft_message,
        "status": t.status,
        "reject_reason": t.reject_reason,
        "result_note": t.result_note,
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }

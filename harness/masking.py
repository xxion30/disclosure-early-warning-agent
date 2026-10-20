"""Harness 6 — 개인정보 마스킹. LLM은 실제 고객 이름을 보지 않는다.

고객 이름 → `고객 C001`, 전화번호·계좌번호 → `***`. 실제 이름은 화면에 보여줄 때만 채운다.
"""
import re
from typing import Any

# 계좌번호(하이픈 포함 10자리 이상 숫자 묶음)를 전화번호보다 먼저 처리한다.
_PHONE = re.compile(r"01[016789][-\s]?\d{3,4}[-\s]?\d{4}")
_ACCOUNT = re.compile(r"\b\d{2,6}(?:-\d{2,8}){1,3}\b")
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")

NAME_KEYS = {"name", "customer_name"}
SECRET_KEYS = {"phone", "tel", "mobile", "account", "account_no", "account_number", "email"}


def mask_customer_name(customer_id: str) -> str:
    return f"고객 {customer_id}"


def mask_text(text: str) -> str:
    text = _PHONE.sub("***", text)
    # 2025-03-21 같은 날짜는 계좌번호 모양(숫자-숫자-숫자)과 같아서 따로 남겨 둔다.
    return _ACCOUNT.sub(lambda m: m.group(0) if _DATE.fullmatch(m.group(0)) else "***", text)


def mask_obj(obj: Any, customer_id: str | None = None) -> Any:
    """dict/list를 재귀로 돌며 이름·연락처를 가린 복사본을 돌려준다.

    dict에 customer_id가 있으면 그 고객의 name 필드를 `고객 C001`로 바꾼다.
    """
    if isinstance(obj, dict):
        cid = obj.get("customer_id", customer_id)
        out = {}
        for key, value in obj.items():
            if key in NAME_KEYS and cid and isinstance(value, str):
                out[key] = mask_customer_name(cid)
            elif key in SECRET_KEYS and isinstance(value, str):
                out[key] = "***"
            else:
                out[key] = mask_obj(value, cid)
        return out
    if isinstance(obj, list):
        return [mask_obj(v, customer_id) for v in obj]
    if isinstance(obj, str):
        return mask_text(obj)
    return obj


def unmask_draft(draft: str, real_name: str) -> str:
    """화면에 보여줄 때만 {고객명}을 실제 이름으로 채운다."""
    return draft.replace("{고객명}", real_name)

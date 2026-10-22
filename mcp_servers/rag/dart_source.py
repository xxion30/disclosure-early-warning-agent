"""OpenDART에서 공시 원문 1건을 받아 평문으로 돌려준다 (운영의 공시별 임시 색인용).

scripts/fetch_dart.py(D)와 같은 방식(document.xml → zip → 태그 제거)이다. 네트워크·키 오류는 예외로 올리고,
rag 도구가 NOT_FOUND·TIMEOUT 형식으로 바꿔 돌려준다.
"""
from __future__ import annotations

import io
import re
import zipfile

import requests

from shared.config import get_settings

BASE = "https://opendart.fss.or.kr/api"


def _decode(b: bytes) -> str:
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp949", errors="ignore")


def strip_markup(raw: str) -> str:
    raw = re.sub(r"<(style|script)\b.*?</\1>", " ", raw, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()


def fetch_text(rcept_no: str, timeout: float = 60) -> str:
    key = get_settings().DART_API_KEY
    if not key:
        raise RuntimeError("DART_API_KEY가 .env에 없어 원문을 받을 수 없습니다.")
    r = requests.get(f"{BASE}/document.xml", params={"crtfc_key": key, "rcept_no": rcept_no}, timeout=timeout)
    r.raise_for_status()
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            raw = "\n".join(_decode(z.read(n)) for n in z.namelist())
    except zipfile.BadZipFile as e:
        raise RuntimeError("DART가 원문 대신 오류를 돌려줬습니다 (접수번호나 호출 한도를 확인하세요).") from e
    return strip_markup(raw)

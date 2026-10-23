"""수집 대상 정하기: 보유 종목 합집합 읽기 → ETF·비상장 제외 → 종목코드를 DART corp_code로 바꾸기.

계약은 docs/contracts/collector.md 3-1. 공시는 고객사가 공유하므로 보유 종목은 고객사 구분 없이 합집합으로 읽는다.
종목코드가 corpCode.xml에 없으면(예: ETF 069500) 수집 대상이 아니다. 조용히 버리지 않고 `skipped`로 돌려준다.
"""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from collector.dart_client import DartClient
from shared.models import Holding

NOT_IN_CORP_CODES = "not_in_corp_code_list"  # corpCode.xml에 종목코드가 없음 (ETF·펀드·비상장)
CORP_CODE_TTL = timedelta(hours=24)  # 하루 1회 받아 캐시한다 (갱신 주기는 TODO 확인 필요)


@dataclass(frozen=True)
class Target:
    stock_code: str
    corp_code: str
    name: str


@dataclass(frozen=True)
class Skipped:
    stock_code: str
    reason: str


@dataclass
class Watchlist:
    targets: list[Target] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)


def parse_corp_codes(zip_bytes: bytes) -> dict[str, tuple[str, str]]:
    """corpCode.xml 압축 파일 → {종목코드: (corp_code, 회사명)}. 종목코드가 없는 회사는 뺀다.

    종목코드가 없는 회사의 값은 빈 문자열이 아니라 공백 한 칸(' ')이라서 strip해서 거른다 (확인됨).
    종목코드가 겹치는 경우는 실제 데이터에서 없었지만(확인됨), 있으면 modify_date가 나중인 것을 쓴다.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        root = ET.fromstring(z.read(z.namelist()[0]))
    best: dict[str, tuple[str, str, str]] = {}
    for item in root.iter("list"):
        stock = (item.findtext("stock_code") or "").strip()
        corp = (item.findtext("corp_code") or "").strip()
        if not stock or not corp:
            continue
        modified = (item.findtext("modify_date") or "").strip()
        if stock not in best or modified > best[stock][2]:
            best[stock] = (corp, (item.findtext("corp_name") or "").strip(), modified)
    return {stock: (corp, name) for stock, (corp, name, _) in best.items()}


def held_stock_codes(session: Session) -> list[str]:
    """모든 고객사의 보유 종목코드 합집합 (중복 제거, 정렬)."""
    return sorted({code for code in session.scalars(select(Holding.stock_code).distinct()) if code})


def resolve(stock_codes: list[str], corp_map: dict[str, tuple[str, str]]) -> Watchlist:
    """종목코드를 corp_code로 바꾼다. 매핑이 없는 종목은 skipped에 사유와 함께 남긴다."""
    result = Watchlist()
    for stock in sorted(set(stock_codes)):
        hit = corp_map.get(stock)
        if hit is None:
            result.skipped.append(Skipped(stock, NOT_IN_CORP_CODES))
        else:
            result.targets.append(Target(stock, hit[0], hit[1]))
    return result


class CorpCodeBook:
    """corpCode.xml을 메모리에 캐시한다 (디스크에 쓰지 않는다)."""

    def __init__(self, ttl: timedelta = CORP_CODE_TTL):
        self._ttl = ttl
        self._map: dict[str, tuple[str, str]] = {}
        self._fetched_at: datetime | None = None

    async def get(self, client: DartClient, *, now: datetime | None = None) -> dict[str, tuple[str, str]]:
        now = now or datetime.now(timezone.utc)
        if self._fetched_at is None or now - self._fetched_at >= self._ttl:
            self._map = parse_corp_codes(await client.corp_code_zip())
            self._fetched_at = now
        return self._map


async def build_watchlist(session: Session, client: DartClient, book: CorpCodeBook | None = None) -> Watchlist:
    """보유 종목 합집합 → 수집 대상. 한 번의 수집 실행(run)의 입구."""
    book = book or CorpCodeBook()
    return resolve(held_stock_codes(session), await book.get(client))

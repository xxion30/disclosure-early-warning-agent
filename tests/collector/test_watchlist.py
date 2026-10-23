"""watchlist 테스트: corpCode.xml은 실제 형식(종목코드 없는 회사는 공백 한 칸)을 줄여 만든 가짜를 쓴다."""
import asyncio
import io
import zipfile
from datetime import datetime, timedelta, timezone

import httpx

from collector.dart_client import DartClient
from collector.watchlist import (
    NOT_IN_CORP_CODES,
    CorpCodeBook,
    Skipped,
    Target,
    build_watchlist,
    held_stock_codes,
    parse_corp_codes,
    resolve,
)
from shared.db import get_session


def corp_xml(*rows) -> bytes:
    items = "".join(
        f"<list><corp_code>{c}</corp_code><corp_name>{n}</corp_name><corp_eng_name>x</corp_eng_name>"
        f"<stock_code>{s}</stock_code><modify_date>{m}</modify_date></list>"
        for c, n, s, m in rows
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", f'<?xml version="1.0" encoding="UTF-8"?><result>{items}</result>')
    return buf.getvalue()


# corp_code·종목코드는 실제 값 (금양, SK하이닉스). 종목코드가 없는 회사는 공백 한 칸으로 온다 (확인됨)
SAMPLE = corp_xml(
    ("00106119", "금양", "001570", "20240101"),
    ("00164779", "SK하이닉스", "000660", "20240101"),
    ("00999999", "비상장회사", " ", "20240101"),
)


def test_parse_corp_codes_skips_blank_stock_code():
    mapping = parse_corp_codes(SAMPLE)
    assert mapping == {"001570": ("00106119", "금양"), "000660": ("00164779", "SK하이닉스")}


def test_parse_corp_codes_prefers_latest_modify_date_for_duplicates():
    mapping = parse_corp_codes(corp_xml(("00000001", "옛회사", "123456", "20200101"), ("00000002", "새회사", "123456", "20250101")))
    assert mapping["123456"] == ("00000002", "새회사")


def test_resolve_skips_codes_missing_from_corp_list():
    mapping = parse_corp_codes(SAMPLE)
    result = resolve(["001570", "069500", "000660", "001570"], mapping)  # 069500 = KODEX 200 (ETF), 중복 입력
    assert result.targets == [Target("000660", "00164779", "SK하이닉스"), Target("001570", "00106119", "금양")]
    assert result.skipped == [Skipped("069500", NOT_IN_CORP_CODES)]


def test_held_stock_codes_is_union_across_tenants(seeded_db):
    with get_session() as s:
        codes = held_stock_codes(s)
    assert codes == sorted(set(codes)) and "069500" in codes and "001570" in codes  # 시드 보유 종목에 ETF가 들어 있다


def test_build_watchlist_excludes_etf_from_seed(seeded_db):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, content=SAMPLE)

    client = DartClient("test-key", transport=httpx.MockTransport(handler), max_rps=0)

    async def main():
        with get_session() as s:
            return await build_watchlist(s, client)

    result = asyncio.run(main())
    assert [t.stock_code for t in result.targets] == ["000660", "001570"]  # 샘플 corpCode에 있는 종목만
    assert "069500" in [k.stock_code for k in result.skipped] and calls == [1]


def test_corp_code_book_caches_within_ttl_and_refreshes_after():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, content=SAMPLE)

    client = DartClient("test-key", transport=httpx.MockTransport(handler), max_rps=0)
    book = CorpCodeBook(ttl=timedelta(hours=24))
    t0 = datetime(2026, 10, 23, 9, 5, tzinfo=timezone.utc)

    async def main():
        await book.get(client, now=t0)
        await book.get(client, now=t0 + timedelta(hours=23))  # 캐시
        await book.get(client, now=t0 + timedelta(hours=24))  # 만료

    asyncio.run(main())
    assert len(calls) == 2

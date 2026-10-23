"""OpenDART 호출 클라이언트 (httpx async, 재시도, 호출 속도 제한).

지금 `scripts/data/fetch_dart.py`와 `mcp_servers/rag/dart_source.py`에 따로 있는 호출 코드를 합친 것이다.
계약은 docs/contracts/collector.md 6장(오류·재시도)과 data_sources.md를 따른다.

- `013`(조회된 데이터 없음)은 오류가 아니라 빈 결과다.
- `020`(호출 한도 초과), 시간 초과·연결 오류·5xx는 백오프로 재시도하고, 계속되면 예외를 올린다.
- `100` 등 그 밖의 오류 상태와 4xx는 재시도하지 않는다.
- API 키와 원문은 예외 메시지·로그에 남기지 않는다. httpx의 예외 문자열에는 키가 든 URL이 들어가므로 우리 메시지로 바꿔 올린다.
- 원문 해시는 이 파일의 `normalize_document` + `content_sha256`만 계산한다 (mcp_rag.md "원문 해시").
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
import time
import zipfile
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import NoReturn

import httpx

from shared.config import get_settings

BASE_URL = "https://opendart.fss.or.kr/api/"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = 1.0
DEFAULT_RPS = 4.0  # 한도를 모른다(TODO 확인 필요). 확인 호출이 0.25초 간격에서 문제없었다는 것뿐이다
DEFAULT_CONCURRENCY = 3
PAGE_COUNT = 100
MAX_PAGES = 200

OK, NO_DATA, RATE_LIMITED, BAD_PARAM = "000", "013", "020", "100"

_KEY_IN_URL = re.compile(r"(crtfc_key=)[^&\s\"']+")


def redact_key(text: str) -> str:
    return _KEY_IN_URL.sub(r"\1***", text)


def _install_log_redaction() -> None:
    """httpx는 요청 URL(쿼리 포함)을 INFO 로그로 남기고, httpcore는 DEBUG로 남긴다. 어떤 로거·핸들러로 가든 키가 새지 않도록
    로그 레코드를 만드는 단계에서 `crtfc_key=` 값을 가린다. 여러 번 불러도 한 번만 설치된다."""
    previous = logging.getLogRecordFactory()
    if getattr(previous, "_redacts_dart_key", False):
        return

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        try:
            message = record.getMessage()
            if "crtfc_key" in message:
                record.msg, record.args = redact_key(message), ()
        except Exception:  # noqa: BLE001 — 로깅 때문에 호출이 실패하면 안 된다
            pass
        return record

    factory._redacts_dart_key = True  # type: ignore[attr-defined]
    logging.setLogRecordFactory(factory)


_install_log_redaction()


class DartError(Exception):
    """DART 호출 실패. status는 DART 상태 코드(HTTP 오류 등은 None)."""

    def __init__(self, message: str, status: str | None = None):
        super().__init__(message)
        self.status = status


class DartRateLimited(DartError):
    """호출 한도 초과(020). 재시도 횟수를 넘겼다."""


class DartBadRequest(DartError):
    """재시도해도 소용없는 오류 (잘못된 값, 4xx, 알 수 없는 상태 코드)."""


class DartUnavailable(DartError):
    """시간 초과·연결 오류·5xx·JSON 아님. 재시도 횟수를 넘겼다."""


class _Retry(Exception):
    def __init__(self, error: DartError):
        self.error = error


def _decode(b: bytes) -> str:
    """DART 원문은 UTF-8이 대부분이지만 일부(옛 공시)는 CP949다."""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp949", errors="ignore")


def strip_markup(raw: str) -> str:
    raw = re.sub(r"<(style|script)\b.*?</\1>", " ", raw, flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()


def normalize_document(files: Iterable[bytes]) -> str:
    """document.xml 압축 안의 파일들(`namelist()` 순서) → 이어 붙여 태그를 지운 평문."""
    return strip_markup("\n".join(_decode(b) for b in files))


def content_sha256(text: str) -> str:
    return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Document:
    rcept_no: str
    text: str  # 비어 있을 수 있다 (호출한 쪽이 NOT_FOUND로 볼지 정한다)
    sha256: str


def _ymd(d: date | str) -> str:
    return d.strftime("%Y%m%d") if isinstance(d, date) else d


class _RateLimiter:
    """호출 간격을 1/max_rps초 이상으로 벌린다. 자리를 잠금 안에서 잡고 기다리는 것은 밖에서 한다."""

    def __init__(self, max_rps: float, clock: Callable[[], float], sleep: Callable[[float], Awaitable[None]]):
        self._interval = 1.0 / max_rps if max_rps and max_rps > 0 else 0.0
        self._clock, self._sleep = clock, sleep
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        if not self._interval:
            return
        async with self._lock:
            now = self._clock()
            start = max(now, self._next)
            self._next = start + self._interval
        if start > now:
            await self._sleep(start - now)


class DartClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_RETRIES,
        backoff: float = DEFAULT_BACKOFF,
        max_rps: float = DEFAULT_RPS,
        max_concurrency: int = DEFAULT_CONCURRENCY,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        key = (api_key if api_key is not None else get_settings().DART_API_KEY).strip()
        if not key:  # 가짜 키도 `000`이 올 수 있어(확인됨) 응답으로는 키 오류를 못 잡는다. 비었을 때만 막는다
            raise ValueError("DART_API_KEY가 비어 있습니다. .env를 확인하세요.")
        self._key = key
        self._max_retries, self._backoff, self._sleep = max_retries, backoff, sleep
        self._sem = asyncio.Semaphore(max_concurrency)
        self._limiter = _RateLimiter(max_rps, clock, sleep)
        self._http = httpx.AsyncClient(base_url=BASE_URL, timeout=timeout, transport=transport)

    @classmethod
    def from_settings(cls, **overrides) -> "DartClient":
        """DART_* 설정이 shared/config.py에 추가되면 읽고, 없으면 기본값을 쓴다 (설정 추가는 R4)."""
        s = get_settings()
        kw = {
            "timeout": getattr(s, "DART_TIMEOUT_SECONDS", DEFAULT_TIMEOUT),
            "max_retries": getattr(s, "DART_RETRY_MAX", DEFAULT_RETRIES),
            "max_rps": getattr(s, "DART_MAX_RPS", DEFAULT_RPS),
            "max_concurrency": getattr(s, "DART_MAX_CONCURRENCY", DEFAULT_CONCURRENCY),
        }
        return cls(**{**kw, **overrides})

    async def __aenter__(self) -> "DartClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    # ---- 공통 호출 ----
    async def _send(self, path: str, params: dict) -> httpx.Response:
        async with self._sem:
            await self._limiter.wait()
            try:
                resp = await self._http.get(path, params={"crtfc_key": self._key, **params})
            except httpx.TimeoutException:
                raise _Retry(DartUnavailable(f"DART {path} 시간 초과")) from None
            except httpx.TransportError:
                raise _Retry(DartUnavailable(f"DART {path} 연결 오류")) from None
        if resp.status_code == 429 or resp.status_code >= 500:
            raise _Retry(DartUnavailable(f"DART {path} HTTP {resp.status_code}"))
        if resp.status_code >= 400:
            raise DartBadRequest(f"DART {path} HTTP {resp.status_code}")
        return resp

    async def _call(self, path: str, params: dict, parse: Callable[[httpx.Response], object]):
        last: DartError | None = None
        for attempt in range(self._max_retries + 1):
            if attempt:
                await self._sleep(self._backoff * 2 ** (attempt - 1))
            try:
                return parse(await self._send(path, params))
            except _Retry as r:
                last = r.error
        assert last is not None
        raise last

    @staticmethod
    def _check_status(path: str, status: str, message: str | None) -> NoReturn:
        """`000`·`013`이 아닌 상태. 020만 재시도 대상이고 `100`을 포함한 나머지는 재시도하지 않는다."""
        text = f"DART {path} 상태 {status}: {message or ''}".strip()
        if status == RATE_LIMITED:
            raise _Retry(DartRateLimited(text, status))
        raise DartBadRequest(text, status)

    def _parse_json(self, path: str) -> Callable[[httpx.Response], dict]:
        def parse(resp: httpx.Response) -> dict:
            try:
                body = resp.json()
            except ValueError:
                raise _Retry(DartUnavailable(f"DART {path} 응답이 JSON이 아닙니다")) from None
            status = str(body.get("status", ""))
            if status in (OK, NO_DATA):
                return body
            self._check_status(path, status, body.get("message"))

        return parse

    def _parse_zip(self, path: str) -> Callable[[httpx.Response], bytes]:
        def parse(resp: httpx.Response) -> bytes:
            if resp.content[:2] == b"PK":
                return resp.content
            # 오류는 zip이 아니라 <result><status>…</status><message>…</message></result> 로 온다
            text = _decode(resp.content)
            m_status = re.search(r"<status>\s*(\d+)\s*</status>", text)
            m_msg = re.search(r"<message>(.*?)</message>", text, re.S)
            if m_status:
                self._check_status(path, m_status.group(1), m_msg.group(1).strip() if m_msg else None)
            raise DartBadRequest(f"DART {path}가 압축 파일 대신 알 수 없는 응답을 돌려줬습니다")

        return parse

    async def get_json(self, path: str, **params) -> dict:
        """JSON API 1회 호출(재시도 포함). `013`이면 `status`가 `013`인 본문을 그대로 돌려준다."""
        return await self._call(path, params, self._parse_json(path))  # type: ignore[return-value]

    # ---- 조회 ----
    async def list_disclosures(
        self, corp_code: str, bgn_de: date | str, end_de: date | str, *, pblntf_ty: str | None = None
    ) -> list[dict]:
        """공시 목록(list.json) 전 페이지. 조회된 데이터가 없으면 빈 목록. pblntf_ty="I"는 거래소 공시."""
        rows: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            params = {"corp_code": corp_code, "bgn_de": _ymd(bgn_de), "end_de": _ymd(end_de),
                      "page_no": page, "page_count": PAGE_COUNT}
            if pblntf_ty:
                params["pblntf_ty"] = pblntf_ty
            body = await self.get_json("list.json", **params)
            if body.get("status") == NO_DATA:
                return rows
            rows.extend(body.get("list") or [])
            if page >= int(body.get("total_page") or 1):
                return rows
        raise DartBadRequest(f"list.json 페이지가 {MAX_PAGES}개를 넘었습니다 (corp_code={corp_code})")

    async def major_report(self, api: str, corp_code: str, bgn_de: date | str, end_de: date | str) -> list[dict]:
        """주요사항보고서 항목 API(예: piicDecsn). 기간 필터를 믿을 수 없으므로 호출한 쪽이 rcept_no로 고른다."""
        body = await self.get_json(f"{api}.json", corp_code=corp_code, bgn_de=_ymd(bgn_de), end_de=_ymd(end_de))
        return body.get("list") or []

    async def periodic_report(self, api: str, corp_code: str, bsns_year: int | str, reprt_code: str) -> list[dict]:
        """정기보고서 주요정보 API(예: accnutAdtorNmNdAdtOpinion)."""
        body = await self.get_json(f"{api}.json", corp_code=corp_code, bsns_year=str(bsns_year), reprt_code=reprt_code)
        return body.get("list") or []

    async def financial_statements(
        self, corp_code: str, bsns_year: int | str, reprt_code: str, fs_div: str
    ) -> list[dict]:
        """fnlttSinglAcntAll. fs_div는 CFS(연결)·OFS(개별). 데이터가 없으면 빈 목록."""
        body = await self.get_json(
            "fnlttSinglAcntAll.json", corp_code=corp_code, bsns_year=str(bsns_year), reprt_code=reprt_code, fs_div=fs_div
        )
        return body.get("list") or []

    async def corp_code_zip(self) -> bytes:
        """corpCode.xml 압축 파일 원본(종목코드 → corp_code 변환용). 파싱은 watchlist.py가 한다."""
        return await self._call("corpCode.xml", {}, self._parse_zip("corpCode.xml"))  # type: ignore[return-value]

    async def fetch_document(self, rcept_no: str) -> Document:
        """공시 원문(document.xml)을 받아 평문과 해시로 돌려준다. 원문은 저장하지 않는다."""
        data = await self._call("document.xml", {"rcept_no": rcept_no}, self._parse_zip("document.xml"))
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:  # type: ignore[arg-type]
                text = normalize_document(z.read(n) for n in z.namelist())
        except zipfile.BadZipFile:
            raise DartBadRequest("DART 원문 압축 파일이 손상됐습니다") from None
        return Document(rcept_no=rcept_no, text=text, sha256=content_sha256(text))

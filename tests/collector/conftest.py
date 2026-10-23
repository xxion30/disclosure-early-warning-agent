"""collector 테스트 공용: 임시 SQLite DB와 가짜 DART 서버(httpx.MockTransport). 키·네트워크가 필요 없다.

응답 모양은 실제 DART에서 본 것을 줄인 것이다 (금양 00106119: 접수번호·금액은 실제 값).
"""
import io
import zipfile

import httpx
import pytest

import shared.db as db_module
from collector.dart_client import DartClient
from shared.config import get_settings

KY = {"corp_code": "00106119", "stock_code": "001570", "name": "금양"}

# 실제 금양 목록에서 본 행 (list.json)
ROW_NOTICE = {"corp_code": "00106119", "corp_name": "금양", "stock_code": "001570", "rcept_no": "20250321802062",
              "rcept_dt": "20250321", "report_nm": "투자유의안내              (상장폐지 기준 해당)", "flr_nm": "유가증권시장본부", "rm": "유"}
ROW_ANNUAL = {"corp_code": "00106119", "corp_name": "금양", "stock_code": "001570", "rcept_no": "20250321001823",
              "rcept_dt": "20250321", "report_nm": "사업보고서 (2024.12)", "flr_nm": "금양", "rm": "연"}


def fs_rows(rcept_no="20250321001823"):
    """fnlttSinglAcntAll 행 (금양 2024 연결, 금액은 실제 값)."""
    def r(sj, acc_id, nm, cur, prev=None):
        return {"rcept_no": rcept_no, "sj_div": sj, "account_id": acc_id, "account_nm": nm, "thstrm_amount": cur, "frmtrm_amount": prev}
    return [
        r("BS", "ifrs-full_Equity", "자본총계", "494536343970"), r("BS", "ifrs-full_IssuedCapital", "자본금", "31983598000"),
        r("BS", "ifrs-full_CashAndCashEquivalents", "현금및현금성자산", "19955152951"),
        r("BS", "-표준계정코드 미사용-", "단기차입금", "245369019460"),
        r("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", "-20344818738", "14930707843"),
        r("CIS", "dart_OperatingIncomeLoss", "영업이익(손실)", "-56002221720"), r("CIS", "ifrs-full_FinanceCosts", "금융원가", "27112738869"),
    ]


AUDIT_ROWS = [{"bsns_year": "제70기\n(당기)", "adt_opinion": "의견거절", "rcept_no": "20250321001823"},
              {"bsns_year": "제69기\n(전기)", "adt_opinion": "적정의견", "rcept_no": "20250321001823"}]


def corp_zip(*companies) -> bytes:
    items = "".join(f"<list><corp_code>{c['corp_code']}</corp_code><corp_name>{c['name']}</corp_name>"
                    f"<stock_code>{c['stock_code']}</stock_code><modify_date>20240101</modify_date></list>" for c in companies)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("CORPCODE.xml", f"<result>{items}</result>")
    return buf.getvalue()


class FakeDart:
    """경로별 응답. `list_rows[corp_code]`, `fs[(corp_code, fs_div)]`, `audit[corp_code]`를 채워 쓴다. 호출은 `calls`에 쌓인다."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.list_rows: dict[str, list[dict]] = {}
        self.fs: dict[tuple[str, str], list[dict]] = {}
        self.audit: dict[str, list[dict]] = {}
        self.fail_corps: set[str] = set()  # 이 회사의 list.json은 서버 오류(503)
        self.corp_zip = corp_zip(KY)

    def paths(self, name: str) -> list[dict]:
        return [p for path, p in self.calls if path == name]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        p = dict(request.url.params)
        self.calls.append((path, p))
        if path == "corpCode.xml":
            return httpx.Response(200, content=self.corp_zip)
        corp = p.get("corp_code", "")
        if path == "list.json":
            if corp in self.fail_corps:
                return httpx.Response(503)
            rows = self.list_rows.get(corp, [])
            return httpx.Response(200, json={"status": "000" if rows else "013", "message": "m", "total_page": 1, "list": rows})
        if path == "fnlttSinglAcntAll.json":
            rows = self.fs.get((corp, p["fs_div"]), [])
            return httpx.Response(200, json={"status": "000" if rows else "013", "message": "m", "list": rows})
        if path == "accnutAdtorNmNdAdtOpinion.json":
            rows = self.audit.get(corp, [])
            return httpx.Response(200, json={"status": "000" if rows else "013", "message": "m", "list": rows})
        return httpx.Response(200, json={"status": "013", "message": "m"})

    def client(self, **kw) -> DartClient:
        async def no_sleep(_):
            return None

        kw.setdefault("max_rps", 0)
        return DartClient("test-key", transport=httpx.MockTransport(self), sleep=no_sleep, **kw)


@pytest.fixture()
def fake():
    return FakeDart()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """임시 SQLite에 모든 표를 만든다 (R4의 init_db)."""
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'collector.db'}")
    get_settings.cache_clear()
    db_module._engine = None
    db_module._SessionLocal = None
    db_module.init_db()
    yield db_module
    db_module._engine = None
    db_module._SessionLocal = None
    get_settings.cache_clear()

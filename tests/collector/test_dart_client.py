"""DartClient 테스트: 가짜 transport(httpx.MockTransport)로 키·네트워크 없이 돈다. 재시도 대기는 기록만 하고 기다리지 않는다."""
import asyncio
import hashlib
import io
import zipfile

import httpx
import pytest

from collector.dart_client import (
    DartBadRequest,
    DartClient,
    DartRateLimited,
    DartUnavailable,
    _RateLimiter,
    content_sha256,
    normalize_document,
)

# 실제 금양(00106119) 공시 목록에서 관측한 행 (접수번호는 실제 것)
ROW_A = {"corp_code": "00106119", "corp_name": "금양", "stock_code": "001570", "rcept_no": "20250321802062",
         "rcept_dt": "20250321", "report_nm": "투자유의안내              (상장폐지 기준 해당)", "flr_nm": "유가증권시장본부", "rm": "유"}
ROW_B = {"corp_code": "00106119", "corp_name": "금양", "stock_code": "001570", "rcept_no": "20250410801159",
         "rcept_dt": "20250410", "report_nm": "기타시장안내              (상장폐지 이의신청서 제출)", "flr_nm": "유가증권시장본부", "rm": "유"}


def make(handler, **kw):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    kw.setdefault("max_rps", 0)  # 속도 제한은 따로 시험한다
    client = DartClient("test-key", transport=httpx.MockTransport(handler), sleep=fake_sleep, **kw)
    return client, sleeps


def run(coro_fn):
    async def main():
        return await coro_fn()

    return asyncio.run(main())


def json_response(status, **extra):
    return httpx.Response(200, json={"status": status, "message": "m", **extra})


def test_empty_key_is_rejected():
    with pytest.raises(ValueError):
        DartClient("   ")


def test_get_json_sends_key_and_params():
    seen = []

    def handler(request):
        seen.append(request.url.params)
        return json_response("000", list=[ROW_A], total_page=1)

    client, _ = make(handler)
    body = run(lambda: client.get_json("list.json", corp_code="00106119"))
    assert body["list"] == [ROW_A]
    assert seen[0]["crtfc_key"] == "test-key" and seen[0]["corp_code"] == "00106119"


def test_no_data_013_is_empty_result_not_error():
    client, sleeps = make(lambda request: json_response("013"))
    assert run(lambda: client.list_disclosures("00106119", "20250101", "20250131")) == []
    assert sleeps == []  # 재시도하지 않았다


def test_list_disclosures_follows_pages():
    pages = []

    def handler(request):
        page = int(request.url.params["page_no"])
        pages.append(page)
        return json_response("000", list=[ROW_A] if page == 1 else [ROW_B], total_page=2)

    client, _ = make(handler)
    rows = run(lambda: client.list_disclosures("00106119", "20250301", "20250430", pblntf_ty="I"))
    assert [r["rcept_no"] for r in rows] == ["20250321802062", "20250410801159"]
    assert pages == [1, 2]


def test_rate_limit_020_retries_with_backoff_then_succeeds():
    codes = iter(["020", "020", "000"])
    client, sleeps = make(lambda request: json_response(next(codes), list=[]), backoff=1.0)
    body = run(lambda: client.get_json("list.json"))
    assert body["status"] == "000"
    assert sleeps == [1.0, 2.0]  # 지수 백오프


def test_rate_limit_020_gives_up_after_max_retries():
    calls = []

    def handler(request):
        calls.append(1)
        return json_response("020")

    client, sleeps = make(handler, max_retries=2)
    with pytest.raises(DartRateLimited) as e:
        run(lambda: client.get_json("list.json"))
    assert len(calls) == 3 and len(sleeps) == 2 and e.value.status == "020"


def test_server_error_is_retried():
    answers = iter([httpx.Response(503), httpx.Response(502), json_response("000", list=[ROW_A], total_page=1)])
    client, sleeps = make(lambda request: next(answers))
    assert run(lambda: client.list_disclosures("00106119", "20250101", "20250430")) == [ROW_A]
    assert len(sleeps) == 2


def test_client_error_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404)

    client, sleeps = make(handler)
    with pytest.raises(DartBadRequest):
        run(lambda: client.get_json("list.json"))
    assert len(calls) == 1 and sleeps == []


def test_bad_param_100_is_not_retried():
    calls = []

    def handler(request):
        calls.append(1)
        return json_response("100")

    client, _ = make(handler)
    with pytest.raises(DartBadRequest) as e:
        run(lambda: client.get_json("list.json", corp_code="123"))
    assert len(calls) == 1 and e.value.status == "100"


def test_timeout_is_retried_and_error_hides_key():
    def handler(request):
        raise httpx.ReadTimeout(f"timeout for {request.url}")  # httpx 예외 문자열에는 키가 든 URL이 들어간다

    client, sleeps = make(handler, max_retries=1)
    with pytest.raises(DartUnavailable) as e:
        run(lambda: client.get_json("list.json"))
    assert len(sleeps) == 1
    assert "test-key" not in str(e.value) and e.value.__cause__ is None


def test_api_key_never_reaches_logs(caplog):
    """httpx는 요청 URL을 INFO로 남기고 httpcore는 DEBUG로 남긴다. 어느 로거로 가든 키가 보이면 안 된다 (실제로 한 번 샜다)."""
    import logging

    caplog.set_level(logging.DEBUG)
    client, _ = make(lambda request: json_response("000", list=[]))
    run(lambda: client.get_json("list.json", corp_code="00106119"))
    logging.getLogger("httpcore.http11").debug("send b'/api/list.json?crtfc_key=test-key&corp_code=1'")  # httpcore 모양
    logging.getLogger("anything").info("GET %s", "https://opendart.fss.or.kr/api/list.json?crtfc_key=test-key")
    assert "HTTP Request" in caplog.text  # 로그는 남되
    assert "test-key" not in caplog.text and "crtfc_key=***" in caplog.text  # 키만 가려진다
    assert all("test-key" not in r.getMessage() for r in caplog.records)


def test_rate_limiter_spaces_calls():
    waits: list[float] = []

    async def fake_sleep(seconds):
        waits.append(seconds)

    async def main():
        limiter = _RateLimiter(2.0, clock=lambda: 0.0, sleep=fake_sleep)  # 시계가 멈춰 있어도 자리가 밀린다
        for _ in range(3):
            await limiter.wait()

    asyncio.run(main())
    assert waits == [0.5, 1.0]


def test_concurrency_is_limited():
    state = {"now": 0, "max": 0}

    async def handler(request):
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0)  # 다른 호출이 끼어들 틈을 준다
        state["now"] -= 1
        return json_response("000", list=[])

    client, _ = make(handler, max_concurrency=2)

    async def main():
        await asyncio.gather(*(client.get_json("list.json") for _ in range(6)))

    asyncio.run(main())
    assert state["max"] <= 2


def make_zip(*files: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, content in enumerate(files):
            z.writestr(f"{i}.xml", content)
    return buf.getvalue()


def test_fetch_document_normalizes_and_hashes():
    utf8 = "<style>p{color:red}</style><p>감사의견   <b>의견거절</b></p>".encode("utf-8")
    cp949 = "<p>옛 공시 본문</p>".encode("cp949")
    client, _ = make(lambda request: httpx.Response(200, content=make_zip(utf8, cp949)))
    doc = run(lambda: client.fetch_document("20250321802062"))
    assert doc.text == "감사의견 의견거절 옛 공시 본문"
    assert doc.sha256 == hashlib.sha256(doc.text.encode("utf-8")).hexdigest()


def test_normalize_matches_existing_rag_source():
    """지금 rag가 쓰는 dart_source.strip_markup과 같은 결과여야 이미 계산한 해시와 어긋나지 않는다."""
    from mcp_servers.rag import dart_source

    raw = "<html><script>x=1</script><body><p>가 나</p>\n<td>다</td></body></html>"
    assert normalize_document([raw.encode("utf-8")]) == dart_source.strip_markup(raw)


def test_content_sha256_ignores_outer_whitespace():
    assert content_sha256("  본문 \n") == content_sha256("본문")


def test_document_error_xml_raises_bad_request_with_status():
    body = "<result><status>014</status><message>파일이 존재하지 않습니다.</message></result>".encode("utf-8")
    client, _ = make(lambda request: httpx.Response(200, content=body))
    with pytest.raises(DartBadRequest) as e:
        run(lambda: client.fetch_document("20250321802062"))
    assert e.value.status == "014"


def test_document_rate_limit_xml_is_retried():
    limit = "<result><status>020</status><message>한도</message></result>".encode("utf-8")
    answers = iter([httpx.Response(200, content=limit), httpx.Response(200, content=make_zip("<p>본문</p>".encode("utf-8")))])
    client, sleeps = make(lambda request: next(answers))
    assert run(lambda: client.fetch_document("20250321802062")).text == "본문"
    assert len(sleeps) == 1

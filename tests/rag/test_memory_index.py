"""운영 모드(RAG_INDEX_MODE=ephemeral)의 메모리 색인 테스트: 가짜 DART(fetch_document 교체) + fake 임베딩 + 임시 SQLite. 키·네트워크 불필요.

원문은 DART에서만 받고, 디스크에는 아무것도 쓰지 않으며, 사용 중인 색인은 지워지지 않는지(R2 요구, harness.md 5장)를 시험한다.
"""
import asyncio
import threading
import time
from pathlib import Path

import pytest

import shared.db as db_module
from collector import repo
from collector.dart_client import DartBadRequest, DartRateLimited, DartUnavailable, Document, content_sha256
from collector.watchlist import Target
from mcp_servers.rag import filing, memory, store
from mcp_servers.rag.tools import release_disclosure_index, search_disclosure_text, search_reference
from shared.config import get_settings
from shared.models import Disclosure

RCEPT = "20250321001823"  # 금양 사업보고서 (실제 접수번호)
SENTENCE = "감사인은 회사의 계속기업 가정에 중요한 불확실성이 있어 감사의견을 표명하지 않습니다. "
BODY = " ".join(f"[{n}] " + SENTENCE * 4 for n in range(1, 13))  # 800자 조각이 여러 개 나오는 길이


class FakeDart:
    def __init__(self, text=BODY):
        self.text, self.calls, self.threads, self.delay, self.error = text, 0, [], 0.0, None

    async def __call__(self, rcept_no):
        self.calls += 1
        self.threads.append(threading.current_thread())
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return Document(rcept_no, self.text, content_sha256(self.text))


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_PROVIDER", "fake")
    monkeypatch.setenv("RAG_INDEX_MODE", "ephemeral")
    monkeypatch.setenv("CHROMA_DIR", str(tmp_path / "chroma"))
    monkeypatch.setenv("DB_URL", f"sqlite:///{tmp_path / 'rag.db'}")
    get_settings.cache_clear()
    db_module._engine = db_module._SessionLocal = None
    db_module.init_db()
    memory.reset_all()
    dart = FakeDart()
    monkeypatch.setattr(filing, "fetch_document", dart)

    def no_chroma(*_a, **_k):
        raise AssertionError("운영 모드에서 Chroma를 열었다 (디스크 색인 금지)")

    monkeypatch.setattr(store, "get_client", no_chroma)
    yield dart
    memory.reset_all()
    db_module._engine = db_module._SessionLocal = None
    get_settings.cache_clear()


def chunks_of(res):
    assert res["ok"], res
    return res["data"]["chunks"]


# ---- 색인 만들기·검색 ----
def test_first_search_downloads_from_dart_once_and_returns_citable_chunks(mem):
    res = search_disclosure_text(RCEPT, "계속기업 불확실성", 3)
    again = search_disclosure_text(RCEPT, "감사의견", 3)
    assert mem.calls == 1  # 두 번째는 메모리 색인을 쓴다
    chunks = chunks_of(res)
    assert len(chunks) == 3 and all(c["rcept_no"] == RCEPT and c["source"] and c["text"] and c["score"] <= 1 for c in chunks)
    assert chunks[0]["location"].startswith("chunk-") and chunks[0]["location"].endswith("자)")  # 이전과 같은 위치 형식
    assert res["data"]["content_sha256"] == content_sha256(BODY) == again["data"]["content_sha256"]
    assert res["data"]["truncated"] is False
    assert all(c["text"] in BODY.replace("\n", " ") for c in chunks)  # 인용은 조각 text 안의 글자 그대로


def test_same_text_gives_same_locations_after_rebuild(mem):
    """release 뒤 다시 받아도 같은 원문이면 같은 조각·위치가 나와 과거 인용을 다시 검증할 수 있다."""
    first = chunks_of(search_disclosure_text(RCEPT, "계속기업", 5))
    release_disclosure_index(RCEPT)
    second = chunks_of(search_disclosure_text(RCEPT, "계속기업", 5))
    assert mem.calls == 2 and [(c["location"], c["text"]) for c in first] == [(c["location"], c["text"]) for c in second]


def test_original_text_is_never_written_to_disk(mem, tmp_path):
    before = {p for p in tmp_path.rglob("*")}
    search_disclosure_text(RCEPT, "계속기업", 3)
    release_disclosure_index(RCEPT)
    assert not (tmp_path / "chroma").exists()  # Chroma 영속 폴더를 만들지 않았다
    new_files = {p for p in tmp_path.rglob("*") if p.is_file()} - {p for p in before if p.is_file()}
    assert all(p.suffix in (".db", "") or p.name.endswith("-journal") for p in new_files)  # 새 파일은 SQLite 관련뿐
    assert not any(BODY[:30] in p.read_text(errors="ignore") for p in new_files if p.is_file())


def test_db_body_text_and_files_are_ignored_but_metadata_names_the_source(mem):
    with db_module.get_session() as s:
        repo.upsert_company(s, Target("001570", "00106119", "금양"))
        repo.upsert_disclosures(s, "00106119", [{"rcept_no": RCEPT, "rcept_dt": "20250321", "report_nm": "사업보고서 (2024.12)"}])
        s.get(Disclosure, RCEPT).body_text = "DB에 남아 있는 옛 본문 — 읽으면 안 된다"
    chunks = chunks_of(search_disclosure_text(RCEPT, "계속기업", 5))
    assert all(c["source"] == "금양 사업보고서 (2024.12) (2025-03-21)" for c in chunks)  # 출처 이름은 메타데이터에서
    assert not any("옛 본문" in c["text"] for c in chunks)  # 본문은 DART에서만


def test_source_falls_back_when_no_metadata(mem):
    assert chunks_of(search_disclosure_text(RCEPT, "x", 1))[0]["source"] == f"DART {RCEPT}"


def test_concurrent_requests_for_same_filing_download_and_index_once(mem):
    mem.delay = 0.05
    results = []

    def go():
        results.append(search_disclosure_text(RCEPT, "계속기업", 2))

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert mem.calls == 1 and len(results) == 8 and all(r["ok"] for r in results)
    assert len({tuple(c["location"] for c in chunks_of(r)) for r in results}) == 1


def test_oversized_filing_is_truncated_and_flagged(mem, monkeypatch):
    monkeypatch.setattr(filing, "MAX_CHUNKS", 2)
    res = search_disclosure_text(RCEPT, "계속기업", 10)
    assert res["data"]["truncated"] is True and res["data"]["count"] == 2


# ---- 오류 ----
@pytest.mark.parametrize("error, code", [
    (DartRateLimited("한도", "020"), "TIMEOUT"), (DartUnavailable("5xx"), "TIMEOUT"),
    (DartBadRequest("파일이 존재하지 않습니다", "014"), "NOT_FOUND"), (ValueError("DART_API_KEY가 비어 있습니다."), "NOT_FOUND"),
])
def test_dart_failures_map_to_tool_errors(mem, error, code):
    mem.error = error
    res = search_disclosure_text(RCEPT, "계속기업")
    assert res["ok"] is False and res["error"]["code"] == code
    assert "test-key" not in str(res)


def test_empty_original_is_not_found(mem):
    mem.text = "   "
    assert search_disclosure_text(RCEPT, "x")["error"]["code"] == "NOT_FOUND"


def test_bad_input_is_rejected_before_any_download(mem):
    for args in [("abc", "x"), (RCEPT, "  "), (RCEPT, "x", 0), (RCEPT, "x", 99), (RCEPT, "x", 5, " "), (RCEPT, "x", 5, "h" * 200)]:
        assert search_disclosure_text(*args)["error"]["code"] == "INVALID_INPUT"
    assert release_disclosure_index(RCEPT, "")["error"]["code"] == "INVALID_INPUT"
    assert mem.calls == 0  # 잘못된 입력으로 DART를 부르지 않았다


# ---- 해제와 사용 중 보호 ----
def test_release_removes_reports_count_and_is_idempotent(mem):
    n = search_disclosure_text(RCEPT, "계속기업", 1)["data"]["count"]
    first = release_disclosure_index(RCEPT)["data"]
    assert first["released_chunks"] > 0 and first["content_sha256"] == content_sha256(BODY) and first["mode"] == "memory"
    assert first["deferred"] is False and RCEPT not in filing.INDEX and n == 1
    assert release_disclosure_index(RCEPT)["data"] == {"rcept_no": RCEPT, "released_chunks": 0, "content_sha256": None, "mode": "memory", "deferred": False}


def test_release_is_deferred_while_another_run_holds_the_filing(mem):
    search_disclosure_text(RCEPT, "계속기업", 1, "run-A")
    search_disclosure_text(RCEPT, "감사의견", 1, "run-B")
    a = release_disclosure_index(RCEPT, "run-A")["data"]
    assert a["deferred"] is True and a["released_chunks"] == 0 and RCEPT in filing.INDEX  # B가 아직 쓴다
    b = release_disclosure_index(RCEPT, "run-B")["data"]
    assert b["deferred"] is False and b["released_chunks"] > 0 and RCEPT not in filing.INDEX
    assert mem.calls == 1


def test_anonymous_release_does_not_remove_a_filing_a_named_run_still_holds(mem):
    search_disclosure_text(RCEPT, "계속기업", 1, "run-1")
    assert release_disclosure_index(RCEPT)["data"]["deferred"] is True  # R4 어댑터처럼 holder 없이 불러도 남의 사용권은 지키지 않는다
    assert RCEPT in filing.INDEX
    assert release_disclosure_index(RCEPT, "run-1")["data"]["released_chunks"] > 0


def make_entry(key="k", n=2):
    return memory.Entry(key=key, texts=["a"] * n, meta=[{"source": "s", "location": f"chunk-{i}"} for i in range(n)], vectors=[[1.0, 0.0]] * n, sha256="h")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_search_in_progress_blocks_release_until_it_ends():
    reg = memory.Registry(clock=Clock())
    with reg.lease("k", make_entry):
        out = reg.release("k")
        assert out["deferred"] is True and out["released_chunks"] == 0 and "k" in reg  # 검색 도중에는 지우지 않는다
    assert "k" not in reg  # 끝나면 요청해 둔 해제가 이루어진다


def test_ttl_removes_idle_filings_but_not_held_or_leased_ones():
    clock = Clock()
    reg = memory.Registry(clock=clock, ttl=100, holder_ttl=500)
    for key in ("idle", "held", "leased"):
        with reg.lease(key, lambda key=key: make_entry(key), holder="run" if key == "held" else None):
            pass
    lease = reg.lease("leased", lambda: make_entry("leased"))
    lease.__enter__()
    clock.now += 101
    with reg.lease("fresh", lambda: make_entry("fresh")):
        pass
    assert "idle" not in reg and "held" in reg and "leased" in reg  # 사용 중인 것은 TTL이 지나도 남는다
    clock.now += 500  # holder가 갱신되지 않으면 죽은 실행으로 본다
    with reg.lease("fresh2", lambda: make_entry("fresh2")):
        pass
    assert "held" not in reg and "leased" in reg  # 진행 중인 검색(lease)은 계속 지킨다
    lease.__exit__(None, None, None)


def test_lru_eviction_skips_filings_in_use():
    reg = memory.Registry(clock=Clock(), max_entries=2)
    keep = reg.lease("A", lambda: make_entry("A"))
    keep.__enter__()  # A는 가장 오래됐지만 사용 중
    for key in ("B", "C"):
        with reg.lease(key, lambda key=key: make_entry(key)):
            pass
    assert "A" in reg and "B" not in reg and "C" in reg
    keep.__exit__(None, None, None)


def test_all_in_use_allows_exceeding_capacity_instead_of_deleting():
    reg = memory.Registry(clock=Clock(), max_entries=1)
    held = [reg.lease(k, lambda k=k: make_entry(k)) for k in ("A", "B")]
    [h.__enter__() for h in held]
    assert len(reg) == 2  # 쓰는 색인을 지워서 상한을 맞추지 않는다
    [h.__exit__(None, None, None) for h in held]


# ---- 참고 문서 ----
def test_reference_search_uses_memory_index(mem):
    res = search_reference("안내문 작성 원칙", "안내문가이드", 3)
    assert res["ok"] and 1 <= res["data"]["count"] <= 3
    assert all(c["category"] == "안내문가이드" and c["source"] and c["location"] for c in res["data"]["chunks"])
    assert search_reference("x", "없는분류")["error"]["code"] == "INVALID_INPUT"
    assert not (Path(get_settings().CHROMA_DIR)).exists()


# ---- 서버 ----
def test_server_runs_tools_off_the_event_loop_thread_and_keeps_signatures(mem):
    from fastmcp import Client

    from mcp_servers.rag.server import mcp

    mem.delay = 0.3
    ticks = []

    async def ticker():
        while True:
            ticks.append(1)
            await asyncio.sleep(0.02)

    async def run():
        async with Client(mcp) as c:
            tools = {t.name: t for t in await c.list_tools()}
            task = asyncio.create_task(ticker())
            res = await c.call_tool("search_disclosure_text", {"rcept_no": RCEPT, "query": "계속기업", "holder": "run-1"})
            task.cancel()
            rel = await c.call_tool("release_disclosure_index", {"rcept_no": RCEPT, "holder": "run-1"})
            return tools, res.data, rel.data

    tools, res, rel = asyncio.run(run())
    assert set(tools) == {"search_disclosure_text", "search_reference", "release_disclosure_index"}
    assert tools["search_disclosure_text"].inputSchema["required"] == ["rcept_no", "query"]  # holder는 선택 → 기존 호출자는 그대로
    assert "holder" in tools["release_disclosure_index"].inputSchema["properties"]
    assert res["ok"] and rel["data"]["released_chunks"] > 0
    assert len(ticks) > 5  # 0.3초 다운로드 동안 이벤트 루프가 돌았다 (서버가 막히지 않는다)
    assert all(t is not threading.main_thread() for t in mem.threads)

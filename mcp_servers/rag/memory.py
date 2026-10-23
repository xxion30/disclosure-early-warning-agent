"""공시 원문의 메모리 색인 레지스트리. 디스크에 아무것도 쓰지 않는다 (Chroma·파일·DB 모두 쓰지 않음).

계약은 docs/contracts/mcp_rag.md, harness.md 5장(R2). 지키는 것:
- 같은 접수번호를 동시에 요청하면 **한 번만 받고 한 번만 색인**한다 (키별 잠금).
- **사용 중인 색인은 지우지 않는다.** 검색이 진행 중인 동안(lease)과, 실행이 사용권(holder)을 들고 있는 동안은
  TTL·LRU·release 어느 것도 지우지 못한다. holder가 갱신되지 않으면(죽은 실행) HOLDER_TTL 뒤에 무시한다.
- release는 멱등이다. 아직 쓰는 쪽이 있으면 실제 제거를 늦추고(deferred) 마지막 사용이 끝나면 지운다. 반환의 released_chunks는 실제 제거 수다.
- 오래 안 쓴 색인은 TTL로, 개수가 넘치면 LRU로 지운다 (사용 중인 것은 제외).

아래 상수는 부하를 재 보기 전의 잠정값이다 (TODO 확인 필요).
"""
from __future__ import annotations

import logging
import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field

log = logging.getLogger("rag-mcp")

INDEX_TTL = 30 * 60  # 마지막 사용 뒤 이 시간이 지나면(사용 중이 아닐 때) 지운다
HOLDER_TTL = 15 * 60  # holder가 이 시간 동안 갱신되지 않으면 죽은 실행으로 본다
MAX_ENTRIES = 20  # 공시 색인을 동시에 몇 개까지 들고 있을지 (사용 중인 것은 넘어도 지우지 않는다)


def _norm(vec: list[float]) -> float:
    return math.sqrt(sum(v * v for v in vec))


@dataclass
class Entry:
    """공시 1건(또는 참고 문서 묶음)의 조각·벡터. 조각마다 meta는 {source, location, ...}."""

    key: str
    texts: list[str]
    meta: list[dict]
    vectors: list[list[float]]
    sha256: str | None = None  # 원문 해시 (원문이 바뀌었는지 비교용). 참고 문서는 None
    truncated: bool = False  # 조각 수 상한 때문에 뒷부분을 색인하지 못했다
    created_at: float = 0.0
    last_used: float = 0.0
    leases: int = 0  # 지금 진행 중인 검색 수
    holders: dict[str, float] = field(default_factory=dict)  # 사용권: holder → 마지막으로 본 시각
    release_requested: bool = False
    _norms: list[float] | None = field(default=None, repr=False)

    def top(self, query: list[float], k: int, where: Callable[[dict], bool] | None = None) -> list[tuple[float, int]]:
        """코사인 유사도가 높은 (점수, 조각 번호) k개. where로 조각을 거른다 (참고 문서의 category)."""
        if self._norms is None:
            self._norms = [_norm(v) for v in self.vectors]
        q_norm = _norm(query)
        scored = []
        for i, (vec, n) in enumerate(zip(self.vectors, self._norms)):
            if where is not None and not where(self.meta[i]):
                continue
            denom = n * q_norm
            scored.append((sum(a * b for a, b in zip(vec, query)) / denom if denom else 0.0, i))
        scored.sort(key=lambda s: s[0], reverse=True)
        return scored[:k]


class Registry:
    def __init__(
        self, *, clock: Callable[[], float] = time.monotonic, ttl: float = INDEX_TTL,
        holder_ttl: float = HOLDER_TTL, max_entries: int = MAX_ENTRIES,
    ):
        self._clock, self._ttl, self._holder_ttl, self._max = clock, ttl, holder_ttl, max_entries
        self._entries: OrderedDict[str, Entry] = OrderedDict()  # 오래 안 쓴 것이 앞
        self._building: dict[str, threading.Lock] = {}
        self._lock = threading.RLock()

    # ---- 상태 ----
    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def __contains__(self, key: str) -> bool:
        with self._lock:
            return key in self._entries

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._building.clear()

    def _live_holders(self, e: Entry) -> list[str]:
        now = self._clock()
        for holder, seen in list(e.holders.items()):
            if now - seen >= self._holder_ttl:
                del e.holders[holder]
        return list(e.holders)

    def _unused(self, e: Entry) -> bool:
        return e.leases == 0 and not self._live_holders(e)

    def _touch(self, e: Entry, holder: str | None) -> None:
        now = self._clock()
        e.last_used = now
        e.release_requested = False  # 다시 쓰는 쪽이 생겼으니 앞선 해제 요청은 접는다
        if holder:
            e.holders[holder] = now
        self._entries.move_to_end(e.key)

    def _remove(self, e: Entry) -> int:
        self._entries.pop(e.key, None)
        return len(e.texts)

    def _sweep(self) -> None:
        now = self._clock()
        for e in list(self._entries.values()):
            if self._unused(e) and (e.release_requested or now - e.last_used >= self._ttl):
                self._remove(e)

    def _evict_over_capacity(self) -> None:
        while len(self._entries) > self._max:
            victim = next((e for e in self._entries.values() if self._unused(e)), None)
            if victim is None:  # 전부 쓰는 중이면 넘는 것을 허용한다 (쓰는 색인을 지우지 않는다)
                log.warning("공시 색인이 %d개로 상한 %d를 넘었지만 모두 사용 중이라 지우지 않는다", len(self._entries), self._max)
                return
            self._remove(victim)

    # ---- 사용 ----
    @contextmanager
    def lease(self, key: str, build: Callable[[], Entry], holder: str | None = None):
        """색인을 빌려 쓴다 (없으면 build로 만든다). with 블록이 끝날 때까지 지워지지 않는다."""
        entry = self._acquire(key, build, holder)
        try:
            yield entry
        finally:
            with self._lock:
                entry.leases -= 1
                entry.last_used = self._clock()
                if entry.release_requested and self._unused(entry):
                    self._remove(entry)

    def _acquire(self, key: str, build: Callable[[], Entry], holder: str | None) -> Entry:
        with self._lock:
            self._sweep()
            hit = self._entries.get(key)
            if hit is not None:
                self._touch(hit, holder)
                hit.leases += 1
                return hit
            build_lock = self._building.setdefault(key, threading.Lock())
        with build_lock:  # 같은 키를 동시에 만들지 않는다
            with self._lock:
                hit = self._entries.get(key)
                if hit is not None:
                    self._touch(hit, holder)
                    hit.leases += 1
                    return hit
            entry = build()  # 다운로드·임베딩이 오래 걸리므로 잠금 밖에서 한다
            with self._lock:
                existing = self._entries.get(key)
                if existing is not None:  # 잠금이 갈라진 사이에 다른 쪽이 먼저 만들었다
                    entry = existing
                else:
                    entry.created_at = self._clock()
                    self._entries[key] = entry
                self._touch(entry, holder)
                entry.leases += 1
                self._evict_over_capacity()
                self._building.pop(key, None)
            return entry

    def release(self, key: str, holder: str | None = None) -> dict:
        """해제 요청. 멱등. 쓰는 쪽이 남아 있으면 지우지 않고 deferred로 돌려준다 (released_chunks는 실제 제거 수)."""
        with self._lock:
            e = self._entries.get(key)
            if e is None:
                return {"released_chunks": 0, "content_sha256": None, "deferred": False}
            if holder:
                e.holders.pop(holder, None)
            e.release_requested = True
            if self._unused(e):
                return {"released_chunks": self._remove(e), "content_sha256": e.sha256, "deferred": False}
            return {"released_chunks": 0, "content_sha256": e.sha256, "deferred": True}


def reset_all() -> None:
    """테스트용: 공시 색인과 참고 문서 색인을 모두 비운다."""
    from mcp_servers.rag import filing, reference

    filing.INDEX.clear()
    reference.reset()

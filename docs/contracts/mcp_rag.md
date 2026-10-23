# rag-mcp 도구 계약

> 담당: **R3** · 상태: **DARTCH 변경안(아래 첫 절)은 R2·R4 합의 대기**이고, 그 아래 본문은 이전 프로젝트(Capstone) 계약을 그대로 옮긴 것이다. 충돌하면 첫 절이 우선한다(합의된 뒤).
> DARTCH Phase 1에서 바뀌는 부분은 [설계서](../dartch_phase1_mvp.md)와 [파일 구성 계획](../dartch_file_plan.md)을 따르고, 바뀔 때 이 문서를 먼저 고친다.

## DARTCH 변경안 (R3 제안, 2026-10-23) — 합의 후 이 절이 아래 본문보다 우선한다

### 바뀌는 것 한 줄 요약
공시 원문을 **DART에서 받아 프로세스 메모리에서만 색인**하고 디스크·DB에 남기지 않는다. 도구 이름과 입출력은 바꾸지 않아서 R2의 `agent/evidence.py`(`source/text/rcept_no/location` 검증)는 영향이 없다.

### 도구 (3개 유지, 입출력 변경 없음)
| 도구 | 변경 |
| --- | --- |
| `search_disclosure_text(rcept_no, query, k=5)` | 시그니처·반환 그대로. **내부만** 바뀐다: 색인에 없으면 DART에서 원문을 받아(`collector.dart_client`) 메모리 색인을 만들고 검색한다. DB `body_text`·`rag_docs/companies` 파일은 더 이상 읽지 않는다 |
| `search_reference(query, category, k=5)` | 그대로. 참고 문서(`rag_docs/reference/`)를 서버 시작 때 메모리에 올린다 (디스크 색인 `data/chroma` 폐지) |
| `release_disclosure_index(rcept_no)` | **단계적으로 정리** (아래 "release 처리") |

### 원문 취득·색인 규칙
1. 원문 출처는 **DART `document.xml` 하나뿐**이다. 받은 압축 파일에서 태그를 지워 평문으로 만든다(기존 `strip_markup` 방식 유지, 설계서가 BeautifulSoup을 쓰지 않기로 함).
2. 색인은 프로세스 메모리(`chromadb.EphemeralClient`, 안 쓰면 단순 코사인 검색으로 대체 가능). 서버를 다시 띄우면 비어 있다.
3. 조각 800자, 겹침 100자는 그대로(`location` 형식 `chunk-0 (0-800자)`도 그대로).
4. **같은 `rcept_no`를 동시에 요청하면** 공시 단위 락으로 한 번만 받고 한 번만 색인한다. (지금 `ensure_indexed`는 "없으면 만든다" 사이에 락이 없다. 동시 호출이 실제로 겹치는지는 확인하지 못했다. `TODO(확인 필요)`)
5. **수명**: `release_disclosure_index`가 불리면 즉시 지운다. 안 불리는 경우를 위해 TTL과 최대 보관 공시 수(LRU)를 둔다. 값은 부하를 재 본 뒤에 정한다 — `TODO(확인 필요)`.
6. **크기 상한**: 정기보고서 같은 아주 큰 문서를 통째로 임베딩하면 시간·비용이 커진다. 상한(글자 수)과 초과 시 동작(앞부분만? 거절?)은 `TODO(확인 필요)`. 🟡 지표는 API 값을 쓰므로 정기보고서 원문 색인은 기본 대상이 아니라고 본다.
7. 원문 해시(sha256)는 받을 때 계산해 보관하고 `release`가 돌려준다 (`content_sha256`).
8. 키(`DART_API_KEY`)와 원문은 로그에 남기지 않는다.

### 오류 (공통 형식 `{ok:false, error:{code,message}}`)
| 상황 | code |
| --- | --- |
| `rcept_no`가 숫자가 아님, `k`·`query`·`category` 오류 | `INVALID_INPUT` |
| DART에 원문이 없음 / 받은 내용이 비어 있음 | `NOT_FOUND` |
| DART 응답 시간 초과 | `TIMEOUT` |
| DART 호출 한도 초과·장애 | 지금 `ErrorCode`에 알맞은 값이 없다. 당분간 `TIMEOUT`으로 돌려주고, **`UPSTREAM_ERROR` 추가를 R4에게 요청**한다 (`shared/schemas.py`) |

### release 처리 (R4·R2 합의 필요)
삭제하면 호출하는 쪽이 깨진다. 지금 `agent/adapters/store.py`가 경보를 저장한 뒤 `release_disclosure_index`와 `purge_disclosure_text`를 순서대로 부르고, `agent/adapters/mcp_client.py`·`tests/wiring/test_assembly.py`도 이 이름을 안다.

| 단계 | 내용 |
| --- | --- |
| 1 (이번 변경) | `release_disclosure_index`는 **유지**한다. 하는 일은 메모리 색인에서 그 공시를 즉시 버리는 것이고, 반환은 `{rcept_no, released_chunks, content_sha256, mode}`로 같다(`mode`는 `"memory"`). 호출하는 쪽 변경 없음 |
| 2 (R4·R2) | 원문을 DB에 저장하지 않으면 `purge_disclosure_text`는 지울 본문이 없다. 단, 원문 해시와 처리 시각(`content_hash`, `processed_at`)을 **기록하는 곳**은 필요하다. 지금은 `purge`가 이를 쓴다. 해시를 어디에 기록할지 R4가 정한다 |
| 3 (R4·R2가 호출 제거 후) | `release_disclosure_index`를 삭제하고 TTL에만 맡길지, 계속 둘지 결정 |

참고: `mcp_business.md`는 `purge_disclosure_text`를 "복원하지 않는다"고 적었지만, 코드에는 복원되어 있다(`CLAUDE.md` 5장도 복원되었다고 적음). 두 문서가 어긋나 있으니 R4가 정리해야 한다.

### `RAG_INDEX_MODE`
`persistent`(미리 ingest)/`ephemeral` 구분이 사라진다. 다만 `rag_docs/companies/`와 `data/disclosures/`를 쓰는 기존 테스트·데모가 남아 있는 동안은 지울 수 없다.
- **이번 PR 묶음**: 새 임시 색인을 추가하고, `persistent` 경로는 옛 데이터가 남아 있는 동안만 유지한다.
- **마지막 별도 PR**(`docs/tasks/r3.md` 8번): 옛 데이터와 함께 `persistent`·`Chroma 영속 저장`·`RAG_INDEX_MODE`·`data/chroma/`를 제거한다. `.env.example`·`shared/config.py`는 R4가 같이 고친다.

### `ingest` 명령
참고 문서만 적재한다. 디스크에 저장하지 않으므로 `--reset`·`--only companies`는 없어진다. `--dry-run`(조각 수 세기)은 유지한다. 서버 시작 때 자동으로 적재하므로 팀원이 따로 `ingest`를 돌릴 필요가 없어진다.

### 참고 문서 `category`
`상장규정 · 공시해설 · 회계용어 · 안내문가이드` 4개는 그대로 둔다(바꾸면 R2 프롬프트·가드레일과 `RefCategory`가 같이 바뀐다). 새 위험 항목 해설은 `공시해설`·`회계용어`에 넣는다.

### 테스트 (`tests/rag/`)
- 가짜 DART 응답으로 임시 색인 생성·검색·해제, 같은 `rcept_no` 동시 요청, TTL 만료
- DB 파일이나 `rag_docs/companies`가 없어도 동작
- 디스크에 원문이 남지 않음(임시 폴더에 파일이 생기지 않는지 확인)
- 임베딩은 `fake`(키 불필요). 의미 검색 품질 실험(`eval/experiments/rag_k.py`)은 실제 임베딩으로 따로 한다

---

## 이전 프로젝트 계약 (위 변경안이 합의되기 전까지의 기준)

## 4-3. rag-mcp 도구 (포트 8002)

| 도구 | 입력 | 출력 |
| --- | --- | --- |
| search_disclosure_text | rcept_no: str, query: str, k: int = 5 | 해당 공시 원문 조각 + 위치 |
| search_reference | query: str, category: str, k: int = 5 | 참고 문서 조각 + 출처 |
| release_disclosure_index | rcept_no: str | released_chunks, content_sha256, mode | 변경 (Agent 코드만, 처리가 끝난 공시의 색인 조각 삭제. 운영 모드에서만 동작) |

- `category`: 상장규정, 공시해설, 회계용어, 안내문가이드
- 결과마다 출처(문서명, rcept_no, 조각 위치)를 꼭 돌려준다. 근거 인용에 쓴다.
- 조각 크기 800자, 겹침 100자.

---

## 서버 사용 설명서 (docs/contracts/mcp_rag.md에서 옮김)

# rag-mcp (포트 8002)

```bash
uv run python -m mcp_servers.rag.ingest            # rag_docs/ → data/chroma (바뀐 문서만 임베딩)
uv run python -m mcp_servers.rag.ingest --reset    # 처음부터 (임베딩 설정을 바꿨을 때 필수)
uv run python -m mcp_servers.rag.ingest --dry-run  # 조각 수만 센다
uv run python -m mcp_servers.rag.server            # 서버
uv run pytest tests/rag -q                         # 테스트 (fake 임베딩, 키 불필요)
```

- 조각 800자, 겹침 100자. 컬렉션 `disclosures`(회사 공시 원문), `reference`(참고 문서 4종). 전체 143건 → 1,350조각, 임베딩 약 17초.
- 임베딩은 `.env`의 `EMBEDDING_PROVIDER` (`openai` | `local` | `fake`). 색인을 만든 설정과 다르면 검색을 거부한다(`--reset`으로 다시 만든다).
  `local`은 `sentence-transformers`(BAAI/bge-m3)를 따로 설치해야 하고, `fake`는 테스트용 해시 임베딩이다.
- `data/chroma/`는 생성물이라 git에 올리지 않는다. 팀원마다 `ingest`를 한 번 돌린다.

## 도구와 반환 형태

A의 `agent/evidence.py`가 요구하는 `source`, `text`, `rcept_no`, `location`을 모두 채운다 (모두 비어 있지 않은 문자열).

```json
{"ok": true, "data": {"rcept_no": "20250321802001", "count": 3, "chunks": [
  {"source": "금양 감사보고서제출 (2025-03-21)", "text": "<조각 전문>", "rcept_no": "20250321802001",
   "location": "chunk-0 (0-800자)", "score": 0.38}]}}
```

- `search_disclosure_text(rcept_no, query, k=5)`: 그 공시의 조각만 찾는다(다른 공시와 섞이지 않음). 원문이 색인에 없으면 `NOT_FOUND`.
  공시 1건의 조각이 k개보다 적으면 있는 만큼만 돌려준다.
- `search_reference(query, category, k=5)`: `category`는 상장규정 · 공시해설 · 회계용어 · 안내문가이드. 조각에 `rcept_no`는 없고 `category`가 있다.
- 인용은 `text`에서 연속된 문장으로 해야 한다(A가 `quote in text`로 검증). `location`은 문서 안 글자 위치라 같은 조각을 다시 가리킬 수 있다.

# rag-mcp 도구 계약

> 담당: **R3** · 상태: **운영 모드(`RAG_INDEX_MODE=ephemeral`)의 메모리 색인은 구현됨**(아래 "구현됨" 절). 데모·평가용 `persistent`(Chroma)는 마지막 정리 PR까지 옛 방식 그대로다. 그 아래 "DARTCH 변경안"은 합의 과정의 기록이고, 맨 끝 본문은 이전 프로젝트(Capstone) 계약을 옮긴 것이다. 충돌하면 "구현됨" 절이 우선한다.
> DARTCH Phase 1에서 바뀌는 부분은 [설계서](../dartch_phase1_mvp.md)와 [파일 구성 계획](../dartch_file_plan.md)을 따르고, 바뀔 때 이 문서를 먼저 고친다.

## 구현됨 (2026-10-23) — 운영 모드(`RAG_INDEX_MODE=ephemeral`)

| 파일 | 역할 |
| --- | --- |
| `mcp_servers/rag/memory.py` | 공시 색인 레지스트리(메모리). 키별 잠금, 사용 중 보호(lease·holder), TTL·LRU, 멱등 release |
| `mcp_servers/rag/filing.py` | DART에서만 원문을 받아(`collector.dart_client`) 조각·벡터를 만들고 검색·해제 |
| `mcp_servers/rag/reference.py` | `rag_docs/reference/`를 메모리에 올림 (서버 시작 때 warm, 임베딩 설정이 바뀌면 다시 만듦) |
| `mcp_servers/rag/server.py` | 도구를 `asyncio.to_thread`로 감싸 등록 |
| `tools.py` | `ephemeral`이면 위 경로, 아니면 옛 Chroma 경로(`persistent`) |

### 동작 (도구 3개, 기존 호출은 그대로 동작한다)
- **`search_disclosure_text(rcept_no, query, k=5, holder=None)`**: 색인이 없으면 DART에서 받아 만든다(`DB body_text`·`rag_docs/companies`는 읽지 않는다. 출처 이름 `금양 사업보고서 (2024.12) (2025-03-21)`을 만들려고 제목·접수일·회사명만 DB에서 읽고, 없으면 `DART <접수번호>`). 응답 `data`에 **`content_sha256`(원문 해시)과 `truncated`(조각 상한 때문에 뒷부분을 색인하지 못함)가 추가**됐다(기존 필드는 그대로).
- **`release_disclosure_index(rcept_no, holder=None)`**: 응답 `{rcept_no, released_chunks, content_sha256, mode: "memory", deferred}`. 멱등. 쓰는 쪽이 남아 있으면 지우지 않고 `deferred: true`, `released_chunks: 0`(실제 제거 수). 이미 없으면 `released_chunks: 0, content_sha256: null`.
- **`search_reference`**: 그대로. 처음 쓸 때(또는 서버 시작 때) 메모리에 올린다.
- `holder`(선택, ≤128자): 이 공시를 쓰는 **실행의 식별자**(예: 분석 실행 ID). 주면 그 실행이 `release`하기 전까지 **다른 실행의 release가 색인을 지우지 못한다**(R2의 "사용권" 요구, harness.md 5장). 안 주면 옛 방식처럼 동작하되, 이름 있는 holder가 쓰는 공시는 holder 없는 release도 지우지 못한다.

### 사용 중 보호와 수명 (잠정값, 부하를 재 보기 전 `TODO(확인 필요)`)
| 항목 | 값 | 규칙 |
| --- | --- | --- |
| 진행 중 검색(lease) | — | 검색하는 동안은 TTL·LRU·release 모두 지우지 못한다. 끝나면 보류된 해제가 이뤄진다 |
| holder 사용권 | `HOLDER_TTL` 15분 | 마지막으로 쓴 뒤 15분 동안 갱신이 없으면 죽은 실행으로 보고 무시한다 |
| 유휴 TTL | `INDEX_TTL` 30분 | 사용 중이 아닌 색인을 마지막 사용 30분 뒤 지운다 |
| 최대 개수 | `MAX_ENTRIES` 20 | 넘으면 가장 오래 안 쓴 **사용 중이 아닌** 색인부터 지운다. 전부 사용 중이면 넘어도 지우지 않는다 |
| 조각 상한 | `MAX_CHUNKS` 300(≈24만 자) | 넘으면 앞부분만 색인하고 `truncated: true` |
- 같은 `rcept_no` 동시 요청은 **한 번만 받고 한 번만 색인**한다(키별 잠금, 8개 스레드 시험).
- 서버가 다시 뜨면 색인은 비어 있고, 다음 검색이 DART에서 다시 받는다. 같은 원문이면 **같은 조각·같은 `location`** 이 나온다(시험함). 해시가 다르면 원문이 바뀐 것이다.
- 디스크·Chroma·DB에 원문을 쓰지 않는다(시험: Chroma 폴더가 생기지 않고 `store.get_client`를 열면 테스트가 실패).

### 오류 (구현)
`INVALID_INPUT`(rcept_no·query·k·holder 오류, **네트워크를 쓰기 전에** 검사) / `NOT_FOUND`(DART에 없음·원문이 비어 있음·키가 비어 있음. 실제 DART는 없는 접수번호에 `013 접수번호 오류`를 준다) / `TIMEOUT`(한도 초과 `020`·5xx·시간 초과, 재시도 소진 뒤). `UPSTREAM_ERROR`는 R4가 추가하면 `TIMEOUT` 대신 쓴다.

### 서버가 막히지 않게
FastMCP는 **동기 도구를 이벤트 루프 스레드에서 그대로 실행한다**(fastmcp 2.14 확인). 다운로드·임베딩이 서버 전체를 막으므로 도구 함수는 동기로 두고(다른 코드·테스트가 직접 부른다) 서버에 등록할 때만 스레드로 넘긴다. 0.3초 다운로드 동안 이벤트 루프가 계속 도는 것을 시험했다.

### 다른 담당에게
| 담당 | 내용 |
| --- | --- |
| **R4** | ① `agent/adapters/phase1.py`가 과거 인용을 재검증하려고 `search_disclosure_text`를 다시 부른다. 색인이 해제된 뒤면 **DART에서 원문을 다시 받고 다시 임베딩한다**(성공하면 같은 `location`). DART가 막히면 재검증이 `TIMEOUT`이 된다. ② `store.py`의 `release_disclosure_index` 호출은 holder 없이도 동작한다. R2가 `holder`를 넘기면 사용 중인 실행을 지켜 준다. ③ `scripts/ops/run_stack.py`·`doctor.py`는 `data/chroma` 색인을 확인한다. **운영 모드에서는 이 색인이 필요 없다**(없어도 정상). ④ `docs/run.md`의 `ingest`·`RAG_INDEX_MODE` 설명 갱신. |
| **R2** | 분석 실행 ID를 `holder`로 `search_disclosure_text`·`release_disclosure_index`에 넘기면 같은 공시를 동시에 분석하는 다른 실행이 색인을 지우지 못한다. 도구 응답의 `content_sha256`로 원문이 바뀌었는지 비교할 수 있다. |

### R2 반영안 (2026-10-23) — 수명·해시·마지막 검색 시점
기존 `search_disclosure_text(rcept_no, query, k=5)`, `search_reference`, `release_disclosure_index` 계약은 유지한다. 구조화 사실 조회 `get_disclosure_facts`는 business MCP(R4) 책임이다. `normalization_version`은 `collector/dart_client.py`의 `NORMALIZATION_VERSION`(현재 `"1"`)이고 `search_disclosure_text` 응답 `data`에 추가 필드로 나온다(**구현됨**). 나머지 수명 규칙은 위 "구현됨"과 같다.

**정규화·해시**
- 정규화·해시의 정본은 `collector.dart_client.normalize_document`(+`content_sha256`) 한 곳이다. ZIP 파일 순서, 태그 제거, 공백 정리, 문자 디코딩을 거기서만 하고 **`normalization_version`을 기록**한다. 정규화 방식을 바꾸면 버전을 올리고 과거 인용과 섞지 않는다.
- 문서 검색 응답은 기존 `source/text/rcept_no/location`과 `content_sha256`을 주고, 정규화 버전은 **호환 가능한 추가 메타데이터**로 준다. `location`은 같은 정규화 텍스트를 기준으로 한다.
- `k` 상한(도구, 최대 10)과 모델 입력 상한은 다르다. 후보는 도구가 허용하는 범위에서 넉넉히 검색하고, **R2가 모델 입력 전체를 최대 5개 조각으로 선정**한다.

**R2의 마지막 원문 검색 시점**
- 공개 판정의 사실 추출, 인용 검증, 필요한 과거 근거 검증까지가 마지막 원문 검색 구간이다. 설명 재작성과 PB 승인 재개는 **저장된 근거**를 쓴다. 안내문 작성의 `search_reference`(가이드 검색)는 공시 원문 검색과 별개다.
- 보존된 근거로 부족하면 명시적 재분석을 요청한다.

**색인 해제와 수명**
- 판정·설명·근거를 저장한 뒤 R4가 `release_disclosure_index`를 호출한다. 원문 해시·`processed_at` 기록은 본문 삭제와 분리한다.
- 성공·실패·취소 어느 경우에도 release와 TTL/LRU 정리를 적용한다. `MANUAL_CHECK`도 무기한 보존하지 않는다.
- 같은 공시를 쓰는 다른 작업이 있으면 사용권(`holder`, 진행 중 검색 lease)으로 보호하고, TTL/LRU도 활성 사용권을 무시하지 않는다. 죽은 작업의 사용권은 유한한 시간(`HOLDER_TTL`) 뒤 회수한다. **(구현됨)**
- release는 멱등이다. 활성 사용자가 있으면 실제 제거를 늦추고(`deferred: true`) `released_chunks`는 **실제 제거 수**를 돌려준다. **R4는 호출자가 제거 수에 의존하지 않는지 확인한다.**
- 서버 재시작 뒤 메모리 색인은 다시 만든다. 이전에 기록한 `content_sha256`과 새로 받은 해시가 다르면 **과거 실행에 새 문서를 조용히 섞지 않고 재분석이 필요하다고 알린다.** 지금 도구는 해시만 돌려주므로 비교와 판단은 호출자(R2·R4)가 한다.
- 조각 상한(`MAX_CHUNKS` 300)을 넘으면 앞부분만 색인하고 `truncated: true`를 돌려준다. **호출자는 이를 전체를 분석한 것처럼 처리하지 않고** 명시적 제한 오류·수동 확인(`MANUAL_CHECK`) 또는 구간 선택 정책으로 다룬다(R2 확인 필요).
- 수명 상한 수치(TTL·최대 개수·조각 수)는 R3의 메모리 용량 측정으로 정한다. 값이 정해지지 않았다는 이유로 무기한 캐시하지 않는다.

**전환 순서의 보충:** 아래 "DARTCH 변경안" 5항의 순서에서 `release_disclosure_index`는 Phase 1에서 명시적 해제 수단으로 유지하고, 삭제는 별도 구조 PR에서만 검토한다(`CLAUDE.md` 4절 9번). 동시 사용·취소·실패·재시작·해시 변경은 6항에서 검증한다.

---

## DARTCH 변경안 (R3 제안, 2026-10-23, 합의 과정의 기록) — 구현과 다른 부분은 위 "구현됨"이 우선한다

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
2. 색인은 프로세스 메모리. **구현: Chroma를 쓰지 않고 직접 만든 코사인 검색**(공시 1건은 많아야 수백 조각이라 충분). 서버를 다시 띄우면 비어 있다.
3. 조각 800자, 겹침 100자는 그대로(`location` 형식 `chunk-0 (0-800자)`도 그대로).
4. **같은 `rcept_no`를 동시에 요청하면** 공시 단위 락으로 한 번만 받고 한 번만 색인한다 (구현됨, 시험함).
5. **수명**: `release_disclosure_index`가 불리면 지운다(쓰는 쪽이 있으면 늦춘다). 안 불리는 경우를 위해 TTL과 최대 보관 공시 수(LRU)를 둔다 (구현됨, 값은 잠정).
6. **크기 상한**: 정기보고서 같은 아주 큰 문서를 통째로 임베딩하면 시간·비용이 커진다. **구현: 300조각(≈24만 자)까지만 색인하고 `truncated: true`로 알린다.** 🟡 지표는 API 값을 쓰므로 정기보고서 원문 색인은 기본 대상이 아니라고 본다.
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

### R2·R4 검토 반영 (PR #31 코멘트, 2026-10-23)

R4·R2가 "방향에는 동의하지만 3단계 상세까지 승인한 것은 아니다"라고 했다. 물어본 것에 답한다.

**1. 원문 해시: 무엇을 해시하나 (R4: 세 담당이 같은 방식으로 맞춰야 함)**
- 해시 대상 = DART `document.xml` 압축 안의 **모든 파일을 `namelist()` 순서대로 `"\n"`으로 이어 붙이고**, `<style>`·`<script>` 블록과 태그를 지우고, 연속 공백을 한 칸으로 줄이고, 앞뒤 공백을 지운 평문. UTF-8로 인코딩한 `sha256` (인코딩이 UTF-8이 아니면 `cp949`로 읽음). 지금 `dart_source.fetch_text` + `filing.ensure_indexed`가 이미 이렇게 한다.
- **이 계산은 `collector.dart_client.normalize_document` 한 곳에만 둔다.** R2·R4는 해시를 직접 계산하지 않고 rag가 돌려주는 값을 쓴다.
- 해시는 원문 변경 확인용이다(R2도 같은 입장). 인용 근거 자체를 대신하지 않는다.

**2. 해시를 어느 단계에서 기록하나**
- rag가 원문을 처음 받아 색인할 때 계산하고, `release_disclosure_index`가 `content_sha256`을 돌려준다(지금과 같음). **기록은 R4**가 `disclosures.content_hash`에 한다. 기록 시점은 R4가 정한다 `TODO(확인 필요)`.
- 같은 공시를 다시 받았을 때 해시가 기록된 값과 다르면 "원문이 바뀌었다"는 뜻이다. 이 경우 조용히 새 내용을 쓰지 않고 호출한 쪽이 알 수 있게 해야 한다. 이를 위해 `search_disclosure_text` 응답 `data`에 **`content_sha256`을 선택 항목으로 추가**하는 것을 제안한다(기존 필드는 그대로, 추가만). R2가 `evidence.py`에서 쓸지는 R2 결정 `TODO(확인 필요)`.
- **`processed_at`은 "공개 공시 분석 완료 시각"으로 정의**한다(R4 제안, R2 동의, R3 동의). 색인 해제 시각, PB 승인·연락 업무 완료 시각과 구분한다.

**3. 분석이 실패하거나 재시작되면 색인은 어떻게 되나 (R4·R2)**
- 색인은 프로세스 메모리에만 있다. **서버가 다시 뜨면 비어 있고**, 이후 `search_disclosure_text`가 불리면 DART에서 다시 받아 만든다(지연 재생성). 재개 때 원문을 다시 받는 경로가 필요하다는 R2의 요구와 같다.
- 다시 받는 데 실패하면 `NOT_FOUND`/`TIMEOUT`을 돌려준다. **호출한 쪽은 추측하지 않고 "확인 필요"로 처리**해야 한다(R2 요구).
- 실패한 분석의 색인이 무기한 남지 않게 TTL·최대 개수(LRU)를 둔다(위 "수명"). 값은 부하를 재 본 뒤에 정한다.
- `release_disclosure_index`는 **멱등**이다. 이미 없으면 `released_chunks: 0`으로 정상 응답한다.
- 영속 저장으로 돌아가지 않는다: 실패 시에도 디스크에 쓰지 않는다.

**4. R2: 마지막 검색 시점·재작성·재개에 필요한 근거**
- 저장 직후 `release`를 부르려면, 이후 단계(설명문 재작성, 승인 후 재개)가 **보존된 인용문과 출처만으로** 진행 가능해야 한다. 재작성에서 검색을 다시 쓰면 `release`를 늦추거나 재생성을 거쳐야 한다. 어느 쪽인지는 R2가 확인해 알려 주세요 `TODO(확인 필요)`.
- rag 도구는 응답에 원문 조각 텍스트(조각당 최대 800자, 한 번에 최대 `k`=10개)를 담는다. **이 도구 응답이 State·체크포인트·로그·trace에 통째로 남지 않는지**는 R2·R4가 점검해야 한다. 남겨도 되는 것은 인용문(`quote`)·출처·위치뿐이라는 R2의 구분에 동의한다.

**5. 단계 (R4가 제안한 순서로 정리)**

| 순서 | 내용 | 담당 |
| --- | --- | --- |
| 1 | 메모리 임시 색인 구현, 원문을 DB·파일에 쓰지 않음 | R3 |
| 2 | 해시·분석 완료 기록을 본문 삭제 도구와 분리 (`processed_at` 정의 4항) | R4 |
| 3 | 원문·체크포인트·trace에 전체 원문이 남는지 점검 | R2 (R4 협조) |
| 4 | `agent/adapters/store.py`의 `purge_disclosure_text` 호출 제거, 호출자가 없음을 확인 | R4 |
| 5 | `purge_disclosure_text` 도구 등록·코드·계약 정리. 이어서 `release_disclosure_index` 유지/삭제 결정 | R4·R3 |
| 6 | 재시작·실패 경로 검증 | 공동 |

**6. 오류 코드:** R4가 `ErrorCode.UPSTREAM_ERROR` 추가를 반영하기로 했다. 추가되면 DART 한도 초과·장애에 그것을 쓰고, 그 전까지는 `TIMEOUT`으로 돌려준다. `020`은 "재시도 가능"으로 명시한다(`collector.md` 6장).

### `RAG_INDEX_MODE`
`persistent`(미리 ingest)/`ephemeral` 구분이 사라진다. 다만 `rag_docs/companies/`와 `data/disclosures/`를 쓰는 기존 테스트·데모가 남아 있는 동안은 지울 수 없다.
- **이번 PR 묶음**: 새 임시 색인을 추가하고, `persistent` 경로는 옛 데이터가 남아 있는 동안만 유지한다.
- **마지막 별도 PR**(`docs/tasks/r3.md` 8번): 옛 데이터와 함께 `persistent`·`Chroma 영속 저장`·`RAG_INDEX_MODE`·`data/chroma/`를 제거한다. `.env.example`·`shared/config.py`는 R4가 같이 고친다.

### `ingest` 명령
**운영 모드에서는 필요 없다**(서버 시작 때 참고 문서를 메모리에 올린다). `ingest`(Chroma 적재)는 `persistent`(데모·평가)용으로 남아 있고 마지막 정리 PR에서 없어진다.

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

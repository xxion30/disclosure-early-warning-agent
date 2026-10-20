# rag-mcp (포트 8002)

```bash
uv run python -m mcp_servers.rag.ingest            # rag_docs/ → data/chroma (바뀐 문서만 임베딩)
uv run python -m mcp_servers.rag.ingest --reset    # 처음부터 (임베딩 설정을 바꿨을 때 필수)
uv run python -m mcp_servers.rag.ingest --dry-run  # 조각 수만 센다
uv run python -m mcp_servers.rag.server            # 서버
uv run pytest tests/mcp_servers/test_rag.py -q     # 테스트 (fake 임베딩, 키 불필요)
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

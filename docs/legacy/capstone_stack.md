# 이전 프로젝트(Capstone) 기술 스택·환경 변수

> 참고용 기록이다. 현재 기준은 `docs/run.md`와 `.env.example`.

## 1. 기술 스택 (확정)

| 영역 | 선택 |
| --- | --- |
| 언어 | Python 3.11 (패키지 관리: uv, `uv.lock`으로 버전 고정) |
| Agent | LangGraph + langgraph-checkpoint-sqlite |
| LLM 연결 | langchain-anthropic(기본), langchain-openai(대체 모델) |
| MCP 서버 | FastMCP, Streamable HTTP 방식 (서버마다 별도 프로세스) |
| MCP 클라이언트 | langchain-mcp-adapters (MultiServerMCPClient) |
| API 서버 | FastAPI + Uvicorn, 진행 이벤트는 SSE |
| 인증 | JWT (PyJWT) + bcrypt 비밀번호 해시 |
| DB | SQLite (`data/app.db`), SQLAlchemy 2 |
| 벡터 DB | Chroma (로컬 파일, `data/chroma/`) |
| 화면 | Vue 3 + Vite + Vue Router + Pinia + axios |
| 관측 | LangSmith |
| 큐 (가산점) | RabbitMQ (docker compose) + aio-pika |

## 모델 (`.env`로 바꿀 수 있게)

| 용도 | 모델 | 이유 |
| --- | --- | --- |
| 기본 LLM | `claude-haiku-5-5` | 입력 $0.10 / 출력 $0.50 (100만 토큰당). 한국어 공시 읽기와 도구 호출에 충분하고 가장 저렴한 편 |
| 대체 LLM (Recovery) | `gpt-5.4-mini` | 다른 회사 모델이라 한쪽 서버 장애(503) 때 넘어갈 수 있음 |
| 임베딩 | `text-embedding-3-small` (OpenAI) | 아래 참고 |

임베딩을 `text-embedding-3-small`로 정한 이유:
- RAG 문서가 회사 5개 공시 + 참고 문서 4개 정도라 양이 적다. 전부 임베딩해도 비용이 몇십 원 수준이다.
- 대체 LLM 때문에 OpenAI 키가 어차피 필요하다. 키 하나로 해결된다.
- 로컬 모델(bge-m3 등)은 한국어 성능은 좋지만 2GB 넘게 내려받아야 하고 노트북마다 속도가 달라서, 이틀 안에 4명 환경을 맞추기 어렵다.
- OpenAI 키를 쓸 수 없게 되면 `EMBEDDING_PROVIDER=local`로 `BAAI/bge-m3`를 쓰도록 코드에 분기만 만들어 둔다.

---

## 3. 환경 변수 (`.env`, git에 올리지 않음)

```
ANTHROPIC_API_KEY=
OPENAI_API_KEY=
LLM_PRIMARY=claude-haiku-5-5
LLM_FALLBACK=gpt-5.4-mini
EMBEDDING_PROVIDER=openai          # openai | local
EMBEDDING_MODEL=text-embedding-3-small
LANGSMITH_TRACING=true
LANGSMITH_API_KEY=
LANGSMITH_PROJECT=disclosure-early-warning
DART_API_KEY=                       # scripts/data/fetch_dart.py용
JWT_SECRET=change-me
DB_URL=sqlite:///data/app.db
CHECKPOINT_DB=data/checkpoints.db
CHROMA_DIR=data/chroma
BUSINESS_MCP_URL=http://localhost:8001/mcp
RAG_MCP_URL=http://localhost:8002/mcp
RABBITMQ_URL=amqp://guest:guest@localhost:5672/
QUEUE_ENABLED=false                 # 가산점 기능 켜기
RAG_INDEX_MODE=persistent           # persistent(데모) | ephemeral(운영: 공시마다 임시 색인, 처리 후 삭제)
```

---

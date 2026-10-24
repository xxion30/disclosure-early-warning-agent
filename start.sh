#!/bin/sh
# Run from any directory. All destructive operations are confined to this project.
set -eu
cd "$(dirname "$0")"
if [ ! -f .env ]; then
  echo '.env.example을 .env로 복사하고 API 키를 설정하세요.' >&2
  exit 1
fi
docker compose version >/dev/null
demo() { docker compose --env-file .env -p dartch-local-demo -f docker-compose.demo.yml "$@"; }
echo '새 시연을 시작합니다. 이전 시연 데이터가 초기화됩니다.'
if ! docker image inspect python:3.11-slim >/dev/null 2>&1; then
  docker pull python:3.11-slim
fi
if ! docker create --name dartch-local-demo-start-lock python:3.11-slim true >/dev/null 2>&1; then
  echo '다른 시작 작업이 실행 중이거나 이전 잠금이 남아 있습니다. docs/run.md를 확인하세요.' >&2
  exit 1
fi
trap 'docker rm dartch-local-demo-start-lock >/dev/null 2>&1 || true' 0
trap 'exit 130' INT
trap 'exit 143' TERM
demo build
demo stop web api business rag
demo up -d --wait postgres
demo run --rm --no-deps prepare
demo up -d --wait
echo '준비 완료. 기본 화면: http://127.0.0.1:8080 (.env의 WEB_PORT 설정 시 해당 포트)'
echo '종료: docker compose --env-file .env -p dartch-local-demo -f docker-compose.demo.yml stop'

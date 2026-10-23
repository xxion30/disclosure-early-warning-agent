"""Invocation-local RAG leases and completeness checks; never checkpoint chunks."""
import asyncio
from contextvars import ContextVar
from dataclasses import dataclass, field
from hashlib import sha256
import json
import logging
import re
from uuid import uuid4

from agent.ports import AgentError
from shared.schemas import ToolResponse

log = logging.getLogger(__name__)
current_rag_scope = ContextVar('agent_rag_scope', default=None)


class RAGContractError(AgentError):
    """Repetition of the same search cannot repair incomplete or changed data."""


def require_complete(data):
    if not isinstance(data, dict):
        raise RAGContractError('공시 검색 응답 형식 확인 필요')
    if 'truncated' in data and type(data['truncated']) is not bool:
        raise RAGContractError('공시 색인 완전성 표시 형식 확인 필요')
    if data.get('truncated') is True:
        raise RAGContractError('공시 일부만 색인됨: 전체 분석 불가, 수동 확인 필요')


@dataclass
class RAGScope:
    tools: object
    holder: str
    expected: dict = field(default_factory=dict)
    receipts: set = field(default_factory=set)

    @classmethod
    def create(cls, tools, context, sources=()):
        # A separate holder per active invocation prevents a late worker's
        # cleanup from releasing its replacement's lease on the same job.
        key = [context.get('kind'), context.get('tenant_id'), context['run_id'],
               context.get('attempt', 1), str(uuid4())]
        holder = 'r2:' + sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()
        scope = cls(tools, holder)
        for source in sources or ():
            receipt, digest = source.get('rcept_no'), source.get('content_sha256')
            if receipt and digest:
                version = (digest, source.get('normalization_version'))
                if receipt in scope.expected and scope.expected[receipt] != version:
                    raise RAGContractError('체크포인트의 공시 출처 버전 충돌')
                scope.expected[receipt] = version
        return scope

    def arguments(self, name, arguments):
        args = dict(arguments)
        if name not in {'search_disclosure_text', 'release_disclosure_index'}:
            return args
        if args.get('holder') not in (None, self.holder):
            raise RAGContractError('다른 실행의 검색 사용권을 사용할 수 없습니다')
        args['holder'] = self.holder
        if name == 'search_disclosure_text':
            # Track even a request whose reply is lost. Cleanup is idempotent.
            self.receipts.add(args['rcept_no'])
        return args

    def validate(self, receipt, data):
        require_complete(data)
        digest, version = data.get('content_sha256'), data.get('normalization_version')
        if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise RAGContractError('공시 원문 해시 누락·형식 오류')
        if not isinstance(version, str) or not version.strip():
            raise RAGContractError('공시 정규화 버전 누락: RAG 응답 연결 필요')
        actual = (digest, version)
        if receipt in self.expected and self.expected[receipt] != actual:
            raise RAGContractError('공시 원문·정규화 버전 변경: 명시적 재분석 필요')
        self.expected[receipt] = actual

    async def release(self):
        """Best effort, bounded cleanup, including failure/cancellation paths.

        No model or normal execution budget is used. R3's finite TTL remains
        the final backstop when a server cannot acknowledge cleanup.
        """
        async def one(receipt):
            try:
                result = ToolResponse.model_validate(await self.tools.call(
                    'release_disclosure_index', {'rcept_no': receipt, 'holder': self.holder}))
                return result.ok
            except Exception:
                return False

        if not self.receipts:
            return
        try:
            outcomes = await asyncio.wait_for(
                asyncio.gather(*(one(r) for r in sorted(self.receipts))), timeout=5)
            if not all(outcomes):
                log.warning('RAG 사용권 해제 일부 실패; TTL 정리 필요')
        except asyncio.TimeoutError:
            log.warning('RAG 사용권 해제 시간 초과; TTL 정리 필요')


async def finish_scope(scope):
    """Keep cleanup alive after cancellation without losing caller cancellation."""
    task = asyncio.create_task(scope.release())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(task)
        finally:
            raise

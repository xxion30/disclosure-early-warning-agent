"""RabbitMQ 연결 (가산점, CLAUDE.md 10장 9). `QUEUE_ENABLED=true`일 때만 쓴다.

큐 세 개 (모두 durable, 메시지는 persistent):
- disclosures        처리할 공시 {"rcept_no", "attempt"}
- disclosures.retry  다시 시도할 공시. 메시지마다 expiration(지연)이 끝나면 dead-letter로 disclosures로 돌아간다
- disclosures.dlq    끝내 실패한 공시 (사람이 확인)

docker compose up -d rabbitmq  → 관리 화면 http://localhost:15672 (guest/guest)
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Protocol

MAIN_QUEUE = "disclosures"
RETRY_QUEUE = "disclosures.retry"
DLQ = "disclosures.dlq"

Handler = Callable[[dict], Awaitable[None]]


class QueueBackend(Protocol):
    async def publish(self, queue: str, body: dict, delay_seconds: float | None = None) -> None: ...

    async def consume(self, handler: Handler) -> None:
        """MAIN_QUEUE에서 하나씩 꺼내 handler를 부른다. 정상 종료면 ack, 예외면 다시 넣는다(requeue)."""

    async def stats(self) -> dict[str, int]: ...

    async def close(self) -> None: ...


class AioPikaBackend:
    def __init__(self, connection, channel, queues, names):
        self._connection = connection
        self._channel = channel
        self._queues = queues  # 논리 이름(MAIN_QUEUE 등) → 선언된 큐
        self._names = names    # 논리 이름 → 실제 큐 이름

    @classmethod
    async def connect(cls, url: str, prefix: str = "") -> "AioPikaBackend":
        """prefix: 테스트처럼 실행 중인 API의 소비자와 큐를 나눠 쓸 때만 준다."""
        import aio_pika

        names = {q: prefix + q for q in (MAIN_QUEUE, RETRY_QUEUE, DLQ)}
        connection = await aio_pika.connect_robust(url, timeout=5)
        channel = await connection.channel()
        # 한 번에 1건만: AgentRuntime은 하나이고, 실적 시즌에 몰려도 순서대로 처리한다
        await channel.set_qos(prefetch_count=1)
        queues = {
            MAIN_QUEUE: await channel.declare_queue(names[MAIN_QUEUE], durable=True),
            RETRY_QUEUE: await channel.declare_queue(names[RETRY_QUEUE], durable=True, arguments={
                "x-dead-letter-exchange": "",
                "x-dead-letter-routing-key": names[MAIN_QUEUE],
            }),
            DLQ: await channel.declare_queue(names[DLQ], durable=True),
        }
        return cls(connection, channel, queues, names)

    async def publish(self, queue: str, body: dict, delay_seconds: float | None = None) -> None:
        import aio_pika

        message = aio_pika.Message(
            json.dumps(body, ensure_ascii=False).encode("utf-8"),
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            expiration=timedelta(seconds=delay_seconds) if delay_seconds else None,
        )
        await self._channel.default_exchange.publish(message, routing_key=self._names[queue])

    async def consume(self, handler: Handler) -> None:
        async with self._queues[MAIN_QUEUE].iterator() as messages:
            async for message in messages:
                async with message.process(requeue=True):
                    await handler(json.loads(message.body))

    async def stats(self) -> dict[str, int]:
        out = {}
        for logical, actual in self._names.items():
            declared = await self._channel.declare_queue(actual, passive=True)
            out[logical] = declared.declaration_result.message_count
        return out

    async def purge(self) -> None:
        for queue in self._queues.values():
            await queue.purge()

    async def close(self) -> None:
        await self._connection.close()

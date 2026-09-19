"""Prepare accepted QA content without depending on a database SDK."""

import asyncio
from collections.abc import Awaitable, Callable

from src.domain.reply_sessions import AcceptedQna, AcceptedReply, QaMemoryRepository


class HistoricalQnaPublisher:
    def __init__(
        self, repository: QaMemoryRepository,
        embed: Callable[[str], Awaitable[list[float]]],
        embedding_dimensions: int,
    ) -> None:
        self.repository = repository
        self.embed = embed
        self.embedding_dimensions = embedding_dimensions

    async def publish(self, reply: AcceptedReply) -> None:
        vector = await self.embed(reply.student_query)
        if len(vector) != self.embedding_dimensions:
            raise ValueError(
                "Embedding dimensions do not match the configured policy: "
                f"expected {self.embedding_dimensions}, got {len(vector)}"
            )
        await asyncio.to_thread(self.repository.save, AcceptedQna(reply, tuple(vector)))

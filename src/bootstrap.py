"""Composition of business services with production infrastructure adapters."""

from src.config import load_cosmos_qna_settings
from src.domain.reply_sessions import AcceptedReply
from src.repositories.historical_qna import CosmosQaMemoryRepository
from src.repositories.reply_sessions import CosmosReplySessionRepository
from src.services.reply_sessions import ReplySessionService
from src.services.embeddings import generate_embedding
from src.services.historical_qna import HistoricalQnaPublisher


def create_session_service() -> ReplySessionService:
    return ReplySessionService(CosmosReplySessionRepository())


async def save_accepted_qna(reply: AcceptedReply) -> None:
    publisher = HistoricalQnaPublisher(
        CosmosQaMemoryRepository(), generate_embedding,
        load_cosmos_qna_settings()["embedding_dimensions"],
    )
    await publisher.publish(reply)

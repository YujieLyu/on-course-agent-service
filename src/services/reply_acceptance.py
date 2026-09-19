"""Recoverable publication of an acceptance decision stored with the session."""

import asyncio
from dataclasses import dataclass

from src.domain.reply_sessions import (
    AcceptedReplyPublisher, InvalidSessionActionError, PendingAcceptanceSource,
    SessionConflictError, SessionNotFoundError, SessionSnapshot,
)
from src.logger import get_logger
from src.models import ReplySession, SessionStatus
from src.reply_sessions import ReplySessionStore

logger = get_logger(__name__)


async def accept_reply(
    conversation_id: str, store: ReplySessionStore, publish: AcceptedReplyPublisher,
) -> ReplySession:
    snapshot = await asyncio.to_thread(store.load_snapshot, conversation_id)
    if snapshot.session.status == SessionStatus.AWAITING_REVIEW:
        # The CAS and immutable payload must commit before any external write.
        snapshot = await asyncio.to_thread(store.begin_acceptance, snapshot)
    return await _publish_acceptance(snapshot, store, publish)


async def _publish_acceptance(
    snapshot: SessionSnapshot, store: ReplySessionStore, publish: AcceptedReplyPublisher,
) -> ReplySession:
    if snapshot.session.status == SessionStatus.ACCEPTED:
        return snapshot.session
    if snapshot.session.status != SessionStatus.ACCEPTING or snapshot.acceptance is None:
        raise InvalidSessionActionError("Session has no pending acceptance")

    await publish(snapshot.acceptance)
    try:
        return await asyncio.to_thread(store.complete_acceptance, snapshot)
    except SessionConflictError:
        # Another publisher may have completed the same immutable decision.
        current = await asyncio.to_thread(store.load_snapshot, snapshot.session.conversation_id)
        if current.session.status == SessionStatus.ACCEPTED and current.acceptance == snapshot.acceptance:
            return current.session
        raise


@dataclass(frozen=True)
class RecoveryResult:
    completed: int
    expired: int
    failed: tuple[str, ...]


async def recover_pending_acceptances(
    source: PendingAcceptanceSource, store: ReplySessionStore,
    publish: AcceptedReplyPublisher, *, limit: int = 100,
) -> RecoveryResult:
    if limit < 1:
        raise ValueError("Recovery limit must be positive")
    ids = await asyncio.to_thread(lambda: list(source.pending_ids(limit)))
    completed = expired = 0
    failed = []
    for conversation_id in ids:
        try:
            snapshot = await asyncio.to_thread(store.load_snapshot, conversation_id)
            # Recovery must never create an acceptance decision, even if the
            # scanner returned a stale or incorrectly classified candidate.
            await _publish_acceptance(snapshot, store, publish)
            completed += 1
        except SessionNotFoundError:
            expired += 1
        except Exception:
            failed.append(conversation_id)
            logger.exception("session.acceptance.recovery_failed", extra={
                "event_fields": {"conversation_id": conversation_id},
            })
    return RecoveryResult(completed, expired, tuple(failed))

from datetime import UTC, datetime

from src.config import load_cosmos_qna_settings
from src.models import ReplySession
from src.repositories import historical_qna as historical_qna_repository
from src.services.embeddings import generate_embedding


async def save_accepted_qna(session: ReplySession) -> None:
    """Persist the final reviewed answer using a deterministic, idempotent ID."""
    if not session.revisions or not session.revisions[-1].revision_content:
        raise ValueError("The session has no generated response to persist")

    settings = load_cosmos_qna_settings()
    question_vector = await generate_embedding(session.student_query)
    expected_dimensions = settings["embedding_dimensions"]
    if len(question_vector) != expected_dimensions:
        raise ValueError(
            "Embedding dimensions do not match the Cosmos vector policy: "
            f"expected {expected_dimensions}, got {len(question_vector)}"
        )

    item = {
        "id": session.conversation_id,
        "document_type": "historical_qna",
        "course_id": session.course_id,
        "student_query": session.student_query,
        "accepted_response": session.revisions[-1].revision_content,
        "revision_number": session.revisions[-1].revision_number,
        "accepted_at": datetime.now(UTC).isoformat(),
        "question_vector": question_vector,
    }
    await historical_qna_repository.upsert_accepted_qna(item)

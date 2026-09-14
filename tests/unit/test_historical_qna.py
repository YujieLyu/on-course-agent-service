import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

from src.models import ReplySession, Revision, SearchSource
from src.repositories import historical_qna as historical_qna_repository
from src.services.historical_qna import save_accepted_qna
from src.tools.search import search
from src.tools.search_providers import historical_qna_search


class HistoricalQnaTests(unittest.IsolatedAsyncioTestCase):
    async def test_container_initialization_and_write_run_off_event_loop(self):
        event_loop_thread = threading.get_ident()
        worker_threads = []
        container = Mock()

        def get_container():
            worker_threads.append(threading.get_ident())
            return container

        with patch.object(historical_qna_repository, "_get_container", side_effect=get_container):
            await historical_qna_repository.upsert_accepted_qna({"id": "conv-1", "course_id": "c1"})
        self.assertNotEqual(worker_threads, [event_loop_thread])
        self.assertEqual(len(worker_threads), 1)
        container.upsert_item.assert_called_once()

    async def test_save_uses_conversation_id_for_idempotent_upsert(self):
        session = ReplySession(
            conversation_id="conv-1",
            course_id="course-1",
            user_id="student-1",
            student_query="What is due?",
            revisions=[Revision(revision_number=1, revision_content="Friday.")],
        )
        settings = {"embedding_dimensions": 2}

        with patch(
            "src.services.historical_qna.generate_embedding",
            new=AsyncMock(return_value=[0.1, 0.2]),
        ), patch(
            "src.services.historical_qna.load_cosmos_qna_settings",
            return_value=settings,
        ), patch(
            "src.services.historical_qna.historical_qna_repository.upsert_accepted_qna",
            new=AsyncMock(),
        ) as upsert_accepted_qna:
            await save_accepted_qna(session)

        item = upsert_accepted_qna.await_args.args[0]
        self.assertEqual(item["id"], "conv-1")
        self.assertEqual(item["course_id"], "course-1")
        self.assertEqual(item["accepted_response"], "Friday.")
        self.assertEqual(item["question_vector"], [0.1, 0.2])

    async def test_search_is_scoped_to_course_partition(self):
        container = Mock()
        container.query_items.return_value = [
            {
                "id": "conv-1",
                "student_query": "What is due?",
                "accepted_response": "Friday.",
                "similarity_score": 0.9,
            }
        ]
        settings = {"top_k": 3}

        with patch(
            "src.tools.search_providers.historical_qna_search.generate_embedding",
            new=AsyncMock(return_value=[0.1, 0.2]),
        ), patch.object(
            historical_qna_repository,
            "load_cosmos_qna_settings",
            return_value=settings,
        ), patch.object(
            historical_qna_repository,
            "_get_container",
            return_value=container,
        ):
            result = await search("deadline", [SearchSource.HISTORICAL_QNA], "course-1")

        kwargs = container.query_items.call_args.kwargs
        self.assertEqual(kwargs["partition_key"], "course-1")
        self.assertEqual(result.results[0].source, SearchSource.HISTORICAL_QNA)
        self.assertIn("Friday.", result.results[0].content)


if __name__ == "__main__":
    unittest.main()

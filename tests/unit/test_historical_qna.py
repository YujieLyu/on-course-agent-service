import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch

from src.domain.reply_sessions import AcceptedReply
from src.domain.retention import utc_now
from src.models import SearchSource
from src.repositories import historical_qna as historical_qna_repository
from src.repositories.historical_qna import CosmosQaMemoryRepository
from src.services.historical_qna import HistoricalQnaPublisher
from src.tools.search import search
from tests.fakes import FakeSessionContainer


def accepted_reply(**updates):
    return AcceptedReply(
        conversation_id="conv-1", course_id="course-1", student_query="What is due?",
        revision_number=1, accepted_response="Friday.", accepted_at=utc_now(),
    ).model_copy(update=updates)


class HistoricalQnaTests(unittest.IsolatedAsyncioTestCase):
    async def test_container_initialization_and_write_run_off_event_loop(self):
        event_loop_thread = threading.get_ident()
        worker_threads = []
        container = FakeSessionContainer(partition_field="course_id")

        def get_container():
            worker_threads.append(threading.get_ident())
            return container

        publisher = HistoricalQnaPublisher(CosmosQaMemoryRepository(), AsyncMock(return_value=[0.1]), 1)
        with patch.object(historical_qna_repository, "_get_container", side_effect=get_container):
            await publisher.publish(accepted_reply())
        self.assertTrue(worker_threads)
        self.assertNotIn(event_loop_thread, worker_threads)
        self.assertIn("conv-1", container.documents)

    async def test_publisher_preserves_the_locked_decision_and_checks_embedding_dimensions(self):
        repository = Mock()
        embed = AsyncMock(return_value=[0.1, 0.2])
        publisher = HistoricalQnaPublisher(repository, embed, 2)
        decision = accepted_reply()
        await publisher.publish(decision)
        item = repository.save.call_args.args[0]
        self.assertEqual(item.reply, decision)
        self.assertEqual(item.question_vector, (0.1, 0.2))
        embed.assert_awaited_once_with("What is due?")
        repository.save.reset_mock()
        embed.return_value = [0.1]
        with self.assertRaisesRegex(ValueError, "dimensions"):
            await publisher.publish(decision)
        repository.save.assert_not_called()

    async def test_search_is_scoped_to_course_partition(self):
        container = Mock()
        container.query_items.return_value = [
            {"id": "conv-1", "student_query": "What is due?", "accepted_response": "Friday.", "similarity_score": 0.9}
        ]
        with patch(
            "src.tools.search_providers.historical_qna_search.generate_embedding",
            new=AsyncMock(return_value=[0.1, 0.2]),
        ), patch.object(historical_qna_repository, "load_cosmos_qna_settings", return_value={"top_k": 3}), patch.object(
            historical_qna_repository, "_get_container", return_value=container,
        ):
            result = await search("deadline", [SearchSource.HISTORICAL_QNA], "course-1")
        self.assertEqual(container.query_items.call_args.kwargs["partition_key"], "course-1")
        self.assertEqual(result.results[0].source, SearchSource.HISTORICAL_QNA)
        self.assertIn("Friday.", result.results[0].content)

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from src.models import SearchResult, SearchResults, SearchSource
from src.tools.search import search
from src.tools.search_providers import azure_ai_search


class AzureSearchHybridSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_returns_search_results_model(self):
        class FakeSearchClient:
            def search(self, **kwargs):
                return [
                    {
                        "content": "Course overview",
                        "source": "course_materials",
                    }
                ]

        search_settings = {
            "keyword_fields": ["content"],
            "semantic_config_name": None,
            "source_field": "source",
            "text_field": "content",
            "top_k": 3,
            "vector_field": "content_vector",
        }
        with patch.object(
            azure_ai_search,
            "generate_embedding",
            new=AsyncMock(return_value=[0.1, 0.2]),
        ), patch.object(
            azure_ai_search,
            "create_search_client",
            return_value=FakeSearchClient(),
        ), patch.object(
            azure_ai_search,
            "_get_settings",
            return_value=search_settings,
        ):
            result = await azure_ai_search.search("How does this course work?", "course-1")

        self.assertIsInstance(result, SearchResults)
        self.assertEqual(result.query, "How does this course work?")
        self.assertEqual(len(result.results), 1)
        self.assertIsInstance(result.results[0], SearchResult)
        self.assertEqual(result.results[0].source, SearchSource.COURSE_MATERIALS)
        self.assertEqual(result.results[0].content, "Course overview")

    async def test_uses_sdk_semantic_configuration_argument(self):
        search_client = unittest.mock.MagicMock()
        search_client.search.return_value = []
        search_settings = {
            "keyword_fields": ["content"],
            "semantic_config_name": "course-semantic-config",
            "source_field": "source",
            "text_field": "content",
            "top_k": 3,
            "vector_field": "content_vector",
        }
        with patch.object(
            azure_ai_search,
            "generate_embedding",
            new=AsyncMock(return_value=[0.1, 0.2]),
        ), patch.object(
            azure_ai_search,
            "create_search_client",
            return_value=search_client,
        ), patch.object(
            azure_ai_search,
            "_get_settings",
            return_value=search_settings,
        ):
            await azure_ai_search.search("question", "course-1")

        kwargs = search_client.search.call_args.kwargs
        self.assertEqual(
            kwargs["semantic_configuration_name"],
            "course-semantic-config",
        )
        self.assertNotIn("semantic_config_name", kwargs)


class FourSourceSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_four_sources_run_concurrently_in_stable_order(self):
        entered = set()
        all_entered = asyncio.Event()
        async def provider(source, query, course_id):
            self.assertEqual((query, course_id), ("question", "course-1"))
            entered.add(source)
            if len(entered) == 4:
                all_entered.set()
            await asyncio.wait_for(all_entered.wait(), 1)
            return SearchResults(query=query, results=[SearchResult(source=source, content=source)])
        methods = {
            source: AsyncMock()
            for source in SearchSource
        }
        for source, mock in methods.items():
            async def run(query, course_id, source=source):
                return await provider(source, query, course_id)
            mock.side_effect = run
        with patch("src.tools.search._SEARCH_METHODS", methods):
            result = await search("question", "course-1")
        self.assertEqual([item.source for item in result.results], list(SearchSource))
        for method in methods.values():
            method.assert_awaited_once_with("question", "course-1")

    async def test_source_failure_and_timeout_preserve_other_results(self):
        async def stalled(*args):
            await asyncio.Event().wait()
        source = SearchResult(source=SearchSource.COURSE_MATERIALS, content="Submit a PDF.")
        methods = {
            SearchSource.COURSE_MATERIALS: AsyncMock(return_value=SearchResults(query="question", results=[source])),
            SearchSource.UNIVERSITY_POLICIES: AsyncMock(side_effect=RuntimeError("Unavailable")),
            SearchSource.CANVAS: stalled,
            SearchSource.HISTORICAL_QNA: AsyncMock(return_value=SearchResults(query="question", results=[])),
        }
        with patch("src.tools.search._SEARCH_METHODS", methods), patch("src.tools.search.SOURCE_TIMEOUT_SECONDS", 0.01):
            result = await search("question", "course-1")
        self.assertEqual(result.results, [source])

    async def test_cancellation_is_not_treated_as_empty_retrieval(self):
        methods = {SearchSource.COURSE_MATERIALS: AsyncMock(side_effect=asyncio.CancelledError())}
        with patch("src.tools.search._SEARCH_METHODS", methods), self.assertRaises(asyncio.CancelledError):
            await search("question", "course-1")

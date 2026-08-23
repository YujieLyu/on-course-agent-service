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
            result = await search(
                "How does this course work?",
                [SearchSource.COURSE_MATERIALS],
                "course-1",
            )

        self.assertIsInstance(result, SearchResults)
        self.assertEqual(result.query, "How does this course work?")
        self.assertEqual(len(result.results), 1)
        self.assertIsInstance(result.results[0], SearchResult)
        self.assertEqual(result.results[0].source, SearchSource.COURSE_MATERIALS)
        self.assertEqual(result.results[0].content, "Course overview")

    async def test_unknown_source_returns_empty_results(self):
        result = await search("question", ["unsupported"], "course-1")

        self.assertEqual(result, SearchResults(query="question", results=[]))

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

    async def test_combines_each_selected_source_once(self):
        course_result = SearchResult(
            source=SearchSource.COURSE_MATERIALS,
            content="Course content",
        )
        policy_result = SearchResult(
            source=SearchSource.UNIVERSITY_POLICIES,
            content="Policy content",
        )

        with patch(
            "src.tools.search_providers.azure_ai_search.search",
            new=AsyncMock(
                return_value=SearchResults(query="question", results=[course_result])
            ),
        ) as course_search, patch(
            "src.tools.search_providers.public_website_search.search",
            new=AsyncMock(
                return_value=SearchResults(query="question", results=[policy_result])
            ),
        ) as policy_search:
            result = await search(
                "question",
                [
                    SearchSource.COURSE_MATERIALS,
                    SearchSource.UNIVERSITY_POLICIES,
                    SearchSource.COURSE_MATERIALS,
                ],
                "course-1",
            )

        self.assertEqual(result.results, [course_result, policy_result])
        course_search.assert_awaited_once_with("question", "course-1")
        policy_search.assert_awaited_once_with("question", "course-1")


if __name__ == "__main__":
    unittest.main()

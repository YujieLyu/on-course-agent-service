import unittest
from unittest.mock import AsyncMock, patch

from src.models import SearchResult, SearchResults, SearchSource
from src.tools import search_methods


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
        with patch.object(search_methods, "_generate_embedding", new=AsyncMock(return_value=[0.1, 0.2])), patch.object(
            search_methods, "create_search_client", return_value=FakeSearchClient()
        ), patch.object(search_methods, "_get_ai_search_settings", return_value=search_settings):
            result = await search_methods.azure_search_hybrid_search("How does this course work?")

        self.assertIsInstance(result, SearchResults)
        self.assertEqual(result.query, "How does this course work?")
        self.assertEqual(len(result.results), 1)
        self.assertIsInstance(result.results[0], SearchResult)
        self.assertEqual(result.results[0].source, SearchSource.COURSE_MATERIALS)
        self.assertEqual(result.results[0].content, "Course overview")


if __name__ == "__main__":
    unittest.main()

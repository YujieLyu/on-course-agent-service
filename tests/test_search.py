import unittest
from unittest.mock import patch

from src.models import SearchResult, SearchResults, SearchSource
from src.tools import search


class AzureSearchHybridSearchTests(unittest.TestCase):
    def test_returns_search_results_model(self):
        class FakeSearchClient:
            def search(self, **kwargs):
                return [
                    {
                        "content": "Course overview",
                        "source": "course_materials",
                    }
                ]

        with patch.object(search, "_generate_embedding", return_value=[0.1, 0.2]), patch.object(
            search, "create_search_client", return_value=FakeSearchClient()
        ):
            result = search.azure_search_hybrid_search("How does this course work?")

        self.assertIsInstance(result, SearchResults)
        self.assertEqual(result.query, "How does this course work?")
        self.assertEqual(len(result.results), 1)
        self.assertIsInstance(result.results[0], SearchResult)
        self.assertEqual(result.results[0].source, SearchSource.COURSE_MATERIALS)
        self.assertEqual(result.results[0].content, "Course overview")


if __name__ == "__main__":
    unittest.main()

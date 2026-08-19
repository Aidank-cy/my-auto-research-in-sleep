import unittest
from unittest.mock import patch

from tools import research_candidate_citation_enrich as citation


class CitationEnrichmentTests(unittest.TestCase):
    def test_admitted_relevance_accepts_only_numeric_three_level_values(self):
        metadata = {"candidates": [
            {"id": "p1", "relevance": 1}, {"id": "p2", "relevance": .5}, {"id": "p3", "relevance": 0},
        ]}
        admitted = [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}]
        self.assertEqual([], citation.validate_admitted_relevance(metadata, admitted))
        metadata["candidates"][1]["relevance"] = "0.5"
        self.assertTrue(citation.validate_admitted_relevance(metadata, admitted))

    def test_title_lookup_uses_configured_retry_policy(self):
        with patch.object(
            citation.semantic_scholar_fetch,
            "search",
            return_value={"data": [{"title": "A Paper", "citationCount": 4}]},
        ) as search:
            paper, error = citation.fetch_semantic_scholar_with_retry(
                "title",
                "A Paper",
                retries=15,
                retry_delay=2.0,
            )

        self.assertIsNone(error)
        self.assertEqual(4, paper["citationCount"])
        self.assertEqual(15, search.call_args.kwargs["retries"])
        self.assertEqual(2.0, search.call_args.kwargs["retry_delay"])

    def test_identifier_lookup_uses_same_configured_retry_policy(self):
        with patch.object(
            citation.semantic_scholar_fetch,
            "get_paper",
            return_value={"title": "A Paper", "citationCount": 4},
        ) as get_paper:
            paper, error = citation.fetch_semantic_scholar_with_retry(
                "arxiv_id",
                "ARXIV:2501.00001",
            )

        self.assertIsNone(error)
        self.assertEqual(4, paper["citationCount"])
        self.assertEqual(citation.SEMANTIC_SCHOLAR_CITATION_RETRIES, get_paper.call_args.kwargs["retries"])
        self.assertEqual(
            citation.SEMANTIC_SCHOLAR_CITATION_RETRY_DELAY_SECONDS,
            get_paper.call_args.kwargs["retry_delay"],
        )


if __name__ == "__main__":
    unittest.main()

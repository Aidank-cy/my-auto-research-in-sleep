import unittest

from tools import arxiv_fetch


class ArxivVenueTests(unittest.TestCase):
    def test_arxiv_only_defaults_to_preprint(self):
        self.assertEqual(arxiv_fetch._venue_from_comment(None), "arXiv preprint")
        self.assertEqual(
            arxiv_fetch._venue_from_comment("Submitted to NeurIPS 2026"),
            "arXiv preprint",
        )

    def test_explicit_acceptance_extracts_formal_venue(self):
        self.assertEqual(
            arxiv_fetch._venue_from_comment("Accepted at NeurIPS 2026 (Spotlight), 18 pages"),
            "NeurIPS 2026 (Spotlight)",
        )
        self.assertEqual(
            arxiv_fetch._venue_from_comment("To appear in ACL 2026."),
            "ACL 2026",
        )
        self.assertEqual(
            arxiv_fetch._venue_from_comment("Accepted for publication in TMLR; 24 pages"),
            "TMLR",
        )


if __name__ == "__main__":
    unittest.main()

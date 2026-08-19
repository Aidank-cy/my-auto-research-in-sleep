import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import research_candidate_rank as ranker  # noqa: E402
import research_candidate_search as search  # noqa: E402
import research_candidate_dedupe as dedupe  # noqa: E402
import research_survey_paths  # noqa: E402
import verify_papers  # noqa: E402


class CandidateVerificationTests(unittest.TestCase):
    def test_topic_name_resolves_to_topic_first_survey_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            resolved = research_survey_paths.survey_root_from_topic_name(
                "reasoning-length",
                repo_root=repo_root,
            )

        self.assertEqual(repo_root / "database" / "reasoning-length" / "survey", resolved)

    def test_candidate_search_default_uses_topic_first_survey_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo_root = Path(temporary)
            expected = repo_root / "database" / "reasoning-length" / "survey"
            resolved = search.default_survey_root(["Reasoning Length"], repo_root=repo_root)

        self.assertEqual(expected, resolved)

    def test_huggingface_candidate_maps_published_at_to_year(self):
        candidates = {}
        result = mock.Mock()
        result.paper = {
            "id": "2501.00001",
            "title": "Budget-aware reasoning",
            "authors": ["Author"],
            "upvotes": 100,
            "publishedAt": "2025-01-15T00:00:00.000Z",
            "summary": "Reasoning token budget",
            "ai_summary": None,
            "ai_keywords": [],
            "url": "https://huggingface.co/papers/2501.00001",
            "abs_url": "https://arxiv.org/abs/2501.00001",
            "pdf_url": "https://arxiv.org/pdf/2501.00001.pdf",
            "githubRepo": None,
            "projectPage": None,
        }
        result.match_score = 1
        result.matched_queries = ["reasoning token budget"]

        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            search.huggingface_papers_fetch,
            "get_window",
            return_value=([result.paper], {}),
        ), mock.patch.object(
            search.huggingface_papers_fetch,
            "search_papers",
            return_value=[result],
        ), mock.patch.object(
            search.huggingface_papers_fetch,
            "_result_to_dict",
            return_value={**result.paper, "match_score": 1, "matched_queries": result.matched_queries},
        ):
            search.run_huggingface(
                ["reasoning token budget"],
                Path(temporary),
                candidates,
                days=1,
                min_upvotes=50,
                refresh=False,
            )

        candidate = candidates["arxiv:2501.00001"]
        self.assertEqual("2025-01-15T00:00:00.000Z", candidate["published"])
        self.assertEqual("2025", candidate["year"])

    def test_semantic_scholar_search_uses_fifteen_retries_and_two_second_retry_delay(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(
            search.semantic_scholar_fetch,
            "search",
            return_value={"data": []},
        ) as fetch:
            status = search.run_semantic_scholar(
                ["reasoning token budget"],
                Path(temporary),
                {},
                max_results=10,
                year_from=2024,
                year_to=2026,
            )

        self.assertTrue(status.succeeded)
        self.assertEqual(15, fetch.call_args.kwargs["retries"])
        self.assertEqual(2.0, fetch.call_args.kwargs["retry_delay"])

    def test_request_pacer_enforces_one_interval_between_clients(self):
        pacer = search.RequestPacer(2.0)
        with mock.patch.object(search.time, "monotonic", side_effect=[10.0, 10.0, 10.5, 12.0]), mock.patch.object(
            search.time, "sleep"
        ) as sleep:
            pacer.wait()
            pacer.wait()

        sleep.assert_called_once_with(1.5)

    def test_configure_request_pacing_installs_one_shared_hook(self):
        modules = (
            search.huggingface_papers_fetch,
            search.arxiv_fetch,
            search.semantic_scholar_fetch,
            search.paper_verifier,
        )
        patches = [mock.patch.object(module, "set_request_hook") for module in modules]
        mocks = [patcher.start() for patcher in patches]
        self.addCleanup(lambda: [patcher.stop() for patcher in patches])

        pacer = search.configure_request_pacing(2.0)

        for setter in mocks:
            setter.assert_called_once_with(pacer.wait)

    def test_sleep_is_the_only_global_request_delay_option(self):
        parser = search.build_parser()
        args = parser.parse_args(["--query", "topic", "--sleep", "1.25"])
        self.assertEqual(1.25, args.sleep)
        option_strings = {option for action in parser._actions for option in action.option_strings}
        self.assertNotIn("--verify-delay", option_strings)

    def test_candidate_schema_exposes_only_status_and_method(self):
        candidates = [
            {"id": "p1", "arxiv_id": "2501.00001", "title": "Verified"},
            {"id": "p2", "title": "Pending"},
        ]
        results = [
            verify_papers.PaperResult("p1", "verified", "arxiv", "high", None),
            verify_papers.PaperResult("p2", "verify_pending", None, None, "s2_verify_pending"),
        ]
        with mock.patch.object(search.paper_verifier, "verify_papers", return_value=results):
            receipt = search.verify_candidates(
                candidates,
                arxiv_batch_size=40,
                delay_seconds=0,
                fuzzy_threshold=0.6,
                cache_scope="none",
                cache_dir=None,
                cache_ttl_days=30,
                no_cache=True,
                hallucination_warn_threshold=0.2,
            )
        self.assertEqual("verified", candidates[0]["verification_status"])
        self.assertNotIn("verification_confidence", candidates[0])
        self.assertNotIn("verification_reason", candidates[1])
        self.assertEqual("high", receipt["papers"][0]["confidence"])
        self.assertEqual("s2_verify_pending", receipt["papers"][1]["reason"])

    def test_minimal_candidate_keeps_verified_gate_fields(self):
        record = search.minimal_candidate(
            {"id": "p1", "title": "A", "arxiv_id": "1", "doi": None,
             "verification_status": "verified", "verification_method": "arxiv"}
        )
        self.assertEqual({"verified", "arxiv"}, {record["verification_status"], record["verification_method"]})
        self.assertNotIn("verification_reason", record)

    def test_ranking_excludes_unverified_and_irrelevant(self):
        report = ranker.rank_candidates(
            [
                {"id": "p1", "verification_status": "verified", "relevance": 1, "citationCount": 1},
                {"id": "p2", "verification_status": "unverified", "relevance": 1, "citationCount": 999},
                {"id": "p3", "verification_status": "verified", "relevance": 0, "citationCount": 999},
            ],
            max_selected=20,
        )
        self.assertEqual(["p1"], report["selected_candidate_ids"])
        reasons = {record["id"]: record["exclusion_reason"] for record in report["ranked_candidates"]}
        self.assertEqual("not_verified", reasons["p2"])
        self.assertEqual("not_relevant", reasons["p3"])

    def test_ranking_admits_partial_relevance_after_direct_matches(self):
        report = ranker.rank_candidates([
            {"id": "partial", "verification_status": "verified", "relevance": .5, "citationCount": 999},
            {"id": "direct", "verification_status": "verified", "relevance": 1, "citationCount": 0},
        ], max_selected=2)
        self.assertEqual(["direct", "partial"], report["selected_candidate_ids"])

    def test_ranking_rejects_missing_or_non_numeric_relevance(self):
        with self.assertRaises(ValueError):
            ranker.rank_candidates([{"id": "p1", "verification_status": "verified"}])
        with self.assertRaises(ValueError):
            ranker.rank_candidates([{"id": "p1", "verification_status": "verified", "relevance": "1"}])
        report = ranker.rank_candidates([{"id": "p2", "verification_status": "unverified"}])
        self.assertEqual([], report["selected_candidate_ids"])

    def test_dedupe_projection_preserves_verified_only_contract(self):
        verified = {"id": "p1", "title": "A", "verification_status": "verified", "verification_method": "arxiv"}
        pending = {"id": "p2", "title": "B", "verification_status": "verify_pending", "verification_method": None}
        projected = [dedupe.minimal_candidate(item) for item in (verified, pending) if item["verification_status"] == "verified"]
        self.assertEqual(1, len(projected))
        self.assertEqual("verified", projected[0]["verification_status"])
        self.assertNotIn("verification_reason", projected[0])

    def test_same_topic_rerun_preserves_model_and_local_fields(self):
        current = {"arxiv:2501.00001": {"arxiv_id": "2501.00001", "title": "Fresh", "sources": ["arxiv"]}}
        prior = [{"id": "p7", "arxiv_id": "2501.00001", "title": "Old", "relevance": 1, "local_pdf_path": "/tmp/p.pdf"}]
        reused = search.merge_prior_candidates(current, prior)
        self.assertEqual(1, reused)
        self.assertEqual("p7", current["arxiv:2501.00001"]["id"])
        self.assertEqual(1, current["arxiv:2501.00001"]["relevance"])
        self.assertEqual("/tmp/p.pdf", current["arxiv:2501.00001"]["local_pdf_path"])
        self.assertEqual(["arxiv"], current["arxiv:2501.00001"]["sources"])

    def test_same_topic_rerun_appends_new_candidate_without_renumbering(self):
        candidates = {
            "arxiv:2501.00001": {"id": "p1", "arxiv_id": "2501.00001", "title": "Zulu", "sources": ["arxiv"]},
            "arxiv:2501.00002": {"id": "p2", "arxiv_id": "2501.00002", "title": "Beta", "sources": ["arxiv"]},
            "arxiv:2501.00003": {"arxiv_id": "2501.00003", "title": "Alpha", "sources": ["huggingface-papers"]},
        }

        ordered = search.assign_candidate_ids(candidates)

        self.assertEqual(["p1", "p2", "p3"], [candidate["id"] for candidate in ordered])
        self.assertEqual("Alpha", ordered[-1]["title"])

    def test_failed_source_does_not_claim_reused_candidates(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source_status.json").write_text(
                '[{"source":"semantic-scholar","succeeded":false,"usable_candidate_count":3}]',
                encoding="utf-8",
            )
            result = dedupe.refresh_source_status(root, [{"sources": ["semantic-scholar"]}])
            self.assertEqual(0, result[0]["usable_candidate_count"])
            self.assertEqual(1, result[0]["historical_candidate_count"])

    def test_dedupe_keeps_current_and_historical_source_counts_separate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source_status.json").write_text(
                '[{"source":"arxiv","succeeded":true,"usable_candidate_count":1}]', encoding="utf-8"
            )
            result = dedupe.refresh_source_status(root, [
                {"sources": ["arxiv"], "current_run_sources": ["arxiv"]},
                {"sources": ["arxiv"], "current_run_sources": []},
            ])
            self.assertEqual(1, result[0]["current_run_usable_candidate_count"])
            self.assertEqual(1, result[0]["usable_candidate_count"])
            self.assertEqual(2, result[0]["historical_candidate_count"])


if __name__ == "__main__":
    unittest.main()

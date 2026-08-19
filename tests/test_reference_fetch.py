import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import reference_fetch


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class ReferenceFetchTests(unittest.TestCase):
    def test_import_prior_same_topic_cache_avoids_network_refetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.json"
            prior = root / "prior"
            cache = root / "current"
            status = root / "status.json"
            write_json(candidates, [{"id": "2401.00001"}, {"id": "2401.00002"}])
            write_json(prior / "2401.00001.json", [{"externalIds": {"ArXiv": "2301.00001"}}])

            result = reference_fetch.import_prior_caches(
                candidates, [prior], cache, status, coverage_threshold=0.95
            )

            self.assertEqual(result, {"candidates": 2, "imported": 1, "reused": 0, "missing": 1})
            record = json.loads(status.read_text())["papers"]["2401.00001"]
            self.assertEqual(record["source"], "prior_same_topic_run")
            self.assertEqual(record["attempts"], 0)
            self.assertTrue((cache / "2401.00001.json").exists())

    def test_fetch_pauses_below_coverage_and_distinguishes_empty_from_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.json"
            cache = root / "references"
            status = root / "status.json"
            write_json(candidates, [{"id": "2401.00001"}, {"id": "2401.00002"}, {"id": "s2-hash"}])
            responses = [
                [{"externalIds": {"ArXiv": "2301.00001"}}],
                [],
                RuntimeError("HTTP 429: Too Many Requests"),
            ]

            with patch.object(reference_fetch.s2, "get_references", side_effect=responses) as fetch:
                summary, exit_code = reference_fetch.fetch_candidates(
                    candidates, cache, status, limit=500, coverage_threshold=0.95
                )

            self.assertEqual(exit_code, reference_fetch.PAUSED_EXIT_CODE)
            self.assertEqual(summary["successful"], 2)
            self.assertFalse(summary["ready_for_scoring"])
            self.assertEqual(fetch.call_args_list[0].args[0], "ARXIV:2401.00001")
            self.assertEqual(fetch.call_args_list[2].args[0], "s2-hash")
            records = json.loads(status.read_text())["papers"]
            self.assertEqual(records["2401.00002"]["status"], "success_empty")
            self.assertEqual(records["s2-hash"]["status"], "retryable_failure")
            self.assertFalse((cache / "s2-hash.json").exists())

    def test_resume_reuses_successes_and_retries_only_retryable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            candidates = root / "candidates.json"
            cache = root / "references"
            status = root / "status.json"
            write_json(candidates, [{"id": "2401.00001"}, {"id": "2401.00002"}])
            with patch.object(
                reference_fetch.s2,
                "get_references",
                side_effect=[[{"externalIds": {}}], RuntimeError("HTTP 503: unavailable")],
            ):
                reference_fetch.fetch_candidates(candidates, cache, status, limit=10, coverage_threshold=1.0)

            with patch.object(reference_fetch.s2, "get_references", return_value=[]) as fetch:
                summary, exit_code = reference_fetch.fetch_candidates(
                    candidates, cache, status, limit=10, coverage_threshold=1.0
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(summary["coverage"], 1.0)
            fetch.assert_called_once_with("ARXIV:2401.00002", limit=10)

    def test_materialize_copies_success_and_skips_not_found_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selected = root / "selected.json"
            cache = root / "references"
            status = root / "status.json"
            first_folder = root / "paper-one"
            second_folder = root / "paper-two"
            write_json(
                selected,
                [
                    {"paper_id": "2401.00001", "paper_folder": str(first_folder)},
                    {"paper_id": "2401.00002", "paper_folder": str(second_folder)},
                ],
            )
            write_json(cache / "2401.00001.json", [{"paperId": "ref"}])
            write_json(
                status,
                {
                    "papers": {
                        "2401.00001": {"status": "success_nonempty", "attempts": 1},
                        "2401.00002": {"status": "not_found", "attempts": 1},
                    }
                },
            )

            with patch.object(reference_fetch, "_fetch_one") as fetch:
                result = reference_fetch.materialize_selected(
                    selected, cache, status, limit=500, retry_missing=True
                )

            fetch.assert_not_called()
            self.assertEqual(result, {"selected": 2, "copied": 1, "retried": 0, "missing": 1})
            self.assertTrue((first_folder / "references.json").exists())
            self.assertFalse((second_folder / "references.json").exists())

    def test_existing_paper_folder_seeds_cache_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper_folder = root / "existing-paper"
            candidates = root / "candidates.json"
            cache = root / "cache"
            status = root / "status.json"
            write_json(
                paper_folder / "references.json",
                [{"externalIds": {"ArXiv": "2301.00001"}}],
            )
            write_json(
                candidates,
                [{"id": "2401.00001", "paper_folder": str(paper_folder)}],
            )

            with patch.object(reference_fetch.s2, "get_references") as fetch:
                summary, exit_code = reference_fetch.fetch_candidates(
                    candidates, cache, status, limit=500, coverage_threshold=0.95
                )

            fetch.assert_not_called()
            self.assertEqual(exit_code, 0)
            self.assertEqual(summary["coverage"], 1.0)
            record = json.loads(status.read_text())["papers"]["2401.00001"]
            self.assertEqual(record["source"], "existing_paper_folder")
            self.assertEqual(record["attempts"], 0)
            self.assertTrue((cache / "2401.00001.json").exists())

    def test_successful_rerun_fetch_backfills_existing_paper_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paper_folder = root / "existing-paper"
            candidates = root / "candidates.json"
            cache = root / "cache"
            status = root / "status.json"
            write_json(
                candidates,
                [{"id": "2401.00001", "paper_folder": str(paper_folder)}],
            )

            with patch.object(
                reference_fetch.s2,
                "get_references",
                return_value=[{"externalIds": {"ArXiv": "2301.00001"}}],
            ):
                summary, exit_code = reference_fetch.fetch_candidates(
                    candidates, cache, status, limit=500, coverage_threshold=0.95
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(summary["coverage"], 1.0)
            self.assertTrue((paper_folder / "references.json").exists())
            self.assertEqual(
                json.loads((paper_folder / "references.json").read_text()),
                [{"externalIds": {"ArXiv": "2301.00001"}}],
            )


if __name__ == "__main__":
    unittest.main()

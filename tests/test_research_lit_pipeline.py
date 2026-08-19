import importlib.util
import json
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
SPEC = importlib.util.spec_from_file_location("pipeline", ROOT / "tools" / "research_lit_pipeline.py")
pipeline = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(pipeline)


class ResearchLitPipelineTests(unittest.TestCase):
    def write(self, path, value):
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_rerun_pool_keeps_old_separate_from_new(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); topic = root / "topic"; topic.mkdir(); old = topic / "old"; old.mkdir()
            self.write(old / "note.json", {"paper_id": "1", "title": "Old"})
            candidates = root / "candidates.json"; output = root / "pool.json"
            self.write(candidates, [{"id": "1", "title": "Duplicate"}, {"id": "2", "title": "New"}])
            pipeline.build_reference_pool(Namespace(candidates=candidates, topic_folder=topic, output=output))
            pool = json.loads(output.read_text())
            self.assertEqual(["existing", "new"], [item["record_origin"] for item in pool])

    def test_selection_persists_required_contract_and_score(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); topic = root / "topic"; topic.mkdir()
            scores = root / "scores.json"; candidates = root / "candidates.json"; verification = root / "verification.json"; output = root / "selected.json"
            score = {"total": .8}
            self.write(scores, [{"paper_id": "2401.00002", "relevance_score": score}, {"paper_id": "2401.00003", "relevance_score": {"total": .9}}])
            self.write(candidates, [{"id": "2401.00002", "title": "A Study"}, {"id": "2401.00003", "title": "Not Verified"}])
            self.write(verification, {"papers": [{"id": "2401.00002", "status": "verified", "method": "arxiv"}, {"id": "2401.00003", "status": "unverified", "method": "none"}]})
            pipeline.select(Namespace(scores=scores, candidates=candidates, verification=verification, topic_folder=topic, max_papers=2, output=output, exclusions_output=None))
            selected = json.loads(output.read_text())
            self.assertEqual(1, len(selected)); self.assertEqual(score, selected[0]["relevance_score"]); self.assertIsNone(selected[0]["pdf_path"])

    def test_selection_excludes_records_without_supported_pdf_route(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); topic = root / "topic"; topic.mkdir()
            scores = root / "scores.json"; candidates = root / "candidates.json"; verification = root / "verification.json"; output = root / "selected.json"; exclusions = root / "exclusions.json"
            self.write(scores, [{"paper_id": "s2-paper", "relevance_score": {"total": .9}}])
            self.write(candidates, [{"id": "s2-paper", "title": "S2 only"}])
            self.write(verification, {"papers": [{"id": "s2-paper", "status": "verified", "method": "semantic_scholar"}]})
            pipeline.select(Namespace(scores=scores, candidates=candidates, verification=verification, topic_folder=topic, max_papers=2, output=output, exclusions_output=exclusions))
            self.assertEqual([], json.loads(output.read_text()))
            self.assertEqual("no_supported_pdf_route", json.loads(exclusions.read_text())[0]["reason"])

    def test_refresh_selected_pdfs_updates_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); folder = root / "paper"; folder.mkdir()
            selected = root / "selected.json"; output = root / "updated.json"
            pdf = folder / "2401.00001.pdf"; pdf.write_bytes(b"x" * 10241)
            self.write(selected, [{"paper_id": "2401.00001", "paper_folder": str(folder), "pdf_path": None}])
            result = pipeline.refresh_selected_pdfs(Namespace(selected=selected, output=output, min_bytes=10240))
            self.assertEqual(0, result)
            self.assertEqual(str(pdf), json.loads(output.read_text())[0]["pdf_path"])

    def test_filter_relevant_preserves_metadata_and_excludes_unverified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            candidates = root / "candidates.json"; verification = root / "verification.json"; judgments = root / "judgments.json"; output = root / "relevant.json"
            self.write(candidates, [{"id": "1", "title": "Keep", "abstract": "A"}, {"id": "2", "title": "Drop"}, {"id": "3", "title": "Unverified"}])
            self.write(verification, {"papers": [{"id": "1", "status": "verified"}, {"id": "2", "status": "verified"}, {"id": "3", "status": "unverified"}]})
            self.write(judgments, [{"paper_id": "1", "relevance": 1}, {"paper_id": "2", "relevance": 0}])
            pipeline.filter_relevant(Namespace(candidates=candidates, verification=verification, judgments=judgments, output=output))
            result = json.loads(output.read_text())
            self.assertEqual([{"id": "1", "title": "Keep", "abstract": "A", "relevance": 1}], result)

    def test_audit_checks_overview_and_step8_receipts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            overview = root / "overview.md"; step8 = root / "step8.json"; sources = root / "sources.json"; output = root / "audit.json"
            overview.write_text("### 领域问题概览与核心挑战\n", encoding="utf-8")
            self.write(step8, [{"paper_id": "x", "returncode": 1}])
            self.write(sources, [{"source": "arxiv", "usable_candidate_count": 1}])
            result = pipeline.audit_run(Namespace(
                reference_status=None, coverage_threshold=.95, selected=None,
                scores=None, final_scores=None, source_status=sources,
                overview=overview, step8_results=step8, output=output,
            ))
            issues = json.loads(output.read_text())["issues"]
            self.assertEqual(1, result)
            self.assertIn("step8_ingest_failed", issues)
            self.assertIn("overview_section_missing:论文范围", issues)


if __name__ == "__main__":
    unittest.main()

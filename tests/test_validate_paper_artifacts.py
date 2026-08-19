import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("validator", ROOT / "tools" / "validate_paper_artifacts.py")
validator = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(validator)


class ValidatePaperArtifactsTests(unittest.TestCase):
    def test_quote_matching_handles_layout_only_changes(self):
        self.assertTrue(validator.quote_is_in_pdf("Efficient reasoning improves fidelity.", "Effi-\ncient reasoning improves ﬁdelity."))
        self.assertFalse(validator.quote_is_in_pdf("Efficient reasoning increases fidelity.", "Efficient reasoning improves fidelity."))

    def test_minimal_bundle_and_placeholder_pass(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "paper.pdf").write_bytes(b"placeholder")
            meta = {"paper_id": "1", "title": "T", "authors": ["A"], "published_date": "2026-01-01", "discovery_source": "arxiv", "verification_status": "verified", "verification_method": "arxiv", "paper_type": "finding-centric"}
            logic = {"paper_id": "1", "abstract": "A", "challenges": {"summary": "", "positioning_against_prior_work": "", "formulas": [], "key_insights": [{"id": "KI-1", "text": "x"}]}, "methodology": {"summary": "", "formulas": [], "key_insights": []}, "results": {"items": [{"id": "R1", "role": "finding", "title": "Finding", "setup": "", "result": "", "author_interpretation": "", "formulas": []}]}, "limitations": {"items": [{"id": "L1", "text": "x"}]}, "insights": ""}
            entries = {key: {"quote": validator.QUOTE_PLACEHOLDER, "images": []} for key in ("KI-1", "R1", "L1")}
            note = "## T\n\n*Authors: A · Published: 2026-01-01 · Source: arxiv*\n*Verification: ✅ verified (via arxiv)*\n\n### Abstract\n\nA\n\n### Challenges\n\n> " + validator.QUOTE_PLACEHOLDER + "\n\n### Methodology\n\n### Results\n\n#### 1. Finding\n\n> " + validator.QUOTE_PLACEHOLDER + "\n\n### Limitations\n\n> " + validator.QUOTE_PLACEHOLDER + "\n\n### Insights\n"
            for name, value in (("note.json", meta), ("logic.json", logic), ("evidence.json", {"paper_id": "1", "entries": entries})):
                (folder / name).write_text(json.dumps(value), encoding="utf-8")
            (folder / "note.md").write_text(note, encoding="utf-8")
            self.assertEqual([], validator.validate_paper_folder(folder, pdf_text=""))


if __name__ == "__main__":
    unittest.main()

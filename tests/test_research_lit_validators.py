import json
import tempfile
import unittest
from pathlib import Path

from tools import research_query_pack_build as query_pack
from tools import research_evidence_prepare as evidence_prepare
from tools import research_relation_validate as relation_validate
from tools import research_synthesis_compile as synthesis_compile
from tools.research_artifact_io import sha256_file


class ResearchLitValidatorTests(unittest.TestCase):
    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_wiki_routes_are_required_and_match_valid_note_receipts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            note = root / "notes" / "papers" / "p1.md"
            note.parent.mkdir(parents=True)
            note.write_text("note", encoding="utf-8")
            pdf = root / "papers" / "p1.pdf"
            pdf.parent.mkdir(parents=True)
            pdf.write_bytes(b"%PDF-1.7\n")
            self.write_json(root / "synthesis" / "paper_note_tasks.json", {
                "tasks": [{
                    "id": "p1", "status": "reusable", "input_sha256": "input-1",
                    "note_path": str(note), "pdf_path": str(pdf), "pdf_sha256": sha256_file(pdf),
                }],
            })
            self.write_json(root / "synthesis" / "validation" / "paper_notes.json", {
                "task_count": 1,
                "valid_count": 1,
                "invalid_count": 0,
                "records": [{
                    "id": "p1", "status": "valid", "input_sha256": "input-1",
                    "note_path": str(note), "note_sha256": sha256_file(note),
                }],
            })
            self.write_json(root / "synthesis" / "wiki_notes.json", [{
                "paper_id": "p1", "note_path": str(note),
            }])
            self.assertEqual("p1", query_pack.load_required_wiki_notes(root)[0]["paper_id"])
            note.write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "changed after validation"):
                query_pack.load_required_wiki_notes(root)
            note.write_text("note", encoding="utf-8")
            (root / "synthesis" / "wiki_notes.json").unlink()
            with self.assertRaises(FileNotFoundError):
                query_pack.load_required_wiki_notes(root)

    def test_invalid_note_receipt_blocks_evidence_preparation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_json(root / "synthesis" / "paper_note_tasks.json", {"tasks": []})
            self.write_json(root / "synthesis" / "validation" / "paper_notes.json", {
                "invalid_count": 1,
                "records": [{"id": "p1", "status": "invalid"}],
            })
            self.write_json(root / "synthesis" / "wiki_notes.json", [])
            self.assertEqual(2, evidence_prepare.main(["--survey-root", str(root)]))

    def test_idea_and_experiment_nodes_are_externally_owned_for_existence(self):
        errors = []
        relation_validate.validate_relation(
            {
                "from": "idea:budget-control",
                "type": "tested_by",
                "to": "exp:budget-ablation",
                "evidence": "The experiment tests the budget-control idea.",
                "confidence": "medium",
                "source_packets": ["p1"],
                "source_nodes": ["idea:budget-control", "exp:budget-ablation"],
                "evidence_level": "fulltext",
                "read_scope": "p1:pages:1-2",
            },
            nodes={},
            packets={"p1": {"evidence_level": "fulltext"}},
            errors=errors,
        )
        self.assertEqual([], errors)

    def test_review_table_must_match_deeply_extracted_papers(self):
        with tempfile.TemporaryDirectory() as temporary:
            review = Path(temporary) / "review.md"
            review.write_text(
                "| Paper | Venue | Method | Key Result | Relevance to Us | Evidence Level | Source |\n"
                "|---|---|---|---|---|---|---|\n"
                "| Paper One | arXiv preprint | Method | Result | Direct | Full text | arXiv |\n\n"
                + "\n\n".join(["A substantive landscape paragraph that contains enough detail for deterministic validation."] * 3),
                encoding="utf-8",
            )
            self.assertEqual([], synthesis_compile.validate_review(review, [{"title": "Paper One"}]))
            errors = synthesis_compile.validate_review(review, [{"title": "Paper One"}, {"title": "Paper Two"}])
            self.assertIn("review_papers_missing", {error["code"] for error in errors})


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import research_note_tasks


class NoteTaskTests(unittest.TestCase):
    def write_json(self, path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def valid_note(self, title="Paper One"):
        bodies = {
            "Abstract": "这篇论文研究推理预算控制问题，并概述核心方法、主要结果及其在受限计算场景中的研究意义。",
            "Challenges": "作者从固定预算不能适配不同难度的问题出发，说明统一长度限制会同时造成简单题浪费与困难题不足。",
            "Methodology": "方法根据输入难度估计可用预算，并在生成过程中持续更新剩余预算状态，以控制推理轨迹和最终答案。",
            "Results": "#### 1. Adaptive Budgeting Improves Efficiency\n\n> The method reduces token use while preserving accuracy.\n\n实验比较多个模型与基线，结果显示动态预算降低推理开销并保持任务准确率，作者将其归因于按难度分配计算。",
            "Limitations": "实验主要集中在数学推理任务，模型规模和分布变化的覆盖仍然有限，因此不能直接推广到全部开放域场景。",
            "Insights": "论文表明推理长度应作为与任务难度共同变化的资源变量，而不是一个对所有样本固定不变的生成上限。",
        }
        return f"## {title}\n\n" + "\n\n".join(f"### {name}\n{bodies[name]}" for name in research_note_tasks.NOTE_SECTIONS)

    def write_ranking(self, root, *selected_ids):
        self.write_json(root / "search" / "candidate_ranking.json", {
            "ranked_candidates": [{"id": paper_id, "selected": True} for paper_id in selected_ids]
        })

    def test_prepare_selects_only_verified_successful_arxiv_pdfs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.7\n")
            self.write_json(root / "search" / "candidate_metadata.json", {
                "candidates": [
                    {"id": "p1", "arxiv_id": "2501.00001", "title": "Paper One", "verification_status": "verified", "verification_method": "arxiv"},
                    {"id": "p2", "title": "Paper Two", "verification_status": "verified", "verification_method": "s2"},
                ]
            })
            self.write_json(root / "search" / "pdf_downloads.json", {"records": [
                {"id": "p1", "source": "arxiv", "status": "downloaded_arxiv_pdf", "path": str(pdf)},
                {"id": "p2", "source": "pdf_url", "status": "downloaded_pdf_url", "path": str(pdf)},
            ]})
            self.write_ranking(root, "p1", "p2")
            report = research_note_tasks.prepare(root)
            self.assertEqual(1, report["task_count"])
            self.assertEqual("note-only", report["tasks"][0]["mode"])
            self.assertEqual("arxiv", report["tasks"][0]["discovery_source"])
            self.assertTrue(report["tasks"][0]["note_path"].endswith("2501.00001.md"))

    def test_prepare_reuses_local_pdf_for_verified_arxiv_candidate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.7\n")
            note = root / "notes" / "papers" / "2501.00001.md"
            note.parent.mkdir(parents=True)
            note.write_text(self.valid_note(), encoding="utf-8")
            self.write_json(root / "search" / "candidate_metadata.json", {
                "candidates": [{
                    "id": "p1",
                    "arxiv_id": "2501.00001",
                    "title": "Paper One",
                    "verification_status": "verified",
                    "verification_method": "arxiv",
                }]
            })
            self.write_json(root / "search" / "pdf_downloads.json", {"records": [{
                "id": "p1",
                "source": "local_pdf_path",
                "status": "reused_local_pdf",
                "path": str(pdf),
            }]})
            self.write_ranking(root, "p1")

            report = research_note_tasks.prepare(root)

            self.assertEqual(1, report["task_count"])
            self.assertEqual(0, report["pending_count"])
            self.assertEqual("reusable", report["tasks"][0]["status"])

    def test_prepare_marks_structurally_invalid_existing_note_pending(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.7\n")
            note = root / "notes" / "papers" / "2501.00001.md"
            note.parent.mkdir(parents=True)
            note.write_text("## Paper One\n\n### Abstract\ntoo short", encoding="utf-8")
            self.write_json(root / "search" / "candidate_metadata.json", {"candidates": [{
                "id": "p1", "arxiv_id": "2501.00001", "title": "Paper One",
                "verification_status": "verified", "verification_method": "arxiv",
            }]})
            self.write_json(root / "search" / "pdf_downloads.json", {"records": [{
                "id": "p1", "status": "reused_local_pdf", "path": str(pdf),
            }]})
            self.write_ranking(root, "p1")
            report = research_note_tasks.prepare(root)
            self.assertEqual("pending", report["tasks"][0]["status"])
            self.assertEqual("note_invalid", report["tasks"][0]["reason"])
            self.assertTrue(report["tasks"][0]["prepared_note_sha256"])

    def test_validate_requires_only_markdown_note(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            note = root / "notes" / "papers" / "2501.00001.md"
            note.parent.mkdir(parents=True)
            note.write_text(self.valid_note(), encoding="utf-8")
            self.write_json(root / "synthesis" / "paper_note_tasks.json", {"tasks": [{
                "id": "p1", "arxiv_id": "2501.00001", "title": "Paper One", "note_path": str(note)
            }]})
            code, report = research_note_tasks.validate(root)
            self.assertEqual(0, code)
            self.assertEqual(1, report["valid_count"])
            self.assertFalse((note.parent / "note.json").exists())
            wiki = json.loads((root / "synthesis" / "wiki_notes.json").read_text())
            self.assertEqual(str(note), wiki[0]["note_path"])
            tasks = json.loads((root / "synthesis" / "paper_note_tasks.json").read_text())
            self.assertEqual("reusable", tasks["tasks"][0]["status"])
            self.assertEqual("validated_note", tasks["tasks"][0]["reason"])

    def test_prepare_marks_note_stale_when_pdf_input_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "paper.pdf"
            pdf.write_bytes(b"%PDF-1.7\nfirst")
            note = root / "notes" / "papers" / "2501.00001.md"
            note.parent.mkdir(parents=True)
            note.write_text(self.valid_note(), encoding="utf-8")
            self.write_json(root / "search" / "candidate_metadata.json", {"candidates": [{
                "id": "p1", "arxiv_id": "2501.00001", "title": "Paper One",
                "verification_status": "verified", "verification_method": "arxiv",
            }]})
            self.write_json(root / "search" / "pdf_downloads.json", {"records": [{
                "id": "p1", "status": "reused_local_pdf", "path": str(pdf),
            }]})
            self.write_ranking(root, "p1")
            first = research_note_tasks.prepare(root)
            self.write_json(root / "synthesis" / "validation" / "paper_notes.json", {"records": [{
                "id": "p1", "status": "valid", "input_sha256": first["tasks"][0]["input_sha256"],
            }]})
            pdf.write_bytes(b"%PDF-1.7\nchanged")
            second = research_note_tasks.prepare(root)
            self.assertEqual("pending", second["tasks"][0]["status"])
            self.assertEqual("stale_input", second["tasks"][0]["reason"])

            code, validation = research_note_tasks.validate(root)
            self.assertEqual(2, code)
            self.assertIn("pending_note_unchanged:stale_input", validation["records"][0]["errors"])
            unchanged = json.loads((root / "synthesis" / "paper_note_tasks.json").read_text())
            self.assertEqual("pending", unchanged["tasks"][0]["status"])

            note.write_text(self.valid_note() + "\n", encoding="utf-8")
            code, validation = research_note_tasks.validate(root)
            self.assertEqual(0, code)
            refreshed = json.loads((root / "synthesis" / "paper_note_tasks.json").read_text())
            self.assertEqual("reusable", refreshed["tasks"][0]["status"])

    def test_validate_rejects_out_of_order_sections(self):
        with tempfile.TemporaryDirectory() as temporary:
            note = Path(temporary) / "note.md"
            order = ("Results", "Abstract", "Challenges", "Methodology", "Limitations", "Insights")
            note.write_text("## Paper\n\n" + "\n\n".join(f"### {name}\nx" for name in order), encoding="utf-8")
            errors = research_note_tasks.validate_note(note, "Paper")
            self.assertIn("section_order_invalid", errors)


if __name__ == "__main__":
    unittest.main()

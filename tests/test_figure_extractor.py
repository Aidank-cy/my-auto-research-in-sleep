import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from tools import figure_extractor


class FigureExtractorTests(unittest.TestCase):
    def test_remote_file_extracts_and_downloads_reported_artifacts(self):
        extraction = {
            "metadata_filename": "paper.json",
            "figures": [{"renderURL": "figures/figure-1.png"}],
            "tables": [{"renderURL": "tables/table-1.png"}],
        }
        post_response = Mock()
        post_response.raise_for_status.return_value = None
        post_response.json.return_value = {"success": True, "data": extraction}

        def get_response(url, stream):
            response = Mock()
            response.raise_for_status.return_value = None
            response.iter_content.return_value = [url.encode("utf-8")]
            return response

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pdf = root / "paper.pdf"
            output = root / "output"
            pdf.write_bytes(b"%PDF-1.4\n")

            with (
                patch.object(figure_extractor.requests, "post", return_value=post_response) as post,
                patch.object(figure_extractor.requests, "get", side_effect=get_response) as get,
            ):
                result = figure_extractor.RemoteExtractor("http://extractor.test").extract_file(
                    str(pdf), str(output)
                )

            self.assertEqual(extraction, result)
            self.assertEqual(1, post.call_count)
            self.assertEqual(3, get.call_count)
            self.assertEqual(
                {"paper.json", "figure-1.png", "table-1.png"},
                {path.name for path in output.iterdir()},
            )


if __name__ == "__main__":
    unittest.main()

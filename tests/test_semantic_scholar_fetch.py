import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.error import HTTPError

from tools import semantic_scholar_fetch as s2


class SemanticScholarFetchTests(unittest.TestCase):
    def test_unauthenticated_requests_default_to_two_second_interval(self):
        with patch.dict(os.environ, {"SEMANTIC_SCHOLAR_API_KEY": ""}, clear=False):
            self.assertEqual(s2._minimum_interval(), 2.0)

    def test_search_paginates_at_api_page_limit(self):
        calls = []

        def fake_request(url):
            calls.append(url)
            query = dict(item.split("=", 1) for item in url.split("?", 1)[1].split("&"))
            offset = int(query["offset"])
            limit = int(query["limit"])
            page = [{"paperId": str(i), "title": f"Paper {i}"} for i in range(offset, min(offset + limit, 125))]
            return {"total": 125, "data": page}

        with patch.object(s2, "_request_json", side_effect=fake_request):
            result = s2.search("topic", max_results=125)

        self.assertEqual(len(result["data"]), 125)
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("limit=100" in url or "limit=25" in url for url in calls))

    def test_search_surfaces_server_error_without_fallback(self):
        with patch.object(s2, "_request_json", side_effect=RuntimeError("HTTP 500: Internal Server Error")), patch.object(
            s2,
            "search_bulk",
            return_value={"api_returned": 1, "returned": 1, "data": [{"paperId": "p1"}]},
        ) as bulk:
            with self.assertRaisesRegex(RuntimeError, "HTTP 500"):
                s2.search("topic", max_results=1)

        bulk.assert_not_called()

    def test_references_keep_external_ids(self):
        payload = {"data": [{"citedPaper": {"paperId": "hash", "externalIds": {"ArXiv": "2401.12345"}, "title": "A"}}]}
        with patch.object(s2, "_request_json", return_value=payload):
            result = s2.get_references("ARXIV:2502.00001")

        self.assertEqual(result[0]["paperId"], "hash")
        self.assertEqual(result[0]["externalIds"]["ArXiv"], "2401.12345")

    def test_unauthenticated_429_explains_required_api_key(self):
        error = HTTPError(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b'{"message":"Too Many Requests"}'),
        )
        with tempfile.TemporaryDirectory() as tmpdir, patch.dict(
            os.environ,
            {
                "S2_RATE_LIMIT_STATE_PATH": os.path.join(tmpdir, "rate-limit.json"),
                "SEMANTIC_SCHOLAR_API_KEY": "",
            },
            clear=False,
        ), patch.object(s2.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "no SEMANTIC_SCHOLAR_API_KEY is configured"):
                s2._request_json("https://example.test", retries=0)

    def test_terminal_429_still_extends_shared_cooldown(self):
        error = HTTPError(
            "https://api.semanticscholar.org/graph/v1/paper/search",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b'{"message":"Too Many Requests"}'),
        )
        with patch.object(s2, "_reserve_request_slot"), patch.object(
            s2, "_extend_global_cooldown"
        ) as extend, patch.object(s2.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                s2._request_json("https://example.test", retries=0)

        self.assertGreaterEqual(extend.call_args.args[0], 2.0)

    def test_retry_after_http_date_is_obeyed(self):
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=90)
        value = retry_at.strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.assertGreater(s2._retry_after_seconds(value, 2.0), 80.0)


if __name__ == "__main__":
    unittest.main()

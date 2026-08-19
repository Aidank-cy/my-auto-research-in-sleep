import json
import unittest
from unittest.mock import Mock, patch

from tools import huggingface_fetch


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class HuggingFaceFetchTests(unittest.TestCase):
    def test_zero_retries_still_performs_initial_request(self):
        payload = {"id": "2501.00001", "title": "Paper", "upvotes": 4, "submittedOnDailyAt": None}
        with patch.object(huggingface_fetch.urllib.request, "urlopen", return_value=_Response(payload)) as fetch:
            result = huggingface_fetch.paper("2501.00001", retries=0, delay=0)

        self.assertEqual(1, fetch.call_count)
        self.assertTrue(result["on_huggingface"])
        self.assertEqual(4, result["upvotes"])

    def test_null_payload_is_a_missing_community_signal(self):
        with patch.object(huggingface_fetch.urllib.request, "urlopen", return_value=_Response(None)):
            result = huggingface_fetch.paper("2501.00001", retries=0, delay=0)

        self.assertFalse(result["on_huggingface"])
        self.assertEqual(0, result["upvotes"])


if __name__ == "__main__":
    unittest.main()

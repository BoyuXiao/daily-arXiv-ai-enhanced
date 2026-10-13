import argparse
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

import enhance


AI = {
    "tldr": "A useful summary",
    "motivation": "Reduce training cost",
    "method": "Balance expert workloads",
    "result": "Lower latency",
    "conclusion": "The method improves efficiency",
}


class EnhancementTests(unittest.TestCase):
    @patch("enhance.is_sensitive", return_value=False)
    def test_api_failure_is_not_converted_to_a_successful_placeholder(self, _filter):
        chain = Mock()
        chain.invoke.side_effect = requests.HTTPError("401 Unauthorized")
        with self.assertRaisesRegex(RuntimeError, "AI request failed"):
            enhance.process_single_item(chain, {"id": "2610.1", "summary": "Abstract"}, "Chinese")

    @patch("enhance.is_sensitive", return_value=False)
    def test_malformed_structured_output_fails(self, _filter):
        from langchain_core.exceptions import OutputParserException

        chain = Mock()
        chain.invoke.side_effect = OutputParserException("Invalid JSON")
        with self.assertRaisesRegex(RuntimeError, "AI request failed"):
            enhance.process_single_item(chain, {"id": "2610.1", "summary": "Abstract"}, "Chinese")

    @patch("enhance.is_sensitive", return_value=False)
    @patch("enhance.requests.post")
    def test_responses_mode_survives_upstream_merge(self, post, _filter):
        response = Mock(status_code=200, ok=True)
        response.json.return_value = {
            "output": [{"content": [{"type": "output_text", "text": json.dumps(AI)}]}]
        }
        post.return_value = response
        with patch.dict(os.environ, {
            "OPENAI_API_MODE": "responses",
            "OPENAI_API_KEY": " test-key ",
            "OPENAI_BASE_URL": " https://relay.example/v1/ ",
        }, clear=True):
            result = enhance.process_all_items(
                [{"id": "2610.1", "summary": "Abstract"}], "test-model", "Chinese", 1,
            )
        self.assertEqual(AI, result[0]["AI"])
        self.assertEqual("https://relay.example/v1/responses", post.call_args.args[0])
        self.assertEqual("Bearer test-key", post.call_args.kwargs["headers"]["Authorization"])

    @patch("enhance.process_single_item", side_effect=RuntimeError("API failed"))
    def test_worker_errors_fail_the_entire_batch(self, _process):
        with patch.dict(os.environ, {
            "OPENAI_API_MODE": "responses",
            "OPENAI_API_KEY": "test-key",
            "OPENAI_BASE_URL": "https://relay.example/v1",
        }, clear=True):
            with self.assertRaisesRegex(RuntimeError, r"1 paper\(s\) failed"):
                enhance.process_all_items([{"id": "2610.1"}], "test-model", "Chinese", 1)

    def test_failed_or_empty_rerun_preserves_existing_output(self):
        for failure in (RuntimeError("API failed"), None):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "2026-10-13.jsonl"
                source.write_text(json.dumps({"id": "2610.1", "summary": "Abstract"}) + "\n")
                target = source.with_name("2026-10-13_AI_enhanced_Chinese.jsonl")
                target.write_text("existing valid output\n")
                args = argparse.Namespace(data=str(source), max_workers=1)
                with patch("enhance.parse_args", return_value=args), patch.dict(os.environ, {"LANGUAGE": "Chinese"}), patch(
                    "enhance.process_all_items", side_effect=failure, return_value=[None],
                ):
                    with self.assertRaises(RuntimeError):
                        enhance.main()
                self.assertEqual("existing valid output\n", target.read_text())


if __name__ == "__main__":
    unittest.main()

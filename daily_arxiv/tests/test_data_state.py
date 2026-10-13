import json
from pathlib import Path
import tempfile
import unittest

from data_state import AI_FIELDS, FAILURE_VALUES, validate_file


class DataStateTests(unittest.TestCase):
    def test_raw_output_without_ai_is_complete_even_with_legacy_duplicate_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "papers.jsonl"
            paper = {"id": "2610.1", "title": "Paper", "summary": "Original abstract", "authors": ["Author"], "categories": ["cs.LG"]}
            row = json.dumps(paper)
            path.write_text(row + "\n" + row + "\n")
            self.assertEqual(1, validate_file(path, require_ai=False))

    def test_raw_output_requires_usable_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "papers.jsonl"
            path.write_text(json.dumps({"id": "2610.1"}))
            with self.assertRaisesRegex(ValueError, "metadata"):
                validate_file(path, require_ai=False)

    def test_valid_output_can_be_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "papers.jsonl"
            path.write_text(json.dumps({"id": "2610.1", "AI": {field: "Summary" for field in AI_FIELDS}}))
            self.assertEqual(1, validate_file(path))

    def test_empty_and_legacy_failed_outputs_must_be_retried(self):
        invalid = ["", "{invalid json", json.dumps({"id": "2610.1"})]
        for value in FAILURE_VALUES:
            invalid.append(json.dumps({"id": "2610.1", "AI": {field: value for field in AI_FIELDS}}))
        for contents in invalid:
            with self.subTest(contents=contents), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "papers.jsonl"
                path.write_text(contents)
                with self.assertRaises(ValueError):
                    validate_file(path)

    def test_duplicate_ids_are_not_treated_as_valid_daily_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "papers.jsonl"
            row = json.dumps({"id": "2610.1", "AI": {field: "Summary" for field in AI_FIELDS}})
            path.write_text(row + "\n" + row + "\n")
            with self.assertRaisesRegex(ValueError, "duplicate"):
                validate_file(path)


if __name__ == "__main__":
    unittest.main()

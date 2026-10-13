import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from data_state import AI_FIELDS


SCRIPT = Path(__file__).resolve().parents[1] / "daily_arxiv" / "check_stats.py"


class DeduplicationTests(unittest.TestCase):
    def test_only_successful_history_is_skipped_and_categories_are_deduplicated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            (root / "crawler").mkdir()
            today = root / "data" / "2026-10-13.jsonl"
            papers = [{"id": value} for value in ("published", "retry", "new", "new")]
            today.write_text("".join(json.dumps(paper) + "\n" for paper in papers))
            history = [
                {"id": "published", "AI": {field: "Valid summary" for field in AI_FIELDS}},
                {"id": "retry", "AI": {field: "Processing failed" for field in AI_FIELDS}},
            ]
            (root / "data" / "2026-10-12_AI_enhanced_Chinese.jsonl").write_text(
                "".join(json.dumps(paper) + "\n" for paper in history)
            )
            env = {**os.environ, "CRAWL_DATE": "2026-10-13", "LANGUAGE": "Chinese", "TZ": "Asia/Shanghai"}
            result = subprocess.run([sys.executable, str(SCRIPT)], cwd=root / "crawler", env=env, capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(["retry", "new"], [json.loads(line)["id"] for line in today.read_text().splitlines()])

    def test_corrupt_crawl_fails_instead_of_claiming_no_new_papers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            (root / "crawler").mkdir()
            (root / "data" / "2026-10-13.jsonl").write_text("{invalid json")
            result = subprocess.run(
                [sys.executable, str(SCRIPT)], cwd=root / "crawler",
                env={**os.environ, "CRAWL_DATE": "2026-10-13"}, capture_output=True, text=True,
            )
            self.assertEqual(2, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()

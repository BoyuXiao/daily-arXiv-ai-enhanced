import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from data_state import AI_FIELDS, validate_file
from prepare_publication import prepare_publication


CONVERTER = Path(__file__).resolve().parents[2] / "to_md" / "convert.py"


def paper(paper_id):
    return {
        "id": paper_id, "title": f"Paper {paper_id}", "summary": "Original abstract",
        "authors": ["An Author"], "categories": ["cs.LG"],
        "abs": f"https://arxiv.org/abs/{paper_id}",
    }


def write_papers(path, papers):
    path.write_text("".join(json.dumps(value) + "\n" for value in papers))


class PublicationTests(unittest.TestCase):
    def test_disabled_ai_publishes_metadata_and_replaces_stale_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "2026-10-13.jsonl"
            write_papers(source, [paper("2610.1"), paper("2610.1")])
            stale = source.with_name("2026-10-13_AI_enhanced_Chinese.jsonl")
            write_papers(stale, [{**paper("2610.1"), "AI": {field: "Processing failed" for field in AI_FIELDS}}])
            output = prepare_publication(source, "Chinese")
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual([paper("2610.1")], rows)
            self.assertEqual(1, validate_file(output, require_ai=False))

    def test_failed_ai_file_does_not_block_publishing(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "2026-10-13.jsonl"
            write_papers(source, [paper("2610.1")])
            enhanced = source.with_name("2026-10-13_AI_enhanced_Chinese.jsonl")
            enhanced.write_text("{invalid json")
            output = prepare_publication(source, "Chinese", enhanced)
            self.assertEqual(paper("2610.1"), json.loads(output.read_text()))

    def test_optional_ai_is_merged_without_losing_unenhanced_papers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "2026-10-13.jsonl"
            write_papers(source, [paper("2610.1"), paper("2610.2")])
            enhanced = source.with_name("2026-10-13_AI_enhanced_Chinese.jsonl")
            ai = {field: "Useful summary" for field in AI_FIELDS}
            write_papers(enhanced, [{**paper("2610.1"), "AI": ai}])
            output = prepare_publication(source, "Chinese", enhanced)
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            self.assertEqual(2, len(rows))
            self.assertEqual(ai, rows[0]["AI"])
            self.assertNotIn("AI", rows[1])

    def test_markdown_includes_original_abstract_without_ai(self):
        with tempfile.TemporaryDirectory(prefix="arxiv_raw_test_") as directory:
            source = Path(directory) / "2026-10-13.jsonl"
            write_papers(source, [paper("2610.1")])
            result = subprocess.run([sys.executable, str(CONVERTER), "--data", str(source)], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stderr)
            markdown = source.with_suffix(".md").read_text()
            self.assertIn("Paper 2610.1", markdown)
            self.assertIn("Original abstract", markdown)
            self.assertNotIn("TL;DR", markdown)


if __name__ == "__main__":
    unittest.main()

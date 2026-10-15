import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

# Existing test commands add daily_arxiv/daily_arxiv for standalone scripts.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from daily_arxiv.crawl import validate_crawl


PROJECT = Path(__file__).resolve().parents[1]
REPLAY = r'''
import os
from unittest.mock import patch

from scrapy.http import HtmlResponse
from scrapy.settings import Settings
from daily_arxiv import crawl
from daily_arxiv import metadata
from daily_arxiv.pipelines import DailyArxivPipeline

scenario = os.environ["CRAWL_TEST_SCENARIO"]
entry = "<dt><a href='/abs/2610.19308' title='Abstract'>arXiv:2610.19308</a></dt><dd><div class='list-subjects'>Machine Learning (cs.LG)</div></dd>"
date = "<h3>Showing new listings for Thursday, 15 October 2026</h3>"

class FixtureDownloader:
    def process_request(self, request, spider):
        if scenario == "malformed":
            body = "<p>Service unavailable</p>"
        elif scenario == "http_error":
            return HtmlResponse(request.url, status=503, body=b"Unavailable", request=request)
        elif "/new?" in request.url:
            section = "Replacement" if scenario == "empty" else "New"
            body = date + f"<dl><h3>{section} submissions (showing 1 of 1 entries)</h3>{entry}</dl>"
        else:
            body = "<dl><h3>Thu, 15 Oct 2026</h3><p>No updates for this time period.</p></dl>"
        return HtmlResponse(request.url, body=f"<div id='dlpage'>{body}</div>".encode(), encoding="utf8", request=request)

class FixtureMetadata:
    def process_item(self, item, spider):
        if scenario == "pipeline_error":
            raise RuntimeError("Metadata service unavailable")
        if scenario in ("metadata_error", "metadata_recovered"):
            return item
        return {**item, "title": "Paper title", "summary": "Original abstract", "authors": ["Author"]}

class FixtureResponse:
    def __init__(self, status):
        self.status_code = status
        self.headers = {"Retry-After": "60"} if status == 429 else {}
        self.content = b"<feed xmlns='http://www.w3.org/2005/Atom'><entry><id>https://arxiv.org/abs/2610.19308v1</id><title>Paper title</title><summary>Original abstract</summary><author><name>Author</name></author><category term='cs.LG'/></entry></feed>"
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False

class FixtureSession:
    def __init__(self):
        self.headers = {}
        self.calls = 0
    def get(self, *args, **kwargs):
        self.calls += 1
        status = 429 if scenario == "metadata_error" or self.calls == 1 else 200
        return FixtureResponse(status)
    def close(self):
        pass

settings = Settings({
    "DOWNLOADER_MIDDLEWARES": {FixtureDownloader: 1},
    "ITEM_PIPELINES": {FixtureMetadata: 1},
    "ROBOTSTXT_OBEY": False,
    "RETRY_ENABLED": False,
    "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
})
with patch.object(crawl, "get_project_settings", return_value=settings), patch.object(
    metadata.requests, "Session", FixtureSession,
), patch.object(metadata.MetadataClient, "wait"):
    raise SystemExit(crawl.main())
'''


class CrawlValidationTests(unittest.TestCase):
    def crawler(self, ids=(), completed=("cs.LG", "cs.AI"), **stats):
        values = {"finish_reason": "finished", "item_scraped_count": len(ids), **stats}
        return SimpleNamespace(
            spider=SimpleNamespace(
                target_categories={"cs.LG", "cs.AI"}, completed_categories=set(completed), seen_ids=set(ids),
            ),
            stats=SimpleNamespace(get_stats=lambda: values),
        )

    def test_verified_empty_crawl_does_not_require_a_feed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.jsonl"
            self.assertEqual(0, validate_crawl(self.crawler(), path))

    def test_partial_or_failed_crawl_cannot_be_treated_as_empty(self):
        cases = [
            self.crawler(completed=("cs.LG",)),
            self.crawler(finish_reason="shutdown"),
            self.crawler(**{"spider_exceptions/ValueError": 1}),
            self.crawler(**{"feedexport/failed_count/FileFeedStorage": 1}),
            self.crawler(ids=("2610.19308",), item_scraped_count=0),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for crawler in cases:
                with self.subTest(crawler=crawler), self.assertRaises(ValueError):
                    validate_crawl(crawler, Path(directory) / "missing.jsonl")

    def test_metadata_and_export_counts_must_match_discovered_papers(self):
        paper = {
            "id": "2610.19308", "title": "Title", "summary": "Abstract",
            "authors": ["Author"], "categories": ["cs.LG"],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "papers.jsonl"
            path.write_text(json.dumps(paper) + "\n")
            self.assertEqual(1, validate_crawl(self.crawler(ids=(paper["id"],)), path))
            with self.assertRaisesRegex(ValueError, "exported"):
                validate_crawl(self.crawler(ids=(paper["id"], "2610.19405")), path)
            path.write_text(json.dumps({"id": paper["id"]}))
            with self.assertRaisesRegex(ValueError, "metadata"):
                validate_crawl(self.crawler(ids=(paper["id"],)), path)


class CrawlCommandTests(unittest.TestCase):
    def test_command_outputs_and_atomic_replacement(self):
        for scenario in ("success", "empty", "malformed", "http_error", "pipeline_error", "metadata_error", "metadata_recovered"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                output = root / "papers.jsonl"
                output.write_text("previous published data\n")
                report = root / "report.json"
                github_output = root / "github-output.txt"
                result = subprocess.run(
                    [sys.executable, "-c", REPLAY, "--output", str(output),
                     "--report", str(report), "--github-output", str(github_output)],
                    cwd=PROJECT, capture_output=True, text=True, timeout=20,
                    env={**os.environ, "PYTHONPATH": str(PROJECT), "CATEGORIES": "cs.LG",
                         "CRAWL_TEST_SCENARIO": scenario},
                )
                expected_code = 0 if scenario in ("success", "empty", "metadata_recovered") else 1
                self.assertEqual(expected_code, result.returncode, result.stdout + result.stderr)
                status = json.loads(report.read_text())["status"]
                if scenario in ("success", "metadata_recovered"):
                    self.assertEqual("success", status)
                    self.assertEqual("2610.19308", json.loads(output.read_text())["id"])
                    self.assertEqual("has_papers=true\npaper_count=1\n", github_output.read_text())
                    if scenario == "metadata_recovered":
                        self.assertEqual(2, json.loads(report.read_text())["metadata"]["api_requests"])
                else:
                    self.assertEqual("previous published data\n", output.read_text())
                    if scenario == "empty":
                        self.assertEqual("no_new_papers", status)
                        self.assertEqual("has_papers=false\npaper_count=0\n", github_output.read_text())
                    else:
                        self.assertEqual("failed", status)
                        self.assertFalse(github_output.exists())
                self.assertEqual([], list(root.glob(".papers.*.jsonl")))


if __name__ == "__main__":
    unittest.main()

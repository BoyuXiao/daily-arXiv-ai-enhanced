"""Crawl atomically and distinguish a verified empty listing from a failed crawl."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from scrapy.crawler import CrawlerProcess
from scrapy.utils.project import get_project_settings

from .data_state import validate_file
from .metadata import complete_metadata
from .spiders.arxiv import ArxivSpider


def validate_crawl(crawler, path, require_metadata=True):
    spider = crawler.spider
    stats = crawler.stats.get_stats()
    if stats.get("finish_reason") != "finished":
        raise ValueError(f"Crawl did not finish normally: {stats.get('finish_reason')}")
    if any(value for key, value in stats.items() if key.startswith("spider_exceptions/")):
        raise ValueError("Listing parsing failed; see the spider error above")
    missing = spider.target_categories - spider.completed_categories
    if missing:
        raise ValueError(f"Incomplete crawl for categories: {', '.join(sorted(missing))}")
    if any(value for key, value in stats.items() if key.startswith("feedexport/failed_count/")):
        raise ValueError("Failed to export the crawled papers")

    expected = len(spider.seen_ids)
    scraped = stats.get("item_scraped_count", 0)
    if scraped != expected:
        raise ValueError(f"Discovered {expected} papers but exported only {scraped} candidates")
    if expected:
        with path.open(encoding="utf-8") as stream:
            papers = [json.loads(line) for line in stream if line.strip()]
        ids = {paper.get("id") for paper in papers if isinstance(paper, dict)}
        if ids != spider.seen_ids or len(papers) != expected:
            raise ValueError(f"Expected {expected} distinct paper IDs but exported {len(ids)}")
        if require_metadata:
            validate_file(path, require_ai=False)
    elif path.exists() and path.read_text(encoding="utf-8").strip():
        raise ValueError("Unexpected output from a crawl with no new papers")
    return expected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--cache", type=Path, help="Per-date metadata checkpoint for failed-run recovery")
    parser.add_argument("--github-output", type=Path, default=os.environ.get("GITHUB_OUTPUT"))
    args = parser.parse_args(argv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cache_path = args.cache or args.output.parent / ".metadata-cache" / args.output.name
    with tempfile.NamedTemporaryFile(
        prefix=f".{args.output.stem}.", suffix=".jsonl", dir=args.output.parent, delete=False,
    ) as stream:
        temporary = Path(stream.name)

    crawler = None
    report = {"status": "failed"}
    try:
        settings = get_project_settings()
        settings.set("FEEDS", {str(temporary): {"format": "jsonlines", "overwrite": True}}, priority="cmdline")
        settings.set("LOG_LEVEL", "INFO", priority="cmdline")
        settings.set("TELNETCONSOLE_ENABLED", False, priority="cmdline")
        process = CrawlerProcess(settings)
        crawler = process.create_crawler(ArxivSpider)
        errors = []
        process.crawl(crawler).addErrback(lambda failure: errors.append(failure.getErrorMessage()))
        process.start()
        if errors:
            raise ValueError("; ".join(errors))
        count = validate_crawl(crawler, temporary, require_metadata=False)
        if count:
            with temporary.open(encoding="utf-8") as stream:
                candidates = [json.loads(line) for line in stream if line.strip()]
            report["metadata"] = {}
            papers = complete_metadata(
                candidates, getattr(crawler.spider, "paper_metadata", {}), cache_path, report["metadata"],
            )
            temporary.write_text("".join(json.dumps(paper, ensure_ascii=False) + "\n" for paper in papers), encoding="utf-8")
            validate_file(temporary, require_ai=False)
            temporary.replace(args.output)
            report.update(status="success", paper_count=count)
            print(f"Crawled and validated {count} papers: {args.output}")
        else:
            report.update(status="no_new_papers", paper_count=0)
            print("No new papers after checking the announcement dates in /new and /recent; publication skipped.")
        if args.github_output:
            with args.github_output.open("a", encoding="utf-8") as stream:
                stream.write(f"has_papers={'true' if count else 'false'}\npaper_count={count}\n")
        return 0
    except Exception as error:
        report["status"] = "failed"
        report["error"] = str(error)
        print(f"Crawling failed: {error}", file=sys.stderr)
        return 1
    finally:
        temporary.unlink(missing_ok=True)
        if args.report:
            stats = getattr(crawler, "stats", None)
            spider = getattr(crawler, "spider", None)
            report["stats"] = stats.get_stats() if stats else {}
            report["pages"] = getattr(spider, "listing_pages", [])
            report["completed_categories"] = sorted(getattr(spider, "completed_categories", []))
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())

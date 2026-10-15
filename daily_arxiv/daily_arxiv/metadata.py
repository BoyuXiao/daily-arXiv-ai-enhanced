"""Reuse complete metadata and fetch only missing papers in rate-limited batches."""

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import logging
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree

import requests

from .data_state import has_valid_raw


logger = logging.getLogger(__name__)
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
API_URL = "https://export.arxiv.org/api/query"


class MetadataFetchError(RuntimeError):
    pass


def retry_after_seconds(value):
    if not value:
        return 0
    try:
        return max(0, int(value))
    except ValueError:
        try:
            until = parsedate_to_datetime(value)
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            return max(0, (until - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0


def parse_metadata(body, requested_ids):
    root = ElementTree.fromstring(body)
    if root.tag != ATOM + "feed":
        raise ValueError("The arXiv metadata API did not return an Atom feed")
    papers = {}
    for entry in root.findall(ATOM + "entry"):
        path = urlsplit(entry.findtext(ATOM + "id", "")).path
        if not path.startswith("/abs/"):
            raise ValueError(f"Invalid API entry: {entry.findtext(ATOM + 'summary', path)}")
        paper_id = re.sub(r"v\d+$", "", path[5:])
        if paper_id not in requested_ids:
            raise ValueError(f"API returned unrequested paper {paper_id}")
        categories = list(dict.fromkeys(category.get("term", "") for category in entry.findall(ATOM + "category")))
        primary = entry.find(ARXIV + "primary_category")
        if primary is not None and primary.get("term"):
            term = primary.get("term")
            categories = [term, *(category for category in categories if category != term)]
        paper = {
            "id": paper_id,
            "title": " ".join(entry.findtext(ATOM + "title", "").split()),
            "summary": entry.findtext(ATOM + "summary", "").strip(),
            "authors": [author.findtext(ATOM + "name", "").strip() for author in entry.findall(ATOM + "author")],
            "categories": categories,
            "comment": entry.findtext(ARXIV + "comment"),
        }
        if has_valid_raw(paper):
            papers[paper_id] = paper
    return papers


class MetadataCache:
    """A per-crawl-date checkpoint; an interrupted last line is safe to ignore."""

    def __init__(self, path):
        self.path = Path(path)
        self.papers = {}
        if self.path.exists():
            with self.path.open(encoding="utf-8") as stream:
                for line in stream:
                    if not line.strip():
                        continue
                    try:
                        paper = json.loads(line)
                        if isinstance(paper, dict) and has_valid_raw(paper):
                            self.papers[paper["id"]] = paper
                    except (ValueError, TypeError):
                        logger.warning("Ignoring an incomplete metadata checkpoint row in %s", self.path)

    def save(self, papers):
        changed = [paper for paper in papers.values() if self.papers.get(paper["id"]) != paper]
        if not changed:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as stream:
            # Separate a possibly interrupted final row before appending new rows.
            stream.write("\n")
            for paper in changed:
                stream.write(json.dumps(paper, ensure_ascii=False) + "\n")
        self.papers.update((paper["id"], paper) for paper in changed)


class MetadataClient:
    batch_size = 100
    max_attempts = 5
    min_interval = 3.0
    backoff_seconds = 60.0

    def __init__(self):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "daily-arxiv/0.1 (+https://github.com/BoyuXiao/daily-arXiv-ai-enhanced)"
        self.next_request_at = 0
        self.stats = {"api_requests": 0, "api_retries": 0, "api_papers": 0}

    def wait(self):
        while (remaining := self.next_request_at - time.monotonic()) > 0:
            time.sleep(min(remaining, 30))

    def fetch_batch(self, ids, on_success):
        pending = list(ids)
        found = {}
        for attempt in range(self.max_attempts):
            self.wait()
            retry_after = 0
            papers = {}
            self.stats["api_requests"] += 1
            logger.info("Requesting metadata for %d papers (attempt %d/%d)", len(pending), attempt + 1, self.max_attempts)
            try:
                with self.session.get(
                    API_URL, params={"id_list": ",".join(pending), "start": 0, "max_results": len(pending)},
                    timeout=(10, 120),
                ) as response:
                    if response.status_code == 429 or 500 <= response.status_code < 600:
                        error = f"HTTP {response.status_code}"
                        retry_after = retry_after_seconds(response.headers.get("Retry-After"))
                    elif response.status_code != 200:
                        raise MetadataFetchError(f"Metadata API returned HTTP {response.status_code}")
                    else:
                        papers = parse_metadata(response.content, set(pending))
                        error = f"API omitted usable metadata for {len(pending) - len(papers)} papers"
            except (requests.RequestException, ElementTree.ParseError, ValueError) as exc:
                error = str(exc)
            finally:
                self.next_request_at = time.monotonic() + self.min_interval

            if papers:
                on_success(papers)
                found.update(papers)
                self.stats["api_papers"] += len(papers)
                pending = [paper_id for paper_id in pending if paper_id not in papers]
            if not pending:
                return found
            if attempt + 1 == self.max_attempts:
                raise MetadataFetchError(f"{error}; {len(pending)} papers still missing. Successful metadata is checkpointed; rerun to resume.")

            delay = max(self.backoff_seconds * 2 ** attempt, retry_after)
            self.next_request_at = max(self.next_request_at, time.monotonic() + delay)
            self.stats["api_retries"] += 1
            logger.warning("%s; pausing all metadata requests for %.1f seconds before retrying %d papers", error, delay, len(pending))
        return found

    def close(self):
        self.session.close()


def complete_metadata(candidates, listing_metadata, cache_path, stats):
    cache = MetadataCache(cache_path)
    papers = {}
    stats.update(from_listing=0, from_cache=0, api_requests=0, api_retries=0, api_papers=0)
    for candidate in candidates:
        paper_id = candidate["id"]
        listing = listing_metadata.get(paper_id, candidate)
        if has_valid_raw(listing):
            papers[paper_id] = listing
            stats["from_listing"] += 1
        elif paper_id in cache.papers:
            papers[paper_id] = cache.papers[paper_id]
            stats["from_cache"] += 1
    cache.save(papers)
    missing = [paper["id"] for paper in candidates if paper["id"] not in papers]
    logger.info("Metadata: %d from listings, %d from checkpoint, %d missing", stats["from_listing"], stats["from_cache"], len(missing))
    if missing:
        client = MetadataClient()
        try:
            for start in range(0, len(missing), client.batch_size):
                batch = missing[start:start + client.batch_size]
                papers.update(client.fetch_batch(batch, cache.save))
        finally:
            stats.update(client.stats)
            client.close()

    # Preserve candidate order and the existing public JSONL schema.
    return [
        {**papers[candidate["id"]], "pdf": f"https://arxiv.org/pdf/{candidate['id']}",
         "abs": f"https://arxiv.org/abs/{candidate['id']}"}
        for candidate in candidates
    ]

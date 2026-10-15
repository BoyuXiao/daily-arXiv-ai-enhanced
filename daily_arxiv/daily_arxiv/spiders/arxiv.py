import os
import re
from datetime import datetime
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import scrapy


def text_content(selector):
    return " ".join(" ".join(selector.xpath(".//text()").getall()).split())


class ArxivSpider(scrapy.Spider):
    name = "arxiv"
    allowed_domains = ["arxiv.org"]
    page_size = 100

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target_categories = {
            value.strip()
            for value in os.environ.get("CATEGORIES", "cs.LG, cs.DC, cs.AI").split(",")
            if value.strip()
        }
        if not self.target_categories:
            raise ValueError("CATEGORIES must contain at least one arXiv category")
        self.start_urls = [self.list_url(cat, "new") for cat in sorted(self.target_categories)]
        self.seen_ids = set()
        self.paper_metadata = {}
        self.completed_categories = set()
        self.announced_dates = {}
        self.category_candidates = dict.fromkeys(self.target_categories, 0)
        self.new_sections = {category: {} for category in self.target_categories}
        self.recent_sections = {}
        self.refreshed_categories = set()
        self.refresh_token = uuid4().hex
        self.recent_date_seen = set()
        self.listing_pages = []

    def list_url(self, category, source):
        return f"https://arxiv.org/list/{category}/{source}?skip=0&show={self.page_size}"

    @staticmethod
    def category(response):
        return urlsplit(response.url).path.split("/")[2]

    @staticmethod
    def listing(response):
        listing = response.css("#dlpage")
        if len(listing) != 1:
            raise ValueError(f"Unrecognized arXiv listing: {response.url}")
        return listing[0]

    @staticmethod
    def section_heading(section):
        # Current pages put the heading inside dl; older layouts put it before dl.
        heading = section.xpath("./h3[1]") or section.xpath("preceding-sibling::h3[1]")
        return text_content(heading)

    @staticmethod
    def section_entries(section, heading):
        entries = section.xpath("./dt")
        shown = re.search(r"\bshowing(?: first| last)? (\d+) of\b", heading, re.IGNORECASE)
        if shown and int(shown[1]) != len(entries):
            raise ValueError(f"Incomplete arXiv section: {heading}; parsed {len(entries)} entries")
        return entries

    @staticmethod
    def section_total(heading):
        match = re.search(r"\bof (\d+) entries\b", heading)
        if not match:
            raise ValueError(f"Missing submission count: {heading}")
        return int(match[1])

    def papers(self, entries):
        papers = []
        for entry in entries:
            if "(replaced)" in text_content(entry).lower():
                continue
            link = entry.css("a[title='Abstract']::attr(href)").get()
            if not link:
                link = entry.css("a[href^='/abs/']::attr(href)").get()
            path = urlsplit(link or "").path
            description = entry.xpath("following-sibling::*[1][self::dd]")
            if not path.startswith("/abs/") or not path[5:] or not description:
                raise ValueError("arXiv entry is missing its abstract link or description")

            paper_id = re.sub(r"v\d+$", "", path[5:])
            subjects = text_content(description.css(".list-subjects"))
            # The primary subject appears first; publication groups on this order.
            categories = list(dict.fromkeys(value.strip() for value in re.findall(r"\(([^)]+)\)", subjects)))
            if not categories:
                self.logger.warning("Missing listing categories for %s; the metadata API will supply them", paper_id)
            title = text_content(description.css(".list-title")).removeprefix("Title:").strip()
            comment = text_content(description.css(".list-comments")).removeprefix("Comments:").strip()
            papers.append({
                "id": paper_id, "categories": categories,
                "title": title,
                "authors": [text_content(author) for author in description.css(".list-authors a")],
                "summary": text_content(description.css("p.mathjax")),
                "comment": comment or None,
            })
        return papers

    @staticmethod
    def next_page(response):
        current = urlsplit(response.url)
        query = parse_qs(current.query)
        skip = int(query.get("skip", ["0"])[0])
        candidates = []
        for href in response.css("#dlpage .paging a::attr(href)").getall():
            url = response.urljoin(href)
            parts = urlsplit(url)
            if (parts.netloc, parts.path) != (current.netloc, current.path):
                continue
            next_skip = int(parse_qs(parts.query).get("skip", ["0"])[0])
            if next_skip > skip:
                if "refresh" in query:
                    next_query = parse_qs(parts.query)
                    next_query["refresh"] = query["refresh"]
                    url = urlunsplit(parts._replace(query=urlencode(next_query, doseq=True)))
                candidates.append((next_skip, url))
        return min(candidates)[1] if candidates else None

    def record_page(self, response, entries, papers, headings):
        category = self.category(response)
        papers = [paper for paper in papers if not paper["categories"] or self.target_categories.intersection(paper["categories"])]
        self.category_candidates[category] += len(papers)
        unique = []
        for paper in papers:
            # A duplicate from /new may supply the abstract missing from /recent.
            # Keep it even when this ID has already been exported as a candidate.
            if all(paper.get(field) for field in ("title", "summary", "authors", "categories")):
                self.paper_metadata[paper["id"]] = dict(paper)
            if paper["id"] not in self.seen_ids:
                self.seen_ids.add(paper["id"])
                unique.append(paper)
        self.listing_pages.append({
            "url": response.url, "headings": headings, "entries": entries,
            "eligible": len(papers), "unique": len(unique),
        })
        self.logger.info(
            "Parsed %s: entries=%d, eligible=%d, unique=%d; %s",
            response.url, entries, len(papers), len(unique), "; ".join(headings),
        )
        return unique

    def check_recent(self, response, announced, reason):
        self.logger.warning("%s for %s; checking /recent for %s", reason, self.category(response), announced)
        return response.follow(
            self.list_url(self.category(response), "recent"), callback=self.parse_recent,
            cb_kwargs={"announced": announced},
        )

    def refresh_recent(self, response, announced, reason):
        category = self.category(response)
        if category in self.refreshed_categories:
            raise ValueError(f"{reason} for {category} after refreshing /recent; retry the crawl")
        self.refreshed_categories.add(category)
        self.recent_sections.pop(category, None)
        self.recent_date_seen.discard(category)
        self.logger.warning("%s for %s; refreshing /recent once", reason, category)
        return response.follow(
            f"{self.list_url(category, 'recent')}&refresh={self.refresh_token}",
            callback=self.parse_recent, cb_kwargs={"announced": announced},
        )

    def parse(self, response):
        listing = self.listing(response)
        category = self.category(response)
        date_heading = text_content(listing.xpath("./h3[1]"))
        prefix = "Showing new listings for "
        if not date_heading.startswith(prefix):
            raise ValueError(f"Missing announcement date in {response.url}")
        announced = datetime.strptime(date_heading[len(prefix):], "%A, %d %B %Y").date().isoformat()
        if self.announced_dates.setdefault(category, announced) != announced:
            raise ValueError(f"Announcement date changed during pagination for {category}")

        papers, headings = [], []
        entry_count = 0
        has_replacements = False
        consistent = True
        sections = listing.css("dl")
        if not sections and "No updates today." not in text_content(listing):
            raise ValueError(f"No recognizable submission sections in {response.url}")
        for section in sections:
            heading = self.section_heading(section)
            headings.append(heading)
            entries = self.section_entries(section, heading)
            entry_count += len(entries)
            if heading.lower().startswith("replacement submissions"):
                has_replacements = True
            elif heading.lower().startswith(("new submissions", "cross submissions", "cross-lists")):
                section_papers = self.papers(entries)
                papers.extend(section_papers)
                kind = "new" if heading.lower().startswith("new submissions") else "cross"
                total = self.section_total(heading)
                state = self.new_sections[category].setdefault(kind, {"total": total, "ids": set()})
                state["ids"].update(paper["id"] for paper in section_papers)
                if state["total"] != total or len(state["ids"]) > total:
                    consistent = False
            else:
                raise ValueError(f"Unrecognized submission section: {heading!r}")

        yield from self.record_page(response, entry_count, papers, [date_heading, *headings])
        next_url = self.next_page(response)
        complete = all(len(state["ids"]) == state["total"] for state in self.new_sections[category].values())
        if not consistent:
            yield self.check_recent(response, announced, "Submission counts changed during pagination")
        elif next_url and not has_replacements:
            yield response.follow(next_url, callback=self.parse)
        elif not complete:
            yield self.check_recent(response, announced, "Incomplete submission sections")
        elif self.category_candidates[category]:
            self.completed_categories.add(category)
        else:
            # /new can briefly contain only replacements while /recent already
            # has new papers. Verify the same announcement date before declaring
            # an empty day; never substitute papers from a previous date.
            yield self.check_recent(response, announced, "No new entries")

    def parse_recent(self, response, announced):
        listing = self.listing(response)
        category = self.category(response)
        papers, headings = [], []
        entry_count = 0
        reached_older_date = False
        sections = listing.css("dl")
        if not sections:
            raise ValueError(f"No dated sections in {response.url}")
        for section in sections:
            heading = self.section_heading(section)
            headings.append(heading)
            match = re.match(r"[A-Za-z]{3}, \d{1,2} [A-Za-z]{3} \d{4}", heading)
            if not match:
                raise ValueError(f"Unrecognized recent submission date: {heading!r}")
            date = datetime.strptime(match[0], "%a, %d %b %Y").date().isoformat()
            entries = self.section_entries(section, heading)
            entry_count += len(entries)
            if date == announced:
                if not entries and "No updates for this time period." not in text_content(section):
                    raise ValueError(f"Empty recent section without an explicit no-updates message: {heading}")
                section_papers = self.papers(entries)
                total = self.section_total(heading) if entries else 0
                state = self.recent_sections.setdefault(category, {"total": total, "ids": set()})
                if state["total"] != total:
                    yield self.refresh_recent(response, announced, "Recent submission count changed during pagination")
                    return
                state["ids"].update(paper["id"] for paper in section_papers)
                self.recent_date_seen.add(category)
                papers.extend(section_papers)
            elif date < announced:
                reached_older_date = True

        yield from self.record_page(response, entry_count, papers, headings)
        next_url = self.next_page(response)
        if next_url and not reached_older_date:
            yield response.follow(next_url, callback=self.parse_recent, cb_kwargs={"announced": announced})
        elif category not in self.recent_date_seen:
            yield self.refresh_recent(response, announced, f"Recent listing does not contain announcement date {announced}")
        elif len(self.recent_sections[category]["ids"]) != self.recent_sections[category]["total"]:
            yield self.refresh_recent(response, announced, f"Incomplete recent listing on {announced}")
        elif not self.recent_sections[category]["total"] and category not in self.refreshed_categories:
            # A cached "no updates" page can lag behind a completed announcement.
            yield self.refresh_recent(response, announced, "No updates in cached recent listing")
        else:
            self.completed_categories.add(category)

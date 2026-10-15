import os
import unittest
from unittest.mock import patch

from scrapy import Request
from scrapy.http import HtmlResponse

from spiders.arxiv import ArxivSpider


def entry(paper_id, subjects="Machine Learning (cs.LG)", anchor="name='item1'", replaced=False):
    marker = "(replaced)" if replaced else ""
    return (
        f"<dt><a {anchor}>[1]</a><a href='/abs/{paper_id}' title='Abstract'>arXiv:{paper_id}</a>{marker}</dt>"
        f"<dd><div class='list-subjects'>{subjects}</div></dd>"
    )


def section(heading, content):
    return f"<dl><h3>{heading}</h3>{content}</dl>"


def response(body, source="new", skip=0):
    heading = "<h3>Showing new listings for Thursday, 15 October 2026</h3>" if source == "new" else ""
    return HtmlResponse(
        url=f"https://arxiv.org/list/cs.LG/{source}?skip={skip}&show=100",
        body=f"<html><div id='dlpage'>{heading}{body}</div></html>".encode(), encoding="utf-8",
    )


def items(results):
    return [value for value in results if isinstance(value, dict)]


def requests(results):
    return [value for value in results if isinstance(value, Request)]


class SpiderTests(unittest.TestCase):
    def setUp(self):
        with patch.dict(os.environ, {"CATEGORIES": " cs.LG, ,cs.AI,cs.LG "}):
            self.spider = ArxivSpider()

    def test_only_new_section_is_not_mistaken_for_replacements(self):
        body = "<ul><li><a href='#item0'>New submissions</a></li></ul>"
        body += section("New submissions (showing 1 of 1 entries)", entry("2610.19308"))
        result = list(self.spider.parse(response(body)))
        self.assertEqual(["2610.19308"], [paper["id"] for paper in items(result)])
        self.assertEqual({"cs.LG"}, self.spider.completed_categories)

    def test_cross_lists_match_secondary_categories_without_named_anchor(self):
        body = section("New submissions (showing 1 of 1 entries)", entry("2610.19308", anchor="id='item1'"))
        body += section("Cross submissions (showing 1 of 1 entries)", entry(
            "2610.19405", "<span class='primary-subject'>Computer Vision (cs.CV)</span>; Machine Learning (cs.LG)",
            anchor="",
        ))
        body += section("Replacement submissions (showing 1 of 1 entries)", entry("2501.12345"))
        result = items(list(self.spider.parse(response(body))))
        self.assertEqual(["2610.19308", "2610.19405"], [paper["id"] for paper in result])
        self.assertEqual(["cs.CV", "cs.LG"], result[1]["categories"])

    def test_pagination_keeps_a_category_incomplete_until_the_last_page(self):
        body = section("New submissions (showing 1 of 2 entries)", entry("2610.19308"))
        body += "<div class='paging'><a href='?skip=100&amp;show=100'>101-200</a></div>"
        first = list(self.spider.parse(response(body)))
        self.assertNotIn("cs.LG", self.spider.completed_categories)
        self.assertEqual(1, len(requests(first)))
        last = section("New submissions (showing 1 of 2 entries)", entry("2610.19405"))
        result = list(self.spider.parse(response(last, skip=100)))
        self.assertEqual(["2610.19405"], [paper["id"] for paper in items(result)])
        self.assertIn("cs.LG", self.spider.completed_categories)

    def test_replacements_only_checks_recent_then_paginates_the_same_date(self):
        body = section("Replacement submissions (showing 1 of 1 entries)", entry("2501.12345", replaced=True))
        fallback = requests(list(self.spider.parse(response(body))))[0]
        self.assertIn("/recent?skip=0&show=100", fallback.url)
        self.assertEqual({"announced": "2026-10-15"}, fallback.cb_kwargs)
        current = section("Thu, 15 Oct 2026 (showing first 1 of 2 entries)", entry("2610.19308"))
        current += "<div class='paging'><a href='?skip=100&amp;show=100'>101-200</a></div>"
        first = list(self.spider.parse_recent(response(current, "recent"), "2026-10-15"))
        self.assertEqual(["2610.19308"], [paper["id"] for paper in items(first)])
        self.assertEqual(1, len(requests(first)))
        self.assertNotIn("cs.LG", self.spider.completed_categories)
        last = section("Thu, 15 Oct 2026 (showing 1 of 2 entries)", entry("2610.19405"))
        last += section("Wed, 14 Oct 2026 (showing 1 of 1 entries)", entry("2610.10000"))
        last += "<div class='paging'><a href='?skip=200&amp;show=100'>201-300</a></div>"
        result = list(self.spider.parse_recent(response(last, "recent", 100), "2026-10-15"))
        self.assertEqual(["2610.19405"], [paper["id"] for paper in items(result)])
        self.assertEqual([], requests(result))
        self.assertIn("cs.LG", self.spider.completed_categories)

    def test_explicit_no_updates_is_successful_only_after_recent_check(self):
        result = list(self.spider.parse(response("<p>No updates today.</p>")))
        self.assertEqual(1, len(requests(result)))
        self.assertNotIn("cs.LG", self.spider.completed_categories)
        empty = section("Thu, 15 Oct 2026", "<p>No updates for this time period.</p>")
        empty += section("Wed, 14 Oct 2026 (showing 1 of 1 entries)", entry("2610.10000"))
        result = list(self.spider.parse_recent(response(empty, "recent"), "2026-10-15"))
        self.assertEqual(1, len(requests(result)))
        self.assertIn("refresh=", requests(result)[0].url)
        self.assertNotIn("cs.LG", self.spider.completed_categories)
        result = list(self.spider.parse_recent(response(empty, "recent"), "2026-10-15"))
        self.assertEqual([], result)
        self.assertIn("cs.LG", self.spider.completed_categories)

    def test_stale_recent_listing_is_not_a_verified_empty_day(self):
        old = section("Wed, 14 Oct 2026 (showing 1 of 1 entries)", entry("2610.10000"))
        result = list(self.spider.parse_recent(response(old, "recent"), "2026-10-15"))
        self.assertIn("refresh=", requests(result)[0].url)
        with self.assertRaisesRegex(ValueError, "does not contain announcement date"):
            list(self.spider.parse_recent(response(old, "recent"), "2026-10-15"))

    def test_changed_new_counts_use_recent_instead_of_publishing_a_partial_batch(self):
        first = section("New submissions (showing first 1 of 2 entries)", entry("2610.19308"))
        first += "<div class='paging'><a href='?skip=100&amp;show=100'>101-200</a></div>"
        list(self.spider.parse(response(first)))
        changed = section("New submissions (continued, showing last 1 of 3 entries)", entry("2610.19405"))
        result = list(self.spider.parse(response(changed, skip=100)))
        self.assertIn("/recent?", requests(result)[0].url)
        self.assertNotIn("cs.LG", self.spider.completed_categories)

    def test_missing_next_page_checks_recent(self):
        body = section("New submissions (showing first 1 of 2 entries)", entry("2610.19308"))
        result = list(self.spider.parse(response(body)))
        self.assertIn("/recent?", requests(result)[0].url)
        self.assertNotIn("cs.LG", self.spider.completed_categories)

    def test_recent_incomplete_or_changed_counts_fail(self):
        self.spider.refreshed_categories.add("cs.LG")
        first = section("Thu, 15 Oct 2026 (showing first 1 of 2 entries)", entry("2610.19308"))
        with self.assertRaisesRegex(ValueError, "Incomplete recent listing"):
            list(self.spider.parse_recent(response(first, "recent"), "2026-10-15"))
        changed = section("Thu, 15 Oct 2026 (continued, showing last 1 of 3 entries)", entry("2610.19405"))
        with self.assertRaisesRegex(ValueError, "count changed"):
            list(self.spider.parse_recent(response(changed, "recent", 100), "2026-10-15"))

    def test_refresh_recovers_consistent_counts_and_is_kept_on_next_page(self):
        first = section("Thu, 15 Oct 2026 (showing first 1 of 2 entries)", entry("2610.19308"))
        first += "<div class='paging'><a href='?skip=100&amp;show=100'>101-200</a></div>"
        list(self.spider.parse_recent(response(first, "recent"), "2026-10-15"))
        changed = section("Thu, 15 Oct 2026 (continued, showing last 1 of 3 entries)", entry("2610.19405"))
        result = list(self.spider.parse_recent(response(changed, "recent", 100), "2026-10-15"))
        refresh = requests(result)[0]
        self.assertIn("refresh=", refresh.url)
        fresh_first = first.replace("of 2 entries", "of 3 entries")
        result = list(self.spider.parse_recent(response(fresh_first, "recent").replace(url=refresh.url), "2026-10-15"))
        self.assertIn("refresh=", requests(result)[0].url)
        last = section("Thu, 15 Oct 2026 (continued, showing last 2 of 3 entries)", entry("2610.19405") + entry("2610.19819"))
        result = list(self.spider.parse_recent(response(last, "recent", 100), "2026-10-15"))
        self.assertEqual(["2610.19405", "2610.19819"], [paper["id"] for paper in items(result)])
        self.assertIn("cs.LG", self.spider.completed_categories)

    def test_unknown_or_truncated_pages_fail(self):
        bodies = ["<p>Service temporarily unavailable</p>", section("Unknown section", entry("2610.19308"))]
        bodies.append(section("New submissions (showing 2 of 2 entries)", entry("2610.19308")))
        bodies.append(section("New submissions (continued, showing last 2 of 3 entries)", entry("2610.19308")))
        bodies.append(section("New submissions (showing 1 of 1 entries)", "<dt>Broken entry</dt>"))
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(ValueError):
                list(self.spider.parse(response(body)))
        with self.assertRaisesRegex(ValueError, "Unrecognized arXiv listing"):
            list(self.spider.parse(HtmlResponse(url="https://arxiv.org/list/cs.LG/new", body=b"<p>Challenge</p>")))

    def test_date_change_mid_pagination_fails(self):
        body = section("New submissions (showing 1 of 1 entries)", entry("2610.19308"))
        list(self.spider.parse(response(body)))
        changed = response(body).replace(body=response(body).body.replace(b"15 October", b"16 October"))
        with self.assertRaisesRegex(ValueError, "date changed"):
            list(self.spider.parse(changed))

    def test_duplicate_ids_are_fetched_once(self):
        body = section("New submissions (showing 2 of 2 entries)", entry("2610.19308v1") + entry("2610.19308"))
        result = items(list(self.spider.parse(response(body))))
        self.assertEqual(["2610.19308"], [paper["id"] for paper in result])
        self.assertEqual([], items(list(self.spider.parse(response(body)))))

    def test_categories_are_trimmed_and_empty_configuration_is_rejected(self):
        self.assertEqual({"cs.LG", "cs.AI"}, self.spider.target_categories)
        self.assertEqual(2, len(self.spider.start_urls))
        with patch.dict(os.environ, {"CATEGORIES": " , "}), self.assertRaises(ValueError):
            ArxivSpider()


if __name__ == "__main__":
    unittest.main()

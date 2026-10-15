from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from daily_arxiv import metadata


def paper(paper_id):
    return {
        "id": paper_id, "title": f"Paper {paper_id}", "summary": "Original abstract",
        "authors": ["An Author"], "categories": ["cs.LG"], "comment": None,
    }


def feed(ids):
    entries = "".join(
        f"<entry><id>https://arxiv.org/abs/{paper_id}v2</id><title>Paper {paper_id}</title>"
        "<summary>Original abstract</summary><author><name>An Author</name></author>"
        "<category term='cs.LG'/></entry>"
        for paper_id in ids
    )
    return f"<feed xmlns='http://www.w3.org/2005/Atom'>{entries}</feed>".encode()


def response(ids=(), status=200, headers=None, body=None):
    result = Mock(status_code=status, headers=headers or {}, content=feed(ids) if body is None else body)
    result.__enter__ = Mock(return_value=result)
    result.__exit__ = Mock(return_value=False)
    return result


class MetadataTests(unittest.TestCase):
    def client(self, responses):
        client = metadata.MetadataClient()
        client.session.close()
        client.session = Mock()
        client.session.get.side_effect = responses
        return client

    def test_parse_batch_by_id_not_response_order(self):
        result = metadata.parse_metadata(feed(["2610.00002", "2610.00001"]), {"2610.00001", "2610.00002"})
        self.assertEqual(paper("2610.00001"), result["2610.00001"])
        self.assertEqual(paper("2610.00002"), result["2610.00002"])
        with self.assertRaisesRegex(ValueError, "unrequested"):
            metadata.parse_metadata(feed(["2610.00003"]), {"2610.00001"})
        with self.assertRaisesRegex(ValueError, "Atom feed"):
            metadata.parse_metadata(b"<html>Unavailable</html>", {"2610.00001"})

    def test_api_primary_category_is_first_for_publication(self):
        body = feed(["2610.00001"]).replace(
            b"<category term='cs.LG'/>",
            b"<category term='cs.AI'/><category term='cs.LG'/>"
            b"<primary_category xmlns='http://arxiv.org/schemas/atom' term='cs.LG'/>",
        )
        result = metadata.parse_metadata(body, {"2610.00001"})
        self.assertEqual(["cs.LG", "cs.AI"], result["2610.00001"]["categories"])

    def test_retry_after_accepts_seconds_and_http_date(self):
        self.assertEqual(120, metadata.retry_after_seconds("120"))
        until = datetime.now(timezone.utc) + timedelta(seconds=120)
        self.assertGreater(metadata.retry_after_seconds(format_datetime(until)), 118)
        for value in (None, "invalid", "-10"):
            self.assertEqual(0, metadata.retry_after_seconds(value))

    def test_429_waits_for_retry_after_and_retries_the_batch(self):
        ids = ["2610.00001", "2610.00002"]
        client = self.client([response(status=429, headers={"Retry-After": "90"}), response(ids)])
        saved = []
        with patch.object(client, "wait") as wait, patch.object(metadata.time, "monotonic", return_value=100):
            result = client.fetch_batch(ids, lambda papers: saved.append(papers))
        self.assertEqual(set(ids), set(result))
        self.assertEqual(2, wait.call_count)
        self.assertEqual(1, len(saved))
        self.assertEqual(1, client.stats["api_retries"])
        self.assertEqual(2, client.session.get.call_count)
        for call in client.session.get.call_args_list:
            self.assertEqual(",".join(ids), call.kwargs["params"]["id_list"])
            self.assertEqual(2, call.kwargs["params"]["max_results"])

    def test_rate_limit_and_server_errors_back_off_globally(self):
        client = self.client([response(status=429, headers={"Retry-After": "90"}), response(status=503), response(["2610.00001"])])
        deadlines = []
        with patch.object(metadata.time, "monotonic", return_value=100), patch.object(
            client, "wait", side_effect=lambda: deadlines.append(client.next_request_at),
        ):
            client.fetch_batch(["2610.00001"], lambda _: None)
        self.assertEqual([0, 190, 220], deadlines)

    def test_minimum_interval_applies_between_batches(self):
        client = self.client([response(["2610.00001"]), response(["2610.00002"])])
        deadlines = []
        with patch.object(metadata.time, "monotonic", return_value=100), patch.object(
            client, "wait", side_effect=lambda: deadlines.append(client.next_request_at),
        ):
            client.fetch_batch(["2610.00001"], lambda _: None)
            client.fetch_batch(["2610.00002"], lambda _: None)
        self.assertEqual([0, 103], deadlines)

    def test_partial_batch_is_checkpointed_and_only_missing_ids_are_retried(self):
        client = self.client([response(["2610.00002"]), response(["2610.00001"])])
        saved = []
        with patch.object(client, "wait"):
            result = client.fetch_batch(["2610.00001", "2610.00002"], lambda papers: saved.append(set(papers)))
        self.assertEqual([{"2610.00002"}, {"2610.00001"}], saved)
        self.assertEqual("2610.00001", client.session.get.call_args_list[1].kwargs["params"]["id_list"])
        self.assertEqual(2, len(result))

    def test_rate_limit_exhaustion_stops_instead_of_moving_to_the_next_batch(self):
        client = self.client([response(status=429)] * 5)
        with patch.object(client, "wait"), self.assertRaisesRegex(metadata.MetadataFetchError, "checkpointed"):
            client.fetch_batch(["2610.00001", "2610.00002"], lambda _: self.fail("No success to checkpoint"))
        self.assertEqual(5, client.session.get.call_count)
        self.assertEqual(4, client.stats["api_retries"])

    def test_non_retryable_http_failure_stops_immediately(self):
        client = self.client([response(status=400)])
        with patch.object(client, "wait"), self.assertRaisesRegex(metadata.MetadataFetchError, "HTTP 400"):
            client.fetch_batch(["2610.00001"], lambda _: None)
        self.assertEqual(1, client.session.get.call_count)

    def test_connection_error_retries_without_losing_the_batch(self):
        client = self.client([metadata.requests.Timeout("timed out"), response(["2610.00001"])])
        with patch.object(client, "wait"):
            result = client.fetch_batch(["2610.00001"], lambda _: None)
        self.assertEqual({"2610.00001"}, set(result))

    def test_complete_listings_need_no_api_and_upgrade_incomplete_candidates(self):
        candidates = [{"id": "2610.00002"}, {"id": "2610.00001"}]
        listed = {value["id"]: value for value in [paper("2610.00001"), paper("2610.00002")]}
        with tempfile.TemporaryDirectory() as directory, patch.object(metadata, "MetadataClient") as client:
            stats = {}
            result = metadata.complete_metadata(candidates, listed, Path(directory) / "cache.jsonl", stats)
            client.assert_not_called()
        self.assertEqual(["2610.00002", "2610.00001"], [row["id"] for row in result])
        self.assertEqual("https://arxiv.org/pdf/2610.00002", result[0]["pdf"])
        self.assertEqual(2, stats["from_listing"])
        self.assertEqual(0, stats["api_requests"])

    def test_five_requests_cover_495_missing_papers(self):
        ids = [f"2610.{i:05d}" for i in range(495)]
        client = self.client([response(ids[start:start + 100]) for start in range(0, len(ids), 100)])
        with tempfile.TemporaryDirectory() as directory, patch.object(metadata, "MetadataClient", return_value=client), patch.object(client, "wait"):
            stats = {}
            result = metadata.complete_metadata([{"id": value} for value in ids], {}, Path(directory) / "cache.jsonl", stats)
        self.assertEqual(ids, [row["id"] for row in result])
        self.assertEqual(5, stats["api_requests"])
        self.assertEqual(495, stats["api_papers"])

    def test_checkpoint_resumes_after_a_later_batch_fails(self):
        ids = [f"2610.{i:05d}" for i in range(101)]
        first = self.client([response(ids[:100]), *([response(status=429)] * 5)])
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.jsonl"
            with patch.object(metadata, "MetadataClient", return_value=first), patch.object(first, "wait"), self.assertRaises(metadata.MetadataFetchError):
                metadata.complete_metadata([{"id": value} for value in ids], {}, cache, {})
            self.assertEqual(100, len(metadata.MetadataCache(cache).papers))
            # A truncated append must not prevent reuse of earlier complete rows.
            with cache.open("a") as stream:
                stream.write('{"id": "broken')
            resumed = self.client([response([ids[-1]])])
            with patch.object(metadata, "MetadataClient", return_value=resumed), patch.object(resumed, "wait"):
                stats = {}
                result = metadata.complete_metadata([{"id": value} for value in ids], {}, cache, stats)
            self.assertEqual(100, stats["from_cache"])
            self.assertEqual(1, stats["api_requests"])
            self.assertEqual(ids[-1], resumed.session.get.call_args.kwargs["params"]["id_list"])
            self.assertEqual(101, len(result))
            self.assertEqual(101, len(metadata.MetadataCache(cache).papers))


if __name__ == "__main__":
    unittest.main()

# Daily update maintenance

The crawler runs in GitHub Actions. Keeping a checkout on a server does not
schedule it there. Code is stored on `main`; published papers and the website's
file index are stored on `data`.

By default, `ENABLE_AI=false`: the daily job publishes paper titles, authors,
categories, links and original abstracts without making any paid AI requests.
No AI API key or model credit is required for this mode.

## Configuration

In repository Settings → Secrets and variables → Actions, configure:

| Kind | Name | Purpose |
| --- | --- | --- |
| Variable | `ENABLE_AI` | Defaults to `false`; set `true` to try optional summaries |
| Secret | `OPENAI_API_KEY` | Only needed when AI is enabled |
| Secret | `OPENAI_BASE_URL` | API base URL matching that credential; only needed for AI |
| Variable | `MODEL_NAME` | Model identifier supported by that provider; only used for AI |
| Variable | `OPENAI_API_MODE` | `chat_completions`, `responses`, or `auto` |
| Variable | `CATEGORIES` | Comma-separated arXiv categories; defaults to `cs.LG, cs.DC, cs.AI` |
| Variable | `LANGUAGE` | Summary language; defaults to `Chinese` |

`auto` retains the existing relay detection. Set the protocol explicitly when
using another Responses-only relay. Local environment files and
`scripts/load-codex-mode-env.sh` do not configure GitHub Actions secrets.

The workflow passes the optional `OPENAI_REASONING_EFFORT`,
`OPENAI_MAX_OUTPUT_TOKENS`, and `OPENAI_API_TIMEOUT` variables to the AI step.
Leave provider-specific options unset unless your provider supports them.

## Running and verifying

The workflow requests runs at 01:37 and 07:37 UTC (09:37 and 15:37 Beijing time).
The second invocation retries a missed or failed update. GitHub schedules can
be delayed or missed; these are requested start times, not a delivery guarantee.
Concurrent invocations are serialized.

Use Actions → arXiv-daily-ai-enhanced → Run workflow on `main` for a manual run.
Check `force` to regenerate today's output even when it already validates.
The completion check uses raw paper metadata and the website data file; it does
not require an AI summary. Empty files or incomplete metadata are retried.

Before crawling, the workflow restores published history. Deduplication only
skips papers with published raw metadata in the preceding seven days, and
removes duplicates across the configured categories. Missing AI summaries do
not cause the same papers to be published again. A successful check may find no
new papers, for example when arXiv has not posted another batch.

After a successful run, verify:

1. The Actions summary says papers and the history index were published.
2. `data/YYYY-MM-DD.jsonl` on the `data` branch contains full paper metadata.
3. `assets/file-list.txt` on `data` lists historical dates as well as the new date.
4. A subsequent run without `force` skips an already valid daily output.

The website and existing CLI use the historical
`YYYY-MM-DD_AI_enhanced_Chinese.jsonl` filename. It is also produced without AI:
those rows contain the original metadata and omit the `AI` field, so the website
and Markdown export display the original abstract. No failure summaries are
inserted. The filename is retained for compatibility.

When enabled, AI runs as an optional step with a 30-minute timeout. API errors,
malformed responses or an empty enhanced batch cause that step to fail, then
publication continues using original abstracts. Successfully enhanced rows are
merged into the raw collection; papers without AI results are still retained.

The crawler reads arXiv's current `/new` listing in pages of 100, including
cross-lists whose secondary categories match the configured categories. It
identifies replacements by their section heading, not by the last navigation
anchor. Duplicate IDs are removed before metadata requests.

If a category has no eligible entries in `/new`, or its section counts change
during pagination or cannot be fully retrieved, the crawler checks `/recent`
for the same announcement date and follows its pagination. This handles
inconsistent or temporarily incomplete listing responses without importing
papers from an older date. Only a completed, recognized listing can produce a
successful "no new papers" outcome. If `/recent` is empty, stale or inconsistent,
it is refreshed once with a unique query parameter carried across pagination.
HTTP failures, malformed pages, persistently incomplete pagination and metadata
failures still fail the job. Per-page counts and dates are logged. The Actions
runner uploads `arxiv-crawl-report.json` as an artifact on success or failure;
its `metadata` section reports listing reuse, checkpoint reuse, API requests
and retries. Existing raw data is replaced only after the new feed validates.

Metadata already present in `/new` (title, authors, categories and abstract) is
retained. After all listings finish, only papers with missing metadata go to
the arXiv API, in batches of up to 100 IDs. Successful API calls are at least
three seconds apart. HTTP 429, server errors and transient failures pause all
metadata requests with 60/120/240/480-second backoff, waiting longer when
`Retry-After` requires it. Only missing IDs are retried; persistent failures
stop the job without publishing an incomplete collection.

Complete metadata is checkpointed before API requests and after each successful
batch. Actions restores and saves this per-date checkpoint even if the crawl
fails, so a new run on the same date can request only the remaining metadata.
The checkpoint is not published as daily output. Only IDs found in a validated
listing are eligible for reuse; category order retains the primary category.

To run the same validation locally, activate the project environment and run
`python -m daily_arxiv.crawl --output ../data/YYYY-MM-DD.jsonl` from
`daily_arxiv/`. A verified empty result exits successfully without publishing
or replacing an existing data file. The default checkpoint lives in
`data/.metadata-cache/YYYY-MM-DD.jsonl`; use `--cache PATH` to choose another
location. `run.sh` calls this same entry point. Bare `scrapy crawl arxiv`
exports listing candidates and does not perform the batch metadata stage.

Fixing the workflow does not automatically reconstruct every missed date or
regenerate old failed summaries.
Summarizing saved historical raw files is optional and requires a separate AI
processing run after model access is restored.

## Upstream updates

Fetch `upstream/main`, then merge it into a review branch. Preserve this fork's
category choices, user preferences, repository configuration, Actions write
permission and Responses API support when resolving conflicts.

```bash
git fetch upstream main
git switch -c maintenance/sync-upstream main
git merge upstream/main
uv sync --locked
PYTHONPATH=ai uv run python -m unittest discover -s ai/tests -v
PYTHONPATH=daily_arxiv/daily_arxiv uv run python -m unittest discover -s daily_arxiv/tests -v
```

Review and deploy the resulting branch to `main`; fetching upstream alone does
not change the deployed workflow. If no new `schedule` events appear after a
workflow change, check whether the workflow is enabled, try disabling and
re-enabling it, and give GitHub Support the last scheduled run and the first
missing date. Successful manual runs alone do not verify automatic scheduling.

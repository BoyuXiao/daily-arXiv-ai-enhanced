# Daily update maintenance

The crawler runs in GitHub Actions. Keeping a checkout on a server does not
schedule it there. Code is stored on `main`; published papers and the website's
file index are stored on `data`.

## Configuration

In repository Settings → Secrets and variables → Actions, configure:

| Kind | Name | Purpose |
| --- | --- | --- |
| Secret | `OPENAI_API_KEY` | Credential issued by your model provider |
| Secret | `OPENAI_BASE_URL` | API base URL matching that credential |
| Variable | `MODEL_NAME` | Model identifier supported by that provider |
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
An empty file or one containing legacy failure placeholders never counts as a
successful daily output, so those cases are retried without `force`.

Before crawling, the workflow restores published history. Deduplication only
skips papers with usable AI summaries in the preceding seven days, and removes
duplicates across the configured categories. Previously failed AI summaries
remain eligible for retry if their paper IDs appear in the new crawl.

After a successful run, verify:

1. The Actions summary says validated summaries and the history index were published.
2. `data/YYYY-MM-DD_AI_enhanced_Chinese.jsonl` on the `data` branch contains real summaries.
3. `assets/file-list.txt` on `data` lists historical dates as well as the new date.
4. A subsequent run without `force` skips an already valid daily output.

AI request errors, malformed responses and an empty enhanced batch fail the
job. They do not replace published output with placeholders. Upstream's content
filter behavior is retained: confirmed flags filter a paper, while a filtering
service outage allows processing to continue.

The crawler reads arXiv's current `/new` listing. Fixing the workflow does not
automatically reconstruct every missed date or regenerate old failed summaries.
Backfilling saved historical raw files requires a separate AI processing run.

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

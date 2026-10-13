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

The crawler reads arXiv's current `/new` listing. Fixing the workflow does not
automatically reconstruct every missed date or regenerate old failed summaries.
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

# AIAJ queue — GitHub Actions (scheduled, no subscription)

## What this does
Runs the existing Python/SQLite queue on GitHub-hosted runners on a two off-the-hour weekday runs per hour (UTC :11 and :41 between 15:00 and 23:59). GitHub may delay or drop scheduled jobs during high load. Queue state is committed to a **private repository** after each run. No cloud VM or paid server subscription is required. GitHub Actions free minutes/usage quotas apply; this is not guaranteed unlimited free throughput.

## Setup
1. Create a **private GitHub repository**, for example `aiaj-adaptive-outreach`.
2. Upload the contents of this folder, including `.github/workflows/adaptive-queue.yml`, `queue.py`, `suppression.csv`, and `.gitignore`. Do **not** upload real prospects until the repository is private and access is restricted.
3. Repository **Settings → Actions → General → Workflow permissions**: allow **Read and write permissions** for `GITHUB_TOKEN` (or set organization permissions accordingly). Scheduled runs use the default branch.
4. Repository **Settings → Secrets and variables → Actions → Secrets**: set `SMTP_HOST` (`mail.privateemail.com` only after confirming provider settings), `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`. Set `SMTP_PORT` in **Variables** to `587` (STARTTLS), after confirming provider settings.
5. Set repository **Variables** `AIAJ_HOURLY_CAP` to your verified provider-approved hourly account ceiling; the workflow defaults to 500 as a configurable upper bound, not a guaranteed safe rate. Set `AIAJ_LIVE_ENABLED` to `false` initially.
6. Add a `prospects.csv` file with columns `recipient,subject,body`, populated only with reviewed, approved personalized campaign messages. Example in `prospects.example.csv`. The worker automatically imports new rows and deduplicates identical content. The 10 hard-failed addresses in `suppression.csv` are excluded.
7. Open **Actions → AIAJ adaptive outreach queue → Run workflow**, select `dry_run=true`; inspect logs. Confirm suppression, queue status and recipients.
8. To test, set `AIAJ_LIVE_ENABLED=true` and trigger `dry_run=false` with **only one approved test email in `prospects.csv`**. Confirm SMTP acceptance and receipt. Then upload the approved campaign list and allow scheduled runs.

## Operations and limitations
- No email is sent until `AIAJ_LIVE_ENABLED=true` or a workflow dispatch `dry_run=false` is invoked while live mode is enabled.
- One workflow at a time (`concurrency`), one SMTP submission at a time within a job. Each run processes up to 250 messages within an 11-minute send-loop budget, subject to rolling hourly cap, per-domain throttling, and SMTP responses.
- The sender accepts SMTP submission; this is **not proof of inbox delivery**. Bounce messages received later are not automatically parsed by this package; update `suppression.csv` from monitored bounce notices. A separate mailbox connector monitor remains necessary.
- The queue database stores message bodies and recipients. The repository must be **private**; GitHub history retains prior commits. Never commit SMTP passwords or `.env` files.
- Scheduled workflows can be delayed; they are not an always-on process. Actual 3,000/week throughput depends on provider limits, queued volume, job execution, and recipient-domain throttling.
- The current worker does not guarantee automatic deduplication of a message that reached SMTP but whose submission response was lost. Such a message is placed into `manual_review` when `reset-inflight` is used; do not blindly requeue.
- SQLite database is committed after every run to preserve queue, throttle and hourly history. Keep the repository private and periodically archive/compact the database; large campaigns can grow repository history substantially.
- For stronger reliability at larger scale, move state to a free hosted database instead of committing SQLite to Git. That is a separate architectural change.

## Timing and recovery
See `TIMING_AND_RECOVERY.md` for the missed-schedule catch-up behavior, monthly free-minute budgeting, and limitations. Exact-minute run guarantees are not available on GitHub Actions.

# Execution timing and recovery — AIAJ GitHub Actions

## Accurate statement of reliability
GitHub Actions schedule is best-effort: runs may start late, sometimes be dropped. No GitHub-hosted workflow can guarantee starting exactly on the requested minute. The durable queue ensures overdue, unprocessed messages remain eligible on the next successful run. There is no guaranteed maximum delay when GitHub is unavailable or every scheduled event is missed.

## Zero-subscription scheduling setup
- Run at UTC :11 and :41 during 15:00–23:59 on Monday–Friday, i.e. 9 hours/day. Off-the-hour times reduce GitHub scheduling contention.
- 90 nominal jobs/week; ~390–414 jobs/month. Minimum one minute billing each; actual usage increases with run duration. GitHub Free private repositories include approximately 2,000 Linux runner minutes/month. Monitor monthly usage and adjust schedule, processing time or active hours accordingly.
- Each job can process up to 250 messages with a maximum send-loop budget of 660 seconds, provided `AIAJ_LIVE_ENABLED=true`, SMTP credentials configured, and account policy permits it. The rolling 1-hour accepted-submission cap defaults to 500 (not permission to exceed provider limits). With 2 jobs/hour, capacity is theoretically 500 submissions/hour, but SMTP and job timing may lower actual throughput.
- Jobs are serialized via `concurrency: cancel-in-progress: false` (a single pending run can be replaced under GitHub concurrency semantics; overdue work stays in queue). Use `workflow_dispatch` to manually recover after missing scheduled jobs.
- `queue.sqlite3` is checked into a PRIVATE repository after every job. Keep database and recipient data private; repository write access must be restricted. GitHub commit history retains old recipient data. No SMTP secrets may be committed.
- Every job imports the latest approved prospects from `prospects.csv` and automatically suppresses known bounces; `identity` hash prevents reimporting identical email content for the same recipient.
- Unexpected messages in `sending` state are moved to `manual_review` on the next run, not retried automatically: an SMTP acceptance may have occurred before the crash, and blindly retrying can duplicate.
- After every job, the updated database is committed, including error cases. If push fails or GitHub is unavailable, state is not guaranteed persisted: check workflow logs before re-enabling.

## Monitoring
Inspect the GitHub Actions run log and `python queue.py status`. Set up an independent alert if no successful run has occurred in the desired window. An alert is not a guarantee of executing the job; a truly guaranteed SLA requires a monitored always-on worker and a provider offering contractual availability.

## Readiness to enable live sending
1. Create a private repository and enable write permissions for the Actions workflow.
2. Add SMTP secrets in GitHub Actions repository settings, never in issues/chat/code.
3. Confirm SMTP provider account caps; ensure DNS SPF/DKIM/DMARC and suppression updates.
4. Upload a *reviewed* `prospects.csv` in a private repo. Start with a controlled self-addressed test.
5. Keep `AIAJ_LIVE_ENABLED=false` until test success, then explicitly switch it on.

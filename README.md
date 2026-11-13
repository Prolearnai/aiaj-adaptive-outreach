# AIAJ Adaptive Outbound Queue (prototype)

A local, dependency-free Python/SQLite outbound queue with suppression, message deduplication, one-at-a-time SMTP submission, per-domain pacing, exponential backoff for temporary SMTP errors, permanent-failure suppression, event audit, and an account-level hourly cap. **SMTP acceptance is not proof of final inbox delivery.**

## Setup

```bash
python3 queue.py init
python3 queue.py suppress --file suppression.csv
python3 queue.py import --file prospects.csv
python3 queue.py status
python3 queue.py run --limit 20                  # dry-run: no email sent
```

`prospects.csv` columns: `recipient,subject,body` (one unique personalized email per row). `suppression.csv` columns: `recipient,reason`.

## Connect a sending mailbox

Set environment variables using your hosting platform's secrets manager, **never put credentials in the CSV or source code**:

```
SMTP_HOST=<your provider's verified SMTP hostname>
SMTP_PORT=587
SMTP_FROM=outreach@aiajacademy.io
SMTP_USER=<SMTP login>
SMTP_PASSWORD=<app password or SMTP password>
AIAJ_HOURLY_CAP=400
AIAJ_DB=/persistent/path/queue.sqlite3
```

The `400` cap is a configurable technical ceiling below Namecheap's *reported paid-plan* limit, not a recommended hourly target. Confirm your account's actual limit first. Trial accounts may require `AIAJ_HOURLY_CAP=20`.

Run `python3 queue.py run --live --limit 100` from a persistent worker or cron job, **only after explicit approval to begin sends**. A live worker requires SMTP credentials. Never run concurrent worker processes against the same database; a production deployment should add a distributed lease/transactional claim. Use persistent storage: ephemeral serverless filesystem is not suitable for SQLite.

## Operational constraints

- `421`/`4xx` response: backoff and retry. `5xx`: suppress. Network errors/unknown outcomes go to retry in this prototype; for production, reconcile Sent/provider logs before retrying to avoid duplicate delivery.
- Provider-specific rate limits, opt-out processing, inbound bounce reconciliation, DNS verification, automated domain-MX grouping, and CRM integration require additional integrations.
- Initial interval is 120 seconds **per destination domain**, not globally. The worker can submit messages to other domains between these intervals.
- SMTP sends are sequential. This avoids concurrent SMTP connections but does not bypass provider limits or receiving-server policies.
- A real 3,000+/week deployment needs a persistent worker and validated throughput; this package is a prototype, not a deployed service.

## Oracle Cloud Always Free Ubuntu VM deployment

1. Create an Oracle Cloud account and provision an **Always Free eligible** Ubuntu VM in your home region. Keep the instance within the Always Free quota. SSH into it.
2. Copy the package contents to the VM (for example with `scp`), then from its extracted directory run `sudo ./install-ubuntu.sh`.
3. Set SMTP_PASSWORD in `/etc/aiaj-queue/smtp.env` with `sudoedit` (never commit or share the password). Confirm the SMTP host and account plan, then set the appropriate hourly ceiling. The installer defaults to 20/hour until verified.
4. Import personalized recipient CSV with `sudo -u aiajqueue env AIAJ_DB=/var/lib/aiaj-queue/queue.sqlite3 python3 /opt/aiaj-queue/queue.py import --file /path/to/prospects.csv` (ensure the service user can read the file).
5. Once you authorize live sending, run `sudo systemctl enable --now aiaj-queue.timer`. Check `systemctl list-timers aiaj-queue.timer` and `journalctl -u aiaj-queue.service -n 100`.
6. Stop sending at any time: `sudo systemctl disable --now aiaj-queue.timer`.

**Operational limitations:** The installed timer runs one worker at a time and uses persistent SQLite. It does not automatically ingest Namecheap bounce notices, opt-outs, or Google Sheet updates. Manual-review outcomes need reconciliation. Do not activate before SMTP credential setup and a one-message test. Oracle Always Free VM availability and network egress policies can vary. The installer does not configure a web UI or public ingress; none is required for the queue worker.

## GitHub Actions timing and recovery (updated)
The free GitHub-hosted version is scheduled at off-the-hour :11 and :41 on weekdays (15:00–23:59 UTC). GitHub Actions does **not** guarantee exact start times or execution of every cron event. The queue retains overdue work for the next successful job. Interrupted SMTP submissions are quarantined for review to prevent duplicate attempts. See `TIMING_AND_RECOVERY.md`. A private repository and SMTP secrets are required; live sending remains disabled until explicitly configured.

#!/usr/bin/env python3
"""AIAJ adaptive outbound queue. Standard library only; local SQLite state."""
import argparse, csv, datetime as dt, email.message, hashlib, json, os, random, re, sqlite3, smtplib, ssl, time
from collections import defaultdict
from pathlib import Path

DB = os.environ.get('AIAJ_DB', 'queue.sqlite3')
UTC = dt.timezone.utc
now = lambda: dt.datetime.now(UTC).timestamp()

def connect():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=DELETE')
    c.execute('PRAGMA busy_timeout=5000')
    return c


def init(c):
    c.executescript('''
        CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY,
            recipient TEXT NOT NULL,
            domain TEXT NOT NULL,
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            identity TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'queued',
            attempts INTEGER NOT NULL DEFAULT 0,
            next_at REAL NOT NULL DEFAULT 0,
            last_code INTEGER,
            last_error TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ready_idx ON messages(status, next_at);
        CREATE TABLE IF NOT EXISTS suppression(email TEXT PRIMARY KEY, reason TEXT NOT NULL, added_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS throttle(domain TEXT PRIMARY KEY, interval_seconds REAL NOT NULL DEFAULT 120, next_at REAL NOT NULL DEFAULT 0, success_streak INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, message_id INTEGER, at REAL NOT NULL, event TEXT NOT NULL, detail TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
    ''')
    c.commit()


def address(s):
    s = s.strip().lower()
    if not re.fullmatch(r"[a-z0-9_+.'-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+", s):
        raise ValueError('Invalid email: ' + s)
    return s


def import_csv(c, path):
    added = skipped = 0
    with open(path, newline='', encoding='utf-8-sig') as f:
        for row in csv.DictReader(f):
            try:
                recipient = address(row['recipient'])
                subject = row['subject'].strip()
                body = row['body'].strip()
                if not subject or not body:
                    raise ValueError('Missing subject or body')
                identity = hashlib.sha256((recipient + '\0' + subject + '\0' + body).encode()).hexdigest()
                if c.execute('SELECT 1 FROM suppression WHERE email=?', (recipient,)).fetchone():
                    skipped += 1
                    continue
                cur = c.execute(
                    'INSERT OR IGNORE INTO messages(recipient,domain,subject,body,identity,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                    (recipient, recipient.split('@')[1], subject, body, identity, now(), now())
                )
                added += cur.rowcount
                skipped += 1 - cur.rowcount
            except (ValueError, KeyError) as e:
                print('SKIP:', e)
                skipped += 1
    c.commit()
    print(json.dumps({'added': added, 'skipped': skipped}))


def suppress(c, path):
    count = 0
    with open(path, newline='', encoding='utf-8-sig') as f:
        for r in csv.DictReader(f):
            try:
                a = address(r['recipient'])
            except (ValueError, KeyError):
                continue
            c.execute('INSERT OR REPLACE INTO suppression VALUES(?,?,?)', (a, r.get('reason', 'bounce'), now()))
            c.execute("UPDATE messages SET status='suppressed',updated_at=? WHERE recipient=? AND status IN ('queued','retry')", (now(), a))
            count += 1
    c.commit()
    print('Suppressed', count)


def smtp_send(row):
    host = os.environ['SMTP_HOST']
    port = int(os.environ.get('SMTP_PORT', '587'))
    sender = os.environ['SMTP_FROM']
    user = os.environ['SMTP_USER']
    password = os.environ['SMTP_PASSWORD']

    m = email.message.EmailMessage()
    m['From'] = sender
    m['To'] = row['recipient']
    m['Subject'] = row['subject']
    m['Message-ID'] = '<' + row['identity'][:32] + '@' + sender.split('@')[1] + '>'
    m.set_content(row['body'])

    if port == 465:
        s = smtplib.SMTP_SSL(host, port, timeout=30, context=ssl.create_default_context())
    else:
        s = smtplib.SMTP(host, port, timeout=30)
        s.ehlo()
        s.starttls(context=ssl.create_default_context())
        s.ehlo()

    try:
        s.login(user, password)
        s.send_message(m)
    finally:
        try:
            s.quit()
        except (smtplib.SMTPException, OSError):
            pass


def event(c, row, kind, detail=''):
    c.execute('INSERT INTO events(message_id,at,event,detail) VALUES(?,?,?,?)', (row['id'], now(), kind, detail[:1000]))


def run(c, limit, dry_run, max_seconds=660):
    deadline = time.monotonic() + max_seconds
    if not dry_run:
        for k in ['SMTP_HOST', 'SMTP_FROM', 'SMTP_USER', 'SMTP_PASSWORD']:
            if not os.getenv(k):
                raise SystemExit('Missing ' + k + '; no sends performed')

    sent = 0
    for _ in range(limit):
        if time.monotonic() >= deadline:
            print('Run-time budget reached; remaining queue stays persistent for catch-up')
            break
        t = now()
        window = t - 3600
        count = c.execute("SELECT count(*) FROM events WHERE event='smtp_accepted' AND at>=?", (window,)).fetchone()[0]
        cap = int(os.environ.get('AIAJ_HOURLY_CAP', '500'))
        if count >= cap:
            print('Hourly account cap reached')
            break
        row = c.execute('''
            SELECT m.* FROM messages m
            LEFT JOIN throttle t ON t.domain = m.domain
            LEFT JOIN suppression s ON s.email = m.recipient
            WHERE m.status IN ('queued', 'retry')
              AND m.next_at <= ?
              AND coalesce(t.next_at, 0) <= ?
              AND s.email IS NULL
            ORDER BY m.next_at, m.id
            LIMIT 1
        ''', (t, t)).fetchone()
        if not row:
            break
        if dry_run:
            print('READY', row['id'], row['recipient'])
            break

        domain = row['domain']
        thr = c.execute('SELECT * FROM throttle WHERE domain=?', (domain,)).fetchone()
        interval = thr['interval_seconds'] if thr else 120.0
        c.execute('UPDATE messages SET status=?,attempts=attempts+1,updated_at=? WHERE id=?', ('sending', t, row['id']))
        c.commit()
        try:
            smtp_send(row)
            c.execute("UPDATE messages SET status='smtp_accepted',updated_at=?,last_error=NULL WHERE id=?", (now(), row['id']))
            event(c, row, 'smtp_accepted', 'SMTP submission accepted; final delivery not proven')
            streak = (thr['success_streak'] if thr else 0) + 1
            if streak >= 20:
                interval = max(30, interval * 0.9)
                streak = 0
            c.execute('INSERT OR REPLACE INTO throttle VALUES(?,?,?,?)', (domain, interval, now() + interval + random.uniform(0, 10), streak))
            sent += 1
        except (smtplib.SMTPResponseException, smtplib.SMTPRecipientsRefused, smtplib.SMTPException, OSError) as e:
            code = getattr(e, 'smtp_code', None)
            reason = str(e)
            if isinstance(e, smtplib.SMTPRecipientsRefused):
                val = next(iter(e.recipients.values()), (None, ''))
                code = val[0]
                reason = str(val[1])
            temporary = (code is None or 400 <= int(code) < 500)
            if temporary:
                interval = min(3600, max(120, interval * 2))
                delay = min(86400, 300 * 2 ** min(row['attempts'], 7))
                status = 'retry' if row['attempts'] < 8 else 'manual_review'
                c.execute('UPDATE messages SET status=?,next_at=?,last_code=?,last_error=?,updated_at=? WHERE id=?',
                          (status, now() + delay, code, reason[:1000], now(), row['id']))
                c.execute('INSERT OR REPLACE INTO throttle VALUES(?,?,?,?)', (domain, interval, now() + interval, 0))
                event(c, row, 'deferred', reason)
            else:
                c.execute('UPDATE messages SET status=?,last_code=?,last_error=?,updated_at=? WHERE id=?',
                          ('failed', code, reason[:1000], now(), row['id']))
                c.execute('INSERT OR REPLACE INTO suppression VALUES(?,?,?)', (row['recipient'], 'SMTP ' + str(code) + ' ' + reason[:200], now()))
                event(c, row, 'permanent_failure', reason)
            print('SMTP ERROR', row['recipient'], code, reason[:150])
        c.commit()
    print('Accepted by SMTP this run:', sent)


def status(c):
    print('Queue:', json.dumps({r[0]: r[1] for r in c.execute('SELECT status,count(*) FROM messages GROUP BY status')}))
    print('Suppressed:', c.execute('SELECT count(*) FROM suppression').fetchone()[0])
    print('Accepted last hour:', c.execute("SELECT count(*) FROM events WHERE event='smtp_accepted' AND at>=?", (now() - 3600,)).fetchone()[0])


p = argparse.ArgumentParser()
p.add_argument('command', choices=['init', 'import', 'suppress', 'run', 'status', 'reset-inflight'])
p.add_argument('--file')
p.add_argument('--limit', type=int, default=100)
p.add_argument('--max-seconds', type=int, default=660)
p.add_argument('--live', action='store_true')
args = p.parse_args()

c = connect()
init(c)

if args.command == 'import':
    import_csv(c, args.file)
elif args.command == 'suppress':
    suppress(c, args.file)
elif args.command == 'run':
    run(c, args.limit, not args.live, args.max_seconds)
elif args.command == 'status':
    status(c)
elif args.command == 'reset-inflight':
    c.execute("UPDATE messages SET status='manual_review',last_error='Previous run interrupted during SMTP submission; investigate before retry',updated_at=? WHERE status='sending'", (now(),))
    c.commit()
    print('In-flight messages quarantined for manual review to avoid duplicate delivery')
else:
    print('Database initialized:', DB)

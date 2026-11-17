#!/usr/bin/env python3
"""AIAJ outreach worker - sends cold email from outreach@aiaj.solutions through Namecheap's SMTP server.

Runs on GitHub Actions (see .github/workflows/outreach.yml). Each weekday morning it:
  1. checks the Namecheap inbox for replies, bounces and unsubscribes and reports them,
  2. sends from 8:00 AM to noon Pacific at about 125 emails per hour, at irregular, human-looking times,
  3. asks the AIAJ Google Apps Script web app for each next email and reports every result back.

The lead list, templates, daily cap and on/off switch live in the Google Sheet + Apps Script, so this
repository holds no lead data. Nothing sensitive is printed to the (public) Actions log.

Brakes: stops for the day after repeated throttling (4xx); stops outreach entirely (and alerts you) on a
login failure, a spam/policy block, or a hard-bounce rate above 10%.

Usage:
  python outreach_worker.py run            # the morning send (what the schedule runs)
  python outreach_worker.py test           # send ONE test email to $TEST_TO and exit
  python outreach_worker.py check-inbox    # only scan the inbox for replies/bounces
"""
import datetime as dt
import email
import email.utils
import hashlib
import imaplib
import json
import os
import random
import re
import smtplib
import ssl
import sys
import time
import urllib.parse
import urllib.request
from email.message import EmailMessage
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Los_Angeles")
env = os.environ.get

WEBAPP = env("AIAJ_WEBAPP_URL", "")
KEY = env("AIAJ_KEY", "")
SMTP_HOST = env("SMTP_HOST", "mail.privateemail.com")
SMTP_PORT = int(env("SMTP_PORT", "465"))
SMTP_SECURITY = env("SMTP_SECURITY", "ssl")          # ssl | starttls | plain (plain = tests only)
SMTP_USER = env("SMTP_USER", "")
SMTP_PASSWORD = env("SMTP_PASSWORD", "")
IMAP_HOST = env("IMAP_HOST", "mail.privateemail.com")
IMAP_PORT = int(env("IMAP_PORT", "993"))
HOURLY = int(env("AIAJ_HOURLY", "18"))
START_HOUR = float(env("AIAJ_START_HOUR", "8"))
STOP_HOUR = float(env("AIAJ_STOP_HOUR", "12"))
MIN_GAP = float(env("AIAJ_MIN_GAP", "6"))             # seconds between any two sends, at least
TIME_SCALE = float(env("AIAJ_TIME_SCALE", "1"))       # tests only: 0.001 runs a morning in seconds
SAVE_SENT = env("AIAJ_SAVE_SENT", "true").lower() == "true"
DRY_RUN = env("AIAJ_DRY_RUN", "false").lower() == "true"
FROM_DOMAIN = "aiaj.solutions"

UNSUB_WORDS = re.compile(r"\b(unsubscribe|no thanks|not interested|remove me|stop emailing|take me off)\b", re.I)
BLOCK_WORDS = re.compile(r"(spam|policy|blocked|blacklist|reputation|abuse|suspend|rate limit|too many)", re.I)


def log(msg):
    print(dt.datetime.now(TZ).strftime("%H:%M:%S"), msg, flush=True)


def tag(addr):
    """Short anonymous label for logs (the Actions log of a public repo is public)."""
    return hashlib.sha256(addr.lower().encode()).hexdigest()[:8]


def now():
    return dt.datetime.now(TZ)


def sleep(seconds):
    if seconds > 0:
        time.sleep(seconds * TIME_SCALE)


# ---------------------------------------------------------------- web app API (Google Apps Script)

def api(route, body):
    if not WEBAPP or not KEY:
        raise SystemExit("AIAJ_WEBAPP_URL and AIAJ_KEY must be set (GitHub repository secrets).")
    url = WEBAPP + ("&" if "?" in WEBAPP else "?") + urllib.parse.urlencode({"route": route, "key": KEY})
    data = json.dumps(body).encode()
    last = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=60) as r:   # Apps Script answers POST with a redirect; urllib follows it
                txt = r.read().decode("utf-8", "replace")
            out = json.loads(txt)
            if isinstance(out, dict) and out.get("error"):
                raise RuntimeError(out["error"])
            return out
        except Exception as e:  # network blips, Apps Script cold starts
            last = e
            sleep(5 * (attempt + 1))
    raise RuntimeError(f"web app {route} failed: {last}")


def stop_everything(reason):
    log("EMERGENCY STOP: " + reason)
    try:
        api("outreach-stop", {"reason": reason})
    except Exception as e:
        log(f"could not reach web app to stop: {e}")


# ---------------------------------------------------------------- SMTP

class Sender:
    def __init__(self):
        self.s = None

    def connect(self):
        self.close()
        if SMTP_SECURITY == "ssl":
            self.s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=60, context=ssl.create_default_context())
        else:
            self.s = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=60)
            if SMTP_SECURITY == "starttls":
                self.s.starttls(context=ssl.create_default_context())
        if SMTP_USER:
            self.s.login(SMTP_USER, SMTP_PASSWORD)

    def close(self):
        if self.s:
            try:
                self.s.quit()
            except Exception:
                pass
        self.s = None

    def send(self, msg):
        for attempt in range(2):
            try:
                if not self.s:
                    self.connect()
                self.s.send_message(msg)
                return
            except (smtplib.SMTPServerDisconnected, ConnectionError, TimeoutError, ssl.SSLError) as e:
                self.s = None
                if attempt:
                    raise smtplib.SMTPResponseException(451, f"connection lost: {e}".encode())


def build(item, from_addr, from_name):
    m = EmailMessage()
    m["From"] = email.utils.formataddr((from_name, from_addr))
    m["To"] = item["email"]
    m["Subject"] = item["subject"]
    m["Date"] = email.utils.formatdate(localtime=True)
    m["Message-ID"] = email.utils.make_msgid(domain=FROM_DOMAIN)
    m["Reply-To"] = from_addr
    m["List-Unsubscribe"] = f"<mailto:{from_addr}?subject=unsubscribe>"
    m.set_content(item["body"])
    return m


QUOTA_WORDS = re.compile(r"too many messages from sender|in last 60 minutes|sending limit|quota", re.I)


def classify(e):
    """-> (kind, code, text); kind in ok|permanent|temporary|account|quota"""
    text0 = str(getattr(e, "smtp_error", b"") or b"")
    if QUOTA_WORDS.search(text0):
        t = e.smtp_error.decode("utf-8", "replace") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        return "quota", getattr(e, "smtp_code", 0), t
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return "account", e.smtp_code, "login failed - check SMTP_USER / SMTP_PASSWORD"
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        code, text = list(e.recipients.values())[0]
        text = text.decode("utf-8", "replace") if isinstance(text, bytes) else str(text)
        if code >= 500 and BLOCK_WORDS.search(text):
            return "account", code, text
        return ("permanent" if code >= 500 else "temporary"), code, text
    if isinstance(e, smtplib.SMTPSenderRefused):
        text = e.smtp_error.decode("utf-8", "replace") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        return "account", e.smtp_code, text
    if isinstance(e, smtplib.SMTPResponseException):
        text = e.smtp_error.decode("utf-8", "replace") if isinstance(e.smtp_error, bytes) else str(e.smtp_error)
        if BLOCK_WORDS.search(text) and e.smtp_code >= 500:
            return "account", e.smtp_code, text
        return ("permanent" if e.smtp_code >= 500 else "temporary"), e.smtp_code, text
    return "temporary", 0, str(e)


# ---------------------------------------------------------------- IMAP: replies, bounces, unsubscribes, sent copies

def imap_login():
    im = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, ssl_context=ssl.create_default_context())
    im.login(SMTP_USER, SMTP_PASSWORD)
    return im


def scan_inbox(days=21):
    replied, bounced, unsub = set(), set(), set()
    im = imap_login()
    try:
        im.select("INBOX", readonly=True)
        since = (dt.date.today() - dt.timedelta(days=days)).strftime("%d-%b-%Y")
        typ, data = im.search(None, "SINCE", since)
        ids = data[0].split()[-800:]
        for i in ids:
            typ, parts = im.fetch(i, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT CONTENT-TYPE)] BODY.PEEK[TEXT]<0.6000>)")
            raw_head = raw_text = b""
            for p in parts:
                if isinstance(p, tuple):
                    if b"HEADER" in p[0]:
                        raw_head = p[1]
                    else:
                        raw_text = p[1]
            h = email.message_from_bytes(raw_head)
            frm = email.utils.parseaddr(h.get("From", ""))[1].lower()
            subj = h.get("Subject", "") or ""
            ctype = h.get("Content-Type", "") or ""
            text = raw_text.decode("utf-8", "replace")
            if not frm or frm.endswith("@" + FROM_DOMAIN):
                continue
            if re.match(r"(mailer-daemon|postmaster)@", frm) or "report-type=delivery-status" in ctype.replace(" ", "").lower():
                for addr in re.findall(r"(?:Final|Original)-Recipient:\s*rfc822;\s*<?([^\s>]+@[^\s>]+)>?", text, re.I):
                    bounced.add(addr.lower())
                continue
            if UNSUB_WORDS.search(subj) or UNSUB_WORDS.search(text[:600]):
                unsub.add(frm)
            else:
                replied.add(frm)
    finally:
        try:
            im.logout()
        except Exception:
            pass
    return replied, bounced, unsub


def report_inbox():
    try:
        replied, bounced, unsub = scan_inbox()
        res = api("outreach-inbox", {"replied": sorted(replied), "bounced": sorted(bounced), "unsubscribed": sorted(unsub)})
        log(f"inbox: marked replied={res.get('replied', 0)} bounced={res.get('bounced', 0)} unsubscribed={res.get('unsubscribed', 0)}")
    except Exception as e:
        log(f"inbox check skipped: {e}")


class SentFolder:
    """Best-effort copy of each sent email into the mailbox's Sent folder, so webmail shows the conversation."""

    def __init__(self):
        self.im = None
        self.box = None
        self.broken = not SAVE_SENT

    def save(self, msg):
        if self.broken:
            return
        try:
            if not self.im:
                self.im = imap_login()
                typ, boxes = self.im.list()
                names = [b.decode().split(' "/" ')[-1].strip('"') for b in boxes or []]
                self.box = next((n for n in names if n.lower() in ("sent", "sent items", "inbox.sent", "sent messages")), "Sent")
            self.im.append(self.box, "\\Seen", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        except Exception as e:
            log(f"(sent-copy disabled: {e})")
            self.broken = True

    def close(self):
        if self.im:
            try:
                self.im.logout()
            except Exception:
                pass


# ---------------------------------------------------------------- the morning schedule

def morning_slots(start, stop):
    """Irregular, human-looking send times: HOURLY moments per hour, random gaps (with a couple of longer
    "coffee breaks"), never closer than MIN_GAP - including across hour boundaries."""
    slots, t = [], start
    while t < stop:
        hour_end = min(t + dt.timedelta(hours=1), stop)
        span = (hour_end - t).total_seconds()
        n = int(round(HOURLY * span / 3600.0))
        mins = [MIN_GAP / 2] + [MIN_GAP] * (n - 1) + [MIN_GAP / 2] if n else []
        if n and sum(mins) <= span:
            w = [random.expovariate(1.0) for _ in range(n + 1)]
            for i in random.sample(range(n + 1), k=min(2, n + 1)):
                w[i] *= random.uniform(4, 8)          # the occasional longer pause
            free, tot = span - sum(mins), sum(w)
            gaps = [m + free * x / tot for m, x in zip(mins, w)]
            pos = 0.0
            for g in gaps[:-1]:
                pos += g
                slots.append(t + dt.timedelta(seconds=pos))
        t = hour_end
    return slots


def run():
    today = now().date()
    start = dt.datetime.combine(today, dt.time(0), TZ) + dt.timedelta(hours=START_HOUR)
    stop = dt.datetime.combine(today, dt.time(0), TZ) + dt.timedelta(hours=STOP_HOUR)
    if now().weekday() >= 5:
        log("weekend - nothing to do")
        return
    if now() >= stop:
        log("past the stop time - nothing to do")
        return
    if now() < start:
        wait = (start - now()).total_seconds()
        log(f"waiting {int(wait // 60)} min until {start:%H:%M} Pacific")
        sleep(wait)

    report_inbox()
    slots = morning_slots(max(now(), start), stop)
    log(f"planned up to {len(slots)} sends until {stop:%H:%M} Pacific")

    sender, sent_box = Sender(), SentFolder()
    queue, meta = [], {}
    sent = perm = temp_streak = quota_waits = 0
    last_inbox = slots[0] if slots else now()
    try:
        for slot in slots:
            wait = (slot - now()).total_seconds()
            if wait > 0:
                sleep(wait)
            if now() >= stop:
                break
            if (slot - last_inbox).total_seconds() >= 3600:   # hourly: catch new replies before more follow-ups go out
                report_inbox()
                last_inbox = slot
            if not queue:
                res = api("outreach-claim", {"n": 10})
                if not res.get("on"):
                    log("outreach is switched OFF in the sheet - stopping")
                    break
                queue = list(res.get("items") or [])
                meta = {"from": res.get("from") or "outreach@aiaj.solutions", "name": res.get("name") or "AIAJ Solutions"}
                if not queue:
                    log("no more emails to send today (daily cap reached or no leads left)")
                    break
            item = queue.pop(0)
            msg = build(item, meta["from"], meta["name"])
            if DRY_RUN:
                log(f"DRY RUN would send step {item['step']} to {tag(item['email'])}")
                api("outreach-result", {"lead_id": item["lead_id"], "ok": False, "permanent": False, "code": 0, "error": "dry run"})
                continue
            try:
                sender.send(msg)
            except Exception as e:
                kind, code, text = classify(e)
                log(f"send to {tag(item['email'])} failed: {kind} {code}")
                if kind == "quota":
                    quota_waits += 1
                    sender.close()
                    if quota_waits > 3:
                        api("outreach-result", {"lead_id": item["lead_id"], "ok": False, "permanent": False, "code": code, "error": "hourly limit"})
                        for q in queue:
                            api("outreach-result", {"lead_id": q["lead_id"], "ok": False, "permanent": False, "code": 0, "error": "hourly limit"})
                        queue = []
                        log("Namecheap hourly limit hit repeatedly - done for today")
                        break
                    queue.insert(0, item)
                    log("Namecheap hourly limit reached - pausing 20 min")
                    sleep(1200)
                    continue
                if kind == "account":
                    api("outreach-result", {"lead_id": item["lead_id"], "ok": False, "permanent": False, "code": code, "error": text[:150]})
                    for q in queue:
                        api("outreach-result", {"lead_id": q["lead_id"], "ok": False, "permanent": False, "code": 0, "error": "stopped"})
                    queue = []
                    stop_everything(f"Namecheap refused sending ({code}): {text[:200]}")
                    return 2
                api("outreach-result", {"lead_id": item["lead_id"], "ok": False, "permanent": kind == "permanent", "code": code, "error": text[:150]})
                if kind == "permanent":
                    perm += 1
                    temp_streak = 0
                else:
                    temp_streak += 1
                    if temp_streak >= 3:
                        log("throttled 3 times in a row - done for today")
                        break
                    sleep(600)
                    sender.close()
                if sent + perm >= 30 and perm / (sent + perm) > 0.10:
                    for q in queue:
                        api("outreach-result", {"lead_id": q["lead_id"], "ok": False, "permanent": False, "code": 0, "error": "stopped"})
                    queue = []
                    stop_everything(f"hard-bounce rate {perm}/{sent + perm} is over 10% - clean the lead list before resuming")
                    return 3
                continue
            temp_streak = quota_waits = 0
            sent += 1
            api("outreach-result", {"lead_id": item["lead_id"], "ok": True})
            sent_box.save(msg)
            if sent % 25 == 0:
                log(f"sent {sent} so far")
    finally:
        for q in queue:   # give back anything claimed but not sent
            try:
                api("outreach-result", {"lead_id": q["lead_id"], "ok": False, "permanent": False, "code": 0, "error": "not sent"})
            except Exception:
                pass
        sender.close()
        sent_box.close()
    log(f"done: {sent} sent, {perm} hard bounces")
    return 0


def test():
    to = env("TEST_TO", "")
    if not to:
        raise SystemExit("Set TEST_TO (the workflow's test_to input) to your own address.")
    item = {"email": to, "subject": "AIAJ outreach test", "body": "Test from the AIAJ outreach worker via Namecheap SMTP.\n\n- AIAJ", "step": 0}
    msg = build(item, env("SMTP_FROM", SMTP_USER or "outreach@aiaj.solutions"), "Alaric Scott | AIAJ Solutions")
    s = Sender()
    s.send(msg)
    s.close()
    SentFolder().save(msg)
    log("test email accepted by the SMTP server")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "run":
        sys.exit(run() or 0)
    if mode == "test":
        test()
    elif mode == "check-inbox":
        report_inbox()
    else:
        raise SystemExit(__doc__)

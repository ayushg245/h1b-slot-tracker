# H-1B slot tracker

Pings your phone (or email) within minutes when someone reports new H-1B
appointment slots at a US consulate in India.

It reads **public, crowdsourced reports only**, from VisaGrader's consulate
pages. It never logs in to the visa appointment site, never asks for or stores
your visa-site password, and never touches a CAPTCHA. When it alerts you, you
open the official site and book yourself.

## What it watches

| Consulate | Page it reads |
|---|---|
| Mumbai | visagrader.com/us-visa-time-slots-availability/india-ind/mumbai-P139/h1b-visa-H1B |
| Hyderabad | …/hyderabad-P85/h1b-visa-H1B |
| Chennai | …/chennai-P48/h1b-visa-H1B |
| New Delhi | …/new-delhi-P147/h1b-visa-H1B |
| Kolkata | …/kolkata-P100/h1b-visa-H1B |

Each page has a "Recent slot activity" table (appointment type, availability
date, slots, reported time). The tracker remembers every row it has seen in a
small SQLite file (`state/slots.sqlite3`) and alerts only on rows it hasn't
seen before. The first run just learns what's already there and sends nothing.

Built-in filters (all in `config.toml`):

- **Appointment types:** dropbox, interview, biometrics (pick any).
- **Bad timestamps:** some community reports carry impossible future "reported"
  times (on Nov 23, 2026 Hyderabad showed rows "reported" in May 2027). Rows
  more than 12 hours in the future are skipped. The 12 hours of slack covers
  reports that look like India time labelled as UTC.
- **Past slot dates** and **reports older than 48 hours** are skipped.

### Sources left out

- **Check US Visa Slots (checkvisaslots.com):** its website sits behind a bot
  checkpoint that turned away an automated browser on Nov 23, 2026, so a
  script shouldn't poll it. If you like it, use its own app or Chrome
  extension alerts.
- **The official appointment site:** not read at all, by design.

### VisaGrader's rules

- `robots.txt` (Nov 23, 2026) allows these pages and disallows `/api` and
  `/dashboard`. The tracker checks robots.txt on every run and only reads the
  pages. It never calls `/api`.
- Its Terms of Service say the content is copyrighted, for educational,
  non-commercial use, with no feed republishing and credit required. So keep
  this **personal**: alerts to yourself only, not reposted to a group or
  channel. Each alert credits VisaGrader and links to the page.
- It polls each page at most every 3 minutes (default 5), spaces requests a
  few seconds apart, sends a clear user agent and backs off on errors or
  HTTP 429.

## Setup (about 10 minutes)

Needs Python 3.11 or newer.

```
cd h1b-slot-tracker
python3 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.toml config.toml
```

### 1. Pick how you get alerts

Secrets go in environment variables, never in `config.toml`.

**ntfy (recommended, free, no account):**
1. Install the ntfy app (iOS or Android).
2. In the app, subscribe to a topic with a long, unguessable name, like
   `h1b-ayush-7Kq2vX9mP4`. Anyone who knows the name can read it, so treat it
   like a password.
3. Set it: `export NTFY_TOPIC=h1b-ayush-7Kq2vX9mP4`
4. Keep `alert_channels = ["console", "ntfy"]` in `config.toml`.

**Telegram bot (free):**
1. In Telegram, message @BotFather, send `/newbot`, and copy the token.
2. Send your new bot any message, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` and copy `chat.id`.
3. `export TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...`
4. Add `"telegram"` to `alert_channels`.

**Email (SMTP):**
1. For Gmail, create an App Password (Google Account, Security, App passwords).
   Don't use your normal password.
2. `export SMTP_USER=you@gmail.com SMTP_PASSWORD=<app password> EMAIL_TO=you@gmail.com`
   (`SMTP_HOST` defaults to smtp.gmail.com and `SMTP_PORT` to 587.)
3. Add `"email"` to `alert_channels`.

### 2. Check it works

```
python -m h1b_tracker check        # reads the 5 pages, prints the rows it parsed, sends nothing
python -m h1b_tracker test-alert   # sends a test message to your channels
python -m h1b_tracker run          # first run: remembers what's there, sends nothing
```

`check` is the live test. You should see a line per row, ending in `-> alert`
or the reason it would be skipped. A page with no recent activity prints
nothing, which is normal. If you see `PROBLEM ... layout may have changed`,
set `fetch_mode = "browser"` in `config.toml`, then
`pip install playwright && python -m playwright install chromium`.

### 3. Keep it running

Pick one.

**A. On your computer, simplest (fastest alerts):**
```
python -m h1b_tracker loop
```
Checks every `interval_seconds` (default 300) with a little random delay.
Leave a terminal open, or run it with `nohup` / `tmux`.

**B. cron (Mac or Linux), every 5 minutes:**
```
crontab -e
*/5 * * * * cd /path/to/h1b-slot-tracker && NTFY_TOPIC=your-topic .venv/bin/python -m h1b_tracker run >> tracker.log 2>&1
```
(On a Mac, cron only runs while it's awake.)

**C. GitHub Actions (free, runs when your laptop is off):**
1. Put this folder in a GitHub repository. Make it **public** if you want
   every 5 minutes: public repos get unlimited free minutes, while a private
   repo at this pace uses more than the free 2,000 minutes a month (use
   `*/30` there). Your secrets stay hidden either way.
2. Repository Settings, Secrets and variables, Actions: add `NTFY_TOPIC`
   (and/or the Telegram or email secrets).
3. Edit `config.toml` (or leave the example) and push. The workflow in
   `.github/workflows/tracker.yml` runs every 5 minutes and keeps its memory
   in the Actions cache. Run it once by hand from the Actions tab.

GitHub starts scheduled runs late when it's busy, often 5 to 15 minutes, so
A or B gets you alerts faster.

## What an alert looks like

```
H1B dropbox slot reported: Hyderabad
- Hyderabad dropbox: 5 slots on Thu Dec 10, 2026, reported Nov 23 15:40 UTC (10:40 AM EST)

Crowdsourced report from VisaGrader (visagrader.com), not verified. Slots often
go within minutes. Book only yourself, on the official site.
```

If the pages change shape and can't be read, you get one "H-1B tracker needs a
look" message (at most every 12 hours) instead of silence.

## Files

- `h1b_tracker/sources.py`: consulate pages, robots.txt check, polite fetching
- `h1b_tracker/parser.py`: reads the "Recent slot activity" table by its column names
- `h1b_tracker/state.py`: SQLite memory of seen reports
- `h1b_tracker/notify.py`: ntfy, Telegram, email, console
- `h1b_tracker/cli.py`: `check`, `run`, `loop`, `test-alert`
- `tests/`: `python -m unittest` (16 tests, sample pages saved from Nov 23, 2026)

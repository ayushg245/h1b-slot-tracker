"""Command line: check, run (once), loop, test-alert."""

from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

from . import notify
from .config import Config, load
from .parser import LayoutChanged, SlotReport, parse_activity
from .sources import DEFAULT_UA, PoliteFetcher, make_fetcher, pages_for
from .state import State

log = logging.getLogger("h1b_tracker")

FOOTER = ("Crowdsourced report from VisaGrader (visagrader.com), not verified. "
          "Slots often go within minutes. Book only yourself, on the official site.")


def collect(cfg: Config, fetcher: PoliteFetcher):
    """Fetch and parse every configured page. Returns (reports, problems)."""
    reports: list[SlotReport] = []
    problems: list[tuple[str, str, bool]] = []  # (consulate, message, is_layout_problem)
    for page in pages_for(cfg.consulates, cfg.visa):
        try:
            html = fetcher.get(page.url)
            rows = parse_activity(html, page.consulate_name, page.visa, page.url)
            log.info("%s: %d row(s) in recent activity", page.consulate_name, len(rows))
            reports.extend(rows)
        except LayoutChanged as e:
            problems.append((page.consulate_name, str(e), True))
        except PermissionError as e:
            problems.append((page.consulate_name, str(e), False))
        except requests.RequestException as e:
            problems.append((page.consulate_name, f"fetch failed: {e}", False))
    for c, msg, _ in problems:
        log.warning("%s: %s", c, msg)
    return reports, problems


def classify(r: SlotReport, cfg: Config, now: datetime) -> str:
    """'alert', or the reason a row is skipped."""
    if r.appointment_type not in cfg.appointment_types:
        return "type not watched"
    if r.slots is not None and r.slots < cfg.min_slots:
        return "too few slots"
    if cfg.skip_past_availability and r.availability_date and r.availability_date < now.date():
        return "slot date already passed"
    if r.reported_at:
        if cfg.skip_future_reports and r.reported_at > now + timedelta(hours=cfg.future_tolerance_hours):
            return "reported time is in the future (bad data)"
        if r.reported_at < now - timedelta(hours=cfg.max_report_age_hours):
            return "old report"
    return "alert"


def suspect_date(r: SlotReport, cfg: Config, now: datetime) -> bool:
    """True when the report's own 'reported' time is impossibly far in the future."""
    return bool(r.reported_at and r.reported_at > now + timedelta(hours=cfg.future_tolerance_hours))


def _fmt_dt(dt: datetime | None, raw: str, tz: ZoneInfo) -> str:
    if not dt:
        return raw
    local = dt.astimezone(tz)
    return f"{dt:%b} {dt.day} {dt:%H:%M} UTC ({local.strftime('%I:%M %p').lstrip('0')} {local.tzname()})"


def format_alert(new: list[SlotReport], cfg: Config, now: datetime | None = None) -> tuple[str, str, str]:
    tz = ZoneInfo(cfg.display_timezone)
    now = now or datetime.now(timezone.utc)
    lines = []
    for r in sorted(new, key=lambda x: (x.consulate, x.appointment_type, x.availability_raw)):
        d = r.availability_date
        when = f"{d:%a %b} {d.day}, {d.year}" if d else r.availability_raw
        slots = f"{r.slots} slot{'s' if r.slots != 1 else ''}" if r.slots is not None else "slots"
        flag = " (SUSPECT: reported date is in the future, may be a typo)" if suspect_date(r, cfg, now) else ""
        lines.append(f"- {r.consulate} {r.appointment_type}: {slots} on {when}, "
                     f"reported {_fmt_dt(r.reported_at, r.reported_raw, tz)}{flag}")
    if len(new) == 1:
        r = new[0]
        title = f"{r.visa} {r.appointment_type} slot reported: {r.consulate}"
    else:
        cities = sorted({r.consulate for r in new})
        title = f"{len(new)} new {new[0].visa} slot reports: {', '.join(cities)}"
    body = "\n".join(lines) + "\n\n" + FOOTER
    return title, body, new[0].source_url


def run_once(cfg: Config, *, dry_run: bool = False, alert_existing: bool = False,
             fetcher: PoliteFetcher | None = None, notifiers=None, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    fetcher = fetcher or make_fetcher(cfg.fetch_mode, cfg.user_agent or DEFAULT_UA)
    notifiers = notifiers if notifiers is not None else notify.build(cfg.alert_channels, cfg.ntfy_priority)
    state = State(cfg.state_file)
    try:
        reports, problems = collect(cfg, fetcher)
        seeding = state.is_empty() and not alert_existing and state.get_meta("seeded") is None

        fresh: list[SlotReport] = []
        for r in reports:
            if state.has(r.uid):
                continue
            verdict = classify(r, cfg, now)
            if seeding or verdict != "alert":
                if not seeding:
                    log.info("Skip %s %s %s: %s", r.consulate, r.appointment_type, r.availability_raw, verdict)
                state.add(r, alerted=False)
            else:
                fresh.append(r)

        if seeding:
            state.set_meta("seeded", now.isoformat())
            log.info("First run: remembered %d existing report(s) without alerting. "
                     "From now on only new reports alert.", len(reports))
        elif fresh:
            title, body, url = format_alert(fresh, cfg, now)
            if dry_run:
                print(f"[dry run] would alert:\n{title}\n{body}")
                sent = True
            else:
                sent = notify.send_all(notifiers, title, body, url) > 0 or not notifiers
            if sent:
                for r in fresh:
                    state.add(r, alerted=True)
            else:
                log.error("No alert channel worked; will retry these %d report(s) next run", len(fresh))
        else:
            log.info("No new reports.")

        layout = [p for p in problems if p[2]]
        if layout and not dry_run:
            last = state.get_meta("layout_alert_at")
            due = not last or datetime.fromisoformat(last) < now - timedelta(hours=cfg.layout_alert_every_hours)
            if due:
                notify.send_all(notifiers, "H-1B tracker needs a look",
                                "The slot pages changed shape, so the tracker can't read them:\n"
                                + "\n".join(f"- {c}: {m}" for c, m, _ in layout))
                state.set_meta("layout_alert_at", now.isoformat())

        state.set_meta("last_run", now.isoformat())
        state.prune()
        state.commit()
        # Non-zero exit only when every page failed, so a scheduler shows it as failing.
        return 2 if problems and len(problems) == len(cfg.consulates) else 0
    finally:
        state.close()


def cmd_check(cfg: Config) -> int:
    now = datetime.now(timezone.utc)
    reports, problems = collect(cfg, make_fetcher(cfg.fetch_mode, cfg.user_agent or DEFAULT_UA))
    tz = ZoneInfo(cfg.display_timezone)
    for r in reports:
        print(f"{r.consulate:10} {r.appointment_type:10} avail={r.availability_raw:11} "
              f"slots={r.slots!s:3} reported={_fmt_dt(r.reported_at, r.reported_raw, tz):34} -> {classify(r, cfg, now)}"
              f"{' (suspect date)' if suspect_date(r, cfg, now) else ''}")
    if not reports:
        print("No rows on any page (pages that say 'No slot activity' are normal).")
    for c, m, _ in problems:
        print(f"PROBLEM {c}: {m}")
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="h1b_tracker", description="Alerts you to new public H-1B slot reports.")
    ap.add_argument("--config", default="config.toml", help="path to config.toml (default: ./config.toml)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check", help="fetch the pages once and print what was parsed; sends nothing")
    r = sub.add_parser("run", help="check once and alert on new reports (for cron or GitHub Actions)")
    r.add_argument("--dry-run", action="store_true", help="print the alert instead of sending it")
    r.add_argument("--alert-existing", action="store_true", help="on the first run, alert on what's already there")
    lp = sub.add_parser("loop", help="keep running, checking every interval_seconds (with jitter)")
    lp.add_argument("--dry-run", action="store_true")
    sub.add_parser("test-alert", help="send a test message through your alert channels")
    a = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    cfg = load(a.config)

    if a.cmd == "check":
        return cmd_check(cfg)
    if a.cmd == "test-alert":
        ns = notify.build(cfg.alert_channels, cfg.ntfy_priority)
        ok = notify.send_all(ns, "H-1B tracker test", "If you can read this, alerts work.")
        print(f"Sent through {ok} of {len(cfg.alert_channels)} channel(s).")
        return 0 if ok == len(cfg.alert_channels) else 1
    if a.cmd == "run":
        return run_once(cfg, dry_run=a.dry_run, alert_existing=a.alert_existing)
    if a.cmd == "loop":
        fetcher = make_fetcher(cfg.fetch_mode, cfg.user_agent or DEFAULT_UA)
        notifiers = notify.build(cfg.alert_channels, cfg.ntfy_priority)
        failures = 0
        while True:
            try:
                rc = run_once(cfg, dry_run=a.dry_run, fetcher=fetcher, notifiers=notifiers)
                failures = failures + 1 if rc else 0
            except Exception:
                log.exception("Run failed")
                failures += 1
            # Back off when the site keeps failing, up to an hour.
            wait = min(cfg.interval_seconds * (2 ** min(failures, 4)), 3600)
            time.sleep(wait + random.uniform(0, 45))
    return 0

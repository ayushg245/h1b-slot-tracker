import os
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest import mock

from h1b_tracker import cli, notify
from h1b_tracker.config import Config
from h1b_tracker.parser import LayoutChanged, parse_activity, parse_datetime_utc

FX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 23, 16, 0, tzinfo=timezone.utc)


def read(name):
    return (FX / name).read_text()


class ParserTests(unittest.TestCase):
    def test_hyderabad_rows(self):
        rows = parse_activity(read("hyderabad_h1b.html"), "Hyderabad", "H1B", "u")
        self.assertEqual(len(rows), 5)
        r = rows[2]
        self.assertEqual(r.appointment_type, "interview")
        self.assertEqual(r.availability_date, date(2026, 12, 2))
        self.assertEqual(r.slots, 1)
        self.assertEqual(r.reported_at, datetime(2026, 11, 30, 13, 19, tzinfo=timezone.utc))

    def test_header_as_td_and_extra_tables(self):
        rows = parse_activity(read("chennai_h1b_divs.html"), "Chennai", "H1B", "u")
        self.assertEqual([r.appointment_type for r in rows], ["biometrics", "interview"])
        self.assertEqual(rows[0].slots, 2)
        self.assertEqual(rows[1].reported_at, datetime(2026, 11, 23, 20, 53, tzinfo=timezone.utc))

    def test_no_activity_is_empty_not_error(self):
        self.assertEqual(parse_activity(read("kolkata_h1b_empty.html"), "Kolkata", "H1B", "u"), [])

    def test_layout_change_raises(self):
        with self.assertRaises(LayoutChanged):
            parse_activity(read("changed_layout.html"), "Mumbai", "H1B", "u")

    def test_iso_and_time_tag(self):
        self.assertEqual(parse_datetime_utc("2026-11-23T10:00:00Z"), datetime(2026, 11, 23, 10, tzinfo=timezone.utc))
        html = ("<table><tr><th>Appointment type</th><th>Availability date</th><th>Slots</th><th>Reported</th></tr>"
                "<tr><td>Dropbox</td><td>2026-12-01</td><td>4</td><td><time datetime='2026-11-23T09:00:00Z'>x</time></td></tr></table>")
        r = parse_activity(html, "Mumbai", "H1B", "u")[0]
        self.assertEqual((r.availability_date, r.reported_at.hour), (date(2026, 12, 1), 9))

    def test_uid_stable_and_distinct(self):
        a = parse_activity(read("hyderabad_h1b.html"), "Hyderabad", "H1B", "u")
        b = parse_activity(read("hyderabad_h1b.html"), "Hyderabad", "H1B", "u")
        self.assertEqual([x.uid for x in a], [x.uid for x in b])
        self.assertEqual(len({x.uid for x in a}), 5)


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.rows = {r.reported_raw + r.availability_raw: r for r in
                     parse_activity(read("hyderabad_h1b.html"), "Hyderabad", "H1B", "u")}
        self.cfg = Config().validate()

    def verdicts(self):
        return {k: cli.classify(r, self.cfg, NOW) for k, r in self.rows.items()}

    def test_defaults_alert_on_future_dates_marked_suspect(self):
        v = self.verdicts()
        self.assertEqual(v["5/12/2027, 4:11:00 PM5/12/2027"], "alert")
        self.assertEqual(v["11/23/2026, 12:38:00 PM4/19/2027"], "alert")
        sus = self.rows["5/12/2027, 4:11:00 PM5/12/2027"]
        ok = self.rows["11/23/2026, 12:38:00 PM4/19/2027"]
        self.assertTrue(cli.suspect_date(sus, self.cfg, NOW))
        self.assertFalse(cli.suspect_date(ok, self.cfg, NOW))
        _, body, _ = cli.format_alert([sus, ok], self.cfg, NOW)
        self.assertEqual(body.count("SUSPECT"), 1)

    def test_skip_future_reports_option(self):
        self.cfg.skip_future_reports = True
        v = self.verdicts()
        self.assertTrue(v["5/12/2027, 4:11:00 PM5/12/2027"].startswith("reported time is in the future"))
        self.assertTrue(v["11/30/2026, 1:19:00 PM12/2/2026"].startswith("reported time is in the future"))
        self.assertEqual(v["11/23/2026, 12:38:00 PM4/19/2027"], "alert")

    def test_type_filter_and_past(self):
        self.cfg.appointment_types = ["interview"]
        self.assertEqual(self.verdicts()["11/23/2026, 12:38:00 PM4/19/2027"], "type not watched")
        chennai = parse_activity(read("chennai_h1b_divs.html"), "Chennai", "H1B", "u")
        self.cfg.appointment_types = ["biometrics", "interview"]
        self.cfg.skip_future_reports = False
        self.assertEqual(cli.classify(chennai[0], self.cfg, NOW), "slot date already passed")


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages

    def get(self, url):
        for key, html in self.pages.items():
            if key in url:
                return html
        return read("kolkata_h1b_empty.html")


class RecordingNotifier(notify.Notifier):
    name = "rec"

    def __init__(self):
        self.sent = []

    def send(self, title, body, url=None):
        self.sent.append((title, body, url))


class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = Config(state_file=os.path.join(self.tmp.name, "s.sqlite3")).validate()
        self.n = RecordingNotifier()

    def tearDown(self):
        self.tmp.cleanup()

    def run_with(self, pages, now=NOW, **kw):
        return cli.run_once(self.cfg, fetcher=FakeFetcher(pages), notifiers=[self.n], now=now, **kw)

    def test_first_run_seeds_then_only_new_alert(self):
        base = read("hyderabad_h1b.html")
        self.assertEqual(self.run_with({"hyderabad": base}), 0)
        self.assertEqual(self.n.sent, [])  # seeding is silent
        self.run_with({"hyderabad": base})
        self.assertEqual(self.n.sent, [])  # nothing new
        newrow = "<tr><td>Dropbox</td><td>12/10/2026</td><td>5</td><td>11/23/2026, 3:40:00 PM</td></tr></tbody>"
        self.run_with({"hyderabad": base.replace("</tbody>", newrow)})
        self.assertEqual(len(self.n.sent), 1)
        title, body, url = self.n.sent[0]
        self.assertIn("dropbox", title)
        self.assertIn("Hyderabad", title)
        self.assertIn("5 slots on Thu Dec 10, 2026", body)
        self.assertIn("10:40 AM EST", body)
        self.run_with({"hyderabad": base.replace("</tbody>", newrow)})
        self.assertEqual(len(self.n.sent), 1)  # no repeat

    def test_alert_existing_skips_bad_rows(self):
        self.run_with({"hyderabad": read("hyderabad_h1b.html")}, alert_existing=True)
        self.assertEqual(len(self.n.sent), 1)
        # two good 11/23 rows plus three future-dated rows, which alert marked SUSPECT
        self.assertIn("5 new H1B slot reports", self.n.sent[0][0])
        self.assertEqual(self.n.sent[0][1].count("SUSPECT"), 3)

    def test_alert_existing_skips_bad_rows_when_asked(self):
        self.cfg.skip_future_reports = True
        self.run_with({"hyderabad": read("hyderabad_h1b.html")}, alert_existing=True)
        self.assertIn("2 new H1B slot reports", self.n.sent[0][0])

    def test_failed_send_retries_next_run(self):
        class Broken(notify.Notifier):
            name = "broken"
            def send(self, *a, **k):
                raise RuntimeError("down")
        cli.run_once(self.cfg, fetcher=FakeFetcher({}), notifiers=[Broken()], now=NOW)  # seed
        page = read("kolkata_h1b_empty.html").replace(
            "<p>No slot", "<table><tr><th>Appointment type</th><th>Availability date</th><th>Slots</th>"
            "<th>Reported</th></tr><tr><td>Interview</td><td>1/5/2027</td><td>2</td><td>11/23/2026, 3:00:00 PM</td></tr></table><p>No slot")
        cli.run_once(self.cfg, fetcher=FakeFetcher({"kolkata": page}), notifiers=[Broken()], now=NOW)
        self.run_with({"kolkata": page})
        self.assertEqual(len(self.n.sent), 1)

    def test_layout_alert_is_throttled(self):
        bad = read("changed_layout.html")
        self.run_with({"mumbai": bad})
        self.run_with({"mumbai": bad})
        layout = [s for s in self.n.sent if "needs a look" in s[0]]
        self.assertEqual(len(layout), 1)


class NotifierTests(unittest.TestCase):
    def test_ntfy_posts_to_topic(self):
        with mock.patch.dict(os.environ, {"NTFY_TOPIC": "my-secret-topic"}), \
             mock.patch("h1b_tracker.notify.requests.post") as post:
            notify.NtfyNotifier().send("T", "B", "https://x")
            url = post.call_args.args[0]
            self.assertEqual(url, "https://ntfy.sh/my-secret-topic")
            self.assertEqual(post.call_args.kwargs["headers"]["Click"], "https://x")

    def test_missing_secrets_dont_crash(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(notify.build(["ntfy", "telegram", "email", "console"])[0].name, "console")


if __name__ == "__main__":
    unittest.main()


class RobotsTests(unittest.TestCase):
    """Uses VisaGrader's robots.txt as read on 2026-11-23."""
    ROBOTS = "User-Agent: *\nAllow: /\nDisallow: /dashboard\nDisallow: /api\n\nSitemap: https://visagrader.com/sitemap.xml\n"

    def fetcher(self):
        from h1b_tracker.sources import PoliteFetcher
        f = PoliteFetcher(min_gap_s=0)
        resp = mock.Mock(status_code=200, text=self.ROBOTS)
        f.s.get = mock.Mock(return_value=resp)
        return f

    def test_slot_pages_allowed_api_not(self):
        from h1b_tracker.sources import pages_for
        f = self.fetcher()
        for p in pages_for(["mumbai", "hyderabad", "chennai", "new-delhi", "kolkata"]):
            self.assertTrue(f._robots_ok(p.url), p.url)
        self.assertFalse(f._robots_ok("https://visagrader.com/api/slots"))

    def test_unreachable_robots_is_a_fetch_error(self):
        import requests
        from h1b_tracker.sources import PoliteFetcher
        f = PoliteFetcher(min_gap_s=0)
        f.s.get = mock.Mock(side_effect=requests.ConnectionError("blocked"))
        with self.assertRaises(requests.RequestException):
            f.get("https://visagrader.com/x")

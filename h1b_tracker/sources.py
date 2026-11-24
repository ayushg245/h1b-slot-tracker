"""Where the public slot reports come from, and how they are fetched politely."""

from __future__ import annotations

import logging
import random
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

VISAGRADER_BASE = "https://visagrader.com/us-visa-time-slots-availability/india-ind"

# VisaGrader's own ids for the five US posts in India (from its India index page).
CONSULATES = {
    "mumbai": ("Mumbai", "mumbai-P139"),
    "hyderabad": ("Hyderabad", "hyderabad-P85"),
    "chennai": ("Chennai", "chennai-P48"),
    "new-delhi": ("New Delhi", "new-delhi-P147"),
    "kolkata": ("Kolkata", "kolkata-P100"),
}

# Visa page suffixes on VisaGrader. H1B is the default; the others work the same way.
VISA_PAGES = {
    "H1B": "h1b-visa-H1B",
    "H4": "h4-visa-H4",
    "L1B": "l1-blanket-L1_B",
    "L1I": "l1-individual-L1_I",
    "L2": "l2-visa-L2",
    "F1": "f1-visa-F1",
}

DEFAULT_UA = "h1b-slot-tracker/0.1 (personal, non-commercial; polls a few pages every few minutes)"


@dataclass(frozen=True)
class Page:
    consulate_key: str
    consulate_name: str
    visa: str
    url: str


def pages_for(consulates: list[str], visa: str = "H1B") -> list[Page]:
    out = []
    suffix = VISA_PAGES[visa]
    for key in consulates:
        if key not in CONSULATES:
            raise ValueError(f"Unknown consulate {key!r}. Use one of: {', '.join(CONSULATES)}")
        name, slug = CONSULATES[key]
        out.append(Page(key, name, visa, f"{VISAGRADER_BASE}/{slug}/{suffix}"))
    return out


class PoliteFetcher:
    """GETs public pages with robots.txt checks, a fixed user agent, spacing and backoff."""

    def __init__(self, user_agent: str = DEFAULT_UA, min_gap_s: float = 3.0, timeout_s: float = 20.0,
                 max_tries: int = 3, session: requests.Session | None = None):
        self.ua = user_agent
        self.min_gap_s = min_gap_s
        self.timeout_s = timeout_s
        self.max_tries = max_tries
        self.s = session or requests.Session()
        self.s.headers.update({"User-Agent": self.ua, "Accept": "text/html,application/xhtml+xml"})
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last = 0.0

    def _robots_ok(self, url: str) -> bool:
        p = urlparse(url)
        root = f"{p.scheme}://{p.netloc}"
        if root not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.s.get(root + "/robots.txt", timeout=self.timeout_s)
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                    self._robots[root] = rp
                elif r.status_code in (401, 403):
                    self._robots[root] = None  # treat as disallow-all
                else:
                    rp.parse([])  # missing robots.txt means allowed
                    self._robots[root] = rp
            except requests.RequestException as e:
                # Don't guess: if robots.txt can't be read, skip the site this run.
                raise requests.ConnectionError(f"could not read {root}/robots.txt ({e}); skipped this run") from e
        rp = self._robots[root]
        return bool(rp and rp.can_fetch(self.ua, url))

    def _space(self):
        wait = self.min_gap_s + random.uniform(0, 1.5) - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get(self, url: str) -> str:
        if not self._robots_ok(url):
            raise PermissionError(f"robots.txt does not allow {url}; not fetching it")
        delay = 10.0
        for attempt in range(1, self.max_tries + 1):
            self._space()
            try:
                r = self.s.get(url, timeout=self.timeout_s)
            except requests.RequestException as e:
                if attempt == self.max_tries:
                    raise
                log.info("Fetch error %s, retrying in %.0fs", e, delay)
                time.sleep(delay)
                delay *= 3
                continue
            if r.status_code == 200:
                return r.text
            if r.status_code in (429, 500, 502, 503, 504) and attempt < self.max_tries:
                ra = r.headers.get("Retry-After", "")
                wait = float(ra) if ra.isdigit() else delay
                log.info("HTTP %s from %s, backing off %.0fs", r.status_code, url, wait)
                time.sleep(min(wait, 300))
                delay *= 3
                continue
            r.raise_for_status()
            raise requests.HTTPError(f"HTTP {r.status_code} for {url}")
        raise RuntimeError("unreachable")


class BrowserFetcher(PoliteFetcher):
    """Fallback for if the site ever starts drawing the table with JavaScript.

    Opens the same public page in a headless Chromium (Playwright) and returns the
    rendered HTML. No login, no clicks, no forms. Install with:
        pip install playwright && python -m playwright install chromium
    """

    def get(self, url: str) -> str:
        if not self._robots_ok(url):
            raise PermissionError(f"robots.txt does not allow {url}; not fetching it")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise RuntimeError("fetch_mode = 'browser' needs Playwright: pip install playwright "
                               "&& python -m playwright install chromium") from e
        self._space()
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True)
            try:
                page = b.new_page(user_agent=self.ua)
                page.goto(url, wait_until="networkidle", timeout=int(self.timeout_s * 1000))
                return page.content()
            finally:
                b.close()


def make_fetcher(mode: str, user_agent: str) -> PoliteFetcher:
    if mode == "browser":
        return BrowserFetcher(user_agent=user_agent)
    return PoliteFetcher(user_agent=user_agent)

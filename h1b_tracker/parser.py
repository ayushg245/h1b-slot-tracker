"""Parse VisaGrader's "Recent slot activity (table)" into SlotReport rows.

The parser goes by the table's column headers, not by CSS classes, so small
layout changes don't break it. If the page has neither the table nor the
"No slot activity" notice, it raises LayoutChanged so you hear about it.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from bs4 import BeautifulSoup

APPOINTMENT_TYPES = {"dropbox", "interview", "biometrics"}

_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%d %b %Y", "%b %d, %Y")
_DATETIME_FORMATS = (
    "%m/%d/%Y, %I:%M:%S %p",
    "%m/%d/%Y, %I:%M %p",
    "%m/%d/%Y %I:%M:%S %p",
    "%m/%d/%Y, %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
)
_NO_ACTIVITY = re.compile(r"no slot activity", re.I)


class LayoutChanged(Exception):
    """The page no longer looks like the one this parser was written for."""


@dataclass(frozen=True)
class SlotReport:
    consulate: str          # e.g. "Hyderabad"
    visa: str               # e.g. "H1B"
    appointment_type: str   # dropbox | interview | biometrics
    availability_date: date | None
    availability_raw: str
    slots: int | None
    reported_at: datetime | None   # UTC, as the page labels it
    reported_raw: str
    source_url: str

    @property
    def uid(self) -> str:
        """Stable id used to spot rows we've already alerted on."""
        key = "|".join([self.consulate, self.visa, self.appointment_type,
                        self.availability_raw, str(self.slots), self.reported_raw])
        return hashlib.sha256(key.encode()).hexdigest()[:24]


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def parse_date(s: str) -> date | None:
    s = _clean(s)
    for f in _DATE_FORMATS:
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def parse_datetime_utc(s: str) -> datetime | None:
    s = _clean(s).replace(" ", " ")
    iso = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(iso)
        return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    for f in _DATETIME_FORMATS:
        try:
            return datetime.strptime(s, f).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _header_map(cells: list[str]) -> dict[str, int] | None:
    want = {"type": "appointment type", "avail": "availability date", "slots": "slots", "reported": "reported"}
    low = [c.lower() for c in cells]
    out = {}
    for k, label in want.items():
        for i, c in enumerate(low):
            if c == label or (label in c and k != "slots") or (k == "slots" and c.startswith("slot")):
                out[k] = i
                break
    return out if len(out) == 4 else None


def _find_activity_table(soup: BeautifulSoup):
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        head = [_clean(c.get_text(" ")) for c in rows[0].find_all(["th", "td"])]
        hm = _header_map(head)
        if hm:
            return table, hm
    return None, None


def _cell_text(cell) -> str:
    # Prefer a machine-readable time if the page ever adds one.
    t = cell.find("time")
    if t and t.get("datetime"):
        return t["datetime"]
    return _clean(cell.get_text(" "))


def parse_activity(html: str, consulate: str, visa: str, source_url: str) -> list[SlotReport]:
    soup = BeautifulSoup(html, "html.parser")
    table, hm = _find_activity_table(soup)
    if table is None:
        if _NO_ACTIVITY.search(soup.get_text(" ")):
            return []
        raise LayoutChanged(
            f"{consulate}: no 'Recent slot activity' table and no 'No slot activity' notice. "
            "The page layout may have changed, or the table is now drawn by JavaScript.")

    out = []
    for tr in table.find_all("tr")[1:]:
        cells = tr.find_all(["td", "th"])
        if len(cells) < 4:
            continue
        vals = [_cell_text(c) for c in cells]
        atype = vals[hm["type"]].lower()
        if atype not in APPOINTMENT_TYPES:
            continue
        avail_raw = vals[hm["avail"]]
        rep_raw = vals[hm["reported"]]
        m = re.search(r"\d+", vals[hm["slots"]])
        out.append(SlotReport(
            consulate=consulate, visa=visa, appointment_type=atype,
            availability_date=parse_date(avail_raw), availability_raw=avail_raw,
            slots=int(m.group()) if m else None,
            reported_at=parse_datetime_utc(rep_raw), reported_raw=rep_raw,
            source_url=source_url,
        ))
    return out

"""Settings, read from a small TOML file. Secrets stay in environment variables."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .parser import APPOINTMENT_TYPES
from .sources import CONSULATES, VISA_PAGES

MIN_INTERVAL_S = 180  # be polite: never poll a page more often than every 3 minutes


@dataclass
class Config:
    consulates: list[str] = field(default_factory=lambda: list(CONSULATES))
    visa: str = "H1B"
    appointment_types: list[str] = field(default_factory=lambda: ["dropbox", "interview", "biometrics"])
    min_slots: int = 1
    skip_past_availability: bool = True
    skip_future_reports: bool = False
    future_tolerance_hours: float = 12.0
    max_report_age_hours: float = 48.0
    alert_channels: list[str] = field(default_factory=lambda: ["console"])
    ntfy_priority: str = "high"
    state_file: str = "state/slots.sqlite3"
    interval_seconds: int = 300
    user_agent: str = ""
    layout_alert_every_hours: float = 12.0
    display_timezone: str = "America/New_York"
    fetch_mode: str = "http"

    def validate(self):
        bad = [c for c in self.consulates if c not in CONSULATES]
        if bad:
            raise ValueError(f"Unknown consulates {bad}; use {list(CONSULATES)}")
        if self.visa not in VISA_PAGES:
            raise ValueError(f"Unknown visa {self.visa!r}; use {list(VISA_PAGES)}")
        bad = [t for t in self.appointment_types if t not in APPOINTMENT_TYPES]
        if bad:
            raise ValueError(f"Unknown appointment types {bad}; use {sorted(APPOINTMENT_TYPES)}")
        if self.fetch_mode not in ("http", "browser"):
            raise ValueError("fetch_mode must be 'http' or 'browser'")
        self.interval_seconds = max(int(self.interval_seconds), MIN_INTERVAL_S)
        return self


def load(path: str | Path | None) -> Config:
    if not path or not Path(path).exists():
        return Config().validate()
    data = tomllib.loads(Path(path).read_text())
    known = Config.__dataclass_fields__
    unknown = set(data) - set(known)
    if unknown:
        raise ValueError(f"Unknown settings in {path}: {sorted(unknown)}")
    return Config(**data).validate()

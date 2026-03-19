from __future__ import annotations

import html
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from icalendar import Calendar
from zoneinfo import ZoneInfo


@dataclass
class Event:
    title: str
    start: datetime
    end: datetime
    location: str
    description: str
    is_all_day: bool


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value or ""


ICS_URL = env("ICS_URL", required=True)
TIMEZONE = env("AGENDA_TIMEZONE", "America/Los_Angeles")
TITLE = env("AGENDA_TITLE", "Live Agenda")
WINDOW_HOURS = int(env("WINDOW_HOURS", "48"))
MAX_EVENTS = int(env("MAX_EVENTS", "40"))
SITE_DIR = Path(env("SITE_DIR", "site"))
SITE_DIR.mkdir(parents=True, exist_ok=True)


def fetch_ics(url: str) -> bytes:
    req = Request(
        url,
        headers={
            "User-Agent": "github-actions-live-agenda/1.0",
            "Accept": "text/calendar, text/plain, */*",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read()
    except HTTPError as exc:
        raise SystemExit(f"Failed to fetch ICS feed: HTTP {exc.code}") from exc
    except URLError as exc:
        raise SystemExit(f"Failed to fetch ICS feed: {exc.reason}") from exc



def ensure_dt(value, tz: ZoneInfo) -> tuple[datetime, bool]:
    from datetime import date

    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=tz)
        return value.astimezone(tz), False
    if isinstance(value, date):
        dt = datetime(value.year, value.month, value.day, tzinfo=tz)
        return dt, True
    raise TypeError(f"Unsupported date type: {type(value)!r}")



def parse_events(raw: bytes, tz: ZoneInfo) -> list[Event]:
    cal = Calendar.from_ical(raw)
    now = datetime.now(tz)
    window_end = now + timedelta(hours=WINDOW_HOURS)
    events: list[Event] = []

    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        status = str(component.get("STATUS", "")).upper()
        if status == "CANCELLED":
            continue

        raw_start = component.get("DTSTART")
        raw_end = component.get("DTEND")
        if raw_start is None:
            continue

        start, is_all_day = ensure_dt(raw_start.dt, tz)
        if raw_end is None:
            end = start + (timedelta(days=1) if is_all_day else timedelta(hours=1))
        else:
            end, _ = ensure_dt(raw_end.dt, tz)

        if end < now or start > window_end:
            continue

        summary = str(component.get("SUMMARY", "Untitled"))
        location = str(component.get("LOCATION", ""))
        description = str(component.get("DESCRIPTION", ""))
        events.append(
            Event(
                title=summary,
                start=start,
                end=end,
                location=location,
                description=description,
                is_all_day=is_all_day,
            )
        )

    events.sort(key=lambda e: (e.start, e.end, e.title.lower()))
    return events[:MAX_EVENTS]



def section_title(reference: datetime, event: Event) -> str:
    if event.start.date() == reference.date():
        return "Today"
    if event.start.date() == (reference + timedelta(days=1)).date():
        return "Tomorrow"
    return event.start.strftime("%A, %b %-d") if sys.platform != "win32" else event.start.strftime("%A, %b %#d")



def format_time(event: Event) -> str:
    if event.is_all_day:
        return "All day"
    start = event.start.strftime("%-I:%M %p") if sys.platform != "win32" else event.start.strftime("%#I:%M %p")
    end = event.end.strftime("%-I:%M %p") if sys.platform != "win32" else event.end.strftime("%#I:%M %p")
    return f"{start} – {end}"



def human_updated(ts: datetime) -> str:
    return ts.strftime("%A, %b %-d at %-I:%M %p %Z") if sys.platform != "win32" else ts.strftime("%A, %b %#d at %#I:%M %p %Z")



def render(events: Iterable[Event], tz: ZoneInfo) -> str:
    now = datetime.now(tz)
    grouped: dict[str, list[Event]] = {}
    for event in events:
        grouped.setdefault(section_title(now, event), []).append(event)

    sections: list[str] = []
    if not grouped:
        sections.append(
            "<section class='empty'><h2>No events</h2><p>No events found in the current window.</p></section>"
        )
    else:
        for heading, group in grouped.items():
            cards: list[str] = []
            for event in group:
                description = html.escape(event.description).replace("\n", "<br>")
                location = (
                    f"<div class='meta-row'><span class='meta-label'>Location</span><span>{html.escape(event.location)}</span></div>"
                    if event.location
                    else ""
                )
                details = (
                    f"<details><summary>Notes</summary><div class='notes'>{description}</div></details>"
                    if event.description
                    else ""
                )
                cards.append(
                    f"""
                    <article class='event-card'>
                      <div class='time'>{html.escape(format_time(event))}</div>
                      <div class='content'>
                        <h3>{html.escape(event.title)}</h3>
                        {location}
                        {details}
                      </div>
                    </article>
                    """
                )
            sections.append(f"<section><h2>{html.escape(heading)}</h2>{''.join(cards)}</section>")

    return f"""<!doctype html>
<html lang=\"en\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <meta http-equiv=\"refresh\" content=\"300\">
  <title>{html.escape(TITLE)}</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0b1020;
      --panel: #11182d;
      --panel-2: #17213d;
      --text: #eef3ff;
      --muted: #a6b3d1;
      --accent: #86b6ff;
      --border: rgba(255,255,255,.08);
      --shadow: 0 10px 30px rgba(0,0,0,.3);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
      background: radial-gradient(circle at top, #1a2550 0%, var(--bg) 45%);
      color: var(--text);
      min-height: 100vh;
    }}
    .wrap {{ max-width: 980px; margin: 0 auto; padding: 32px 20px 64px; }}
    .hero {{
      background: linear-gradient(180deg, rgba(255,255,255,.06), rgba(255,255,255,.03));
      border: 1px solid var(--border);
      border-radius: 24px;
      box-shadow: var(--shadow);
      padding: 28px;
      margin-bottom: 24px;
      backdrop-filter: blur(8px);
    }}
    h1 {{ margin: 0 0 8px; font-size: clamp(2rem, 4vw, 3rem); }}
    .sub {{ color: var(--muted); margin: 0; }}
    .pill-row {{ display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }}
    .pill {{
      background: rgba(134,182,255,.12);
      color: var(--accent);
      padding: 8px 12px;
      border-radius: 999px;
      border: 1px solid rgba(134,182,255,.18);
      font-size: .92rem;
    }}
    section {{ margin-top: 24px; }}
    h2 {{ margin: 0 0 14px; font-size: 1.3rem; }}
    .event-card {{
      display: grid;
      grid-template-columns: 165px 1fr;
      gap: 16px;
      padding: 16px;
      border-radius: 18px;
      border: 1px solid var(--border);
      background: linear-gradient(180deg, var(--panel), var(--panel-2));
      box-shadow: var(--shadow);
      margin-bottom: 12px;
    }}
    .time {{
      font-weight: 700;
      color: var(--accent);
      font-size: 1rem;
      align-self: start;
    }}
    .content h3 {{ margin: 0 0 8px; font-size: 1.08rem; }}
    .meta-row {{ display: flex; flex-wrap: wrap; gap: 10px; color: var(--muted); margin-bottom: 8px; }}
    .meta-label {{ color: #c2d2ff; font-weight: 600; }}
    details {{ margin-top: 10px; }}
    summary {{ cursor: pointer; color: #c2d2ff; }}
    .notes {{ color: var(--muted); margin-top: 8px; line-height: 1.5; }}
    .empty {{
      padding: 24px;
      border-radius: 18px;
      border: 1px solid var(--border);
      background: linear-gradient(180deg, var(--panel), var(--panel-2));
      box-shadow: var(--shadow);
    }}
    footer {{ color: var(--muted); margin-top: 28px; font-size: .95rem; }}
    @media (max-width: 720px) {{
      .event-card {{ grid-template-columns: 1fr; gap: 10px; }}
      .time {{ font-size: .95rem; }}
    }}
  </style>
</head>
<body>
  <main class=\"wrap\">
    <header class=\"hero\">
      <h1>{html.escape(TITLE)}</h1>
      <p class=\"sub\">Automatically rebuilt from your published ICS feed by GitHub Actions.</p>
      <div class=\"pill-row\">
        <span class=\"pill\">Window: next {WINDOW_HOURS} hours</span>
        <span class=\"pill\">Timezone: {html.escape(TIMEZONE)}</span>
        <span class=\"pill\">Updated: {html.escape(human_updated(now))}</span>
      </div>
    </header>
    {''.join(sections)}
    <footer>
      This page refreshes in your browser every 5 minutes. GitHub Actions rebuild frequency is controlled by your workflow schedule.
    </footer>
  </main>
</body>
</html>
"""


def write_json(events: Iterable[Event]) -> None:
    import json

    payload = [
        {
            "title": e.title,
            "start": e.start.isoformat(),
            "end": e.end.isoformat(),
            "location": e.location,
            "description": e.description,
            "is_all_day": e.is_all_day,
        }
        for e in events
    ]
    (SITE_DIR / "agenda.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


if __name__ == "__main__":
    tz = ZoneInfo(TIMEZONE)
    raw = fetch_ics(ICS_URL)
    events = parse_events(raw, tz)
    html_doc = render(events, tz)
    (SITE_DIR / "index.html").write_text(html_doc, encoding="utf-8")
    write_json(events)
    print(f"Wrote {len(events)} event(s) to {SITE_DIR / 'index.html'}")

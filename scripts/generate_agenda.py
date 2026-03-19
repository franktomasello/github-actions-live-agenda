from __future__ import annotations

import html
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from icalendar import Calendar
from zoneinfo import ZoneInfo


@dataclass(slots=True)
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

_WIN = sys.platform == "win32"


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


def ensure_dt(value: date | datetime, tz: ZoneInfo) -> tuple[datetime, bool]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=tz)
        return value.astimezone(tz), False
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=tz), True
    raise TypeError(f"Unsupported date type: {type(value)!r}")


def parse_events(raw: bytes, tz: ZoneInfo) -> list[Event]:
    cal = Calendar.from_ical(raw)
    now = datetime.now(tz)
    window_end = now + timedelta(hours=WINDOW_HOURS)
    events: list[Event] = []

    for component in cal.walk("VEVENT"):
        status = str(component.get("STATUS", "")).upper()
        if status == "CANCELLED":
            continue

        raw_start = component.get("DTSTART")
        if raw_start is None:
            continue

        start, is_all_day = ensure_dt(raw_start.dt, tz)
        raw_end = component.get("DTEND")
        if raw_end is None:
            end = start + (timedelta(days=1) if is_all_day else timedelta(hours=1))
        else:
            end, _ = ensure_dt(raw_end.dt, tz)

        if end < now or start > window_end:
            continue

        events.append(
            Event(
                title=str(component.get("SUMMARY", "Untitled")),
                start=start,
                end=end,
                location=str(component.get("LOCATION", "")),
                description=str(component.get("DESCRIPTION", "")),
                is_all_day=is_all_day,
            )
        )

    events.sort(key=lambda e: (e.start, e.end, e.title.lower()))
    return events[:MAX_EVENTS]


def _fmt(dt: datetime, fmt_posix: str, fmt_win: str) -> str:
    return dt.strftime(fmt_win if _WIN else fmt_posix)


def section_title(reference: datetime, event: Event) -> str:
    if event.start.date() == reference.date():
        return "Today"
    if event.start.date() == (reference + timedelta(days=1)).date():
        return "Tomorrow"
    return _fmt(event.start, "%A, %b %-d", "%A, %b %#d")


def format_time(event: Event) -> str:
    if event.is_all_day:
        return "All day"
    start = _fmt(event.start, "%-I:%M %p", "%#I:%M %p")
    end = _fmt(event.end, "%-I:%M %p", "%#I:%M %p")
    return f"{start} – {end}"


def format_time_short(event: Event) -> str:
    if event.is_all_day:
        return "All day"
    return _fmt(event.start, "%-I:%M %p", "%#I:%M %p")


def human_updated(ts: datetime) -> str:
    return _fmt(ts, "%A, %b %-d at %-I:%M %p %Z", "%A, %b %#d at %#I:%M %p %Z")


def time_until(event: Event, now: datetime) -> str:
    """Return a human-friendly relative time like 'in 25 min' or 'Now'."""
    diff = event.start - now
    total_min = int(diff.total_seconds() // 60)
    if event.is_all_day:
        return ""
    if total_min < -5:
        return "In progress"
    if total_min <= 0:
        return "Now"
    if total_min < 60:
        return f"in {total_min} min"
    hours = total_min // 60
    mins = total_min % 60
    if mins == 0:
        return f"in {hours}h"
    return f"in {hours}h {mins}m"


def duration_str(event: Event) -> str:
    if event.is_all_day:
        return ""
    diff = event.end - event.start
    total_min = int(diff.total_seconds() // 60)
    if total_min < 60:
        return f"{total_min}m"
    hours = total_min // 60
    mins = total_min % 60
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def _esc(text: str) -> str:
    return html.escape(text)


def render(events: Iterable[Event], tz: ZoneInfo) -> str:
    now = datetime.now(tz)
    event_list = list(events)
    grouped: dict[str, list[Event]] = {}
    for event in event_list:
        grouped.setdefault(section_title(now, event), []).append(event)

    sections: list[str] = []
    if not grouped:
        sections.append(
            '<section class="day-section">'
            '<div class="empty-state">'
            '<div class="empty-icon">&#9786;</div>'
            "<h2>Nothing scheduled</h2>"
            f"<p>No events in the next {WINDOW_HOURS} hours. Enjoy the free time!</p>"
            "</div></section>"
        )
    else:
        section_idx = 0
        for heading, group in grouped.items():
            cards: list[str] = []
            for event in group:
                rel = time_until(event, now)
                dur = duration_str(event)
                is_now = rel in ("Now", "In progress")

                now_class = " is-now" if is_now else ""
                badge = f'<span class="badge badge-now">{_esc(rel)}</span>' if is_now else ""
                rel_html = f'<span class="relative-time">{_esc(rel)}</span>' if rel and not is_now else ""
                dur_html = f'<span class="duration">{_esc(dur)}</span>' if dur else ""

                location = (
                    f'<div class="meta"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>{_esc(event.location)}</div>'
                    if event.location
                    else ""
                )
                description = _esc(event.description).replace("\n", "<br>")
                details = (
                    f'<details><summary>Notes</summary><div class="notes">{description}</div></details>'
                    if event.description
                    else ""
                )

                cards.append(
                    f'<article class="event{now_class}">'
                    f'<div class="time-col">'
                    f'<span class="time">{_esc(format_time_short(event))}</span>'
                    f"{dur_html}"
                    f"{rel_html}"
                    f"</div>"
                    f'<div class="event-body">'
                    f"<h3>{badge}{_esc(event.title)}</h3>"
                    f'<div class="time-range">{_esc(format_time(event))}</div>'
                    f"{location}"
                    f"{details}"
                    f"</div>"
                    f"</article>"
                )

            is_today = heading == "Today"
            open_class = " open" if section_idx == 0 else ""
            day_label = heading
            count = len(group)
            count_label = f'<span class="event-count">{count} event{"s" if count != 1 else ""}</span>'

            sections.append(
                f'<section class="day-section{open_class}">'
                f'<div class="day-header{"" if not is_today else " today"}">'
                f"<h2>{_esc(day_label)}</h2>"
                f"{count_label}"
                f"</div>"
                f'<div class="day-events">{"".join(cards)}</div>'
                f"</section>"
            )
            section_idx += 1

    total_events = len(event_list)
    next_event = event_list[0] if event_list else None
    next_summary = ""
    if next_event:
        rel = time_until(next_event, now)
        next_summary = (
            f'<div class="next-up">'
            f'<span class="next-label">Next up</span>'
            f'<span class="next-title">{_esc(next_event.title)}</span>'
            f'<span class="next-time">{_esc(rel)}</span>'
            f"</div>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="refresh" content="300">
  <title>{_esc(TITLE)}</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    *,*::before,*::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    :root {{
      color-scheme: light dark;
      --bg: #f5f5f7;
      --surface: #ffffff;
      --surface-2: #f9f9fb;
      --text: #1d1d1f;
      --text-secondary: #6e6e73;
      --accent: #0071e3;
      --accent-soft: rgba(0,113,227,.08);
      --accent-medium: rgba(0,113,227,.14);
      --now-bg: rgba(52,199,89,.06);
      --now-border: rgba(52,199,89,.35);
      --now-text: #248a3d;
      --border: rgba(0,0,0,.06);
      --border-strong: rgba(0,0,0,.1);
      --shadow-sm: 0 1px 3px rgba(0,0,0,.04), 0 1px 2px rgba(0,0,0,.06);
      --shadow-md: 0 4px 12px rgba(0,0,0,.06), 0 1px 3px rgba(0,0,0,.04);
      --shadow-lg: 0 8px 30px rgba(0,0,0,.08), 0 2px 8px rgba(0,0,0,.04);
      --radius: 16px;
      --radius-sm: 10px;
    }}

    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #0a0a0c;
        --surface: #1c1c1e;
        --surface-2: #2c2c2e;
        --text: #f5f5f7;
        --text-secondary: #98989d;
        --accent: #4db8ff;
        --accent-soft: rgba(77,184,255,.1);
        --accent-medium: rgba(77,184,255,.18);
        --now-bg: rgba(48,209,88,.08);
        --now-border: rgba(48,209,88,.35);
        --now-text: #30d158;
        --border: rgba(255,255,255,.06);
        --border-strong: rgba(255,255,255,.1);
        --shadow-sm: 0 1px 3px rgba(0,0,0,.2);
        --shadow-md: 0 4px 12px rgba(0,0,0,.2);
        --shadow-lg: 0 8px 30px rgba(0,0,0,.3);
      }}
    }}

    html {{ -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale; }}

    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      min-height: 100vh;
    }}

    .container {{
      max-width: 720px;
      margin: 0 auto;
      padding: 40px 20px 80px;
    }}

    /* ── Header ── */
    header {{
      margin-bottom: 32px;
    }}

    header h1 {{
      font-size: 2rem;
      font-weight: 700;
      letter-spacing: -0.025em;
      margin-bottom: 4px;
    }}

    .subtitle {{
      color: var(--text-secondary);
      font-size: 0.9rem;
    }}

    .header-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 16px;
    }}

    .chip {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      background: var(--accent-soft);
      color: var(--accent);
      padding: 5px 12px;
      border-radius: 999px;
      font-size: 0.8rem;
      font-weight: 500;
    }}

    /* ── Next up banner ── */
    .next-up {{
      display: flex;
      align-items: center;
      gap: 12px;
      background: var(--surface);
      border: 1px solid var(--border-strong);
      border-radius: var(--radius);
      padding: 16px 20px;
      margin-bottom: 28px;
      box-shadow: var(--shadow-md);
    }}

    .next-label {{
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      color: var(--accent);
      flex-shrink: 0;
    }}

    .next-title {{
      font-weight: 600;
      font-size: 0.95rem;
      flex: 1;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}

    .next-time {{
      color: var(--text-secondary);
      font-size: 0.85rem;
      flex-shrink: 0;
    }}

    /* ── Day sections ── */
    .day-section {{
      margin-bottom: 24px;
    }}

    .day-header {{
      display: flex;
      align-items: baseline;
      gap: 10px;
      margin-bottom: 12px;
      padding: 0 4px;
    }}

    .day-header h2 {{
      font-size: 1.15rem;
      font-weight: 700;
      letter-spacing: -0.01em;
    }}

    .day-header.today h2 {{
      color: var(--accent);
    }}

    .event-count {{
      font-size: 0.8rem;
      color: var(--text-secondary);
      font-weight: 400;
    }}

    /* ── Event cards ── */
    .day-events {{
      display: flex;
      flex-direction: column;
      gap: 8px;
    }}

    .event {{
      display: grid;
      grid-template-columns: 90px 1fr;
      gap: 16px;
      padding: 16px 18px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius-sm);
      box-shadow: var(--shadow-sm);
      transition: box-shadow 0.15s ease, border-color 0.15s ease;
    }}

    .event:hover {{
      box-shadow: var(--shadow-md);
      border-color: var(--border-strong);
    }}

    .event.is-now {{
      background: var(--now-bg);
      border-color: var(--now-border);
    }}

    .time-col {{
      display: flex;
      flex-direction: column;
      gap: 2px;
      padding-top: 2px;
    }}

    .time {{
      font-size: 0.9rem;
      font-weight: 600;
      color: var(--text);
      white-space: nowrap;
    }}

    .duration {{
      font-size: 0.75rem;
      color: var(--text-secondary);
    }}

    .relative-time {{
      font-size: 0.75rem;
      color: var(--accent);
      font-weight: 500;
    }}

    .event-body h3 {{
      font-size: 0.95rem;
      font-weight: 600;
      margin-bottom: 2px;
      line-height: 1.35;
    }}

    .time-range {{
      font-size: 0.8rem;
      color: var(--text-secondary);
      margin-bottom: 6px;
    }}

    .badge {{
      display: inline-block;
      padding: 1px 8px;
      border-radius: 999px;
      font-size: 0.7rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.03em;
      vertical-align: middle;
      margin-right: 6px;
    }}

    .badge-now {{
      background: var(--now-border);
      color: var(--now-text);
    }}

    .meta {{
      display: flex;
      align-items: center;
      gap: 5px;
      font-size: 0.82rem;
      color: var(--text-secondary);
      margin-bottom: 4px;
    }}

    .meta svg {{
      flex-shrink: 0;
      opacity: 0.55;
    }}

    details {{
      margin-top: 8px;
    }}

    summary {{
      cursor: pointer;
      font-size: 0.82rem;
      font-weight: 500;
      color: var(--accent);
      user-select: none;
    }}

    summary:hover {{
      text-decoration: underline;
    }}

    .notes {{
      color: var(--text-secondary);
      font-size: 0.82rem;
      margin-top: 6px;
      line-height: 1.6;
    }}

    /* ── Empty state ── */
    .empty-state {{
      text-align: center;
      padding: 48px 24px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      box-shadow: var(--shadow-sm);
    }}

    .empty-icon {{
      font-size: 2.5rem;
      margin-bottom: 12px;
      opacity: 0.5;
    }}

    .empty-state h2 {{
      font-size: 1.1rem;
      margin-bottom: 6px;
    }}

    .empty-state p {{
      color: var(--text-secondary);
      font-size: 0.9rem;
    }}

    /* ── Footer ── */
    footer {{
      margin-top: 32px;
      padding-top: 20px;
      border-top: 1px solid var(--border);
      color: var(--text-secondary);
      font-size: 0.78rem;
      text-align: center;
      line-height: 1.6;
    }}

    /* ── Mobile ── */
    @media (max-width: 600px) {{
      .container {{ padding: 24px 16px 64px; }}
      header h1 {{ font-size: 1.6rem; }}
      .event {{ grid-template-columns: 1fr; gap: 6px; }}
      .time-col {{ flex-direction: row; gap: 10px; align-items: baseline; }}
      .next-up {{ flex-wrap: wrap; gap: 8px; }}
      .next-title {{ width: 100%; order: 3; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header>
      <h1>{_esc(TITLE)}</h1>
      <p class="subtitle">Updated {_esc(human_updated(now))}</p>
      <div class="header-meta">
        <span class="chip">{WINDOW_HOURS}h window</span>
        <span class="chip">{_esc(TIMEZONE)}</span>
        <span class="chip">{total_events} event{"s" if total_events != 1 else ""}</span>
      </div>
    </header>
    {next_summary}
    {''.join(sections)}
    <footer>
      Auto-refreshes every 5 minutes &middot; Rebuilt by GitHub Actions
    </footer>
  </div>
</body>
</html>
"""


def write_json(events: Iterable[Event]) -> None:
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

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


@dataclass(slots=True)
class GitHubMention:
    title: str
    repo: str
    number: int
    url: str
    state: str
    is_pr: bool
    author: str
    labels: list[str]
    updated: datetime
    created: datetime
    comments: int


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
GITHUB_TOKEN = env("GITHUB_TOKEN")
GITHUB_USERNAME = env("GITHUB_USERNAME")
MAX_MENTIONS = int(env("MAX_MENTIONS", "15"))
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


def fetch_github_mentions(token: str, username: str, tz: ZoneInfo) -> list[GitHubMention]:
    """Fetch open issues/PRs where the user is @mentioned."""
    if not token or not username:
        return []

    query = f"mentions:{username} is:open is:issue sort:updated-desc"
    api_url = f"https://api.github.com/search/issues?q={query}&per_page={MAX_MENTIONS}"
    req = Request(
        api_url,
        headers={
            "User-Agent": "github-actions-live-agenda/1.0",
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="GET",
    )
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except (HTTPError, URLError) as exc:
        print(f"Warning: GitHub API request failed: {exc}", file=sys.stderr)
        return []

    mentions: list[GitHubMention] = []
    for item in data.get("items", []):
        repo_url = item.get("repository_url", "")
        repo = "/".join(repo_url.rsplit("/", 2)[-2:]) if repo_url else ""
        is_pr = "pull_request" in item
        updated = datetime.fromisoformat(item["updated_at"].replace("Z", "+00:00")).astimezone(tz)
        created = datetime.fromisoformat(item["created_at"].replace("Z", "+00:00")).astimezone(tz)

        mentions.append(
            GitHubMention(
                title=item.get("title", "Untitled"),
                repo=repo,
                number=item.get("number", 0),
                url=item.get("html_url", ""),
                state=item.get("state", "open"),
                is_pr=is_pr,
                author=item.get("user", {}).get("login", ""),
                labels=[l.get("name", "") for l in item.get("labels", [])],
                updated=updated,
                created=created,
                comments=item.get("comments", 0),
            )
        )

    return mentions


def _time_ago(dt: datetime, now: datetime) -> str:
    """Return human-friendly relative time like '2h ago' or '3d ago'."""
    diff = now - dt
    total_min = int(diff.total_seconds() // 60)
    if total_min < 1:
        return "just now"
    if total_min < 60:
        return f"{total_min}m ago"
    hours = total_min // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days < 30:
        return f"{days}d ago"
    months = days // 30
    return f"{months}mo ago"


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


def _event_accent(event: Event) -> str:
    """Pick a left-border accent color based on time of day."""
    if event.is_all_day:
        return "#af52de"  # purple for all-day
    h = event.start.hour
    if h < 12:
        return "#ff9f0a"  # amber morning
    if h < 17:
        return "#007aff"  # blue afternoon
    return "#5e5ce6"  # indigo evening


def render_github_section(mentions: list[GitHubMention], now: datetime, start_idx: int) -> tuple[str, int]:
    """Render the GitHub mentions section. Returns (html, next_global_idx)."""
    if not mentions:
        return "", start_idx

    idx = start_idx
    cards: list[str] = []
    for i, m in enumerate(mentions):
        is_last = i == len(mentions) - 1
        last_class = " is-last" if is_last else ""
        delay = f' style="animation-delay:{idx * 40}ms"'
        idx += 1

        type_icon = (
            '<svg class="gh-type-icon" width="14" height="14" viewBox="0 0 16 16" fill="currentColor">'
            '<path d="M7.177 3.073L9.573.677A.25.25 0 0110 .854v4.792a.25.25 0 01-.427.177L7.177 3.427a.25.25 0 010-.354zM3.75 '
            '2.5a.75.75 0 100 1.5.75.75 0 000-1.5zm-2.25.75a2.25 2.25 0 113 2.122v5.256a2.251 2.251 0 11-1.5 0V5.372A2.25 '
            '2.25 0 011.5 3.25zM11 2.5h-1V4h1a1 1 0 011 1v5.628a2.251 2.251 0 101.5 0V5A2.5 2.5 0 0011 2.5zm1 '
            '10.25a.75.75 0 111.5 0 .75.75 0 01-1.5 0zM3.75 12a.75.75 0 100 1.5.75.75 0 000-1.5z"/></svg>'
            if m.is_pr
            else '<svg class="gh-type-icon" width="14" height="14" viewBox="0 0 16 16" fill="currentColor">'
            '<path d="M8 9.5a1.5 1.5 0 100-3 1.5 1.5 0 000 3z"/>'
            '<path d="M8 0a8 8 0 100 16A8 8 0 008 0zM1.5 8a6.5 6.5 0 1113 0 6.5 6.5 0 01-13 0z"/></svg>'
        )
        type_label = "PR" if m.is_pr else "Issue"

        label_html = ""
        if m.labels:
            label_chips = "".join(
                f'<span class="gh-label">{_esc(l)}</span>' for l in m.labels[:3]
            )
            label_html = f'<div class="gh-labels">{label_chips}</div>'

        comments_html = ""
        if m.comments > 0:
            comments_html = (
                f'<span class="gh-comments">'
                f'<svg width="12" height="12" viewBox="0 0 16 16" fill="currentColor">'
                f'<path d="M1 2.75C1 1.784 1.784 1 2.75 1h10.5c.966 0 1.75.784 1.75 1.75v7.5A1.75 1.75 0 0113.25 12H9.06l-2.573 '
                f'2.573A1.458 1.458 0 014 13.543V12H2.75A1.75 1.75 0 011 10.25v-7.5z"/></svg>'
                f'{m.comments}</span>'
            )

        updated_ago = _time_ago(m.updated, now)

        cards.append(
            f'<div class="tl-item{last_class} fade-in"{delay}>'
            f'<div class="tl-marker"><span class="dot gh-dot"></span></div>'
            f'<a href="{_esc(m.url)}" target="_blank" rel="noopener" class="card gh-card" style="--accent-bar:#8b5cf6">'
            f'<div class="card-top">'
            f'<div class="gh-meta">'
            f'{type_icon}'
            f'<span class="gh-repo">{_esc(m.repo)}</span>'
            f'<span class="gh-number">#{m.number}</span>'
            f'</div>'
            f'<div class="card-meta-right">'
            f'<span class="gh-type-badge gh-type-{type_label.lower()}">{type_label}</span>'
            f'{comments_html}'
            f'</div>'
            f'</div>'
            f'<h3>{_esc(m.title)}</h3>'
            f'<div class="gh-footer">'
            f'<span class="gh-author">@{_esc(m.author)}</span>'
            f'<span class="gh-updated">updated {updated_ago}</span>'
            f'</div>'
            f'{label_html}'
            f'</a>'
            f'</div>'
        )

    section = (
        f'<section class="day-group gh-section">'
        f'<div class="day-head gh-head">'
        f'<svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor" style="opacity:.6">'
        f'<path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 01-5.45 7.59c-.4.08-.55-.17-.55-.38 '
        f'0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 '
        f'0-.88-.31-1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68 '
        f'0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 '
        f'1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.58.93.65 '
        f'1.16.13.42.55 1.2 1.07 1.21 0 .83.01 1.2.01 1.2 0 .21-.15.46-.55.38A8.013 8.013 0 010 8c0-4.42 3.58-8 8-8z"/>'
        f'</svg>'
        f'<h2>Mentions</h2>'
        f'<span class="cnt">{len(mentions)}</span>'
        f'</div>'
        f'<div class="timeline">{"".join(cards)}</div>'
        f'</section>'
    )
    return section, idx


def render(events: Iterable[Event], tz: ZoneInfo, mentions: list[GitHubMention] | None = None) -> str:
    now = datetime.now(tz)
    event_list = list(events)
    grouped: dict[str, list[Event]] = {}
    for event in event_list:
        grouped.setdefault(section_title(now, event), []).append(event)

    global_idx = 0
    sections: list[str] = []
    if not grouped:
        sections.append(
            '<section class="day-group fade-in">'
            '<div class="empty-state">'
            '<div class="empty-icon">'
            '<svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><path d="M8 14h.01"/><path d="M12 14h.01"/><path d="M16 14h.01"/><path d="M8 18h.01"/><path d="M12 18h.01"/></svg>'
            "</div>"
            "<h2>All clear</h2>"
            f"<p>Nothing on the books for the next {WINDOW_HOURS} hours.</p>"
            "</div></section>"
        )
    else:
        for heading, group in grouped.items():
            is_today = heading == "Today"
            cards: list[str] = []
            for i, event in enumerate(group):
                rel = time_until(event, now)
                dur = duration_str(event)
                is_now = rel in ("Now", "In progress")
                is_last = i == len(group) - 1
                accent = _event_accent(event)

                now_class = " is-now" if is_now else ""
                last_class = " is-last" if is_last else ""

                indicator = '<span class="pulse"></span>' if is_now else '<span class="dot"></span>'

                badge = f'<span class="badge live">{_esc(rel)}</span>' if is_now else ""
                rel_html = f'<span class="countdown">{_esc(rel)}</span>' if rel and not is_now else ""
                dur_html = f'<span class="dur">{_esc(dur)}</span>' if dur else ""

                loc_icon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>'
                location = (
                    f'<div class="loc">{loc_icon}<span>{_esc(event.location)}</span></div>'
                    if event.location
                    else ""
                )
                description = _esc(event.description).replace("\n", "<br>")
                notes_icon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>'
                details = (
                    f'<details><summary>{notes_icon} Notes</summary><div class="notes">{description}</div></details>'
                    if event.description
                    else ""
                )

                delay = f' style="animation-delay:{global_idx * 40}ms"'
                global_idx += 1

                cards.append(
                    f'<div class="tl-item{now_class}{last_class} fade-in"{delay}>'
                    f'<div class="tl-marker">{indicator}</div>'
                    f'<article class="card" style="--accent-bar:{accent}">'
                    f'<div class="card-top">'
                    f'<div class="card-time">'
                    f'<span class="t">{_esc(format_time_short(event))}</span>'
                    f"{dur_html}"
                    f"</div>"
                    f'<div class="card-meta-right">{badge}{rel_html}</div>'
                    f"</div>"
                    f"<h3>{_esc(event.title)}</h3>"
                    f'<div class="range">{_esc(format_time(event))}</div>'
                    f"{location}"
                    f"{details}"
                    f"</article>"
                    f"</div>"
                )

            count = len(group)
            date_sub = ""
            if not is_today and heading != "Tomorrow" and count > 0:
                date_sub = f'<span class="day-date">{_fmt(group[0].start, "%B %-d, %Y", "%B %#d, %Y")}</span>'

            sections.append(
                f'<section class="day-group">'
                f'<div class="day-head{"" if not is_today else " is-today"}">'
                f"<h2>{_esc(heading)}</h2>"
                f"{date_sub}"
                f'<span class="cnt">{count}</span>'
                f"</div>"
                f'<div class="timeline">{"".join(cards)}</div>'
                f"</section>"
            )

    # ── GitHub mentions section ──
    gh_html = ""
    if mentions:
        gh_html, global_idx = render_github_section(mentions, now, global_idx)

    total_events = len(event_list)
    next_event = event_list[0] if event_list else None
    hero_next = ""
    if next_event:
        rel = time_until(next_event, now)
        is_now = rel in ("Now", "In progress")
        rel_display = (
            f'<span class="hero-live">{_esc(rel)}</span>'
            if is_now
            else f'<span class="hero-eta">{_esc(rel)}</span>'
        )
        hero_next = (
            f'<div class="hero-next fade-in" style="animation-delay:60ms">'
            f'<span class="hero-next-label">{"Live" if is_now else "Next"}</span>'
            f'<span class="hero-next-title">{_esc(next_event.title)}</span>'
            f"{rel_display}"
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
  <link href="https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap" rel="stylesheet">
  <style>
    /* ── Reset ── */
    *,*::before,*::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    /* ── Tokens — dark default ── */
    :root {{
      color-scheme: dark;
      --bg:        #000000;
      --surface:   rgba(28,28,30,.92);
      --surface-2: rgba(44,44,46,.6);
      --text:      #f5f5f7;
      --text-2:    #98989d;
      --text-3:    #636366;
      --accent:    #0a84ff;
      --accent-bg: rgba(10,132,255,.14);
      --live:      #30d158;
      --live-bg:   rgba(48,209,88,.1);
      --live-text: #32d74b;
      --border:    rgba(255,255,255,.04);
      --border-2:  rgba(255,255,255,.07);
      --tl-line:   rgba(255,255,255,.06);
      --card-shadow: 0 1px 2px rgba(0,0,0,.4), 0 4px 16px rgba(0,0,0,.2);
      --card-hover: 0 2px 6px rgba(0,0,0,.5), 0 12px 28px rgba(0,0,0,.3);
      --r:  16px;
      --r2: 22px;
    }}

    /* ── Light override ── */
    [data-theme="light"] {{
      color-scheme: light;
      --bg:        #f2f2f7;
      --surface:   #ffffff;
      --surface-2: #f8f8fa;
      --text:      #1c1c1e;
      --text-2:    #8e8e93;
      --text-3:    #aeaeb2;
      --accent:    #007aff;
      --accent-bg: rgba(0,122,255,.07);
      --live:      #34c759;
      --live-bg:   rgba(52,199,89,.08);
      --live-text: #248a3d;
      --border:    rgba(0,0,0,.04);
      --border-2:  rgba(0,0,0,.07);
      --tl-line:   rgba(0,0,0,.06);
      --card-shadow: 0 1px 2px rgba(0,0,0,.03), 0 4px 16px rgba(0,0,0,.04);
      --card-hover: 0 2px 6px rgba(0,0,0,.05), 0 12px 28px rgba(0,0,0,.07);
    }}

    /* ── Animations ── */
    @keyframes fade-up {{
      from {{ opacity: 0; transform: translateY(12px); }}
      to   {{ opacity: 1; transform: translateY(0); }}
    }}
    .fade-in {{
      animation: fade-up .45s cubic-bezier(.22,1,.36,1) both;
    }}
    @keyframes pulse-glow {{
      0%,100% {{ box-shadow: 0 0 0 0 rgba(52,199,89,.35); }}
      50%     {{ box-shadow: 0 0 0 7px rgba(52,199,89,0); }}
    }}

    /* ── Base ── */
    html {{
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      scroll-behavior: smooth;
    }}
    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      min-height: 100vh;
      font-feature-settings: 'cv01', 'cv02', 'cv03', 'cv04';
    }}

    .wrap {{
      max-width: 640px;
      margin: 0 auto;
      padding: 56px 24px 100px;
    }}

    /* ── Header ── */
    .hero {{
      margin-bottom: 44px;
    }}
    .hero h1 {{
      font-size: clamp(1.85rem, 4.5vw, 2.5rem);
      font-weight: 750;
      letter-spacing: -0.04em;
      line-height: 1.1;
      background: linear-gradient(135deg, var(--text) 0%, var(--text-2) 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
    }}
    .hero-sub {{
      color: var(--text-3);
      font-size: 0.82rem;
      margin-top: 8px;
      font-weight: 420;
      letter-spacing: 0.005em;
    }}
    .hero-chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin-top: 20px;
    }}
    .chip {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      background: var(--accent-bg);
      color: var(--accent);
      padding: 5px 12px;
      border-radius: 999px;
      font-size: 0.72rem;
      font-weight: 560;
      letter-spacing: 0.015em;
      backdrop-filter: blur(8px);
      -webkit-backdrop-filter: blur(8px);
    }}

    /* ── Hero next-up ── */
    .hero-next {{
      display: flex;
      align-items: center;
      gap: 14px;
      margin-top: 28px;
      padding: 16px 20px;
      background: var(--surface);
      border: 1px solid var(--border-2);
      border-radius: var(--r);
      box-shadow: var(--card-shadow);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
    }}
    .hero-next-label {{
      font-size: 0.62rem;
      font-weight: 720;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--accent);
      background: var(--accent-bg);
      padding: 4px 10px;
      border-radius: 8px;
      flex-shrink: 0;
    }}
    .hero-next-title {{
      flex: 1;
      font-weight: 620;
      font-size: 0.92rem;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .hero-eta {{
      color: var(--text-2);
      font-size: 0.8rem;
      font-weight: 500;
      flex-shrink: 0;
    }}
    .hero-live {{
      color: var(--live-text);
      font-size: 0.7rem;
      font-weight: 720;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      flex-shrink: 0;
      padding: 3px 10px;
      background: var(--live-bg);
      border-radius: 999px;
    }}

    /* ── Day groups ── */
    .day-group {{
      margin-bottom: 40px;
    }}
    .day-group:last-of-type {{
      margin-bottom: 0;
    }}
    .day-head {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 18px;
      padding-left: 2px;
    }}
    .day-head h2 {{
      font-size: 0.98rem;
      font-weight: 700;
      letter-spacing: -0.01em;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      font-size: 0.72rem;
      color: var(--text-2);
    }}
    .day-head.is-today h2 {{
      color: var(--accent);
    }}
    .day-date {{
      font-size: 0.72rem;
      color: var(--text-3);
      font-weight: 420;
    }}
    .cnt {{
      margin-left: auto;
      font-size: 0.65rem;
      font-weight: 620;
      color: var(--text-3);
      background: var(--surface-2);
      padding: 3px 10px;
      border-radius: 999px;
      border: 1px solid var(--border);
    }}

    /* ── Timeline ── */
    .timeline {{
      position: relative;
      padding-left: 24px;
    }}

    .tl-item {{
      position: relative;
      padding-bottom: 10px;
    }}
    .tl-item:last-child {{
      padding-bottom: 0;
    }}

    /* Vertical connector */
    .tl-item::before {{
      content: '';
      position: absolute;
      left: -17px;
      top: 12px;
      bottom: -2px;
      width: 1.5px;
      background: var(--tl-line);
      border-radius: 1px;
    }}
    .tl-item.is-last::before {{
      display: none;
    }}

    /* Marker */
    .tl-marker {{
      position: absolute;
      left: -24px;
      top: 6px;
      width: 14px;
      height: 14px;
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1;
    }}
    .dot {{
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--border-2);
      border: 2px solid var(--bg);
      box-shadow: 0 0 0 1.5px var(--tl-line);
      transition: transform .2s ease;
    }}
    .tl-item:hover .dot {{
      transform: scale(1.3);
    }}
    .pulse {{
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--live);
      animation: pulse-glow 2.5s cubic-bezier(.4,0,.6,1) infinite;
    }}

    /* ── Cards ── */
    .card {{
      position: relative;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--r);
      padding: 18px 20px 18px 24px;
      box-shadow: var(--card-shadow);
      transition: box-shadow .25s cubic-bezier(.22,1,.36,1), border-color .25s ease, transform .25s cubic-bezier(.22,1,.36,1);
      overflow: hidden;
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
    }}
    /* Left accent bar */
    .card::before {{
      content: '';
      position: absolute;
      left: 0;
      top: 12px;
      bottom: 12px;
      width: 3px;
      border-radius: 0 3px 3px 0;
      background: var(--accent-bar, var(--accent));
      opacity: .65;
      transition: opacity .2s ease;
    }}
    .card:hover {{
      box-shadow: var(--card-hover);
      border-color: var(--border-2);
      transform: translateY(-2px);
    }}
    .card:hover::before {{
      opacity: 1;
    }}
    .tl-item.is-now .card {{
      background: var(--live-bg);
      border-color: rgba(52,199,89,.18);
    }}
    .tl-item.is-now .card::before {{
      background: var(--live);
      opacity: 1;
    }}

    .card-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 6px;
    }}
    .card-time {{
      display: flex;
      align-items: baseline;
      gap: 8px;
    }}
    .t {{
      font-size: 0.78rem;
      font-weight: 600;
      color: var(--text-2);
      letter-spacing: 0.01em;
    }}
    .dur {{
      font-size: 0.68rem;
      color: var(--text-3);
      font-weight: 480;
      padding: 1px 7px;
      background: var(--surface-2);
      border-radius: 6px;
    }}
    .card-meta-right {{
      display: flex;
      align-items: center;
      gap: 6px;
    }}

    .card h3 {{
      font-size: 1.02rem;
      font-weight: 640;
      line-height: 1.35;
      letter-spacing: -0.015em;
      margin-bottom: 3px;
    }}
    .range {{
      font-size: 0.75rem;
      color: var(--text-3);
      margin-bottom: 2px;
      font-weight: 420;
    }}

    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 0.62rem;
      font-weight: 720;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .badge.live {{
      background: var(--live-bg);
      color: var(--live-text);
      border: 1px solid rgba(52,199,89,.18);
    }}

    .countdown {{
      font-size: 0.72rem;
      color: var(--accent);
      font-weight: 560;
      font-variant-numeric: tabular-nums;
    }}

    /* Location */
    .loc {{
      display: inline-flex;
      align-items: center;
      gap: 5px;
      font-size: 0.76rem;
      color: var(--text-2);
      margin-top: 6px;
      font-weight: 440;
    }}
    .loc svg {{
      opacity: .4;
      flex-shrink: 0;
    }}

    /* Notes */
    details {{
      margin-top: 12px;
    }}
    summary {{
      cursor: pointer;
      font-size: 0.76rem;
      font-weight: 560;
      color: var(--accent);
      user-select: none;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 0;
      transition: opacity .15s ease;
    }}
    summary svg {{
      opacity: .5;
    }}
    summary:hover {{
      opacity: .75;
    }}
    details[open] summary {{
      margin-bottom: 8px;
    }}
    .notes {{
      color: var(--text-2);
      font-size: 0.76rem;
      line-height: 1.7;
      padding: 12px 16px;
      background: var(--surface-2);
      border: 1px solid var(--border);
      border-radius: 12px;
      font-weight: 400;
    }}

    /* ── GitHub mentions ── */
    .gh-section {{
      margin-top: 48px;
      padding-top: 32px;
      border-top: 1px solid var(--border);
    }}
    .gh-head svg {{
      color: var(--text-2);
    }}
    .gh-head h2 {{
      color: #8b5cf6 !important;
    }}
    .gh-chip {{
      background: rgba(139,92,246,.12);
      color: #8b5cf6;
    }}
    .gh-card {{
      display: block;
      text-decoration: none;
      color: inherit;
      cursor: pointer;
    }}
    .gh-card:hover {{
      border-color: rgba(139,92,246,.25);
    }}
    .gh-card:hover::before {{
      opacity: 1;
    }}
    .gh-dot {{
      box-shadow: 0 0 0 1.5px rgba(139,92,246,.3) !important;
    }}
    .gh-meta {{
      display: flex;
      align-items: center;
      gap: 6px;
      min-width: 0;
      overflow: hidden;
    }}
    .gh-type-icon {{
      color: var(--text-3);
      flex-shrink: 0;
    }}
    .gh-repo {{
      font-size: 0.72rem;
      color: var(--text-2);
      font-weight: 520;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .gh-number {{
      font-size: 0.72rem;
      color: var(--text-3);
      font-weight: 480;
      flex-shrink: 0;
    }}
    .gh-type-badge {{
      font-size: 0.6rem;
      font-weight: 680;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      padding: 3px 8px;
      border-radius: 6px;
      flex-shrink: 0;
    }}
    .gh-type-pr {{
      background: rgba(139,92,246,.1);
      color: #a78bfa;
    }}
    .gh-type-issue {{
      background: rgba(34,197,94,.1);
      color: #4ade80;
    }}
    [data-theme="light"] .gh-type-pr {{
      background: rgba(139,92,246,.08);
      color: #7c3aed;
    }}
    [data-theme="light"] .gh-type-issue {{
      background: rgba(34,197,94,.08);
      color: #16a34a;
    }}
    .gh-comments {{
      display: inline-flex;
      align-items: center;
      gap: 3px;
      font-size: 0.68rem;
      color: var(--text-3);
      font-weight: 500;
    }}
    .gh-comments svg {{
      opacity: .5;
    }}
    .gh-card h3 {{
      margin-top: 2px;
    }}
    .gh-footer {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-top: 4px;
    }}
    .gh-author {{
      font-size: 0.72rem;
      color: var(--accent);
      font-weight: 540;
    }}
    .gh-updated {{
      font-size: 0.68rem;
      color: var(--text-3);
      font-weight: 420;
    }}
    .gh-labels {{
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      margin-top: 8px;
    }}
    .gh-label {{
      font-size: 0.62rem;
      font-weight: 580;
      padding: 2px 8px;
      border-radius: 999px;
      background: var(--surface-2);
      color: var(--text-2);
      border: 1px solid var(--border);
      letter-spacing: 0.01em;
    }}

    /* ── Empty state ── */
    .empty-state {{
      text-align: center;
      padding: 64px 32px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--r2);
      box-shadow: var(--card-shadow);
    }}
    .empty-icon {{
      color: var(--text-3);
      margin-bottom: 20px;
      opacity: .45;
    }}
    .empty-state h2 {{
      font-size: 1.15rem;
      font-weight: 680;
      margin-bottom: 8px;
      letter-spacing: -0.01em;
    }}
    .empty-state p {{
      color: var(--text-2);
      font-size: 0.88rem;
      font-weight: 400;
    }}

    /* ── Footer ── */
    footer {{
      margin-top: 56px;
      padding-top: 24px;
      border-top: 1px solid var(--border);
      color: var(--text-3);
      font-size: 0.7rem;
      text-align: center;
      line-height: 1.7;
      letter-spacing: 0.02em;
      font-weight: 420;
    }}

    /* ── Mobile ── */
    @media (max-width: 600px) {{
      .wrap {{ padding: 32px 18px 72px; }}
      .hero h1 {{ font-size: 1.65rem; }}
      .timeline {{ padding-left: 20px; }}
      .tl-marker {{ left: -20px; }}
      .tl-item::before {{ left: -14px; }}
      .card {{ padding: 15px 16px 15px 20px; border-radius: 14px; }}
      .hero-next {{ flex-wrap: wrap; gap: 8px; padding: 14px 16px; }}
      .hero-next-title {{ width: 100%; order: 3; white-space: normal; }}
    }}

    /* ── Print ── */
    @media print {{
      body {{ background: white; color: black; }}
      .card {{ box-shadow: none; border: 1px solid #ddd; break-inside: avoid; }}
      .card::before {{ display: none; }}
      .pulse {{ animation: none; background: #34c759; }}
      .hero-next {{ box-shadow: none; }}
      .fade-in {{ animation: none; opacity: 1; }}
    }}

    /* ── Theme toggle ── */
    .theme-toggle {{
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 100;
      width: 48px;
      height: 48px;
      border-radius: 50%;
      border: 1px solid var(--border-2);
      background: var(--surface);
      box-shadow: 0 2px 12px rgba(0,0,0,.15), 0 1px 3px rgba(0,0,0,.1);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      transition: transform .3s cubic-bezier(.22,1,.36,1), box-shadow .3s ease, background .3s ease;
      -webkit-tap-highlight-color: transparent;
    }}
    .theme-toggle:hover {{
      transform: scale(1.08);
      box-shadow: 0 4px 20px rgba(0,0,0,.2), 0 2px 6px rgba(0,0,0,.12);
    }}
    .theme-toggle:active {{
      transform: scale(.95);
    }}
    .theme-toggle svg {{
      width: 20px;
      height: 20px;
      color: var(--text-2);
      transition: transform .5s cubic-bezier(.22,1,.36,1), opacity .3s ease;
    }}
    .theme-toggle .icon-sun {{ position: absolute; opacity: 0; transform: rotate(-90deg) scale(.5); }}
    .theme-toggle .icon-moon {{ position: absolute; opacity: 1; transform: rotate(0) scale(1); }}
    [data-theme="light"] .theme-toggle .icon-sun {{ opacity: 1; transform: rotate(0) scale(1); }}
    [data-theme="light"] .theme-toggle .icon-moon {{ opacity: 0; transform: rotate(90deg) scale(.5); }}

    @media (max-width: 600px) {{
      .theme-toggle {{ bottom: 18px; right: 18px; width: 42px; height: 42px; }}
      .theme-toggle svg {{ width: 18px; height: 18px; }}
    }}

    /* ── Reduce motion ── */
    @media (prefers-reduced-motion: reduce) {{
      .fade-in {{ animation: none; opacity: 1; }}
      .pulse {{ animation: none; }}
      .card {{ transition: none; }}
      .dot {{ transition: none; }}
      .theme-toggle, .theme-toggle svg {{ transition: none; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <div class="hero fade-in">
      <h1>{_esc(TITLE)}</h1>
      <p class="hero-sub">Updated {_esc(human_updated(now))}</p>
      <div class="hero-chips">
        <span class="chip">{WINDOW_HOURS}h window</span>
        <span class="chip">{_esc(TIMEZONE)}</span>
        <span class="chip">{total_events} event{"s" if total_events != 1 else ""}</span>
        {'<span class="chip gh-chip">' + str(len(mentions)) + ' mention' + ('s' if len(mentions) != 1 else '') + '</span>' if mentions else ''}
      </div>
      {hero_next}
    </div>
    {''.join(sections)}
    {gh_html}
    <footer>
      Auto-refreshes every 5&nbsp;min &middot; Rebuilt on push via Cloudflare&nbsp;Pages
    </footer>
  </main>
  <button class="theme-toggle" aria-label="Toggle light/dark mode" title="Toggle theme">
    <svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
    <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
  </button>
  <script>
  (function(){{
    var KEY='agenda-theme';
    var root=document.documentElement;
    var saved=localStorage.getItem(KEY);
    if(saved==='light')root.setAttribute('data-theme','light');
    var btn=document.querySelector('.theme-toggle');
    btn.addEventListener('click',function(){{
      var isLight=root.getAttribute('data-theme')==='light';
      if(isLight){{root.removeAttribute('data-theme');localStorage.setItem(KEY,'dark');}}
      else{{root.setAttribute('data-theme','light');localStorage.setItem(KEY,'light');}}
    }});
  }})();
  </script>
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
    mentions = fetch_github_mentions(GITHUB_TOKEN, GITHUB_USERNAME, tz)
    html_doc = render(events, tz, mentions=mentions)
    (SITE_DIR / "index.html").write_text(html_doc, encoding="utf-8")
    write_json(events)
    print(f"Wrote {len(events)} event(s) and {len(mentions)} mention(s) to {SITE_DIR / 'index.html'}")

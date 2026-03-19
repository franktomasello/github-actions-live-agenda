<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/deploy-Cloudflare%20Pages-f38020?style=flat-square&logo=cloudflare&logoColor=white" alt="Cloudflare Pages">
  <img src="https://img.shields.io/badge/auth-Cloudflare%20Access-f38020?style=flat-square&logo=cloudflare&logoColor=white" alt="Cloudflare Access">
</p>

# Live Agenda

> **https://github-actions-live-agenda.pages.dev**

A private, auto-refreshing agenda page generated from an ICS calendar feed. Dark mode by default with a light/dark toggle. Deployed to Cloudflare Pages, secured with Cloudflare Access.

---

## How it works

```
ICS feed (Outlook / Reclaim / Google)
        ↓
  generate_agenda.py      ← parses events, renders HTML
        ↓
  site/index.html         ← static output
        ↓
  Cloudflare Pages        ← builds on push, serves the page
        ↓
  Cloudflare Access       ← email-gated authentication
```

## Features

- **Timeline UI** — events grouped by day with a vertical timeline, color-coded accent bars, and staggered fade-in animations
- **Live indicators** — pulsing dot and "Now" / "In progress" badges for current events
- **Dark / Light mode** — dark by default, toggle persisted in `localStorage`
- **Auto-refresh** — page reloads every 2 minutes via `<meta http-equiv="refresh">`
- **Responsive** — optimized for desktop, tablet, and mobile
- **Glassmorphism** — frosted-glass cards with `backdrop-filter: blur()`
- **Accessible** — `prefers-reduced-motion` support, semantic HTML, print styles

## Repo structure

```
.
├── scripts/
│   └── generate_agenda.py   # Fetches ICS → generates site/index.html + agenda.json
├── site/                    # Build output (not committed)
├── requirements.txt         # icalendar>=6.0.0
└── README.md
```

## Setup

### 1. Get your ICS link

Outlook: **Calendar → Settings → Shared calendars → Publish a calendar** → copy the **ICS** URL.

### 2. Deploy to Cloudflare Pages

1. **Cloudflare dashboard** → Workers & Pages → Create → Pages → Connect to Git
2. Select this repo
3. Build configuration:

   | Field | Value |
   |---|---|
   | Framework preset | None |
   | Build command | `pip install -r requirements.txt && python scripts/generate_agenda.py` |
   | Output directory | `site` |

4. Environment variables:

   | Variable | Required | Default |
   |---|---|---|
   | `ICS_URL` | **Yes** | — |
   | `AGENDA_TITLE` | No | `Live Agenda` |
   | `AGENDA_TIMEZONE` | No | `America/Los_Angeles` |
   | `WINDOW_HOURS` | No | `48` |
   | `MAX_EVENTS` | No | `40` |

5. Deploy

### 3. Lock it down with Cloudflare Access

1. **one.dash.cloudflare.com** → Access → Applications → Add → Self-hosted
2. Domain: your `.pages.dev` URL
3. Policy: **Allow** → Selector: **Emails** → your email
4. Save — visitors now need a one-time email code

## Local dev

```bash
export ICS_URL='https://...'
pip install -r requirements.txt
python scripts/generate_agenda.py
open site/index.html
```

## Auto-rebuild

The site rebuilds automatically on every push **and** every 5 minutes via a scheduled deploy hook, so calendar changes appear within minutes.

To manually trigger a rebuild:

**Cloudflare Pages → Deployments → Retry deployment**

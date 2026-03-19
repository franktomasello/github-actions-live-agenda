<p align="center">
  <img src="https://img.shields.io/badge/python-3.12+-3776ab?style=flat-square&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/deploy-Cloudflare%20Pages-f38020?style=flat-square&logo=cloudflare&logoColor=white" alt="Cloudflare Pages">
  <img src="https://img.shields.io/badge/auth-Cloudflare%20Access-f38020?style=flat-square&logo=cloudflare&logoColor=white" alt="Cloudflare Access">
</p>

# Live Agenda

> **https://github-actions-live-agenda.pages.dev**

A private, live-updating agenda page generated from an ICS calendar feed. Dark mode by default with a light/dark toggle. Deployed to Cloudflare Pages, secured with Cloudflare Access.

---

## How it works

```
Outlook / Reclaim / Google  →  ICS feed
                                  ↓
              ┌───────────────────┴───────────────────┐
              │                                       │
    generate_agenda.py                     /api/events (CF Function)
    builds static HTML shell               fetches ICS live per request
              │                                       │
     site/index.html                          JSON → client JS
              │                                       │
              └───────────────────┬───────────────────┘
                                  ↓
                          Cloudflare Pages
                                  ↓
                          Cloudflare Access
                       (email-gated auth)
```

The static HTML is the initial shell. A Cloudflare Pages Function (`/api/events`) fetches the ICS feed live on every request. Client-side JS polls every 30 seconds and updates the DOM without page reloads.

## Features

- **Live data** — polls every 30s via Cloudflare Pages Function, no rebuild needed
- **Timeline UI** — events grouped by day with color-coded accent bars and staggered animations
- **Live indicators** — pulsing dot and "Now" / "In progress" badges for current events
- **Dark / Light mode** — dark by default, toggle persisted in `localStorage`
- **Smart updates** — lightweight tick every 15s updates countdowns without full re-render
- **Tab-aware** — fetches fresh data immediately when you switch back to the tab
- **Responsive** — optimized for desktop, tablet, and mobile
- **Glassmorphism** — frosted-glass cards with `backdrop-filter: blur()`
- **Accessible** — `prefers-reduced-motion` support, semantic HTML, print styles
- **Edge-cached** — 10s `s-maxage` on API responses to avoid hammering the ICS source

## Repo structure

```
.
├── scripts/
│   └── generate_agenda.py   # Fetches ICS → generates site/index.html + agenda.json
├── functions/
│   └── api/
│       └── events.js        # CF Pages Function — live ICS → JSON endpoint
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

> Note: The `/api/events` endpoint only runs on Cloudflare. Locally, the page shows build-time data.

<div align="center">

<br>

# `Live Agenda`

**A private, real-time agenda dashboard powered by your calendar.**

[![Python](https://img.shields.io/badge/python-3.12+-3776ab?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![Cloudflare Pages](https://img.shields.io/badge/Cloudflare%20Pages-f38020?style=for-the-badge&logo=cloudflare&logoColor=white)](https://pages.cloudflare.com)
[![Cloudflare Access](https://img.shields.io/badge/Cloudflare%20Access-f38020?style=for-the-badge&logo=cloudflare&logoColor=white)](https://www.cloudflare.com/zero-trust/products/access/)

<br>

<sub>Dark mode by default · Glassmorphism UI · Secured with Cloudflare Access</sub>

---

</div>

<br>

## Architecture

```mermaid
flowchart TB
    A["Outlook Calendar"] -- "Graph API (real-time)" --> B
    A -- "ICS feed (fallback)" --> B

    subgraph B ["Cloudflare Pages"]
        direction TB
        C["generate_agenda.py<br/>Static HTML shell"]
        D["/api/events<br/>Graph API → JSON<br/>ICS fallback"]
        E["Client-side JS<br/>30s poll · 1s tick"]
        C --> E
        D --> E
    end

    E --> F["Cloudflare Access"]
```

<br>

## Features

| | Feature | Detail |
|---|---|---|
| **⚡** | **Real-time data** | Microsoft Graph API reflects changes in seconds; ICS fallback available |
| **🕐** | **Real-time UI** | 1s tick updates countdowns, progress bars, and clock |
| **📅** | **Timeline view** | Events grouped by day with color-coded accent bars |
| **🔴** | **Live indicators** | Pulsing dot + "Now" / "In progress" badges |
| **🌗** | **Dark / Light mode** | Dark by default, toggle persisted in `localStorage` |
| **🧊** | **Glassmorphism** | Frosted-glass cards with `backdrop-filter: blur()` |
| **📱** | **Responsive** | Optimized for desktop, tablet, and mobile |
| **👁** | **Tab-aware** | Fetches fresh data the moment you switch back |
| **♿** | **Accessible** | `prefers-reduced-motion`, semantic HTML, print styles |
| **🌐** | **Edge-cached** | 30s `s-maxage` + stale-while-revalidate on API |

<br>

## Repo Structure

```
.
├── scripts/
│   ├── generate_agenda.py    # ICS → static HTML shell + agenda.json
│   └── get_graph_token.py    # One-time OAuth helper for Graph API setup
├── functions/
│   └── api/
│       └── events.js         # CF Pages Function — Graph API → JSON (ICS fallback)
├── site/                     # Build output (not committed)
├── requirements.txt          # icalendar>=6.0.0
└── README.md
```

<br>

## Setup

### 1 — Register an Azure AD app (for real-time Graph API)

> This gives you instant calendar updates. Skip to **1b** if you only want the simpler ICS feed (15–30 min delay).

1. **[Azure Portal](https://portal.azure.com)** → Azure Active Directory → App registrations → **New registration**
2. Name it anything (e.g. `Live Agenda`), select **Single tenant**, and add redirect URI:
   - Platform: **Web**
   - URI: `http://localhost:3847/callback`
3. Copy the **Application (client) ID** and **Directory (tenant) ID**
4. Under **Certificates & secrets** → New client secret → copy the **Value**
5. Under **API permissions** → Add a permission → Microsoft Graph → **Delegated** → `Calendars.Read` → Grant admin consent

### 1a — Get your refresh token

```bash
python scripts/get_graph_token.py \
    --client-id  YOUR_CLIENT_ID \
    --client-secret YOUR_CLIENT_SECRET \
    --tenant-id  YOUR_TENANT_ID
```

This opens your browser, you sign in, and it prints the four env vars to set.

### 1b — Alternative: ICS feed (simpler, but 15–30 min delay)

> **Outlook** → Settings → Calendar → Shared calendars → Publish a calendar → copy the **ICS** URL

### 2 — Deploy to Cloudflare Pages

1. **Cloudflare Dashboard** → Workers & Pages → Create → Pages → Connect to Git
2. Select this repo
3. Build configuration:

   | Field | Value |
   |---|---|
   | Framework preset | `None` |
   | Build command | `pip install -r requirements.txt && python scripts/generate_agenda.py` |
   | Output directory | `site` |

4. Environment variables:

   **Graph API (real-time):**

   | Variable | Required | Description |
   |---|---|---|
   | `MS_CLIENT_ID` | **Yes** | Azure AD application (client) ID |
   | `MS_CLIENT_SECRET` | **Yes** | Azure AD client secret value |
   | `MS_TENANT_ID` | **Yes** | Azure AD directory (tenant) ID |
   | `MS_REFRESH_TOKEN` | **Yes** | OAuth refresh token from `get_graph_token.py` |

   **ICS fallback (or standalone):**

   | Variable | Required | Description |
   |---|---|---|
   | `ICS_URL` | No* | Published ICS feed URL (\*required if Graph API vars aren't set) |

   **General:**

   | Variable | Required | Default |
   |---|---|---|
   | `AGENDA_TITLE` | No | `Live Agenda` |
   | `AGENDA_TIMEZONE` | No | `America/Los_Angeles` |
   | `WINDOW_HOURS` | No | `48` |
   | `MAX_EVENTS` | No | `40` |

   > **Tip:** Set `ICS_URL` alongside the Graph API vars — it serves as an automatic fallback if the Graph token expires.

5. Deploy 🚀

### 3 — Lock it down with Cloudflare Access

1. **Zero Trust Dashboard** → Access → Applications → Add → Self-hosted
2. Domain: your `.pages.dev` URL
3. Policy: **Allow** → Selector: **Emails** → your email
4. Save — visitors now need a one-time email code 🔒

<br>

## Local Development

```bash
export ICS_URL='https://...'
pip install -r requirements.txt
python scripts/generate_agenda.py
open site/index.html
```

> [!NOTE]
> The `/api/events` endpoint only runs on Cloudflare. Locally, the page shows build-time data only.

<br>

---

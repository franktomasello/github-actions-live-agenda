# Live Agenda

**https://github-actions-live-agenda.pages.dev**

A static agenda page generated from an Outlook or Reclaim-backed ICS calendar feed, deployed via Cloudflare Pages and secured with Cloudflare Access.

## What this is optimized for

- No Slack app approvals or Microsoft Graph app registration
- Automatic builds on every push via Cloudflare Pages
- Private access via Cloudflare Access (email-based authentication)
- A clean, responsive static page with dark/light mode

## How it works

1. Publish an Outlook calendar and copy the **ICS** link.
2. Connect this repo to **Cloudflare Pages**.
3. Cloudflare builds the site on every push and serves it at your `.pages.dev` URL.
4. **Cloudflare Access** restricts the site to your email only.

## Files

- `scripts/generate_agenda.py` — downloads the ICS feed and generates `site/index.html`
- `requirements.txt` — Python dependencies
- `site/` — generated output (build artifact, not committed)

## Setup

### 1. Get the Outlook ICS link

- Outlook on the web > **Calendar** > **Settings** > **Shared calendars** > **Publish a calendar**
- Choose your calendar and detail level, click **Publish**
- Copy the **ICS** link

If you do not see this option, your tenant probably has calendar publishing restricted.

### 2. Connect to Cloudflare Pages

1. Go to Cloudflare dashboard > **Workers & Pages** > **Create** > **Pages** > **Connect to Git**
2. Select this repository
3. Build settings:
   - **Framework preset:** None
   - **Build command:** `pip install -r requirements.txt && python scripts/generate_agenda.py`
   - **Build output directory:** `site`
4. Add environment variables:
   - `ICS_URL` (required) — your Outlook-published ICS URL
   - `AGENDA_TITLE` — defaults to `Live Agenda`
   - `AGENDA_TIMEZONE` — defaults to `America/Los_Angeles`
   - `WINDOW_HOURS` — defaults to `48`
   - `MAX_EVENTS` — defaults to `40`
5. Click **Deploy**

### 3. Restrict access with Cloudflare Access

1. Go to **one.dash.cloudflare.com** (Zero Trust dashboard)
2. **Access** > **Applications** > **Add an application** > **Self-hosted**
3. Set the application domain to your `.pages.dev` URL
4. Create a policy: **Allow** > **Selector: Emails** > enter your email
5. Save

Anyone visiting the site will need a one-time code sent to the allowed email.

## Tuning

### Time window

The page shows the next 48 hours by default. Change `WINDOW_HOURS` in your Cloudflare Pages environment variables.

### Browser refresh

The generated page auto-refreshes every 5 minutes.

### Rebuild frequency

The site rebuilds on every push. To trigger a rebuild without code changes, go to **Cloudflare Pages > your project > Deployments > Retry deployment**.

## Local testing

```bash
export ICS_URL='https://example.com/path/to/calendar.ics'
export AGENDA_TIMEZONE='America/Los_Angeles'
pip install -r requirements.txt
python scripts/generate_agenda.py
```

Then open `site/index.html`.

## Troubleshooting

### Build fails

Check the build logs in **Cloudflare Pages > your project > Deployments**. The most common cause is a missing `ICS_URL` environment variable.

### Feed blocked or expired

If Outlook rotates or revokes the published link, update the `ICS_URL` environment variable in Cloudflare Pages settings.

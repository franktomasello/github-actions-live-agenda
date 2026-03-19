# GitHub Actions Live Agenda

This project builds a small agenda website from a published Outlook or Reclaim-backed ICS feed and deploys it with GitHub Actions + GitHub Pages.

## What this is optimized for

- No Slack app approvals
- No Microsoft Graph app registration
- Cheap and simple operation on GitHub Actions schedule
- A clean static page you can bookmark on desktop or mobile

## How it works

1. You publish an Outlook calendar and copy the **ICS** link.
2. You save that ICS link as a GitHub Actions secret named `ICS_URL`.
3. GitHub Actions runs on a schedule, downloads the calendar feed, generates `site/index.html`, and deploys it to GitHub Pages.

## Important privacy note

This solution is only appropriate if you are comfortable exposing the resulting agenda wherever the GitHub Pages site is published.

- On **GitHub Free**, GitHub Pages is available for **public repositories**.
- GitHub Pages is also available for **private repositories** on paid plans such as GitHub Pro, Team, Enterprise Cloud, and Enterprise Server.
- **Private GitHub Pages access control** is an Enterprise Cloud feature for organization-owned project sites; it is not a general personal-account privacy feature.

If your calendar is sensitive, do **not** publish it publicly. Use a private repo + a plan that supports your desired Pages visibility, or use a different hosting approach.

## Files

- `.github/workflows/publish-agenda.yml` — scheduled workflow
- `scripts/generate_agenda.py` — downloads the ICS and builds the site
- `site/` — generated output folder used by Pages deployment

## Setup

### 1. Create a repository

Create a new repo and upload these files.

### 2. Add the ICS feed secret

In your repo:

- **Settings → Secrets and variables → Actions → New repository secret**
- Name: `ICS_URL`
- Value: your Outlook-published ICS URL

### 3. Add optional repo variables

In **Settings → Secrets and variables → Actions → Variables**, you can set:

- `AGENDA_TITLE` — defaults to `Live Agenda`
- `AGENDA_TIMEZONE` — defaults to `America/Los_Angeles`
- `WINDOW_HOURS` — defaults to `48`
- `MAX_EVENTS` — defaults to `40`

### 4. Enable GitHub Pages

Go to:

- **Settings → Pages**
- Under **Build and deployment**, set **Source** to **GitHub Actions**

### 5. Run the workflow

Go to **Actions → Publish live agenda → Run workflow**.

After the first successful run, GitHub Pages will give you the site URL.

## Getting the Outlook ICS link

If your Microsoft tenant allows it, Outlook on the web lets you publish a calendar and exposes both **HTML** and **ICS** links.

Typical path:

- Outlook on the web
- **Calendar**
- **Settings**
- **Shared calendars**
- **Publish a calendar**
- Choose your calendar and detail level
- Click **Publish**
- Copy the **ICS** link

If you do not see this option, your tenant probably has calendar publishing restricted.

## Tuning

### Refresh frequency

The workflow runs every 15 minutes at minutes 7, 22, 37, and 52 of each hour. You can change the cron in `.github/workflows/publish-agenda.yml`.

### Time window

By default, the page shows the next 48 hours. Change `WINDOW_HOURS` to tighten or widen the window.

### Browser refresh

The generated page auto-refreshes in the browser every 5 minutes so you do not need to manually reload it.

## Local testing

You can test locally with:

```bash
export ICS_URL='https://example.com/path/to/calendar.ics'
export AGENDA_TIMEZONE='America/Los_Angeles'
python -m pip install icalendar
python scripts/generate_agenda.py
```

Then open `site/index.html`.

## Common failure modes

### Missing `ICS_URL`

The workflow will fail immediately if the secret is missing.

### Feed blocked or expired

If Outlook rotates or revokes the published link, replace the `ICS_URL` secret.

### GitHub Pages not enabled

If the workflow builds but does not publish, confirm **Settings → Pages → Source = GitHub Actions**.

## Best use case

This is best when you want a lightweight, near-live agenda page and your environment allows publishing an ICS calendar but does **not** allow the heavier Slack / Microsoft Graph integration path.

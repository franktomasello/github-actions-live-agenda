/**
 * Cloudflare Pages Function — /api/events
 *
 * Fetches the Outlook ICS URL on every request and returns filtered,
 * parsed events as JSON. No caching — always live data.
 *
 * Environment variables (same ones the Python build uses):
 *   ICS_URL            required
 *   AGENDA_TIMEZONE    default: America/Los_Angeles
 *   WINDOW_HOURS       default: 48
 *   MAX_EVENTS         default: 40
 */
export async function onRequestGet(context) {
  const { env } = context;
  const icsUrl = env.ICS_URL;
  if (!icsUrl) {
    return json({ error: 'ICS_URL not configured' }, 500);
  }

  const timezone   = env.AGENDA_TIMEZONE || 'America/Los_Angeles';
  const windowHrs  = parseInt(env.WINDOW_HOURS || '48', 10);
  const maxEvents  = parseInt(env.MAX_EVENTS  || '40',  10);

  let icsText;
  try {
    const resp = await fetch(icsUrl, {
      headers: {
        'User-Agent': 'github-actions-live-agenda/1.0',
        'Accept': 'text/calendar, text/plain, */*',
      },
    });
    if (!resp.ok) return json({ error: `ICS fetch failed: HTTP ${resp.status}` }, 502);
    icsText = await resp.text();
  } catch (err) {
    return json({ error: `ICS fetch error: ${err.message}` }, 502);
  }

  const events = parseICS(icsText, timezone, windowHrs, maxEvents);
  return json({ events, timezone, generatedAt: new Date().toISOString() });
}

// ── JSON helper ──────────────────────────────────────────────────────────────

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'no-store',
    },
  });
}

// ── Timezone conversion ──────────────────────────────────────────────────────

/**
 * Convert a local date/time expressed in `tz` to a UTC Date object.
 * Uses the "probe then correct" technique — valid for all IANA timezones.
 */
function localToUTC(y, mo, d, h, m, s, tz) {
  const probe = new Date(Date.UTC(y, mo, d, h, m, s));
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en', {
      timeZone: tz,
      year: 'numeric', month: 'numeric', day: 'numeric',
      hour: 'numeric', minute: 'numeric', second: 'numeric',
      hour12: false,
    }).formatToParts(probe).map(p => [p.type, p.value])
  );
  const probeAsLocal = Date.UTC(
    +parts.year, +parts.month - 1, +parts.day,
    +parts.hour % 24, +parts.minute, +parts.second,
  );
  return new Date(probe.getTime() + (probe.getTime() - probeAsLocal));
}

// ── ICS datetime parsing ─────────────────────────────────────────────────────

function parseDatetime(value, params, defaultTz) {
  value = value.trim();

  // All-day: YYYYMMDD
  if (value.length === 8) {
    const y = +value.slice(0, 4), mo = +value.slice(4, 6) - 1, d = +value.slice(6, 8);
    return { dt: new Date(Date.UTC(y, mo, d)), isAllDay: true };
  }

  const m = value.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$/);
  if (!m) return { dt: null, isAllDay: false };
  const [, y, mo, d, h, mi, s, utc] = m;

  if (utc === 'Z') {
    return { dt: new Date(Date.UTC(+y, +mo - 1, +d, +h, +mi, +s)), isAllDay: false };
  }

  const tzid = (params.match(/TZID=([^;]+)/i) || [])[1] || defaultTz;
  return { dt: localToUTC(+y, +mo - 1, +d, +h, +mi, +s, tzid), isAllDay: false };
}

// ── ICS parser ───────────────────────────────────────────────────────────────

const decode = s => s
  .replace(/\\n/g, '\n').replace(/\\,/g, ',')
  .replace(/\\;/g, ';').replace(/\\\\/g, '\\');

function parseICS(text, timezone, windowHours, maxEvents) {
  // RFC 5545 line unfolding
  const unfolded = text
    .replace(/\r\n[ \t]/g, '')
    .replace(/\r\n/g, '\n')
    .replace(/\n[ \t]/g, '');

  const now       = new Date();
  const windowEnd = new Date(now.getTime() + windowHours * 3_600_000);
  const events    = [];

  for (const block of unfolded.split(/BEGIN:VEVENT\n?/).slice(1)) {
    const content = block.slice(0, block.indexOf('END:VEVENT') >>> 0 || block.length);

    const props = new Map();
    for (const line of content.split('\n')) {
      const ci = line.indexOf(':');
      if (ci < 0) continue;
      const keyPart = line.slice(0, ci);
      const val     = line.slice(ci + 1).trimEnd();
      const si      = keyPart.indexOf(';');
      const name    = (si >= 0 ? keyPart.slice(0, si) : keyPart).toUpperCase();
      const params  = si >= 0 ? keyPart.slice(si + 1) : '';
      if (!props.has(name)) props.set(name, { value: val, params });
    }

    if ((props.get('STATUS')?.value || '').toUpperCase() === 'CANCELLED') continue;

    const dts = props.get('DTSTART');
    if (!dts) continue;
    const { dt: start, isAllDay } = parseDatetime(dts.value, dts.params, timezone);
    if (!start) continue;

    const dte  = props.get('DTEND');
    const end  = dte
      ? parseDatetime(dte.value, dte.params, timezone).dt
      : new Date(start.getTime() + (isAllDay ? 86_400_000 : 3_600_000));

    if (end < now || start > windowEnd) continue;

    events.push({
      title:       decode(props.get('SUMMARY')?.value     || 'Untitled'),
      start:       start.toISOString(),
      end:         end.toISOString(),
      location:    decode(props.get('LOCATION')?.value    || ''),
      description: decode(props.get('DESCRIPTION')?.value || ''),
      isAllDay,
    });
  }

  events.sort((a, b) =>
    a.start.localeCompare(b.start) ||
    a.end.localeCompare(b.end)     ||
    a.title.toLowerCase().localeCompare(b.title.toLowerCase())
  );
  return events.slice(0, maxEvents);
}

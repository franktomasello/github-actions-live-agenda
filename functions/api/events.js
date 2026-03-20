/**
 * Cloudflare Pages Function — /api/events
 *
 * Fetches the ICS feed on each request and returns parsed events as JSON.
 * Short edge cache (2s) prevents hammering the ICS source on rapid polls.
 */
export async function onRequestGet(context) {
  const { env } = context;
  const icsUrl = env.ICS_URL;
  if (!icsUrl) {
    return json({ error: 'ICS_URL not configured' }, 500);
  }

  const timezone  = env.AGENDA_TIMEZONE || 'America/Los_Angeles';
  const windowHrs = parseInt(env.WINDOW_HOURS || '48', 10);
  const maxEvents = parseInt(env.MAX_EVENTS  || '40',  10);

  let icsText;
  try {
    const resp = await fetch(icsUrl, {
      headers: {
        'User-Agent': 'github-actions-live-agenda/1.0',
        'Accept': 'text/calendar, text/plain, */*',
        'Cache-Control': 'no-cache',
      },
      cf: { cacheTtl: 0, cacheEverything: false },
    });
    if (!resp.ok) return json({ error: `ICS fetch failed: HTTP ${resp.status}` }, 502);
    icsText = await resp.text();
  } catch (err) {
    return json({ error: `ICS fetch error: ${err.message}` }, 502);
  }

  const events = parseICS(icsText, timezone, windowHrs, maxEvents);
  return json(
    { events, timezone, generatedAt: new Date().toISOString() },
    200,
    { 'Cache-Control': 'public, s-maxage=2, stale-while-revalidate=5' },
  );
}

// ── JSON helper ──────────────────────────────────────────────────────────────

function json(body, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: {
      'Content-Type': 'application/json',
      ...extraHeaders,
    },
  });
}

// ── Timezone conversion ──────────────────────────────────────────────────────

// Cache DateTimeFormat instances keyed by timezone (expensive to construct).
const _fmtCache = new Map();
function getFmt(tz) {
  let f = _fmtCache.get(tz);
  if (!f) {
    f = new Intl.DateTimeFormat('en', {
      timeZone: tz,
      year: 'numeric', month: 'numeric', day: 'numeric',
      hour: 'numeric', minute: 'numeric', second: 'numeric',
      hour12: false,
    });
    _fmtCache.set(tz, f);
  }
  return f;
}

function localToUTC(y, mo, d, h, m, s, tz) {
  const probe = new Date(Date.UTC(y, mo, d, h, m, s));
  const parts = Object.fromEntries(
    getFmt(tz).formatToParts(probe).map(p => [p.type, p.value])
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
  // RFC 5545 line unfolding — single pass
  const unfolded = text.replace(/\r\n/g, '\n').replace(/\n[ \t]/g, '');

  const now       = new Date();
  const windowEnd = new Date(now.getTime() + windowHours * 3_600_000);
  const events    = [];

  const blocks = unfolded.split('BEGIN:VEVENT');
  for (let bi = 1; bi < blocks.length; bi++) {
    const endIdx = blocks[bi].indexOf('END:VEVENT');
    const content = endIdx >= 0 ? blocks[bi].slice(0, endIdx) : blocks[bi];

    const props = new Map();
    const lines = content.split('\n');
    for (let li = 0; li < lines.length; li++) {
      const line = lines[li];
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

    const dte = props.get('DTEND');
    const end = dte
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
    a.start < b.start ? -1 : a.start > b.start ? 1 :
    a.end   < b.end   ? -1 : a.end   > b.end   ? 1 :
    a.title.toLowerCase().localeCompare(b.title.toLowerCase())
  );
  return events.slice(0, maxEvents);
}

/**
 * Cloudflare Pages Function — /api/events
 *
 * Fetches the ICS feed on each request and returns parsed events as JSON.
 * Minimal edge cache (2s) prevents duplicate requests while keeping data fresh.
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
      cf: { cacheTtl: 0 },
      headers: {
        'User-Agent': 'github-actions-live-agenda/1.0',
        'Accept': 'text/calendar, text/plain, */*',
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
      },
    });
    if (!resp.ok) return json({ error: `ICS fetch failed: HTTP ${resp.status}` }, 502);
    icsText = await resp.text();
  } catch (err) {
    return json({ error: `ICS fetch error: ${err.message}` }, 502);
  }

  let events;
  try {
    events = parseICS(icsText, timezone, windowHrs, maxEvents);
  } catch (err) {
    return json({ error: `ICS parse error: ${err.message}`, icsLength: icsText.length }, 500);
  }

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

// ── Windows → IANA timezone mapping ──────────────────────────────────────────

const WIN_TZ = {
  'AUS Central Standard Time': 'Australia/Darwin',
  'AUS Eastern Standard Time': 'Australia/Sydney',
  'Afghanistan Standard Time': 'Asia/Kabul',
  'Alaskan Standard Time': 'America/Anchorage',
  'Arab Standard Time': 'Asia/Riyadh',
  'Arabian Standard Time': 'Asia/Dubai',
  'Arabic Standard Time': 'Asia/Baghdad',
  'Argentina Standard Time': 'America/Buenos_Aires',
  'Atlantic Standard Time': 'America/Halifax',
  'Azerbaijan Standard Time': 'Asia/Baku',
  'Azores Standard Time': 'Atlantic/Azores',
  'Canada Central Standard Time': 'America/Regina',
  'Cape Verde Standard Time': 'Atlantic/Cape_Verde',
  'Central America Standard Time': 'America/Guatemala',
  'Central Asia Standard Time': 'Asia/Almaty',
  'Central Brazilian Standard Time': 'America/Cuiaba',
  'Central Europe Standard Time': 'Europe/Budapest',
  'Central European Standard Time': 'Europe/Warsaw',
  'Central Pacific Standard Time': 'Pacific/Guadalcanal',
  'Central Standard Time': 'America/Chicago',
  'Central Standard Time (Mexico)': 'America/Mexico_City',
  'China Standard Time': 'Asia/Shanghai',
  'E. Africa Standard Time': 'Africa/Nairobi',
  'E. Australia Standard Time': 'Australia/Brisbane',
  'E. Europe Standard Time': 'Europe/Chisinau',
  'E. South America Standard Time': 'America/Sao_Paulo',
  'Eastern Standard Time': 'America/New_York',
  'Eastern Standard Time (Mexico)': 'America/Cancun',
  'Egypt Standard Time': 'Africa/Cairo',
  'FLE Standard Time': 'Europe/Kiev',
  'Fiji Standard Time': 'Pacific/Fiji',
  'GMT Standard Time': 'Europe/London',
  'GTB Standard Time': 'Europe/Bucharest',
  'Georgian Standard Time': 'Asia/Tbilisi',
  'Greenland Standard Time': 'America/Godthab',
  'Greenwich Standard Time': 'Atlantic/Reykjavik',
  'Haiti Standard Time': 'America/Port-au-Prince',
  'Hawaiian Standard Time': 'Pacific/Honolulu',
  'India Standard Time': 'Asia/Calcutta',
  'Iran Standard Time': 'Asia/Tehran',
  'Israel Standard Time': 'Asia/Jerusalem',
  'Jordan Standard Time': 'Asia/Amman',
  'Korea Standard Time': 'Asia/Seoul',
  'Mauritius Standard Time': 'Indian/Mauritius',
  'Middle East Standard Time': 'Asia/Beirut',
  'Mountain Standard Time': 'America/Denver',
  'Mountain Standard Time (Mexico)': 'America/Chihuahua',
  'Myanmar Standard Time': 'Asia/Rangoon',
  'N. Central Asia Standard Time': 'Asia/Novosibirsk',
  'Namibia Standard Time': 'Africa/Windhoek',
  'Nepal Standard Time': 'Asia/Katmandu',
  'New Zealand Standard Time': 'Pacific/Auckland',
  'Newfoundland Standard Time': 'America/St_Johns',
  'North Asia East Standard Time': 'Asia/Irkutsk',
  'North Asia Standard Time': 'Asia/Krasnoyarsk',
  'Pacific SA Standard Time': 'America/Santiago',
  'Pacific Standard Time': 'America/Los_Angeles',
  'Pacific Standard Time (Mexico)': 'America/Tijuana',
  'Pakistan Standard Time': 'Asia/Karachi',
  'Romance Standard Time': 'Europe/Paris',
  'Russian Standard Time': 'Europe/Moscow',
  'SA Eastern Standard Time': 'America/Cayenne',
  'SA Pacific Standard Time': 'America/Bogota',
  'SA Western Standard Time': 'America/La_Paz',
  'SE Asia Standard Time': 'Asia/Bangkok',
  'Samoa Standard Time': 'Pacific/Apia',
  'Singapore Standard Time': 'Asia/Singapore',
  'South Africa Standard Time': 'Africa/Johannesburg',
  'Sri Lanka Standard Time': 'Asia/Colombo',
  'Taipei Standard Time': 'Asia/Taipei',
  'Tasmania Standard Time': 'Australia/Hobart',
  'Tokyo Standard Time': 'Asia/Tokyo',
  'Tonga Standard Time': 'Pacific/Tongatapu',
  'Turkey Standard Time': 'Europe/Istanbul',
  'US Eastern Standard Time': 'America/Indianapolis',
  'US Mountain Standard Time': 'America/Phoenix',
  'UTC': 'Etc/UTC',
  'Venezuela Standard Time': 'America/Caracas',
  'W. Australia Standard Time': 'Australia/Perth',
  'W. Central Africa Standard Time': 'Africa/Lagos',
  'W. Europe Standard Time': 'Europe/Berlin',
  'West Asia Standard Time': 'Asia/Tashkent',
  'West Pacific Standard Time': 'Pacific/Port_Moresby',
  'Yakutsk Standard Time': 'Asia/Yakutsk',
};

function resolveTimezone(tz, fallback) {
  const candidate = WIN_TZ[tz] || tz;
  try {
    Intl.DateTimeFormat(undefined, { timeZone: candidate });
    return candidate;
  } catch {
    // Unknown timezone — fall back to the configured default
    return fallback || 'UTC';
  }
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

  const rawTz = (params.match(/TZID=([^;]+)/i) || [])[1] || defaultTz;
  const tzid = resolveTimezone(rawTz, defaultTz);
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

/**
 * Cloudflare Pages Function — /api/events
 *
 * Two data-source modes (in priority order):
 *
 * 1. Microsoft Graph API — real-time, reflects calendar changes within seconds.
 *    Requires: MS_CLIENT_ID, MS_CLIENT_SECRET, MS_TENANT_ID, MS_REFRESH_TOKEN
 *    Optional: MS_USER_EMAIL (uses client-credentials flow instead of refresh-token)
 *
 * 2. ICS feed — fallback; Outlook published ICS feeds have a 15–30 min delay.
 *    Requires: ICS_URL
 */

// In-memory access-token cache (survives across requests within one CF isolate)
let _accessToken = null;
let _tokenExpiry = 0;

export async function onRequestGet(context) {
  const { env } = context;

  const timezone  = env.AGENDA_TIMEZONE || 'America/Los_Angeles';
  const windowHrs = parseInt(env.WINDOW_HOURS || '48', 10);
  const maxEvents = parseInt(env.MAX_EVENTS  || '40',  10);

  // ── 1. Prefer Microsoft Graph API for real-time data ───────────────────────
  const graphReady = env.MS_CLIENT_ID && env.MS_CLIENT_SECRET && env.MS_TENANT_ID
    && (env.MS_REFRESH_TOKEN || env.MS_USER_EMAIL);

  if (graphReady) {
    try {
      const events = await fetchFromGraph(env, timezone, windowHrs, maxEvents);
      return json(
        { events, timezone, source: 'graph', generatedAt: new Date().toISOString() },
        200,
        { 'Cache-Control': 'public, s-maxage=5, stale-while-revalidate=10' },
      );
    } catch (err) {
      console.error('[graph] ' + err.message);
      if (!env.ICS_URL) {
        return json({ error: 'Graph API error: ' + err.message }, 502);
      }
      // Graph failed but ICS_URL is available — fall through
    }
  }

  // ── 2. Fall back to ICS feed ───────────────────────────────────────────────
  const icsUrl = env.ICS_URL;
  if (!icsUrl) {
    return json({
      error: 'No data source configured. Set MS_CLIENT_ID + MS_CLIENT_SECRET + MS_TENANT_ID + MS_REFRESH_TOKEN for real-time Graph API, or ICS_URL for ICS feed.',
    }, 500);
  }

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
    if (!resp.ok) return json({ error: 'ICS fetch failed: HTTP ' + resp.status }, 502);
    icsText = await resp.text();
  } catch (err) {
    return json({ error: 'ICS fetch error: ' + err.message }, 502);
  }

  let events;
  try {
    events = parseICS(icsText, timezone, windowHrs, maxEvents);
  } catch (err) {
    return json({ error: 'ICS parse error: ' + err.message, icsLength: icsText.length }, 500);
  }

  return json(
    { events, timezone, source: 'ics', generatedAt: new Date().toISOString() },
    200,
    { 'Cache-Control': 'public, s-maxage=30, stale-while-revalidate=30' },
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

// ── Microsoft Graph API ──────────────────────────────────────────────────────

async function getAccessToken(env) {
  // Return cached token if still valid (with 5-min safety margin)
  if (_accessToken && Date.now() < _tokenExpiry - 300_000) {
    return _accessToken;
  }

  const url = `https://login.microsoftonline.com/${env.MS_TENANT_ID}/oauth2/v2.0/token`;
  const body = new URLSearchParams({
    client_id:     env.MS_CLIENT_ID,
    client_secret: env.MS_CLIENT_SECRET,
  });

  if (env.MS_REFRESH_TOKEN) {
    // Delegated flow: refresh token → access token
    body.set('grant_type', 'refresh_token');
    body.set('refresh_token', env.MS_REFRESH_TOKEN);
    body.set('scope', 'https://graph.microsoft.com/Calendars.Read offline_access');
  } else {
    // Client credentials flow: app-only token (requires MS_USER_EMAIL)
    body.set('grant_type', 'client_credentials');
    body.set('scope', 'https://graph.microsoft.com/.default');
  }

  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: body.toString(),
  });

  if (!resp.ok) {
    const text = await resp.text();
    throw new Error('Token request failed (' + resp.status + '): ' + text);
  }

  const data = await resp.json();
  _accessToken = data.access_token;
  _tokenExpiry = Date.now() + (data.expires_in || 3600) * 1000;
  return _accessToken;
}

async function fetchFromGraph(env, timezone, windowHours, maxEvents) {
  const token = await getAccessToken(env);

  const now       = new Date();
  const windowEnd = new Date(now.getTime() + windowHours * 3_600_000);

  // /me for delegated (refresh-token) flow, /users/{email} for client-credentials
  const basePath = env.MS_REFRESH_TOKEN
    ? '/me'
    : '/users/' + encodeURIComponent(env.MS_USER_EMAIL);

  const params = new URLSearchParams({
    startDateTime: now.toISOString(),
    endDateTime:   windowEnd.toISOString(),
    $top:          String(maxEvents),
    $select:       'subject,start,end,location,bodyPreview,isCancelled,isAllDay',
    $orderby:      'start/dateTime',
  });

  const resp = await fetch(
    'https://graph.microsoft.com/v1.0' + basePath + '/calendarView?' + params,
    {
      headers: {
        Authorization: 'Bearer ' + token,
        Prefer: 'outlook.timezone="UTC"',
      },
    },
  );

  if (!resp.ok) {
    if (resp.status === 401) { _accessToken = null; _tokenExpiry = 0; }
    const text = await resp.text();
    throw new Error('Graph calendarView failed (' + resp.status + '): ' + text);
  }

  const data   = await resp.json();
  const events = [];

  for (const ev of (data.value || [])) {
    if (ev.isCancelled) continue;

    const startRaw = ev.start?.dateTime;
    const endRaw   = ev.end?.dateTime;
    if (!startRaw || !endRaw) continue;

    // Graph returns datetime without trailing Z when Prefer: outlook.timezone="UTC"
    const start = new Date(startRaw.endsWith('Z') ? startRaw : startRaw + 'Z');
    const end   = new Date(endRaw.endsWith('Z')   ? endRaw   : endRaw + 'Z');

    events.push({
      title:       ev.subject || 'Untitled',
      start:       start.toISOString(),
      end:         end.toISOString(),
      location:    ev.location?.displayName || '',
      description: ev.bodyPreview || '',
      isAllDay:    ev.isAllDay || false,
    });
  }

  return events;
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

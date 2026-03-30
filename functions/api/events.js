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
    { 'Cache-Control': 'no-store' },
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

// ── RRULE expansion ──────────────────────────────────────────────────────────

const DAY_MAP = { SU: 0, MO: 1, TU: 2, WE: 3, TH: 4, FR: 5, SA: 6 };

function parseRRule(value) {
  const rule = {};
  for (const part of value.split(';')) {
    const eq = part.indexOf('=');
    if (eq < 0) continue;
    rule[part.slice(0, eq).toUpperCase()] = part.slice(eq + 1);
  }
  return rule;
}

function parseExdates(props, defaultTz) {
  const dates = new Set();
  // Collect all EXDATE entries (there can be multiple lines)
  for (const [key, entry] of props) {
    if (key !== 'EXDATE') continue;
    for (const v of entry.value.split(',')) {
      const { dt } = parseDatetime(v.trim(), entry.params, defaultTz);
      if (dt) dates.add(dt.getTime());
    }
  }
  return dates;
}

function expandRRule(ruleStr, dtStart, duration, isAllDay, windowStart, windowEnd, exdates, defaultTz) {
  const rule = parseRRule(ruleStr);
  const freq = (rule.FREQ || '').toUpperCase();
  const interval = parseInt(rule.INTERVAL || '1', 10);
  const count = rule.COUNT ? parseInt(rule.COUNT, 10) : Infinity;
  const wkst = DAY_MAP[rule.WKST] ?? 1;

  let until = null;
  if (rule.UNTIL) {
    const { dt } = parseDatetime(rule.UNTIL, '', defaultTz);
    if (dt) until = dt;
  }

  const byDay = rule.BYDAY
    ? rule.BYDAY.split(',').map(d => {
        const m = d.match(/^(-?\d+)?(SU|MO|TU|WE|TH|FR|SA)$/i);
        return m ? { ord: m[1] ? parseInt(m[1], 10) : 0, day: DAY_MAP[m[2].toUpperCase()] } : null;
      }).filter(Boolean)
    : null;
  const byMonthDay = rule.BYMONTHDAY
    ? rule.BYMONTHDAY.split(',').map(Number)
    : null;
  const byMonth = rule.BYMONTH
    ? rule.BYMONTH.split(',').map(Number)
    : null;
  const bySetPos = rule.BYSETPOS
    ? rule.BYSETPOS.split(',').map(Number)
    : null;

  const occurrences = [];
  let generated = 0;
  const hardLimit = 1000;

  // Iterate by advancing a candidate date according to FREQ+INTERVAL
  let cursor = new Date(dtStart.getTime());

  for (let iter = 0; iter < hardLimit && generated < count; iter++) {
    let candidates = [new Date(cursor.getTime())];

    // Expand BYDAY for WEEKLY
    if (freq === 'WEEKLY' && byDay) {
      candidates = [];
      const baseDay = cursor.getUTCDay();
      // Find the start-of-week for this cursor
      let weekStart = new Date(cursor.getTime());
      let diff = (baseDay - wkst + 7) % 7;
      weekStart.setUTCDate(weekStart.getUTCDate() - diff);
      // Reset to same time as dtStart
      weekStart.setUTCHours(dtStart.getUTCHours(), dtStart.getUTCMinutes(), dtStart.getUTCSeconds(), 0);
      for (const bd of byDay) {
        const target = (bd.day - wkst + 7) % 7;
        const cand = new Date(weekStart.getTime());
        cand.setUTCDate(cand.getUTCDate() + target);
        candidates.push(cand);
      }
      candidates.sort((a, b) => a.getTime() - b.getTime());
    }

    // Expand BYDAY for MONTHLY (with ordinal, e.g. 2TU = second Tuesday)
    if (freq === 'MONTHLY' && byDay) {
      candidates = [];
      const y = cursor.getUTCFullYear(), m = cursor.getUTCMonth();
      for (const bd of byDay) {
        if (bd.ord !== 0) {
          // Nth weekday of month
          const daysInMonth = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
          const matches = [];
          for (let d = 1; d <= daysInMonth; d++) {
            if (new Date(Date.UTC(y, m, d)).getUTCDay() === bd.day) matches.push(d);
          }
          const idx = bd.ord > 0 ? bd.ord - 1 : matches.length + bd.ord;
          if (idx >= 0 && idx < matches.length) {
            const cand = new Date(Date.UTC(y, m, matches[idx],
              dtStart.getUTCHours(), dtStart.getUTCMinutes(), dtStart.getUTCSeconds()));
            candidates.push(cand);
          }
        } else {
          // Every weekday in the month
          const daysInMonth = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
          for (let d = 1; d <= daysInMonth; d++) {
            if (new Date(Date.UTC(y, m, d)).getUTCDay() === bd.day) {
              candidates.push(new Date(Date.UTC(y, m, d,
                dtStart.getUTCHours(), dtStart.getUTCMinutes(), dtStart.getUTCSeconds())));
            }
          }
        }
      }
      candidates.sort((a, b) => a.getTime() - b.getTime());
    }

    // Expand BYMONTHDAY for MONTHLY/YEARLY
    if ((freq === 'MONTHLY' || freq === 'YEARLY') && byMonthDay && !byDay) {
      candidates = [];
      const y = cursor.getUTCFullYear(), m = cursor.getUTCMonth();
      const months = (freq === 'YEARLY' && byMonth) ? byMonth.map(x => x - 1) : [m];
      for (const mo of months) {
        const daysInMonth = new Date(Date.UTC(y, mo + 1, 0)).getUTCDate();
        for (const md of byMonthDay) {
          const day = md > 0 ? md : daysInMonth + md + 1;
          if (day >= 1 && day <= daysInMonth) {
            candidates.push(new Date(Date.UTC(y, mo, day,
              dtStart.getUTCHours(), dtStart.getUTCMinutes(), dtStart.getUTCSeconds())));
          }
        }
      }
      candidates.sort((a, b) => a.getTime() - b.getTime());
    }

    // BYSETPOS filtering
    if (bySetPos && candidates.length > 0) {
      const filtered = [];
      for (const pos of bySetPos) {
        const idx = pos > 0 ? pos - 1 : candidates.length + pos;
        if (idx >= 0 && idx < candidates.length) filtered.push(candidates[idx]);
      }
      candidates = filtered;
    }

    for (const cand of candidates) {
      if (generated >= count) break;
      if (until && cand > until) return occurrences;
      if (cand < dtStart) continue;
      if (cand > windowEnd) return occurrences;

      if (cand >= windowStart && !exdates.has(cand.getTime())) {
        occurrences.push({
          start: new Date(cand.getTime()),
          end:   new Date(cand.getTime() + duration),
        });
      }
      generated++;
    }

    // Advance cursor by FREQ * INTERVAL
    switch (freq) {
      case 'DAILY':
        cursor.setUTCDate(cursor.getUTCDate() + interval);
        break;
      case 'WEEKLY':
        cursor.setUTCDate(cursor.getUTCDate() + 7 * interval);
        break;
      case 'MONTHLY':
        cursor.setUTCMonth(cursor.getUTCMonth() + interval);
        break;
      case 'YEARLY':
        cursor.setUTCFullYear(cursor.getUTCFullYear() + interval);
        break;
      default:
        return occurrences;
    }
  }
  return occurrences;
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

  // Collect RECURRENCE-ID overrides: UID → Set of epoch millis
  // These mark specific occurrences that have been modified (the modified
  // version appears as its own VEVENT, so we skip the generated occurrence).
  const overriddenUIDs = new Map();

  const blocks = unfolded.split('BEGIN:VEVENT');

  // First pass: collect all RECURRENCE-ID entries
  for (let bi = 1; bi < blocks.length; bi++) {
    const endIdx = blocks[bi].indexOf('END:VEVENT');
    const content = endIdx >= 0 ? blocks[bi].slice(0, endIdx) : blocks[bi];
    const lines = content.split('\n');
    let uid = null, recId = null;
    for (const line of lines) {
      const ci = line.indexOf(':');
      if (ci < 0) continue;
      const keyPart = line.slice(0, ci);
      const val     = line.slice(ci + 1).trimEnd();
      const si      = keyPart.indexOf(';');
      const name    = (si >= 0 ? keyPart.slice(0, si) : keyPart).toUpperCase();
      const params  = si >= 0 ? keyPart.slice(si + 1) : '';
      if (name === 'UID') uid = val;
      if (name === 'RECURRENCE-ID') {
        const { dt } = parseDatetime(val, params, timezone);
        if (dt) recId = dt.getTime();
      }
    }
    if (uid && recId != null) {
      if (!overriddenUIDs.has(uid)) overriddenUIDs.set(uid, new Set());
      overriddenUIDs.get(uid).add(recId);
    }
  }

  // Second pass: parse events and expand recurrences
  for (let bi = 1; bi < blocks.length; bi++) {
    const endIdx = blocks[bi].indexOf('END:VEVENT');
    const content = endIdx >= 0 ? blocks[bi].slice(0, endIdx) : blocks[bi];

    // Parse props — collect ALL EXDATE lines (not just the first)
    const props = new Map();
    const exdateEntries = [];
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
      if (name === 'EXDATE') {
        exdateEntries.push({ value: val, params });
      }
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

    const duration = end.getTime() - start.getTime();
    const title       = decode(props.get('SUMMARY')?.value     || 'Untitled');
    const location    = decode(props.get('LOCATION')?.value    || '');
    const description = decode(props.get('DESCRIPTION')?.value || '');
    const uid         = props.get('UID')?.value || '';
    const rrule       = props.get('RRULE');
    const recurrenceId = props.get('RECURRENCE-ID');

    // Helper: check if a UTC timestamp falls in the AM in the configured timezone
    const isAM = (dt) => {
      const hour = +getFmt(timezone).formatToParts(dt)
        .find(p => p.type === 'hour').value;
      return (hour % 24) < 12;
    };

    // Hide the morning "Away from Desk" block (keep the evening one)
    const isAwayFromDesk = title.toLowerCase() === 'away from desk';

    // Build EXDATE set for this event
    const exdates = new Set();
    for (const entry of exdateEntries) {
      for (const v of entry.value.split(',')) {
        const { dt } = parseDatetime(v.trim(), entry.params, timezone);
        if (dt) exdates.add(dt.getTime());
      }
    }

    // Also exclude dates overridden by RECURRENCE-ID entries
    const uidOverrides = overriddenUIDs.get(uid);
    if (uidOverrides) {
      for (const ts of uidOverrides) exdates.add(ts);
    }

    if (rrule && !recurrenceId) {
      // Expand recurring event
      const occurrences = expandRRule(
        rrule.value, start, duration, isAllDay, now, windowEnd, exdates, timezone
      );
      for (const occ of occurrences) {
        if (isAwayFromDesk && isAM(occ.start)) continue;
        events.push({
          title, location, description, isAllDay,
          start: occ.start.toISOString(),
          end:   occ.end.toISOString(),
        });
      }
    } else {
      // Single event or a RECURRENCE-ID override instance
      if (end < now || start > windowEnd) continue;
      if (isAwayFromDesk && isAM(start)) continue;
      events.push({ title, start: start.toISOString(), end: end.toISOString(), location, description, isAllDay });
    }
  }

  events.sort((a, b) =>
    a.start < b.start ? -1 : a.start > b.start ? 1 :
    a.end   < b.end   ? -1 : a.end   > b.end   ? 1 :
    a.title.toLowerCase().localeCompare(b.title.toLowerCase())
  );
  return events.slice(0, maxEvents);
}

from __future__ import annotations

import html
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from icalendar import Calendar
from zoneinfo import ZoneInfo

import recurring_ical_events

# ── Client-side renderer ──────────────────────────────────────────────────────
# Raw string so normal JS braces don't need escaping in the f-string template.
_RENDER_JS = r"""
(function () {
  'use strict';
  var TZ           = window.__AGENDA_TZ__;
  var WINDOW_HOURS = window.__AGENDA_WINDOW_HOURS__;
  var POLL_MS      = 5000;
  var TICK_MS      = 1000;

  var currentSig   = null;
  var currentData  = null;

  // ── Cached formatters (Intl.DateTimeFormat is expensive to construct) ──────
  var _fmtTime = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ, hour: 'numeric', minute: '2-digit', hour12: true,
  });
  var _fmtDate = new Intl.DateTimeFormat('en-CA', {
    timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit',
  });
  var _fmtSection = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ, weekday: 'long', month: 'short', day: 'numeric',
  });
  var _fmtLongDate = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ, month: 'long', day: 'numeric', year: 'numeric',
  });
  var _fmtHour = new Intl.DateTimeFormat('en', {
    timeZone: TZ, hour: 'numeric', hour12: false,
  });
  var _fmtClockDate = new Intl.DateTimeFormat('en-US', {
    timeZone: TZ, weekday: 'long', month: 'short', day: 'numeric',
  });

  // ── Clock helpers (avoid innerHTML to preserve CSS animations) ─────────────
  function clockParts(now) {
    var parts = _fmtTime.formatToParts(now);
    var hr = '', min = '', period = '';
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].type === 'hour') hr = parts[i].value;
      else if (parts[i].type === 'minute') min = parts[i].value;
      else if (parts[i].type === 'dayPeriod') period = parts[i].value.toUpperCase();
    }
    if (min.length < 2) min = '0' + min;
    return { hr: hr, min: min, period: period };
  }

  // ── HTML helpers ────────────────────────────────────────────────────────────
  function esc(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── Time helpers ────────────────────────────────────────────────────────────
  // Use formatToParts (same path as the clock) so event times are
  // *identical* in casing, spacing and precision to the live clock.
  function fmtTime(iso) {
    var p = clockParts(new Date(iso));
    return p.hr + ':' + p.min + ' ' + p.period;
  }
  function fmtTimeRange(s, e) { return fmtTime(s) + ' \u2013 ' + fmtTime(e); }

  function durStr(s, e) {
    var m = Math.round((new Date(e) - new Date(s)) / 60000);
    if (m < 60) return m + 'm';
    var h = Math.floor(m / 60), r = m % 60;
    return r ? h + 'h ' + r + 'm' : h + 'h';
  }

  function timeUntil(s, e, isAllDay, now) {
    if (isAllDay) return '';
    var start = new Date(s), end = new Date(e);
    if (end < now) return '';
    var diffSec = Math.floor((start - now) / 1000);
    var diffMin = Math.floor(diffSec / 60);
    if (diffMin < -5)  return 'In progress';
    if (diffSec <= 0)  return 'Now';
    if (diffSec < 60)  return 'in ' + diffSec + 's';
    if (diffMin < 5) {
      var rs = diffSec % 60;
      return 'in ' + diffMin + 'm ' + (rs < 10 ? '0' : '') + rs + 's';
    }
    if (diffMin < 60)  return 'in ' + diffMin + ' min';
    var h = Math.floor(diffMin / 60), r = diffMin % 60;
    return r ? 'in ' + h + 'h ' + r + 'm' : 'in ' + h + 'h';
  }

  function progressInfo(s, e, isAllDay, now) {
    if (isAllDay) return null;
    var start = new Date(s), end = new Date(e);
    if (now < start || now > end) return null;
    var total = end - start;
    var elapsed = now - start;
    var pct = Math.min(100, Math.max(0, (elapsed / total) * 100));
    var remainSec = Math.max(0, Math.ceil((end - now) / 1000));
    var remainMin = Math.ceil(remainSec / 60);
    var remainStr;
    if (remainSec < 60) {
      remainStr = remainSec + 's left';
    } else if (remainMin < 60) {
      remainStr = remainMin + 'm left';
    } else {
      var h = Math.floor(remainMin / 60), r = remainMin % 60;
      remainStr = r ? h + 'h ' + r + 'm left' : h + 'h left';
    }
    return { pct: Math.round(pct * 10) / 10, remainStr: remainStr };
  }

  function accent(s, isAllDay) {
    if (isAllDay) return '#af52de';
    var h = +_fmtHour.format(new Date(s)) % 24;
    return h < 12 ? '#ff9f0a' : h < 17 ? '#007aff' : '#5e5ce6';
  }

  function localDate(iso) { return _fmtDate.format(new Date(iso)); }

  function sectionTitle(s, now) {
    var d = localDate(s), t = localDate(now.toISOString());
    var tmr = localDate(new Date(now.getTime() + 86400000).toISOString());
    if (d === t)   return 'Today';
    if (d === tmr) return 'Tomorrow';
    return _fmtSection.format(new Date(s));
  }

  // ── SVG icons ───────────────────────────────────────────────────────────────
  var LOC_ICON   = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>';
  var NOTES_ICON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>';

  // ── Card & section rendering ────────────────────────────────────────────────
  var _isFirstRender = true;
  var _liveFetchDone = false;

  function renderCard(ev, isLast, delay, now, animate) {
    var rel       = timeUntil(ev.start, ev.end, ev.isAllDay, now);
    var isNow     = rel === 'Now' || rel === 'In progress';
    var timeDisp  = ev.isAllDay ? 'All day' : fmtTime(ev.start);
    var rangeDisp = ev.isAllDay ? 'All day' : fmtTimeRange(ev.start, ev.end);
    var dur       = ev.isAllDay ? '' : durStr(ev.start, ev.end);
    var clr       = accent(ev.start, ev.isAllDay);

    var desc = ev.description
      ? ev.description.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\n/g,'<br>')
      : '';

    var prog = progressInfo(ev.start, ev.end, ev.isAllDay, now);
    var progressHtml = '';
    if (prog) {
      progressHtml = '<div class="progress-wrap">'
        + '<div class="progress-track">'
        + '<div class="progress-fill" style="width:' + prog.pct + '%"></div>'
        + '<div class="progress-glow" style="left:' + prog.pct + '%"></div>'
        + '</div>'
        + '<div class="progress-meta">'
        + '<span class="progress-pct">' + prog.pct + '%</span>'
        + '<span class="progress-remain">' + esc(prog.remainStr) + '</span>'
        + '</div></div>';
    }

    var animClass = animate ? ' fade-in' : '';
    var animStyle = animate ? ' style="animation-delay:' + delay + 'ms"' : '';

    return (
      '<div class="tl-item' + (isNow ? ' is-now' : '') + (isLast ? ' is-last' : '') + animClass + '"'
        + ' data-start="' + esc(ev.start) + '" data-end="' + esc(ev.end) + '" data-allday="' + (ev.isAllDay ? '1' : '0') + '"'
        + animStyle + '>'
      + '<div class="tl-marker">' + (isNow ? '<span class="pulse"></span>' : '<span class="dot"></span>') + '</div>'
      + '<article class="card" style="--accent-bar:' + clr + '">'
      + '<div class="card-top">'
      + '<div class="card-time"><span class="t">' + esc(timeDisp) + '</span>'
        + (dur ? '<span class="dur">' + esc(dur) + '</span>' : '') + '</div>'
      + '<div class="card-meta-right">'
        + (isNow ? '<span class="badge live">' + esc(rel) + '</span>' : '')
        + (rel && !isNow ? '<span class="countdown">' + esc(rel) + '</span>' : '')
      + '</div></div>'
      + '<h3>' + esc(ev.title) + '</h3>'
      + '<div class="range">' + esc(rangeDisp) + '</div>'
      + progressHtml
      + (ev.location ? '<div class="loc">' + LOC_ICON + '<span>' + esc(ev.location) + '</span></div>' : '')
      + (ev.description ? '<details><summary>' + NOTES_ICON + ' Notes</summary><div class="notes">' + desc + '</div></details>' : '')
      + '</article></div>'
    );
  }

  function renderSection(heading, evts, idx, now, animate) {
    var isToday = heading === 'Today';
    var cards = '', i;
    for (i = 0; i < evts.length; i++) {
      cards += renderCard(evts[i], i === evts.length - 1, idx * 40, now, animate);
      idx++;
    }
    var dateSub = (!isToday && heading !== 'Tomorrow' && evts.length > 0)
      ? '<span class="day-date">' + esc(_fmtLongDate.format(new Date(evts[0].start))) + '</span>'
      : '';
    var tomorrowBlurb = '';
    var _sunIcon = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v4"/><path d="M4.93 4.93l2.83 2.83"/><path d="M19.07 4.93l-2.83 2.83"/><path d="M1 18h22"/><path d="M5 18a7 7 0 0 1 14 0"/></svg>';
    if (heading === 'Tomorrow' && evts.length > 0 && !evts[0].isAllDay) {
      tomorrowBlurb = '<div class="tomorrow-starts">' + _sunIcon + '<span>Your day starts at <strong>' + fmtTime(evts[0].start) + '</strong></span></div>';
    } else if (heading === 'Tomorrow' && evts.length > 0 && evts[0].isAllDay) {
      for (var ti = 0; ti < evts.length; ti++) {
        if (!evts[ti].isAllDay) {
          tomorrowBlurb = '<div class="tomorrow-starts">' + _sunIcon + '<span>Your day starts at <strong>' + fmtTime(evts[ti].start) + '</strong></span></div>';
          break;
        }
      }
    }
    return {
      html: '<section class="day-group">'
        + '<div class="day-head' + (isToday ? ' is-today' : '') + '">'
        + '<h2>' + esc(heading) + '</h2>' + dateSub
        + '<span class="cnt">' + evts.length + '</span></div>'
        + tomorrowBlurb
        + '<div class="timeline">' + cards + '</div></section>',
      idx: idx,
    };
  }

  // ── Full DOM update ─────────────────────────────────────────────────────────
  function renderAll(events, _now) {
    var now = _now || new Date();
    var animate = _isFirstRender;
    _isFirstRender = false;
    events = events.filter(function (e) { return new Date(e.end) >= now; });

    // Live clock — set innerHTML once with span structure
    var clockEl = document.getElementById('clock-time');
    var clockDate = document.getElementById('clock-date');
    var cp = clockParts(now);
    if (clockEl) clockEl.innerHTML = '<span class="clock-hr">' + cp.hr + '</span><span class="clock-sep">:</span><span class="clock-min">' + cp.min + '</span><span class="clock-period">' + cp.period + '</span>';
    if (clockDate) clockDate.textContent = _fmtClockDate.format(now);

    // Event-count chip
    var chips = document.querySelectorAll('.chip');
    if (chips.length >= 3) chips[2].textContent = events.length + ' event' + (events.length !== 1 ? 's' : '');

    // Group by section
    var grouped = {}, order = [];
    events.forEach(function (e) {
      var h = sectionTitle(e.start, now);
      if (!grouped[h]) { grouped[h] = []; order.push(h); }
      grouped[h].push(e);
    });

    // Render sections
    var html = '', idx = 0;
    var EMPTY_ICON = '<div class="empty-icon"><svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><path d="M8 14h.01"/><path d="M12 14h.01"/><path d="M16 14h.01"/><path d="M8 18h.01"/><path d="M12 18h.01"/></svg></div>';
    if (order.length === 0 && !_liveFetchDone) {
      html = '<section class="day-group fade-in"><div class="empty-state loading-state">'
        + EMPTY_ICON
        + '<h2>Loading&hellip;</h2><p>Checking your calendar</p>'
        + '</div></section>';
    } else if (order.length === 0) {
      html = '<section class="day-group' + (animate ? ' fade-in' : '') + '"><div class="empty-state">'
        + EMPTY_ICON
        + '<h2>All clear</h2><p>Nothing on the books for the next ' + WINDOW_HOURS + ' hours.</p>'
        + '</div></section>';
    } else {
      order.forEach(function (h) {
        var r = renderSection(h, grouped[h], idx, now, animate);
        html += r.html; idx = r.idx;
      });
    }
    var container = document.getElementById('agenda-events');
    if (container) container.innerHTML = html;

    // Hero next-up
    updateHero(events, now, animate);

    // TGIF banner — only on Friday when no events
    updateTGIF(events.length > 0);

    // Sunday Scaries banner — all day Sunday
    updateSundayScaries();
  }

  function updateHero(events, now, animate) {
    var heroWrap = document.getElementById('hero-next-wrap');
    if (!heroWrap) return;
    if (events.length > 0) {
      var next = events[0];
      var rel  = timeUntil(next.start, next.end, next.isAllDay, now);
      var isNow = rel === 'Now' || rel === 'In progress';
      var animClass = animate ? ' fade-in' : '';
      var animStyle = animate ? ' style="animation-delay:60ms"' : '';
      heroWrap.innerHTML = '<div class="hero-next' + animClass + '"' + animStyle + '>'
        + '<span class="hero-next-label">' + (isNow ? 'Live' : 'Next') + '</span>'
        + '<span class="hero-next-title">' + esc(next.title) + '</span>'
        + (isNow
          ? '<span class="hero-live">'  + esc(rel) + '</span>'
          : '<span class="hero-eta">'   + esc(rel) + '</span>')
        + '</div>';
    } else {
      heroWrap.innerHTML = '';
    }
  }

  // ── Tick — seamless in-place DOM updates (no innerHTML replacement) ─────────
  function tick() {
    if (!currentData) return;
    var now = new Date();

    // Update clock — textContent only, preserves blink animation on separator
    var cp = clockParts(now);
    var clockHr = document.querySelector('.clock-hr');
    var clockMin = document.querySelector('.clock-min');
    var clockPer = document.querySelector('.clock-period');
    var clockDateEl = document.getElementById('clock-date');
    if (clockHr)  clockHr.textContent  = cp.hr;
    if (clockMin) clockMin.textContent = cp.min;
    if (clockPer) clockPer.textContent = cp.period;
    if (clockDateEl) clockDateEl.textContent = _fmtClockDate.format(now);

    // Update event count chip
    var activeEvents = currentData.filter(function (e) { return new Date(e.end) >= now; });
    var chips = document.querySelectorAll('.chip');
    if (chips.length >= 3) chips[2].textContent = activeEvents.length + ' event' + (activeEvents.length !== 1 ? 's' : '');

    // Update each card in-place
    var items = document.querySelectorAll('.tl-item');
    var removedAny = false;
    for (var i = 0; i < items.length; i++) {
      var item = items[i];
      var start = item.getAttribute('data-start');
      var end = item.getAttribute('data-end');
      var allDay = item.getAttribute('data-allday') === '1';
      if (!start || !end) continue;

      var rel = timeUntil(start, end, allDay, now);
      var wasNow = item.classList.contains('is-now');
      var isNow = rel === 'Now' || rel === 'In progress';
      var hasEnded = new Date(end) < now;

      // Ended event — fade out and remove from DOM
      if (hasEnded) {
        item.style.transition = 'opacity .4s ease, max-height .4s ease';
        item.style.opacity = '0';
        item.style.maxHeight = '0';
        item.style.overflow = 'hidden';
        item.style.paddingBottom = '0';
        removedAny = true;
        (function(el) {
          setTimeout(function() { if (el.parentNode) el.parentNode.removeChild(el); }, 400);
        })(item);
        continue;
      }

      // State transition: not-now → now
      if (isNow && !wasNow) {
        item.classList.add('is-now');
        // Swap marker: dot → pulse
        var marker = item.querySelector('.tl-marker');
        if (marker) marker.innerHTML = '<span class="pulse"></span>';
        // Swap countdown → live badge
        var metaRight = item.querySelector('.card-meta-right');
        if (metaRight) metaRight.innerHTML = '<span class="badge live">' + esc(rel) + '</span>';
        // Add progress bar if not present
        var card = item.querySelector('.card');
        var prog = progressInfo(start, end, allDay, now);
        if (prog && card && !item.querySelector('.progress-wrap')) {
          var range = item.querySelector('.range');
          if (range) {
            var progDiv = document.createElement('div');
            progDiv.className = 'progress-wrap';
            progDiv.innerHTML = '<div class="progress-track">'
              + '<div class="progress-fill" style="width:' + prog.pct + '%"></div>'
              + '<div class="progress-glow" style="left:' + prog.pct + '%"></div>'
              + '</div><div class="progress-meta">'
              + '<span class="progress-pct">' + prog.pct + '%</span>'
              + '<span class="progress-remain">' + esc(prog.remainStr) + '</span>'
              + '</div>';
            range.parentNode.insertBefore(progDiv, range.nextSibling);
          }
        }
        continue;
      }

      // State transition: now → not-now (rare, but handle it)
      if (!isNow && wasNow) {
        item.classList.remove('is-now');
        var marker2 = item.querySelector('.tl-marker');
        if (marker2) marker2.innerHTML = '<span class="dot"></span>';
        var metaRight2 = item.querySelector('.card-meta-right');
        if (metaRight2 && rel) metaRight2.innerHTML = '<span class="countdown">' + esc(rel) + '</span>';
        else if (metaRight2) metaRight2.innerHTML = '';
        // Remove progress bar
        var progWrap = item.querySelector('.progress-wrap');
        if (progWrap) progWrap.parentNode.removeChild(progWrap);
        continue;
      }

      // Steady state — update countdown/badge text
      var countdown = item.querySelector('.countdown');
      if (countdown) countdown.textContent = rel;

      var badge = item.querySelector('.badge.live');
      if (badge) badge.textContent = rel;

      // Keep displayed start time & range in sync with the clock formatter
      if (!allDay) {
        var tEl = item.querySelector('.t');
        if (tEl) { var ft = fmtTime(start); if (tEl.textContent !== ft) tEl.textContent = ft; }
        var rangeEl = item.querySelector('.range');
        if (rangeEl) { var fr = fmtTimeRange(start, end); if (rangeEl.textContent !== fr) rangeEl.textContent = fr; }
      }

      // Update progress bar
      var prog2 = progressInfo(start, end, allDay, now);
      if (prog2) {
        var fill = item.querySelector('.progress-fill');
        var glow = item.querySelector('.progress-glow');
        var pct = item.querySelector('.progress-pct');
        var remain = item.querySelector('.progress-remain');
        if (fill) fill.style.width = prog2.pct + '%';
        if (glow) glow.style.left = prog2.pct + '%';
        if (pct) pct.textContent = prog2.pct + '%';
        if (remain) remain.textContent = prog2.remainStr;
      }
    }

    // Update section counts and remove empty sections after card removal
    if (removedAny) {
      setTimeout(function() {
        var sections = document.querySelectorAll('.day-group');
        for (var s = 0; s < sections.length; s++) {
          var cards = sections[s].querySelectorAll('.tl-item');
          if (cards.length === 0) {
            sections[s].style.transition = 'opacity .3s ease';
            sections[s].style.opacity = '0';
            (function(el) {
              setTimeout(function() { if (el.parentNode) el.parentNode.removeChild(el); }, 300);
            })(sections[s]);
          } else {
            var cnt = sections[s].querySelector('.cnt');
            if (cnt) cnt.textContent = cards.length;
          }
        }
        // Check if all events gone
        var remaining = document.querySelectorAll('.tl-item');
        if (remaining.length === 0) {
          renderAll(currentData, new Date());
        }
      }, 450);
    }

    // Update hero next-up in-place
    if (activeEvents.length > 0) {
      var next = activeEvents[0];
      var heroRel = timeUntil(next.start, next.end, next.isAllDay, now);
      var heroIsNow = heroRel === 'Now' || heroRel === 'In progress';
      var heroEta = document.querySelector('.hero-eta');
      var heroLive = document.querySelector('.hero-live');
      var heroLabel = document.querySelector('.hero-next-label');
      var heroTitle = document.querySelector('.hero-next-title');

      // Update the countdown/live text
      if (heroEta) heroEta.textContent = heroRel;
      if (heroLive) heroLive.textContent = heroRel;

      // Handle hero state transitions (next→live or live→next)
      if (heroIsNow && heroEta) {
        // Was showing eta, now should show live — swap in-place
        updateHero(activeEvents, now, false);
      } else if (!heroIsNow && heroLive) {
        // Was showing live, now should show eta — swap in-place
        updateHero(activeEvents, now, false);
      }

      // Update title if the next event changed
      if (heroTitle && heroTitle.textContent !== next.title) {
        updateHero(activeEvents, now, false);
      }

      // Update label
      if (heroLabel) heroLabel.textContent = heroIsNow ? 'Live' : 'Next';
    } else {
      var heroWrap = document.getElementById('hero-next-wrap');
      if (heroWrap && heroWrap.innerHTML !== '') heroWrap.innerHTML = '';
    }
  }

  // ── Data fetching & polling ─────────────────────────────────────────────────
  function sig(events) {
    var s = '';
    for (var i = 0; i < events.length; i++) {
      s += events[i].title + '|' + events[i].start + '|' + events[i].end + '\n';
    }
    return s;
  }

  function fetchAndUpdate() {
    fetch('/api/events?_=' + Date.now())
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        var ct = r.headers.get('content-type') || '';
        if (ct.indexOf('application/json') < 0) throw new Error('Not JSON: ' + ct);
        return r.json();
      })
      .then(function (data) {
        var events = data.events || [];
        var needsRender = !_liveFetchDone;
        _liveFetchDone = true;
        var s = sig(events);
        currentData = events;
        if (s !== currentSig || needsRender) { currentSig = s; renderAll(events); }
      })
      .catch(function (err) {
        if (!_liveFetchDone) {
          _liveFetchDone = true;
          renderAll(currentData);
        }
        console.warn('[agenda] fetch failed:', err.message || err);
      });
  }

  // ── Bootstrap from build-time JSON if available ─────────────────────────────
  function initFromBuildData() {
    if (window.__AGENDA_EVENTS__) {
      currentData = window.__AGENDA_EVENTS__;
      currentSig = sig(currentData);
      renderAll(currentData);
    }
  }

  // ── Init ────────────────────────────────────────────────────────────────────
  initFromBuildData();
  fetchAndUpdate();
  setInterval(fetchAndUpdate, POLL_MS);
  setInterval(tick, TICK_MS);

  // Fetch immediately when tab becomes visible (user switches back)
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) fetchAndUpdate();
  });

  // ── TGIF Friday Banner (only when empty state + Friday) ─────────────────────
  var _tgifInitialized = false;

  function updateTGIF(hasEvents) {
    var fmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday: 'long' });
    var dayName = fmt.format(new Date());
    var isFriday = dayName === 'Friday';
    var showBanner = isFriday && !hasEvents;

    var banner = document.getElementById('tgif-banner');
    if (!banner) return;

    if (!showBanner) {
      banner.style.display = 'none';
      banner.setAttribute('aria-hidden', 'true');
      return;
    }

    // Initialize letter animation + confetti once
    if (!_tgifInitialized) {
      var textEl = document.getElementById('tgif-text');
      var confettiEl = document.getElementById('tgif-confetti');
      if (textEl) {
        var phrase = 'THANK GOD IT\u2019S FRIDAY!';
        var html = '';
        for (var i = 0; i < phrase.length; i++) {
          if (phrase[i] === ' ') {
            html += '<span class="tgif-space"></span>';
          } else {
            var delay = (i * 0.06);
            html += '<span class="tgif-letter" style="animation-delay:' + delay + 's,' + (delay + 0.3) + 's">' + phrase[i] + '</span>';
          }
        }
        textEl.innerHTML = html;
      }
      if (confettiEl) {
        var colors = ['#ff9f0a','#ff375f','#af52de','#5e5ce6','#007aff','#30d158','#ffd60a','#ff6482','#64d2ff','#bf5af2'];
        var shapes = ['circle','square','rect','star'];
        for (var c = 0; c < 50; c++) {
          var p = document.createElement('span');
          var shape = shapes[Math.floor(Math.random() * shapes.length)];
          p.className = 'tgif-particle tgif-p-' + shape;
          var left = Math.random() * 100;
          var dur = 3 + Math.random() * 5;
          var del = Math.random() * 6;
          var size = 3 + Math.random() * 5;
          var color = colors[Math.floor(Math.random() * colors.length)];
          var drift = -30 + Math.random() * 60;
          p.style.cssText = 'left:' + left + '%;width:' + size + 'px;height:'
            + (shape === 'rect' ? size * 2.5 : size) + 'px;background:' + color
            + ';animation-duration:' + dur + 's;animation-delay:' + del
            + 's;--drift:' + drift + 'px;opacity:0';
          confettiEl.appendChild(p);
        }
      }
      _tgifInitialized = true;
    }

    banner.style.display = 'flex';
    banner.setAttribute('aria-hidden', 'false');
  }

  // ── Sunday Scaries Banner (all day Sunday) ──────────────────────────────────
  var _sundayInitialized = false;

  function updateSundayScaries() {
    var fmt = new Intl.DateTimeFormat('en-US', { timeZone: TZ, weekday: 'long' });
    var dayName = fmt.format(new Date());
    var isSunday = dayName === 'Sunday';

    var banner = document.getElementById('sunday-banner');
    if (!banner) return;

    if (!isSunday) {
      banner.style.display = 'none';
      banner.setAttribute('aria-hidden', 'true');
      return;
    }

    if (!_sundayInitialized) {
      var textEl = document.getElementById('sunday-text');
      var wispEl = document.getElementById('sunday-wisps');
      if (textEl) {
        var phrase = 'SUNDAY SCARIES';
        var html = '';
        for (var i = 0; i < phrase.length; i++) {
          if (phrase[i] === ' ') {
            html += '<span class="ss-space"></span>';
          } else {
            var delay = (i * 0.08);
            html += '<span class="ss-letter" style="animation-delay:' + delay + 's,' + (delay + 0.4) + 's">' + phrase[i] + '</span>';
          }
        }
        textEl.innerHTML = html;
      }
      if (wispEl) {
        var colors = ['rgba(139,92,246,0.5)','rgba(88,80,236,0.4)','rgba(109,40,217,0.35)','rgba(167,139,250,0.3)','rgba(79,70,229,0.4)','rgba(147,197,253,0.25)'];
        for (var w = 0; w < 30; w++) {
          var wisp = document.createElement('span');
          wisp.className = 'ss-wisp';
          var left = Math.random() * 100;
          var dur = 4 + Math.random() * 6;
          var del = Math.random() * 10;
          var size = 2 + Math.random() * 4;
          var color = colors[Math.floor(Math.random() * colors.length)];
          var drift = -20 + Math.random() * 40;
          wisp.style.cssText = 'left:' + left + '%;width:' + size + 'px;height:'
            + size + 'px;background:' + color
            + ';animation-duration:' + dur + 's;animation-delay:' + del
            + 's;--drift:' + drift + 'px;opacity:0';
          wispEl.appendChild(wisp);
        }
      }
      _sundayInitialized = true;
    }

    banner.style.display = 'flex';
    banner.setAttribute('aria-hidden', 'false');

    // Cycle through funny Sunday dread quotes
    var bubble = document.getElementById('ss-bubble');
    if (bubble && !bubble.classList.contains('is-visible')) {
      var quotes = [
        'I can feel Monday coming\u2026',
        'Is it too late to call in sick tomorrow?',
        'My weekend had a weekend and it\u2019s over',
        'Back to the work grind!',
        'Sunday: the day anxiety picks its outfit for Monday',
        'POV: you remembered tomorrow is Monday',
        'The Sunday Scaries are undefeated',
        'Alexa, cancel Monday',
        'Weekend status: critically low',
        'The dread is real\u2026',
      ];
      var pick = quotes[Math.floor(Math.random() * quotes.length)];
      bubble.textContent = pick;
      setTimeout(function () { bubble.classList.add('is-visible'); }, 1200);

      // Swap quote every 8s with fade
      setInterval(function () {
        bubble.classList.remove('is-visible');
        setTimeout(function () {
          pick = quotes[Math.floor(Math.random() * quotes.length)];
          bubble.textContent = pick;
          bubble.classList.add('is-visible');
        }, 600);
      }, 8000);
    }

    // Nervous eye-shift on the ghost
    var eyes = document.querySelectorAll('.ss-ghost .ss-eye');
    if (eyes.length) {
      var dirs = [
        { x: -1.5, y: 0 }, { x: 1.5, y: 0 },
        { x: 0, y: -1 }, { x: -1, y: 1 },
        { x: 1.5, y: -0.5 }, { x: 0, y: 0 },
      ];
      var ei = 0;
      setInterval(function () {
        ei = (ei + 1) % dirs.length;
        var d = dirs[ei];
        for (var k = 0; k < eyes.length; k++) {
          eyes[k].style.transition = 'transform 0.3s ease';
          eyes[k].style.transform = 'translate(' + d.x + 'px,' + d.y + 'px)';
        }
      }, 1800);
    }
  }
})();
"""


@dataclass(slots=True)
class Event:
    title: str
    start: datetime
    end: datetime
    location: str
    description: str
    is_all_day: bool


def env(name: str, default: str | None = None, required: bool = False) -> str:
    value = os.getenv(name, default)
    if required and not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value or ""


ICS_URL = env("ICS_URL", required=True)
TIMEZONE = env("AGENDA_TIMEZONE", "America/Los_Angeles")
TITLE = env("AGENDA_TITLE", "Live Agenda")
WINDOW_HOURS = int(env("WINDOW_HOURS", "48"))
MAX_EVENTS = int(env("MAX_EVENTS", "40"))
SITE_DIR = Path(env("SITE_DIR", "site"))
SITE_DIR.mkdir(parents=True, exist_ok=True)

_WIN = sys.platform == "win32"


def fetch_ics(url: str, retries: int = 3) -> bytes:
    import time as _time

    req = Request(
        url,
        headers={
            "User-Agent": "github-actions-live-agenda/1.0",
            "Accept": "text/calendar, text/plain, */*",
        },
        method="GET",
    )
    for attempt in range(retries):
        try:
            with urlopen(req, timeout=30) as resp:
                return resp.read()
        except (HTTPError, URLError) as exc:
            code = getattr(exc, "code", None)
            reason = getattr(exc, "reason", exc)
            if attempt < retries - 1:
                wait = 2 ** attempt * 3  # 3s, 6s, 12s
                print(f"ICS fetch attempt {attempt + 1} failed ({code or reason}), retrying in {wait}s...")
                _time.sleep(wait)
            else:
                label = f"HTTP {code}" if code else str(reason)
                raise SystemExit(f"Failed to fetch ICS feed after {retries} attempts: {label}") from exc
    return b""  # unreachable


def ensure_dt(value: date | datetime, tz: ZoneInfo) -> tuple[datetime, bool]:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=tz)
        return value.astimezone(tz), False
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=tz), True
    raise TypeError(f"Unsupported date type: {type(value)!r}")


def parse_events(raw: bytes, tz: ZoneInfo) -> list[Event]:
    cal = Calendar.from_ical(raw)
    now = datetime.now(tz)
    window_end = now + timedelta(hours=WINDOW_HOURS)
    events: list[Event] = []

    # recurring_ical_events expands RRULEs, handles EXDATE/RECURRENCE-ID
    for component in recurring_ical_events.of(cal).between(now, window_end):
        status = str(component.get("STATUS", "")).upper()
        if status == "CANCELLED":
            continue

        raw_start = component.get("DTSTART")
        if raw_start is None:
            continue

        start, is_all_day = ensure_dt(raw_start.dt, tz)
        raw_end = component.get("DTEND")
        if raw_end is None:
            end = start + (timedelta(days=1) if is_all_day else timedelta(hours=1))
        else:
            end, _ = ensure_dt(raw_end.dt, tz)

        title = str(component.get("SUMMARY", "Untitled"))

        # Hide the morning "Away from Desk" block (keep the evening one)
        if title.lower() == "away from desk" and start.hour < 12:
            continue

        events.append(
            Event(
                title=title,
                start=start,
                end=end,
                location=str(component.get("LOCATION", "")),
                description=str(component.get("DESCRIPTION", "")),
                is_all_day=is_all_day,
            )
        )

    events.sort(key=lambda e: (e.start, e.end, e.title.lower()))
    return events[:MAX_EVENTS]


def _fmt(dt: datetime, fmt_posix: str, fmt_win: str) -> str:
    return dt.strftime(fmt_win if _WIN else fmt_posix)


def _fmt_clock(dt: datetime) -> str:
    """Format a time identically to the JS clockParts() function.

    Produces e.g. ``2:30 PM`` — no leading zero on the hour, two-digit
    minutes, one plain space, then uppercase AM/PM.  This guarantees the
    server-side initial render matches the client-side Intl.DateTimeFormat
    output exactly so there is no flash of reformatted text on hydration.
    """
    hour = dt.hour % 12 or 12
    minute = f"{dt.minute:02d}"
    period = "AM" if dt.hour < 12 else "PM"
    return f"{hour}:{minute} {period}"


def section_title(reference: datetime, event: Event) -> str:
    if event.start.date() == reference.date():
        return "Today"
    if event.start.date() == (reference + timedelta(days=1)).date():
        return "Tomorrow"
    return _fmt(event.start, "%A, %b %-d", "%A, %b %#d")


def format_time(event: Event) -> str:
    if event.is_all_day:
        return "All day"
    return f"{_fmt_clock(event.start)} – {_fmt_clock(event.end)}"


def format_time_short(event: Event) -> str:
    if event.is_all_day:
        return "All day"
    return _fmt_clock(event.start)



def time_until(event: Event, now: datetime) -> str:
    """Return a human-friendly relative time like 'in 25 min' or 'Now'."""
    diff = event.start - now
    total_min = int(diff.total_seconds() // 60)
    if event.is_all_day:
        return ""
    if total_min < -5:
        return "In progress"
    if total_min <= 0:
        return "Now"
    if total_min < 60:
        return f"in {total_min} min"
    hours = total_min // 60
    mins = total_min % 60
    if mins == 0:
        return f"in {hours}h"
    return f"in {hours}h {mins}m"


def duration_str(event: Event) -> str:
    if event.is_all_day:
        return ""
    diff = event.end - event.start
    total_min = int(diff.total_seconds() // 60)
    if total_min < 60:
        return f"{total_min}m"
    hours = total_min // 60
    mins = total_min % 60
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def _esc(text: str) -> str:
    return html.escape(text)


def _event_accent(event: Event) -> str:
    """Pick a left-border accent color based on time of day."""
    if event.is_all_day:
        return "#af52de"  # purple for all-day
    h = event.start.hour
    if h < 12:
        return "#ff9f0a"  # amber morning
    if h < 17:
        return "#007aff"  # blue afternoon
    return "#5e5ce6"  # indigo evening


def _build_events_json(events: list[Event]) -> str:
    """Serialize events to JSON for client-side bootstrap."""
    return json.dumps(
        [
            {
                "title": e.title,
                "start": e.start.isoformat(),
                "end": e.end.isoformat(),
                "location": e.location,
                "description": e.description,
                "isAllDay": e.is_all_day,
            }
            for e in events
        ],
        separators=(",", ":"),
    )


def render(events: Iterable[Event], tz: ZoneInfo) -> str:
    now = datetime.now(tz)
    event_list = list(events)
    grouped: dict[str, list[Event]] = {}
    for event in event_list:
        grouped.setdefault(section_title(now, event), []).append(event)

    global_idx = 0
    sections: list[str] = []
    if not grouped:
        sections.append(
            '<section class="day-group fade-in">'
            '<div class="empty-state loading-state">'
            '<div class="empty-icon">'
            '<svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round" stroke-linejoin="round"><rect x="3" y="4" width="18" height="18" rx="2" ry="2"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/><path d="M8 14h.01"/><path d="M12 14h.01"/><path d="M16 14h.01"/><path d="M8 18h.01"/><path d="M12 18h.01"/></svg>'
            "</div>"
            "<h2>Loading&hellip;</h2>"
            "<p>Checking your calendar</p>"
            "</div></section>"
        )
    else:
        for heading, group in grouped.items():
            is_today = heading == "Today"
            cards: list[str] = []
            for i, event in enumerate(group):
                rel = time_until(event, now)
                dur = duration_str(event)
                is_now = rel in ("Now", "In progress")
                is_last = i == len(group) - 1
                accent = _event_accent(event)

                now_class = " is-now" if is_now else ""
                last_class = " is-last" if is_last else ""

                indicator = '<span class="pulse"></span>' if is_now else '<span class="dot"></span>'

                badge = f'<span class="badge live">{_esc(rel)}</span>' if is_now else ""
                rel_html = f'<span class="countdown">{_esc(rel)}</span>' if rel and not is_now else ""
                dur_html = f'<span class="dur">{_esc(dur)}</span>' if dur else ""

                loc_icon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0z"/><circle cx="12" cy="10" r="3"/></svg>'
                location = (
                    f'<div class="loc">{loc_icon}<span>{_esc(event.location)}</span></div>'
                    if event.location
                    else ""
                )
                description = _esc(event.description).replace("\n", "<br>")
                notes_icon = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>'
                details = (
                    f'<details><summary>{notes_icon} Notes</summary><div class="notes">{description}</div></details>'
                    if event.description
                    else ""
                )

                # Progress bar for in-progress events
                progress_html = ""
                if is_now and not event.is_all_day:
                    total = (event.end - event.start).total_seconds()
                    elapsed = (now - event.start).total_seconds()
                    pct = max(0, min(100, int(elapsed / total * 100))) if total > 0 else 0
                    remain_min = max(0, int((event.end - now).total_seconds() // 60) + 1)
                    if remain_min < 60:
                        remain_str = f"{remain_min}m left"
                    else:
                        rh, rm = divmod(remain_min, 60)
                        remain_str = f"{rh}h {rm}m left" if rm else f"{rh}h left"
                    progress_html = (
                        f'<div class="progress-wrap">'
                        f'<div class="progress-track">'
                        f'<div class="progress-fill" style="width:{pct}%"></div>'
                        f'<div class="progress-glow" style="left:{pct}%"></div>'
                        f'</div>'
                        f'<div class="progress-meta">'
                        f'<span class="progress-pct">{pct}%</span>'
                        f'<span class="progress-remain">{_esc(remain_str)}</span>'
                        f'</div></div>'
                    )

                delay = f' style="animation-delay:{global_idx * 40}ms"'
                global_idx += 1

                cards.append(
                    f'<div class="tl-item{now_class}{last_class} fade-in"{delay}>'
                    f'<div class="tl-marker">{indicator}</div>'
                    f'<article class="card" style="--accent-bar:{accent}">'
                    f'<div class="card-top">'
                    f'<div class="card-time">'
                    f'<span class="t">{_esc(format_time_short(event))}</span>'
                    f"{dur_html}"
                    f"</div>"
                    f'<div class="card-meta-right">{badge}{rel_html}</div>'
                    f"</div>"
                    f"<h3>{_esc(event.title)}</h3>"
                    f'<div class="range">{_esc(format_time(event))}</div>'
                    f"{progress_html}"
                    f"{location}"
                    f"{details}"
                    f"</article>"
                    f"</div>"
                )

            count = len(group)
            date_sub = ""
            if not is_today and heading != "Tomorrow" and count > 0:
                date_sub = f'<span class="day-date">{_fmt(group[0].start, "%B %-d, %Y", "%B %#d, %Y")}</span>'

            tomorrow_blurb = ""
            if heading == "Tomorrow" and count > 0:
                first_timed = next((e for e in group if not e.is_all_day), None)
                if first_timed:
                    _sun_svg = (
                        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"'
                        ' stroke-linecap="round" stroke-linejoin="round">'
                        '<path d="M12 2v4"/><path d="M4.93 4.93l2.83 2.83"/>'
                        '<path d="M19.07 4.93l-2.83 2.83"/><path d="M1 18h22"/>'
                        '<path d="M5 18a7 7 0 0 1 14 0"/></svg>'
                    )
                    tomorrow_blurb = (
                        f'<div class="tomorrow-starts">'
                        f"{_sun_svg}"
                        f"<span>Your day starts at <strong>{_fmt_clock(first_timed.start)}</strong></span>"
                        f"</div>"
                    )

            sections.append(
                f'<section class="day-group">'
                f'<div class="day-head{"" if not is_today else " is-today"}">'
                f"<h2>{_esc(heading)}</h2>"
                f"{date_sub}"
                f'<span class="cnt">{count}</span>'
                f"</div>"
                f"{tomorrow_blurb}"
                f'<div class="timeline">{"".join(cards)}</div>'
                f"</section>"
            )

    total_events = len(event_list)

    # Server-side clock (visible immediately, JS takes over on hydration)
    # Uses the same logic as _fmt_clock / JS clockParts() for exact match.
    _clock_hr = str(now.hour % 12 or 12)
    _clock_min = f"{now.minute:02d}"
    _clock_period = "AM" if now.hour < 12 else "PM"
    _clock_date = now.strftime("%A, %b %-d") if not _WIN else now.strftime("%A, %b %#d")
    _clock_html = (
        f'<span class="clock-hr">{_clock_hr}</span>'
        f'<span class="clock-sep">:</span>'
        f'<span class="clock-min">{_clock_min}</span>'
        f'<span class="clock-period">{_clock_period}</span>'
    )

    next_event = event_list[0] if event_list else None
    hero_next = ""
    if next_event:
        rel = time_until(next_event, now)
        is_now = rel in ("Now", "In progress")
        rel_display = (
            f'<span class="hero-live">{_esc(rel)}</span>'
            if is_now
            else f'<span class="hero-eta">{_esc(rel)}</span>'
        )
        hero_next = (
            f'<div class="hero-next fade-in" style="animation-delay:60ms">'
            f'<span class="hero-next-label">{"Live" if is_now else "Next"}</span>'
            f'<span class="hero-next-title">{_esc(next_event.title)}</span>'
            f"{rel_display}"
            f"</div>"
        )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="theme-color" content="#000000" id="tc">
  <link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📅</text></svg>">
  <title>{_esc(TITLE)}</title>
  <script>
  (function(){{var s=localStorage.getItem('agenda-theme');if(s==='light'){{document.documentElement.setAttribute('data-theme','light');document.getElementById('tc').content='#f2f2f7';}}}})();
  </script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link rel="preload" href="https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap" as="style">
  <link href="https://fonts.googleapis.com/css2?family=Inter:ital,opsz,wght@0,14..32,100..900;1,14..32,100..900&display=swap" rel="stylesheet">
  <style>
    /* ── Reset ── */
    *,*::before,*::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    /* ── Tokens — dark default ── */
    :root {{
      color-scheme: dark;
      --bg:        #000000;
      --surface:   rgba(28,28,30,.92);
      --surface-2: rgba(44,44,46,.6);
      --text:      #f5f5f7;
      --text-2:    #98989d;
      --text-3:    #636366;
      --accent:    #0a84ff;
      --accent-bg: rgba(10,132,255,.14);
      --live:      #30d158;
      --live-bg:   rgba(48,209,88,.1);
      --live-text: #32d74b;
      --border:    rgba(255,255,255,.04);
      --border-2:  rgba(255,255,255,.07);
      --tl-line:   rgba(255,255,255,.06);
      --card-shadow: 0 1px 2px rgba(0,0,0,.4), 0 4px 16px rgba(0,0,0,.2);
      --card-hover: 0 2px 6px rgba(0,0,0,.5), 0 12px 28px rgba(0,0,0,.3);
      --r:  16px;
      --r2: 22px;
    }}

    /* ── Light override ── */
    [data-theme="light"] {{
      color-scheme: light;
      --bg:        #f2f2f7;
      --surface:   #ffffff;
      --surface-2: #f8f8fa;
      --text:      #1c1c1e;
      --text-2:    #8e8e93;
      --text-3:    #aeaeb2;
      --accent:    #007aff;
      --accent-bg: rgba(0,122,255,.07);
      --live:      #34c759;
      --live-bg:   rgba(52,199,89,.08);
      --live-text: #248a3d;
      --border:    rgba(0,0,0,.04);
      --border-2:  rgba(0,0,0,.07);
      --tl-line:   rgba(0,0,0,.06);
      --card-shadow: 0 1px 2px rgba(0,0,0,.03), 0 4px 16px rgba(0,0,0,.04);
      --card-hover: 0 2px 6px rgba(0,0,0,.05), 0 12px 28px rgba(0,0,0,.07);
    }}

    /* ── Animations ── */
    @keyframes fade-up {{
      from {{ opacity: 0; transform: translateY(12px); }}
      to   {{ opacity: 1; transform: translateY(0); }}
    }}
    .fade-in {{
      animation: fade-up .45s cubic-bezier(.22,1,.36,1) both;
    }}
    @keyframes pulse-glow {{
      0%,100% {{ box-shadow: 0 0 0 0 rgba(52,199,89,.35); }}
      50%     {{ box-shadow: 0 0 0 7px rgba(52,199,89,0); }}
    }}
    @keyframes loading-breathe {{
      0%,100% {{ opacity: .4; transform: scale(1); }}
      50%     {{ opacity: .8; transform: scale(1.08); }}
    }}
    .loading-state .empty-icon {{
      animation: loading-breathe 2s ease-in-out infinite;
      opacity: 1;
    }}
    .loading-state h2 {{
      background: linear-gradient(90deg, var(--text-2), var(--accent), var(--text-2));
      background-size: 200% 100%;
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      animation: loading-shimmer 2s ease-in-out infinite;
    }}
    @keyframes loading-shimmer {{
      0%   {{ background-position: 100% 50%; }}
      100% {{ background-position: -100% 50%; }}
    }}

    /* ── Base ── */
    html {{
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
      scroll-behavior: smooth;
    }}
    body {{
      font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'SF Pro Display', 'Segoe UI', Roboto, sans-serif;
      background: var(--bg);
      color: var(--text);
      line-height: 1.5;
      min-height: 100vh;
      font-feature-settings: 'cv01', 'cv02', 'cv03', 'cv04';
    }}

    .wrap {{
      max-width: 680px;
      margin: 0 auto;
      padding: 72px 32px 100px;
      padding-left: max(32px, env(safe-area-inset-left, 0px));
      padding-right: max(32px, env(safe-area-inset-right, 0px));
      padding-bottom: max(100px, calc(80px + env(safe-area-inset-bottom, 0px)));
    }}

    /* ── Header ── */
    .hero {{
      margin-bottom: 52px;
    }}
    .hero h1 {{
      font-size: clamp(2rem, 5vw, 2.8rem);
      font-weight: 800;
      letter-spacing: -0.05em;
      line-height: 1.05;
      color: var(--text);
      overflow-wrap: break-word;
    }}
    @keyframes clock-separator-blink {{
      0%, 100% {{ opacity: 1; }}
      50% {{ opacity: 0.15; }}
    }}
    .clock {{
      display: flex;
      align-items: baseline;
      gap: 0;
      margin-top: 12px;
    }}
    .clock-time {{
      font-size: 1.1rem;
      font-weight: 650;
      letter-spacing: -0.02em;
      line-height: 1;
      color: var(--text);
      font-weight: 800;
      font-variant-numeric: tabular-nums;
      font-feature-settings: "tnum";
      display: flex;
      align-items: baseline;
      gap: 0;
      white-space: nowrap;
    }}
    .clock-hr, .clock-min {{
      display: inline-block;
      min-width: 0;
    }}
    .clock-sep {{
      animation: clock-separator-blink 1s ease-in-out infinite;
      display: inline-block;
      margin: 0 0.5px;
    }}
    .clock-period {{
      font-size: 0.52rem;
      font-weight: 800;
      letter-spacing: 0.04em;
      color: var(--text);
      margin-left: 3px;
      text-transform: uppercase;
    }}
    .clock-dot {{
      color: var(--text);
      margin: 0 8px;
      font-size: 0.7rem;
      font-weight: 700;
      line-height: 1;
      opacity: .4;
    }}
    .clock-date {{
      font-size: 0.78rem;
      font-weight: 800;
      color: var(--text);
      letter-spacing: 0.01em;
    }}

    .hero-chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 22px;
    }}
    .chip {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      background: var(--accent-bg);
      color: var(--accent);
      padding: 5px 13px;
      border-radius: 999px;
      font-size: 0.68rem;
      font-weight: 560;
      letter-spacing: 0.02em;
      border: 1px solid rgba(10,132,255,.08);
    }}

    /* ── Hero next-up ── */
    .hero-next {{
      display: flex;
      align-items: center;
      gap: 14px;
      margin-top: 20px;
      padding: 13px 18px;
      background: var(--surface);
      border: 1px solid var(--border-2);
      border-radius: 14px;
      box-shadow: var(--card-shadow);
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
    }}
    .hero-next-label {{
      font-size: 0.58rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.1em;
      color: var(--accent);
      background: var(--accent-bg);
      padding: 4px 10px;
      border-radius: 8px;
      flex-shrink: 0;
    }}
    .hero-next-title {{
      flex: 1;
      font-weight: 620;
      font-size: 0.88rem;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }}
    .hero-eta {{
      color: var(--text-2);
      font-size: 0.75rem;
      font-weight: 500;
      flex-shrink: 0;
      font-variant-numeric: tabular-nums;
    }}
    .hero-live {{
      color: var(--live-text);
      font-size: 0.62rem;
      font-weight: 720;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      flex-shrink: 0;
      padding: 4px 12px;
      background: var(--live-bg);
      border-radius: 999px;
    }}

    /* ── Day groups ── */
    .day-group {{
      margin-bottom: 40px;
      content-visibility: auto;
      contain-intrinsic-size: auto 400px;
    }}
    .day-group:last-of-type {{
      margin-bottom: 0;
    }}
    .day-head {{
      display: flex;
      align-items: center;
      gap: 10px;
      margin-bottom: 14px;
      padding-left: 2px;
    }}
    .day-head h2 {{
      font-size: 0.66rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--text-2);
    }}
    .day-head.is-today h2 {{
      color: var(--accent);
    }}
    .day-date {{
      font-size: 0.66rem;
      color: var(--text-3);
      font-weight: 420;
    }}
    .cnt {{
      margin-left: auto;
      font-size: 0.6rem;
      font-weight: 620;
      color: var(--text-3);
      background: var(--surface-2);
      padding: 3px 10px;
      border-radius: 999px;
      border: 1px solid var(--border);
    }}
    .tomorrow-starts {{
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 0.74rem;
      font-weight: 450;
      color: var(--text-2);
      padding: 10px 16px;
      margin-bottom: 16px;
      margin-left: 2px;
      background: linear-gradient(135deg, var(--surface) 0%, var(--surface-2) 100%);
      border: 1px solid var(--border-2);
      border-left: 3px solid var(--accent);
      border-radius: 10px;
      letter-spacing: 0.01em;
      line-height: 1.4;
    }}
    .tomorrow-starts svg {{
      flex-shrink: 0;
      width: 18px;
      height: 18px;
      color: var(--accent);
      opacity: 0.8;
    }}
    .tomorrow-starts strong {{
      font-weight: 700;
      color: var(--text);
    }}

    /* ── Timeline ── */
    .timeline {{
      position: relative;
      padding-left: 28px;
    }}

    .tl-item {{
      position: relative;
      padding-bottom: 14px;
    }}
    .tl-item:last-child {{
      padding-bottom: 0;
    }}

    /* Vertical connector */
    .tl-item::before {{
      content: '';
      position: absolute;
      left: -20px;
      top: 16px;
      bottom: 0;
      width: 1.5px;
      background: var(--tl-line);
      border-radius: 1px;
    }}
    .tl-item.is-last::before {{
      display: none;
    }}

    /* Marker */
    .tl-marker {{
      position: absolute;
      left: -28px;
      top: 8px;
      width: 14px;
      height: 14px;
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 1;
    }}
    .dot {{
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--border-2);
      border: 2px solid var(--bg);
      box-shadow: 0 0 0 1.5px var(--tl-line);
      transition: transform .2s ease;
    }}
    .tl-item:hover .dot {{
      transform: scale(1.3);
    }}
    .pulse {{
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--live);
      animation: pulse-glow 2.5s cubic-bezier(.4,0,.6,1) infinite;
    }}

    /* ── Cards ── */
    .card {{
      position: relative;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--r);
      padding: 16px 20px 16px 20px;
      box-shadow: var(--card-shadow);
      transition: box-shadow .25s cubic-bezier(.22,1,.36,1), border-color .25s ease, transform .25s cubic-bezier(.22,1,.36,1);
      overflow: hidden;
      backdrop-filter: blur(12px);
      -webkit-backdrop-filter: blur(12px);
      contain: layout style paint;
    }}
    /* Left accent bar */
    .card::before {{
      content: '';
      position: absolute;
      left: 0;
      top: 16px;
      bottom: 16px;
      width: 3px;
      border-radius: 0 3px 3px 0;
      background: var(--accent-bar, var(--accent));
      opacity: .5;
      transition: opacity .2s ease;
    }}
    .card:hover {{
      box-shadow: var(--card-hover);
      border-color: var(--border-2);
      transform: translateY(-1px);
    }}
    .card:hover::before {{
      opacity: 1;
    }}
    .tl-item.is-now .card {{
      background: var(--live-bg);
      border-color: rgba(52,199,89,.18);
    }}
    .tl-item.is-now .card::before {{
      background: var(--live);
      opacity: 1;
    }}

    .card-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      margin-bottom: 6px;
    }}
    .card-time {{
      display: flex;
      align-items: baseline;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .t {{
      font-size: 0.73rem;
      font-weight: 600;
      color: var(--text-2);
      letter-spacing: 0.01em;
      font-variant-numeric: tabular-nums;
    }}
    .dur {{
      font-size: 0.62rem;
      color: var(--text-3);
      font-weight: 480;
      padding: 2px 8px;
      background: var(--surface-2);
      border-radius: 6px;
    }}
    .card-meta-right {{
      display: flex;
      align-items: center;
      gap: 6px;
    }}

    .card h3 {{
      font-size: 0.96rem;
      font-weight: 650;
      line-height: 1.35;
      letter-spacing: -0.015em;
      margin-bottom: 3px;
    }}
    .range {{
      font-size: 0.7rem;
      color: var(--text-3);
      margin-bottom: 0;
      font-weight: 420;
      font-variant-numeric: tabular-nums;
    }}

    /* ── Progress bar ── */
    .progress-wrap {{
      margin-top: 12px;
      margin-bottom: 2px;
    }}
    .progress-track {{
      position: relative;
      height: 3px;
      background: var(--surface-2);
      border-radius: 3px;
      overflow: visible;
    }}
    .progress-fill {{
      height: 100%;
      border-radius: 3px;
      background: linear-gradient(90deg, var(--live) 0%, #5af078 100%);
      transition: width .8s cubic-bezier(.22,1,.36,1);
      position: relative;
      z-index: 1;
    }}
    .progress-glow {{
      position: absolute;
      top: 50%;
      transform: translate(-50%, -50%);
      width: 9px;
      height: 9px;
      border-radius: 50%;
      background: var(--live);
      box-shadow: 0 0 6px 2px rgba(48,209,88,.4), 0 0 16px 4px rgba(48,209,88,.12);
      z-index: 2;
      transition: left .8s cubic-bezier(.22,1,.36,1);
    }}
    .progress-meta {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-top: 6px;
    }}
    .progress-pct {{
      font-size: 0.62rem;
      font-weight: 680;
      color: var(--live-text);
      font-variant-numeric: tabular-nums;
    }}
    .progress-remain {{
      font-size: 0.62rem;
      font-weight: 480;
      color: var(--text-3);
      font-variant-numeric: tabular-nums;
    }}
    [data-theme="light"] .progress-glow {{
      box-shadow: 0 0 5px 2px rgba(52,199,89,.3), 0 0 12px 4px rgba(52,199,89,.08);
    }}

    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 0.6rem;
      font-weight: 720;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .badge.live {{
      background: var(--live-bg);
      color: var(--live-text);
      border: 1px solid rgba(52,199,89,.18);
    }}

    .countdown {{
      font-size: 0.7rem;
      color: var(--accent);
      font-weight: 560;
      font-variant-numeric: tabular-nums;
    }}

    /* Location */
    .loc {{
      display: inline-flex;
      align-items: flex-start;
      gap: 5px;
      font-size: 0.7rem;
      color: var(--text-2);
      margin-top: 8px;
      font-weight: 440;
      word-break: break-word;
    }}
    .loc svg {{
      opacity: .35;
      flex-shrink: 0;
    }}

    /* Notes */
    details {{
      margin-top: 10px;
    }}
    summary {{
      cursor: pointer;
      font-size: 0.73rem;
      font-weight: 560;
      color: var(--accent);
      user-select: none;
      display: inline-flex;
      align-items: center;
      gap: 5px;
      padding: 4px 0;
      transition: opacity .15s ease;
    }}
    summary svg {{
      opacity: .5;
    }}
    summary:hover {{
      opacity: .75;
    }}
    details[open] summary {{
      margin-bottom: 6px;
    }}
    .notes {{
      color: var(--text-2);
      font-size: 0.73rem;
      line-height: 1.7;
      padding: 10px 14px;
      background: var(--surface-2);
      border: 1px solid var(--border);
      border-radius: 10px;
      font-weight: 400;
    }}

    /* ── Empty state ── */
    .empty-state {{
      text-align: center;
      padding: 56px 28px;
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: var(--r2);
      box-shadow: var(--card-shadow);
    }}
    .empty-icon {{
      color: var(--text-3);
      margin-bottom: 16px;
      opacity: .4;
    }}
    .empty-state h2 {{
      font-size: 1.1rem;
      font-weight: 680;
      margin-bottom: 6px;
      letter-spacing: -0.01em;
    }}
    .empty-state p {{
      color: var(--text-2);
      font-size: 0.85rem;
      font-weight: 400;
    }}

    /* ── Footer ── */
    footer {{
      margin-top: 56px;
      padding-top: 18px;
      border-top: 1px solid var(--border);
      color: var(--text-3);
      font-size: 0.64rem;
      text-align: center;
      line-height: 1.7;
      letter-spacing: 0.03em;
      font-weight: 420;
    }}

    /* ── Small mobile (≤480px) ── */
    @media (max-width: 480px) {{
      .wrap {{
        padding: 40px 18px 80px;
        padding-left: max(18px, env(safe-area-inset-left, 0px));
        padding-right: max(18px, env(safe-area-inset-right, 0px));
        padding-bottom: max(80px, calc(64px + env(safe-area-inset-bottom, 0px)));
      }}
      .hero {{ margin-bottom: 32px; }}
      .hero h1 {{ font-size: 1.55rem; letter-spacing: -0.04em; }}
      .clock {{ margin-top: 8px; }}
      .clock-time {{ font-size: 0.92rem; }}
      .clock-period {{ font-size: 0.4rem; margin-left: 2px; }}
      .clock-dot {{ margin: 0 6px; font-size: 0.6rem; }}
      .clock-date {{ font-size: 0.68rem; }}
      .hero-chips {{ margin-top: 16px; gap: 6px; }}
      .chip {{ padding: 5px 11px; font-size: 0.62rem; }}
      .hero-next {{
        margin-top: 16px;
        gap: 10px;
        padding: 12px 14px;
        border-radius: 12px;
        flex-wrap: wrap;
      }}
      .hero-next-label {{ font-size: 0.56rem; padding: 4px 9px; }}
      .hero-next-title {{
        font-size: 0.84rem;
        width: 100%;
        order: 3;
        white-space: normal;
        line-height: 1.4;
        margin-top: 2px;
      }}
      .hero-eta {{ font-size: 0.72rem; }}
      .hero-live {{ font-size: 0.58rem; padding: 4px 10px; }}
      .day-group {{ margin-bottom: 28px; }}
      .day-head {{ margin-bottom: 12px; gap: 8px; }}
      .day-head h2 {{ font-size: 0.62rem; }}
      .day-date {{ font-size: 0.62rem; }}
      .cnt {{ font-size: 0.56rem; padding: 3px 9px; }}
      .timeline {{ padding-left: 22px; }}
      .tl-marker {{ left: -22px; top: 8px; width: 16px; height: 16px; }}
      .tl-item::before {{ left: -15px; top: 16px; }}
      .tl-item {{ padding-bottom: 12px; }}
      .card {{ padding: 14px 16px; border-radius: 12px; }}
      .card::before {{ top: 14px; bottom: 14px; }}
      .card-top {{ margin-bottom: 5px; }}
      .t {{ font-size: 0.7rem; }}
      .dur {{ font-size: 0.58rem; padding: 2px 7px; }}
      .card h3 {{ font-size: 0.9rem; margin-bottom: 3px; }}
      .range {{ font-size: 0.66rem; }}
      .countdown {{ font-size: 0.66rem; }}
      .badge {{ font-size: 0.56rem; padding: 3px 9px; }}
      .loc {{ font-size: 0.66rem; margin-top: 8px; }}
      .progress-wrap {{ margin-top: 10px; }}
      .progress-meta {{ margin-top: 5px; }}
      .progress-pct, .progress-remain {{ font-size: 0.6rem; }}
      details {{ margin-top: 8px; }}
      summary {{ font-size: 0.7rem; padding: 5px 0; }}
      .notes {{ font-size: 0.7rem; padding: 10px 14px; border-radius: 8px; line-height: 1.65; }}
      .empty-state {{ padding: 44px 22px; border-radius: 16px; }}
      .empty-state h2 {{ font-size: 1rem; }}
      .empty-state p {{ font-size: 0.82rem; }}
      footer {{ margin-top: 40px; font-size: 0.6rem; padding-bottom: env(safe-area-inset-bottom, 0px); }}
    }}

    /* ── Standard mobile (481px–700px) ── */
    @media (min-width: 481px) and (max-width: 700px) {{
      .wrap {{
        padding: 48px 24px 88px;
        padding-left: max(24px, env(safe-area-inset-left, 0px));
        padding-right: max(24px, env(safe-area-inset-right, 0px));
        padding-bottom: max(88px, calc(72px + env(safe-area-inset-bottom, 0px)));
      }}
      .hero {{ margin-bottom: 44px; }}
      .hero h1 {{ font-size: 1.85rem; }}
      .clock {{ margin-top: 10px; }}
      .clock-time {{ font-size: 1rem; }}
      .clock-period {{ font-size: 0.44rem; margin-left: 2px; }}
      .clock-dot {{ margin: 0 7px; font-size: 0.65rem; }}
      .clock-date {{ font-size: 0.72rem; }}
      .hero-chips {{ margin-top: 18px; gap: 7px; }}
      .chip {{ padding: 5px 12px; font-size: 0.65rem; }}
      .hero-next {{ margin-top: 18px; padding: 13px 16px; border-radius: 12px; }}
      .hero-next-title {{ font-size: 0.86rem; }}
      .day-group {{ margin-bottom: 34px; }}
      .day-head {{ margin-bottom: 13px; }}
      .timeline {{ padding-left: 26px; }}
      .tl-marker {{ left: -26px; }}
      .tl-item::before {{ left: -19px; }}
      .tl-item {{ padding-bottom: 14px; }}
      .card {{ padding: 16px 18px; border-radius: 14px; }}
      .card h3 {{ font-size: 0.94rem; }}
      footer {{ margin-top: 48px; padding-bottom: env(safe-area-inset-bottom, 0px); }}
    }}

    /* ── Large desktop (>900px) ── */
    @media (min-width: 900px) {{
      .wrap {{ max-width: 720px; padding: 80px 40px 120px; }}
      .hero {{ margin-bottom: 56px; }}
      .hero h1 {{ font-size: 2.8rem; }}
      .clock {{ margin-top: 14px; }}
      .clock-time {{ font-size: 1.2rem; }}
      .clock-period {{ font-size: 0.54rem; margin-left: 3px; }}
      .clock-dot {{ margin: 0 10px; font-size: 0.75rem; }}
      .clock-date {{ font-size: 0.84rem; }}
      .hero-chips {{ margin-top: 24px; gap: 8px; }}
      .chip {{ padding: 6px 14px; font-size: 0.7rem; }}
      .hero-next {{ padding: 14px 20px; }}
      .day-group {{ margin-bottom: 44px; }}
      .day-head {{ margin-bottom: 16px; }}
      .timeline {{ padding-left: 30px; }}
      .tl-marker {{ left: -30px; }}
      .tl-item::before {{ left: -22px; }}
      .tl-item {{ padding-bottom: 16px; }}
      .card {{ padding: 18px 22px 18px 22px; }}
      .card h3 {{ font-size: 1rem; }}
      footer {{ margin-top: 64px; }}
    }}

    /* ── Print ── */
    @media print {{
      body {{ background: white; color: black; }}
      .card {{ box-shadow: none; border: 1px solid #ddd; break-inside: avoid; }}
      .card::before {{ display: none; }}
      .pulse {{ animation: none; background: #34c759; }}
      .hero-next {{ box-shadow: none; }}
      .fade-in {{ animation: none; opacity: 1; }}
    }}

    /* ── Theme toggle ── */
    .theme-toggle {{
      position: fixed;
      bottom: 24px;
      right: 24px;
      z-index: 100;
      width: 44px;
      height: 44px;
      border-radius: 50%;
      border: 1px solid var(--border-2);
      background: var(--surface);
      box-shadow: 0 2px 12px rgba(0,0,0,.15), 0 1px 3px rgba(0,0,0,.1);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      transition: transform .3s cubic-bezier(.22,1,.36,1), box-shadow .3s ease, background .3s ease;
      -webkit-tap-highlight-color: transparent;
    }}
    .theme-toggle:hover {{
      transform: scale(1.08);
      box-shadow: 0 4px 20px rgba(0,0,0,.2), 0 2px 6px rgba(0,0,0,.12);
    }}
    .theme-toggle:active {{
      transform: scale(.95);
    }}
    .theme-toggle svg {{
      width: 18px;
      height: 18px;
      color: var(--text-2);
      transition: transform .5s cubic-bezier(.22,1,.36,1), opacity .3s ease;
    }}
    .theme-toggle .icon-sun {{ position: absolute; opacity: 0; transform: rotate(-90deg) scale(.5); }}
    .theme-toggle .icon-moon {{ position: absolute; opacity: 1; transform: rotate(0) scale(1); }}
    [data-theme="light"] .theme-toggle .icon-sun {{ opacity: 1; transform: rotate(0) scale(1); }}
    [data-theme="light"] .theme-toggle .icon-moon {{ opacity: 0; transform: rotate(90deg) scale(.5); }}

    @media (max-width: 480px) {{
      .theme-toggle {{
        bottom: max(16px, env(safe-area-inset-bottom, 0px));
        right: max(16px, env(safe-area-inset-right, 0px));
        width: 42px;
        height: 42px;
      }}
      .theme-toggle svg {{ width: 16px; height: 16px; }}
    }}
    @media (min-width: 481px) and (max-width: 700px) {{
      .theme-toggle {{
        bottom: max(20px, env(safe-area-inset-bottom, 0px));
        right: max(20px, env(safe-area-inset-right, 0px));
        width: 42px;
        height: 42px;
      }}
    }}

    /* ── Reduce motion ── */
    @media (prefers-reduced-motion: reduce) {{
      .fade-in {{ animation: none; opacity: 1; }}
      .pulse {{ animation: none; }}
      .loading-state .empty-icon {{ animation: none; opacity: .4; }}
      .loading-state h2 {{ animation: none; -webkit-text-fill-color: unset; background: none; }}
      .card {{ transition: none; }}
      .dot {{ transition: none; }}
      .theme-toggle, .theme-toggle svg {{ transition: none; }}
      .progress-fill, .progress-glow {{ transition: none; }}
      .progress-glow {{ box-shadow: none; }}
      .clock-time .clock-sep {{ animation: none; opacity: 1; }}
      .tgif-banner, .tgif-banner::before, .tgif-banner::after {{ animation: none !important; }}
      .tgif-banner .tgif-letter {{ animation: none !important; }}
      .tgif-emoji {{ animation: none !important; }}
      .tgif-sub {{ animation: none !important; opacity: 0.6 !important; }}
      .tgif-glow-border {{ animation: none !important; }}
      .tgif-confetti {{ display: none !important; }}
    }}

    /* ── TGIF Friday Banner ── */
    .tgif-banner {{
      display: none;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      margin: -8px 0 32px;
      padding: 36px 28px 30px;
      border-radius: var(--r2);
      position: relative;
      overflow: hidden;
      isolation: isolate;
      background: var(--surface);
      border: 1px solid var(--border-2);
      box-shadow: var(--card-shadow);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
    }}

    /* Animated aurora gradient behind the card */
    .tgif-banner::before {{
      content: '';
      position: absolute;
      inset: 0;
      background: linear-gradient(
        135deg,
        rgba(255,100,130,0.10) 0%,
        rgba(175,82,222,0.12) 20%,
        rgba(94,92,230,0.10) 40%,
        rgba(0,122,255,0.08) 60%,
        rgba(48,209,88,0.10) 80%,
        rgba(255,214,10,0.08) 100%
      );
      background-size: 300% 300%;
      animation: tgif-aurora 8s ease-in-out infinite;
      z-index: -2;
    }}

    /* Shimmer sweep overlay */
    .tgif-banner::after {{
      content: '';
      position: absolute;
      inset: 0;
      background: linear-gradient(
        105deg,
        transparent 40%,
        rgba(255,255,255,0.04) 45%,
        rgba(255,255,255,0.08) 50%,
        rgba(255,255,255,0.04) 55%,
        transparent 60%
      );
      background-size: 200% 100%;
      animation: tgif-shimmer 6s ease-in-out infinite;
      z-index: -1;
    }}

    [data-theme="light"] .tgif-banner {{
      box-shadow: 0 2px 12px rgba(0,0,0,.04), 0 8px 32px rgba(175,82,222,.06);
    }}
    [data-theme="light"] .tgif-banner::before {{
      background: linear-gradient(
        135deg,
        rgba(255,100,130,0.06) 0%,
        rgba(175,82,222,0.07) 20%,
        rgba(94,92,230,0.06) 40%,
        rgba(0,122,255,0.05) 60%,
        rgba(48,209,88,0.06) 80%,
        rgba(255,214,10,0.05) 100%
      );
      background-size: 300% 300%;
    }}
    [data-theme="light"] .tgif-banner::after {{
      background: linear-gradient(
        105deg,
        transparent 40%,
        rgba(0,0,0,0.01) 45%,
        rgba(0,0,0,0.02) 50%,
        rgba(0,0,0,0.01) 55%,
        transparent 60%
      );
      background-size: 200% 100%;
    }}

    @keyframes tgif-aurora {{
      0%, 100% {{ background-position: 0% 50%; }}
      25% {{ background-position: 100% 25%; }}
      50% {{ background-position: 50% 100%; }}
      75% {{ background-position: 0% 75%; }}
    }}
    @keyframes tgif-shimmer {{
      0%, 100% {{ background-position: 200% 0; }}
      50% {{ background-position: -100% 0; }}
    }}

    /* Glowing border animation */
    .tgif-glow-border {{
      position: absolute;
      inset: -1px;
      border-radius: inherit;
      padding: 1px;
      background: linear-gradient(
        var(--tgif-border-angle, 0deg),
        rgba(255,159,10,0.3),
        rgba(255,55,95,0.3),
        rgba(175,82,222,0.3),
        rgba(94,92,230,0.3),
        rgba(0,122,255,0.3),
        rgba(48,209,88,0.3),
        rgba(255,214,10,0.3)
      );
      -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
      mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
      -webkit-mask-composite: xor;
      mask-composite: exclude;
      animation: tgif-border-spin 4s linear infinite;
      z-index: 0;
      pointer-events: none;
    }}
    [data-theme="light"] .tgif-glow-border {{
      background: linear-gradient(
        var(--tgif-border-angle, 0deg),
        rgba(255,159,10,0.2),
        rgba(255,55,95,0.2),
        rgba(175,82,222,0.2),
        rgba(94,92,230,0.2),
        rgba(0,122,255,0.2),
        rgba(48,209,88,0.2),
        rgba(255,214,10,0.2)
      );
    }}
    @keyframes tgif-border-spin {{
      to {{ --tgif-border-angle: 360deg; }}
    }}
    @property --tgif-border-angle {{
      syntax: '<angle>';
      initial-value: 0deg;
      inherits: false;
    }}

    .tgif-emoji {{
      font-size: 2.4rem;
      margin-bottom: 8px;
      animation: tgif-bounce 2s ease-in-out infinite;
      filter: drop-shadow(0 2px 8px rgba(255,159,10,.3));
    }}
    @keyframes tgif-bounce {{
      0%, 100% {{ transform: translateY(0) scale(1); }}
      25% {{ transform: translateY(-6px) scale(1.05); }}
      50% {{ transform: translateY(0) scale(1); }}
      75% {{ transform: translateY(-3px) scale(1.02); }}
    }}

    .tgif-text {{
      display: inline-flex;
      flex-wrap: wrap;
      justify-content: center;
      gap: 2px;
      font-size: clamp(1.3rem, 4vw, 1.8rem);
      font-weight: 850;
      letter-spacing: 0.03em;
      line-height: 1.2;
      position: relative;
      z-index: 1;
    }}
    .tgif-space {{
      width: 8px;
      display: inline-block;
    }}
    .tgif-letter {{
      display: inline-block;
      background: linear-gradient(135deg, #ff9f0a, #ff375f, #af52de, #5e5ce6, #007aff, #30d158);
      background-size: 300% 300%;
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      animation: tgif-wave 3s ease-in-out infinite, tgif-gradient 6s ease infinite;
      filter: drop-shadow(0 0 12px rgba(175,82,222,.15));
    }}
    [data-theme="light"] .tgif-letter {{
      background: linear-gradient(135deg, #e08600, #e02050, #9030c0, #4040d0, #0060e0, #20a040);
      background-size: 300% 300%;
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      filter: drop-shadow(0 0 8px rgba(175,82,222,.1));
    }}
    @keyframes tgif-wave {{
      0%, 100% {{ transform: translateY(0) rotate(0deg); }}
      30% {{ transform: translateY(-5px) rotate(-1deg); }}
      60% {{ transform: translateY(1px) rotate(0.5deg); }}
    }}
    @keyframes tgif-gradient {{
      0%, 100% {{ background-position: 0% 50%; }}
      33% {{ background-position: 100% 0%; }}
      66% {{ background-position: 50% 100%; }}
    }}

    .tgif-sub {{
      display: block;
      margin-top: 10px;
      font-size: 0.82rem;
      font-weight: 520;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-2);
      position: relative;
      z-index: 1;
      opacity: 0;
      animation: tgif-sub-in 0.8s ease-out 1.6s forwards;
    }}
    @keyframes tgif-sub-in {{
      from {{ opacity: 0; transform: translateY(8px); }}
      to {{ opacity: 0.6; transform: translateY(0); }}
    }}

    /* Confetti system */
    .tgif-confetti {{
      position: absolute;
      top: 0; left: 0; right: 0; bottom: 0;
      pointer-events: none;
      overflow: hidden;
      z-index: 0;
    }}
    .tgif-particle {{
      position: absolute;
      top: -10px;
      opacity: 0;
      animation: tgif-fall linear infinite;
    }}
    .tgif-p-circle {{ border-radius: 50%; }}
    .tgif-p-square {{ border-radius: 1px; }}
    .tgif-p-rect {{ border-radius: 1px; }}
    .tgif-p-star {{
      background: transparent !important;
      border-left: 3px solid transparent;
      border-right: 3px solid transparent;
      border-bottom: 6px solid currentColor;
    }}
    @keyframes tgif-fall {{
      0% {{ transform: translateY(-10px) translateX(0) rotate(0deg); opacity: 0; }}
      8% {{ opacity: 0.8; }}
      50% {{ opacity: 0.6; }}
      90% {{ opacity: 0.3; }}
      100% {{ transform: translateY(120px) translateX(var(--drift, 0px)) rotate(720deg); opacity: 0; }}
    }}

    @media (max-width: 480px) {{
      .tgif-banner {{ padding: 28px 18px 22px; margin: -4px 0 24px; }}
      .tgif-emoji {{ font-size: 2rem; }}
      .tgif-text {{ font-size: 1.1rem; gap: 1px; }}
      .tgif-space {{ width: 5px; }}
      .tgif-sub {{ font-size: 0.68rem; margin-top: 8px; }}
    }}
    @media (min-width: 481px) and (max-width: 700px) {{
      .tgif-banner {{ padding: 32px 24px 26px; }}
      .tgif-emoji {{ font-size: 2.2rem; }}
      .tgif-text {{ font-size: 1.4rem; }}
      .tgif-sub {{ font-size: 0.75rem; }}
    }}
    @media (min-width: 900px) {{
      .tgif-banner {{ padding: 40px 32px 34px; margin: -8px 0 36px; }}
      .tgif-emoji {{ font-size: 2.8rem; }}
      .tgif-text {{ font-size: 1.9rem; }}
      .tgif-sub {{ font-size: 0.85rem; margin-top: 12px; }}
    }}

    /* ── Sunday Scaries Banner ── */
    :root {{
      --ss-eye: #8b5cf6;
      --ss-ghost: rgba(167,139,250,0.85);
    }}
    [data-theme="light"] {{
      --ss-eye: #6d28d9;
      --ss-ghost: rgba(139,92,246,0.9);
    }}

    .sunday-banner {{
      display: none;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      text-align: center;
      margin: -8px 0 32px;
      padding: 36px 28px 30px;
      border-radius: var(--r2);
      position: relative;
      overflow: hidden;
      isolation: isolate;
      background: var(--surface);
      border: 1px solid var(--border-2);
      box-shadow: var(--card-shadow);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
    }}

    .sunday-banner::before {{
      content: '';
      position: absolute;
      inset: 0;
      background: linear-gradient(
        135deg,
        rgba(88,28,135,0.12) 0%,
        rgba(109,40,217,0.10) 20%,
        rgba(79,70,229,0.10) 40%,
        rgba(55,48,163,0.08) 60%,
        rgba(30,27,75,0.10) 80%,
        rgba(88,28,135,0.08) 100%
      );
      background-size: 300% 300%;
      animation: ss-aurora 10s ease-in-out infinite;
      z-index: -2;
    }}

    .sunday-banner::after {{
      content: '';
      position: absolute;
      inset: 0;
      background: linear-gradient(
        105deg,
        transparent 40%,
        rgba(139,92,246,0.03) 45%,
        rgba(139,92,246,0.06) 50%,
        rgba(139,92,246,0.03) 55%,
        transparent 60%
      );
      background-size: 200% 100%;
      animation: ss-shimmer 10s ease-in-out infinite;
      z-index: -1;
    }}

    [data-theme="light"] .sunday-banner {{
      box-shadow: 0 2px 12px rgba(0,0,0,.04), 0 8px 32px rgba(109,40,217,.06);
    }}
    [data-theme="light"] .sunday-banner::before {{
      background: linear-gradient(
        135deg,
        rgba(88,28,135,0.05) 0%,
        rgba(109,40,217,0.06) 20%,
        rgba(79,70,229,0.05) 40%,
        rgba(55,48,163,0.04) 60%,
        rgba(30,27,75,0.05) 80%,
        rgba(88,28,135,0.04) 100%
      );
      background-size: 300% 300%;
    }}
    [data-theme="light"] .sunday-banner::after {{
      background: linear-gradient(
        105deg,
        transparent 40%,
        rgba(109,40,217,0.02) 45%,
        rgba(109,40,217,0.04) 50%,
        rgba(109,40,217,0.02) 55%,
        transparent 60%
      );
      background-size: 200% 100%;
    }}

    @keyframes ss-aurora {{
      0%, 100% {{ background-position: 0% 50%; }}
      25% {{ background-position: 100% 25%; }}
      50% {{ background-position: 50% 100%; }}
      75% {{ background-position: 0% 75%; }}
    }}
    @keyframes ss-shimmer {{
      0%, 100% {{ background-position: 200% 0; }}
      50% {{ background-position: -100% 0; }}
    }}

    .ss-glow-border {{
      position: absolute;
      inset: -1px;
      border-radius: inherit;
      padding: 1px;
      background: linear-gradient(
        var(--ss-border-angle, 0deg),
        rgba(139,92,246,0.3),
        rgba(109,40,217,0.25),
        rgba(79,70,229,0.3),
        rgba(55,48,163,0.2),
        rgba(88,28,135,0.25),
        rgba(167,139,250,0.3)
      );
      -webkit-mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
      mask: linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0);
      -webkit-mask-composite: xor;
      mask-composite: exclude;
      animation: ss-border-spin 10s linear infinite;
      z-index: 0;
      pointer-events: none;
    }}
    [data-theme="light"] .ss-glow-border {{
      background: linear-gradient(
        var(--ss-border-angle, 0deg),
        rgba(139,92,246,0.2),
        rgba(109,40,217,0.18),
        rgba(79,70,229,0.2),
        rgba(55,48,163,0.15),
        rgba(88,28,135,0.18),
        rgba(167,139,250,0.2)
      );
    }}
    @keyframes ss-border-spin {{
      to {{ --ss-border-angle: 360deg; }}
    }}
    @property --ss-border-angle {{
      syntax: '<angle>';
      initial-value: 0deg;
      inherits: false;
    }}

    /* Ghost + bubble group — floats together */
    .ss-ghost-group {{
      display: flex;
      flex-direction: column;
      align-items: center;
      margin-bottom: 10px;
      animation: ss-float 5s ease-in-out infinite;
      position: relative;
      z-index: 1;
    }}

    /* Ghost character */
    .ss-ghost {{
      width: 56px;
      height: 70px;
      color: var(--ss-ghost);
      filter: drop-shadow(0 0 16px rgba(139,92,246,.35));
      animation: ss-tremble 0.15s ease-in-out infinite alternate;
    }}
    @keyframes ss-tremble {{
      0% {{ transform: translateX(-0.4px) rotate(-0.3deg); }}
      100% {{ transform: translateX(0.4px) rotate(0.3deg); }}
    }}
    .ss-ghost svg {{
      width: 100%;
      height: 100%;
    }}
    /* Speech bubble */
    .ss-bubble {{
      position: relative;
      margin-bottom: 10px;
      padding: 6px 14px;
      font-size: 0.72rem;
      font-weight: 520;
      line-height: 1.35;
      color: var(--text);
      background: var(--surface-2);
      border: 1px solid var(--border-2);
      border-radius: 12px;
      box-shadow: 0 2px 12px rgba(0,0,0,.15);
      opacity: 0;
      transform: translateY(6px) scale(0.95);
      transition: opacity 0.6s ease, transform 0.6s ease;
      pointer-events: none;
      z-index: 2;
      white-space: normal;
      max-width: 260px;
      text-align: center;
    }}
    .ss-bubble::after {{
      content: '';
      position: absolute;
      left: 50%;
      bottom: -6px;
      width: 10px;
      height: 10px;
      background: var(--surface-2);
      border-right: 1px solid var(--border-2);
      border-bottom: 1px solid var(--border-2);
      transform: translateX(-50%) rotate(45deg);
    }}
    .ss-bubble.is-visible {{
      opacity: 1;
      transform: translateY(0) scale(1);
    }}

    @keyframes ss-float {{
      0%, 100% {{ transform: translateY(0) rotate(0deg); }}
      15% {{ transform: translateY(-8px) rotate(-2deg); }}
      30% {{ transform: translateY(-3px) rotate(0deg); }}
      50% {{ transform: translateY(-12px) rotate(2deg); }}
      70% {{ transform: translateY(-5px) rotate(-1deg); }}
      85% {{ transform: translateY(-9px) rotate(1deg); }}
    }}

    /* Text styling */
    .ss-text {{
      display: inline-flex;
      flex-wrap: wrap;
      justify-content: center;
      gap: 2px;
      font-size: clamp(1.3rem, 4vw, 1.8rem);
      font-weight: 850;
      letter-spacing: 0.03em;
      line-height: 1.2;
      position: relative;
      z-index: 1;
    }}
    .ss-space {{
      width: 8px;
      display: inline-block;
    }}
    .ss-letter {{
      display: inline-block;
      background: linear-gradient(135deg, #8b5cf6, #6d28d9, #4f46e5, #7c3aed, #a78bfa, #818cf8);
      background-size: 300% 300%;
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      animation: ss-wave 4s ease-in-out infinite, ss-gradient 10s ease infinite;
      filter: drop-shadow(0 0 10px rgba(139,92,246,.2));
    }}
    [data-theme="light"] .ss-letter {{
      background: linear-gradient(135deg, #7c3aed, #5b21b6, #4338ca, #6d28d9, #8b5cf6, #6366f1);
      background-size: 300% 300%;
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      background-clip: text;
      filter: drop-shadow(0 0 6px rgba(109,40,217,.12));
    }}
    @keyframes ss-wave {{
      0%, 100% {{ transform: translateY(0) rotate(0deg); }}
      25% {{ transform: translateY(-3px) rotate(-0.5deg); }}
      50% {{ transform: translateY(1px) rotate(0.3deg); }}
      75% {{ transform: translateY(-2px) rotate(-0.3deg); }}
    }}
    @keyframes ss-gradient {{
      0%, 100% {{ background-position: 0% 50%; }}
      33% {{ background-position: 100% 0%; }}
      66% {{ background-position: 50% 100%; }}
    }}

    .ss-sub {{
      display: block;
      margin-top: 10px;
      font-size: 0.82rem;
      font-weight: 520;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-2);
      position: relative;
      z-index: 1;
      opacity: 0;
      animation: ss-sub-in 0.8s ease-out 1.6s forwards;
    }}
    @keyframes ss-sub-in {{
      from {{ opacity: 0; transform: translateY(8px); }}
      to {{ opacity: 0.6; transform: translateY(0); }}
    }}

    /* Floating wisps (eerie particles) */
    .ss-wisps {{
      position: absolute;
      top: 0; left: 0; right: 0; bottom: 0;
      pointer-events: none;
      overflow: hidden;
      z-index: 0;
    }}
    .ss-wisp {{
      position: absolute;
      bottom: -6px;
      border-radius: 50%;
      opacity: 0;
      animation: ss-rise linear infinite;
      filter: blur(1px);
    }}
    @keyframes ss-rise {{
      0% {{ transform: translateY(0) translateX(0); opacity: 0; }}
      10% {{ opacity: 0.6; }}
      50% {{ opacity: 0.4; }}
      90% {{ opacity: 0.15; }}
      100% {{ transform: translateY(-130px) translateX(var(--drift, 0px)); opacity: 0; }}
    }}

    @media (prefers-reduced-motion: reduce) {{
      .sunday-banner, .sunday-banner::before, .sunday-banner::after {{ animation: none !important; }}
      .ss-letter {{ animation: none !important; }}
      .ss-ghost-group {{ animation: none !important; }}
      .ss-ghost {{ animation: none !important; }}
      .ss-sub {{ animation: none !important; opacity: 0.6 !important; }}
      .ss-glow-border {{ animation: none !important; }}
      .ss-wisps {{ display: none !important; }}
      .ss-bubble {{ opacity: 1 !important; transform: none !important; }}
    }}

    @media (max-width: 480px) {{
      .sunday-banner {{ padding: 24px 18px 22px; margin: -4px 0 24px; }}
      .ss-ghost {{ width: 44px; height: 55px; }}
      .ss-text {{ font-size: 1.1rem; gap: 1px; }}
      .ss-space {{ width: 5px; }}
      .ss-sub {{ font-size: 0.68rem; margin-top: 8px; }}
      .ss-bubble {{ font-size: 0.65rem; padding: 5px 11px; }}
    }}
    @media (min-width: 481px) and (max-width: 700px) {{
      .sunday-banner {{ padding: 28px 24px 26px; }}
      .ss-ghost {{ width: 50px; height: 62px; }}
      .ss-text {{ font-size: 1.4rem; }}
      .ss-sub {{ font-size: 0.75rem; }}
    }}
    @media (min-width: 900px) {{
      .sunday-banner {{ padding: 36px 32px 34px; margin: -8px 0 36px; }}
      .ss-ghost {{ width: 64px; height: 80px; }}
      .ss-ghost-group {{ margin-bottom: 14px; }}
      .ss-text {{ font-size: 1.9rem; }}
      .ss-sub {{ font-size: 0.85rem; margin-top: 12px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <div class="hero fade-in">
      <h1>{_esc(TITLE)}</h1>
      <div class="clock" aria-live="polite" aria-label="Current time">
        <span class="clock-time" id="clock-time">{_clock_html}</span>
        <span class="clock-dot">&middot;</span>
        <span class="clock-date" id="clock-date">{_clock_date}</span>
      </div>
      <div class="hero-chips">
        <span class="chip">{WINDOW_HOURS}h window</span>
        <span class="chip">{_esc(TIMEZONE)}</span>
        <span class="chip">{total_events} event{"s" if total_events != 1 else ""}</span>
      </div>
      <div id="hero-next-wrap">{hero_next}</div>
    </div>
    <div class="tgif-banner" id="tgif-banner" aria-hidden="true">
      <div class="tgif-glow-border"></div>
      <div class="tgif-confetti" id="tgif-confetti"></div>
      <div class="tgif-emoji">🎉</div>
      <span class="tgif-text" id="tgif-text"></span>
      <span class="tgif-sub">You made it through the week</span>
    </div>
    <div class="sunday-banner" id="sunday-banner" aria-hidden="true">
      <div class="ss-glow-border"></div>
      <div class="ss-wisps" id="sunday-wisps"></div>
      <div class="ss-ghost-group">
        <div class="ss-bubble" id="ss-bubble">Back to the work grind!</div>
        <div class="ss-ghost" aria-hidden="true">
          <svg viewBox="0 0 80 100" fill="none" xmlns="http://www.w3.org/2000/svg">
            <path d="M40 8C22.3 8 8 22.3 8 40v30c0 2 0.5 3.8 1.2 5.2L16 68l8 8 8-8 8 8 8-8 8 8 8-8 6.8 7.2c0.7-1.4 1.2-3.2 1.2-5.2V40c8 0-14.3-32-32-32z" fill="currentColor" opacity="0.9"/>
            <ellipse cx="28" cy="40" rx="5" ry="6" fill="var(--bg)" opacity="0.85"/>
            <ellipse cx="52" cy="40" rx="5" ry="6" fill="var(--bg)" opacity="0.85"/>
            <ellipse class="ss-eye" cx="28" cy="41" rx="3" ry="4" fill="var(--ss-eye)"/>
            <ellipse class="ss-eye" cx="52" cy="41" rx="3" ry="4" fill="var(--ss-eye)"/>
            <ellipse cx="40" cy="54" rx="4" ry="3" fill="var(--bg)" opacity="0.5"/>
          </svg>
        </div>
      </div>
      <span class="ss-text" id="sunday-text"></span>
      <span class="ss-sub">Tomorrow is Monday&hellip;</span>
    </div>
    <div id="agenda-events">{''.join(sections)}</div>
    <footer>
      Live data &middot; Powered by Cloudflare&nbsp;Pages
    </footer>
  </main>
  <button class="theme-toggle" aria-label="Toggle light/dark mode" title="Toggle theme">
    <svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
    <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
  </button>
  <script>
  (function(){{
    var KEY='agenda-theme',tc=document.getElementById('tc'),root=document.documentElement;
    var btn=document.querySelector('.theme-toggle');
    btn.addEventListener('click',function(){{
      var isLight=root.getAttribute('data-theme')==='light';
      if(isLight){{root.removeAttribute('data-theme');localStorage.setItem(KEY,'dark');tc.content='#000000';}}
      else{{root.setAttribute('data-theme','light');localStorage.setItem(KEY,'light');tc.content='#f2f2f7';}}
    }});
  }})();
  </script>
  <script>
  window.__AGENDA_TZ__ = {json.dumps(TIMEZONE)};
  window.__AGENDA_WINDOW_HOURS__ = {WINDOW_HOURS};
  window.__AGENDA_EVENTS__ = {_build_events_json(event_list)};
  </script>
  <script>{_RENDER_JS}</script>
</body>
</html>
"""


def write_json(events: Iterable[Event]) -> None:
    payload = [
        {
            "title": e.title,
            "start": e.start.isoformat(),
            "end": e.end.isoformat(),
            "location": e.location,
            "description": e.description,
            "is_all_day": e.is_all_day,
        }
        for e in events
    ]
    (SITE_DIR / "agenda.json").write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    tz = ZoneInfo(TIMEZONE)
    raw = fetch_ics(ICS_URL)
    events = parse_events(raw, tz)
    html_doc = render(events, tz)
    (SITE_DIR / "index.html").write_text(html_doc, encoding="utf-8")
    write_json(events)
    (SITE_DIR / "_headers").write_text(
        "/index.html\n"
        "  Cache-Control: public, max-age=60, s-maxage=120, stale-while-revalidate=300\n"
        "  X-Content-Type-Options: nosniff\n"
        "  X-Frame-Options: DENY\n"
        "  Referrer-Policy: strict-origin-when-cross-origin\n"
        "\n"
        "/agenda.json\n"
        "  Cache-Control: public, max-age=60, s-maxage=120\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(events)} event(s) to {SITE_DIR / 'index.html'}")

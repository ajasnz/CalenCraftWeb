/* Pure helpers for the public viewer. No DOM access here so they can be unit-tested with Node. */
(function (root) {
  'use strict';

  var VIEWS = ['month', 'week', 'day', 'list'];

  function pad(n) { return String(n).padStart(2, '0'); }

  function escHtml(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  // Escape first, then turn http(s) URLs into links. Nothing else can become a link, so a
  // description can't smuggle in javascript: URLs or markup.
  function linkify(text) {
    return escHtml(text).replace(/(https?:\/\/[^\s<]+[^\s<.,;:!?)\]'"])/g,
      '<a href="$1" target="_blank" rel="noopener noreferrer">$1</a>');
  }

  // Day bucket in the visitor's own timezone (toISOString() would bucket by UTC date).
  function localDayKey(d) { return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate()); }

  function utcStamp(d) {
    return d.getUTCFullYear() + pad(d.getUTCMonth() + 1) + pad(d.getUTCDate()) + 'T' +
      pad(d.getUTCHours()) + pad(d.getUTCMinutes()) + pad(d.getUTCSeconds()) + 'Z';
  }

  // The viewer API sends: a trailing Z for real instants, a bare date-time for floating times,
  // and a midnight date-time (isAllday) for all-day events.
  function icsWhen(iso, isAllday) {
    if (isAllday) return { prop: ';VALUE=DATE', value: iso.slice(0, 10).replace(/-/g, '') };
    if (/Z$/.test(iso)) return { prop: '', value: utcStamp(new Date(iso)) };
    return { prop: '', value: iso.slice(0, 19).replace(/[-:]/g, '') };
  }

  function icsEscape(s) {
    return String(s || '').replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\r?\n/g, '\\n');
  }

  // Fold lines at 75 octets-ish (characters are fine for the ASCII structure; content is escaped).
  function fold(line) {
    var out = [];
    while (line.length > 74) { out.push(line.slice(0, 74)); line = ' ' + line.slice(74); }
    out.push(line);
    return out.join('\r\n');
  }

  function eventToIcs(ev, now) {
    now = now || new Date();
    var s = icsWhen(ev.start, ev.isAllday), e = icsWhen(ev.end || ev.start, ev.isAllday);
    var lines = [
      'BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//CalenCraft//Viewer//EN', 'CALSCALE:GREGORIAN',
      'BEGIN:VEVENT',
      'UID:' + String(ev.id || 'event').replace(/[^A-Za-z0-9@._-]/g, '') + '@calencraft',
      'DTSTAMP:' + utcStamp(now),
      'DTSTART' + s.prop + ':' + s.value,
      'DTEND' + e.prop + ':' + e.value,
      'SUMMARY:' + icsEscape(ev.title || 'Busy')
    ];
    if (ev.location) lines.push('LOCATION:' + icsEscape(ev.location));
    if (ev.body) lines.push('DESCRIPTION:' + icsEscape(ev.body));
    lines.push('END:VEVENT', 'END:VCALENDAR');
    return lines.map(fold).join('\r\n') + '\r\n';
  }

  function googleCalendarUrl(ev) {
    var s = icsWhen(ev.start, ev.isAllday), e = icsWhen(ev.end || ev.start, ev.isAllday);
    var q = ['action=TEMPLATE', 'text=' + encodeURIComponent(ev.title || 'Busy'),
      'dates=' + s.value + '/' + e.value];
    if (ev.location) q.push('location=' + encodeURIComponent(ev.location));
    if (ev.body) q.push('details=' + encodeURIComponent(ev.body.slice(0, 1500)));
    return 'https://calendar.google.com/calendar/render?' + q.join('&');
  }

  function safeFileName(title) {
    return (String(title || 'event').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 50) || 'event') + '.ics';
  }

  // URL state: #v=week&d=2026-11-03&q=maker  (all parts optional, all validated)
  function parseHash(hash) {
    var out = {}, params = new URLSearchParams(String(hash || '').replace(/^#/, ''));
    var v = params.get('v'), d = params.get('d'), q = params.get('q');
    if (VIEWS.indexOf(v) !== -1) out.view = v;
    if (d && /^\d{4}-\d{2}-\d{2}$/.test(d)) {
      var dt = new Date(+d.slice(0, 4), +d.slice(5, 7) - 1, +d.slice(8, 10));
      if (!isNaN(dt) && localDayKey(dt) === d) out.date = dt;      // rejects 2026-02-31 and friends
    }
    if (q) out.query = q.slice(0, 80);
    return out;
  }

  function buildHash(state) {
    var p = new URLSearchParams();
    if (state.view) p.set('v', state.view);
    if (state.date) p.set('d', localDayKey(state.date));
    if (state.query) p.set('q', state.query);
    var s = p.toString();
    return s ? '#' + s : '';
  }

  function matchesQuery(ev, q) {
    if (!q) return true;
    q = q.toLowerCase();
    return [ev.title, ev.location, ev.body].some(function (x) { return x && String(x).toLowerCase().indexOf(q) !== -1; });
  }

  var api = {
    VIEWS: VIEWS, escHtml: escHtml, linkify: linkify, localDayKey: localDayKey, eventToIcs: eventToIcs,
    googleCalendarUrl: googleCalendarUrl, safeFileName: safeFileName, parseHash: parseHash,
    buildHash: buildHash, matchesQuery: matchesQuery
  };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.CCViewer = api;
})(typeof window !== 'undefined' ? window : this);

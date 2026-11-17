/* Read-only event dialog for the public viewer and embed.
 *
 * Replaces the calendar library's own popup, which showed Edit/Delete buttons on a read-only
 * calendar and positioned itself next to the clicked event, so it often ended up off-screen.
 * This dialog is fixed to the viewport (centred card on desktop, bottom sheet on phones).
 */
(function (root) {
  'use strict';
  var H = root.CCViewer || (typeof require !== 'undefined' ? require('./viewer-helpers.js') : null);

  function dayText(d) {
    return d.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  }

  // "Wednesday, 18 November 2026 · 19:00–21:00", or a date range for all-day / multi-day events.
  function whenText(ev, fmtTime) {
    var s = new Date(ev.start), e = new Date(ev.end || ev.start);
    if (ev.isAllday) {
      var last = new Date(e.getTime() - 1);                       // all-day ends are exclusive
      return H.localDayKey(last) > H.localDayKey(s) ? dayText(s) + ' – ' + dayText(last) : dayText(s);
    }
    if (H.localDayKey(e) !== H.localDayKey(s)) return dayText(s) + ' ' + fmtTime(s) + ' – ' + dayText(e) + ' ' + fmtTime(e);
    return dayText(s) + ' · ' + fmtTime(s) + (e > s ? '–' + fmtTime(e) : '');
  }

  // Pure (no DOM) so it can be unit-tested under Node.
  function render(ev, fmtTime) {
    fmtTime = fmtTime || function (d) { return d.toTimeString().slice(0, 5); };
    return '<div class="cc-pop-head">' +
        '<h2 id="cc-pop-title">' + H.escHtml(ev.title || 'Busy') + '</h2>' +
        '<button type="button" class="cc-pop-close" data-close aria-label="Close">&times;</button>' +
      '</div>' +
      '<div class="cc-pop-when">' + H.escHtml(whenText(ev, fmtTime)) + '</div>' +
      (ev.location ? '<div class="cc-pop-row"><span class="cc-pop-label">Where</span>' + H.linkify(ev.location) + '</div>' : '') +
      (ev.body ? '<div class="cc-pop-desc">' + H.linkify(ev.body) + '</div>' : '') +
      '<div class="cc-pop-actions">' +
        '<button type="button" class="cc-pop-btn" data-act="ics">Add to calendar (.ics)</button>' +
        '<a class="cc-pop-btn" href="' + H.escHtml(H.googleCalendarUrl(ev)) + '" target="_blank" rel="noopener noreferrer">Google Calendar &#8599;</a>' +
      '</div>';
  }

  // The library hands us its own event object; find the original payload so all-day and
  // floating times keep their exact meaning when exported to a calendar file.
  function fromTui(event, list) {
    var t = event.start && event.start.toDate ? event.start.toDate().getTime() : NaN;
    var hit = (list || []).filter(function (e) { return e.id === event.id && new Date(e.start).getTime() === t; })[0];
    if (hit) return hit;
    var s = event.start.toDate(), e = event.end ? event.end.toDate() : s;
    return { id: event.id, title: event.title, body: event.body || '', location: event.location || '',
             isAllday: !!event.isAllday, start: s.toISOString(), end: e.toISOString() };
  }

  function downloadIcs(ev) {
    var url = URL.createObjectURL(new Blob([H.eventToIcs(ev)], { type: 'text/calendar;charset=utf-8' }));
    var a = document.createElement('a');
    a.href = url; a.download = H.safeFileName(ev.title);
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }

  var overlay = null, opener = null, current = null;

  function focusables() {
    return Array.prototype.slice.call(overlay.querySelectorAll('button, a[href]'));
  }

  function onKey(e) {
    if (!overlay) return;
    if (e.key === 'Escape') { e.preventDefault(); close(); return; }
    if (e.key === 'Tab') {                                         // keep focus inside the dialog
      var f = focusables();
      if (!f.length) return;
      var first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }

  function close() {
    if (!overlay) return;
    document.removeEventListener('keydown', onKey, true);
    overlay.remove();
    overlay = null;
    document.documentElement.classList.remove('cc-pop-open');
    if (opener && opener.focus) { try { opener.focus(); } catch (e) { /* element may be gone */ } }
    opener = null; current = null;
  }

  var lastKey = '', lastAt = 0;

  function open(ev, fmtTime) {
    // The calendar library reports a single click twice. Ignore the repeat, otherwise the second
    // call would record the first dialog's close button as the element to return focus to.
    var key = ev.id + '|' + ev.start, now = Date.now();
    if (overlay && key === lastKey && now - lastAt < 500) return;
    lastKey = key; lastAt = now;
    close();
    opener = document.activeElement;
    current = ev;
    overlay = document.createElement('div');
    overlay.className = 'cc-pop-overlay';
    overlay.innerHTML = '<div class="cc-pop" role="dialog" aria-modal="true" aria-labelledby="cc-pop-title">' + render(ev, fmtTime) + '</div>';
    overlay.addEventListener('mousedown', function (e) { if (e.target === overlay) close(); });
    overlay.addEventListener('click', function (e) {
      if (e.target.closest('[data-close]')) close();
      else if (e.target.closest('[data-act="ics"]')) downloadIcs(current);
    });
    document.body.appendChild(overlay);
    document.documentElement.classList.add('cc-pop-open');
    document.addEventListener('keydown', onKey, true);
    var btn = overlay.querySelector('.cc-pop-close');
    if (btn) btn.focus();
  }

  var api = { render: render, whenText: whenText, fromTui: fromTui, open: open, close: close, downloadIcs: downloadIcs };
  if (typeof module !== 'undefined' && module.exports) module.exports = api; else root.CCPopup = api;
})(typeof window !== 'undefined' ? window : this);

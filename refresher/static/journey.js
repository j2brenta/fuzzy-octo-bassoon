/* Device-local navigation memory, scoped to this vault; read/skimmed decisions remain in the vault. */
'use strict';
(() => {
  const key = 'refresher-journey:' + (document.body?.dataset.workspace || 'preview');
  let data = {recent: [], resume: null, positions: {}};
  try { Object.assign(data, JSON.parse(localStorage.getItem(key) || '{}')); } catch { /* optional storage */ }
  const save = () => { try { localStorage.setItem(key, JSON.stringify(data)); } catch { /* private browsing still works */ } };
  const safe = value => {
    try { const u = new URL(value, location.origin); return u.origin === location.origin && ['/brief/', '/topic/', '/library/'].includes(u.pathname) ? u.pathname + u.search : null; } catch { return null; }
  };
  const rememberPosition = () => {
    const pane = document.getElementById('reading');
    data.positions[location.pathname + location.search] = {y: scrollY, pane: pane?.scrollTop || 0};
    const entries = Object.entries(data.positions).slice(-80); data.positions = Object.fromEntries(entries); save();
  };
  window.RefresherJourney = {
    data,
    subject(title) {
      const u = new URL(location.href); ['read', 'thread', 'pos', 'in'].forEach(k => u.searchParams.delete(k));
      const href = safe(u.href); if (!href) return;
      data.recent = [{title, href}, ...data.recent.filter(x => x.href !== href)].slice(0, 6); save();
    },
    reading(title) {
      const href = safe(location.href); if (!href) return;
      data.resume = {title, href}; save();
      {
        const p = data.positions[href];
        if (p) requestAnimationFrame(() => requestAnimationFrame(() => { window.scrollTo(0, p.y); const pane = document.getElementById('reading'); if (pane) pane.scrollTop = p.pane; }));
      }
    },
    position: rememberPosition,
    safe
  };
  addEventListener('pagehide', rememberPosition);
  // Keep the position before navigation inside either reader changes its URL.
  document.addEventListener('click', rememberPosition, true);
})();

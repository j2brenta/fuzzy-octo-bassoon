/* The shared bar: marks the current page and shows how many proposals wait. Opened from disk (wiki/graph/) there is
   no app behind the page, so the links stay hidden and nothing is fetched. */
'use strict';
(() => {
  const nav = document.getElementById('app-nav');
  if (!nav || !/^https?:$/.test(location.protocol)) return;
  nav.hidden = false;
  for (const link of nav.querySelectorAll('a[data-page]')) {
    if (location.pathname.startsWith('/' + link.dataset.page + '/')) link.setAttribute('aria-current', 'page');
  }
  const waiting = document.getElementById('nav-waiting');
  if (!waiting) return;
  fetch('/api/proposals').then(r => r.ok ? r.json() : []).then(rows => {
    const n = rows.filter(p => p.status === 'proposed').length;
    waiting.textContent = n ? String(n) : '';
    waiting.classList.toggle('waiting', n > 0);
  }).catch(() => { /* the count is a convenience; the page works without it */ });
})();

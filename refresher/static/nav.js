/* The shared bar: marks the current page, shows how many proposals wait, and holds the Consolidate button with the
   last review's results. Opened from disk (wiki/graph/) there is no app behind the page, so the links stay hidden and
   nothing is fetched. */
'use strict';
(() => {
  const nav = document.getElementById('app-nav');
  if (!nav || !/^https?:$/.test(location.protocol)) return;
  nav.hidden = false;
  const here = location.pathname === '/' || /^\/(topic|brief)\//.test(location.pathname) ? 'topics'
    : new URLSearchParams(location.search).get('layout') === 'read' ? 'read' : location.pathname.split('/')[1];
  for (const link of nav.querySelectorAll('a[data-page]')) {
    if (link.dataset.page === here) link.setAttribute('aria-current', 'page');
  }
  const waiting = document.getElementById('nav-waiting');
  const countWaiting = () => {
    if (!waiting) return;
    fetch('/api/proposals').then(r => r.ok ? r.json() : []).then(rows => {
      const n = rows.filter(p => p.status === 'proposed').length;
      waiting.textContent = n ? String(n) : '';
      waiting.classList.toggle('waiting', n > 0);
    }).catch(() => { /* the count is a convenience; the page works without it */ });
  };
  countWaiting();
  consolidation(nav, countWaiting);

  // --- Consolidate: the hourly review, run now; the dialog says what the last run did ---
  function consolidation(nav, onDone) {
    const el = (tag, cls, text) => {
      const node = document.createElement(tag);
      if (cls) node.className = cls;
      if (text !== undefined) node.textContent = text;
      return node;
    };
    const button = el('button', 'nav-run');
    button.type = 'button';
    button.setAttribute('aria-haspopup', 'dialog');
    const label = el('span', '', 'Consolidate');
    const ago = el('span', 'ago');
    button.append(label, ago);
    const review = nav.querySelector('a[href="/atlas/#proposals"]');
    nav.insertBefore(button, review ? review.nextSibling : null);

    const dialog = el('dialog', 'run-dialog');
    dialog.setAttribute('aria-labelledby', 'run-title');
    const title = el('h2', '', 'Consolidate');
    title.id = 'run-title';
    const intro = el('p', 'run-intro', 'Reviews the whole graph: relations a later source changed, duplicate items, '
      + 'Ideas confirmed by two authors, topic and merge proposals, people, and the map. It also runs by itself '
      + 'once an hour when something new was placed.');
    const state = el('p', 'run-state');
    state.setAttribute('role', 'status');
    const body = el('div', 'run-body');
    const actions = el('div', 'run-actions');
    const run = el('button', 'run-now', 'Run now');
    run.type = 'button';
    const close = el('button', 'run-close', 'Close');
    close.type = 'button';
    actions.append(run, close);
    dialog.append(title, intro, state, body, actions);
    document.body.append(dialog);

    let status = null, timer = null, wasRunning = false;
    const when = iso => new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    const since = iso => {
      const minutes = Math.round((Date.now() - new Date(iso)) / 60000);
      if (minutes < 1) return 'just now';
      if (minutes < 60) return `${minutes} min ago`;
      if (minutes < 48 * 60) return `${Math.round(minutes / 60)} h ago`;
      return `${Math.round(minutes / 1440)} days ago`;
    };

    function render() {
      const s = status;
      if (!s) return;
      ago.textContent = s.running ? 'running…' : s.last ? since(s.last.at) : 'never';
      button.classList.toggle('running', s.running);
      run.disabled = s.running;
      run.textContent = s.running ? 'Running…' : 'Run now';
      state.classList.toggle('error', Boolean(s.error));
      if (s.running) {
        state.textContent = `Running since ${when(s.started_at)}. A run takes a few minutes with the model; it waits `
          + 'first if the review container or the bot is reviewing.';
      } else if (s.error) {
        state.textContent = `The last run from here failed: ${s.error}. The graph is unchanged.`;
      } else if (!s.last) {
        state.textContent = 'No review has run on this vault yet.';
      } else {
        state.textContent = s.pending ? 'Something new was placed since: the next automatic run will pick it up.'
          : `Next automatic run after ${when(s.next_at)}, once something new is placed.`;
      }
      body.replaceChildren();
      if (!s.last) return;
      const l = s.last;
      if (l.took === undefined) {  // a run from before results were kept: only its time is known
        body.append(el('h3', '', `Last run · ${when(l.at)} (${since(l.at)})`),
          el('p', 'run-intro', 'Its results were not kept; the next run’s will be.'));
        return;
      }
      body.append(el('h3', '', `Last run · ${when(l.at)} (${since(l.at)}), took ${Math.round(l.took)} s`));
      const rows = [
        ['Items related', l.related], ['Duplicate items', l.duplicates], ['Ideas firmed', l.firmed],
        ['New proposals', l.proposed], ['Waiting for you', l.waiting], ['People looked up', l.enriched],
        ['Clusters named', l.named], ['Ideas placed in themes', l.placed],
      ];
      if (l.model_calls) rows.push(['Model calls', `${l.model_calls} (about ${l.tokens.toLocaleString()} tokens)`]);
      const list = el('dl', 'run-results');
      for (const [name, value] of rows) list.append(el('dt', '', name), el('dd', value ? '' : 'zero', String(value)));
      body.append(list);
      if (l.waiting) {
        const link = el('a', 'run-link', `Decide the ${l.waiting} waiting proposal${l.waiting === 1 ? '' : 's'} ↗`);
        link.href = '/atlas/#proposals';
        body.append(link);
      }
      for (const note of l.notes || []) body.append(el('p', 'run-note', note));
    }

    function refresh() {
      return fetch('/api/consolidate').then(r => r.ok ? r.json() : null).then(s => {
        if (!s) return;
        status = s;
        if (wasRunning && !s.running) {
          onDone();
          if (!s.error && !dialog.querySelector('.run-reload')) {
            const reload = el('button', 'run-reload', 'Reload to see changes');
            reload.type = 'button';
            reload.addEventListener('click', () => location.reload());
            actions.prepend(reload);
          }
        }
        wasRunning = s.running;
        render();
        clearTimeout(timer);
        if (s.running) timer = setTimeout(refresh, 3000);
      }).catch(() => { /* shown again on the next poll or click */ });
    }

    button.addEventListener('click', () => { refresh(); dialog.showModal(); });
    close.addEventListener('click', () => dialog.close());
    dialog.addEventListener('click', event => { if (event.target === dialog) dialog.close(); });  // the backdrop
    run.addEventListener('click', () => {
      run.disabled = true;
      fetch('/api/consolidate', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Refresher': '1' } })
        .then(r => r.json()).then(s => {
          status = s;
          wasRunning = s.running;
          render();
          clearTimeout(timer);
          if (s.running) timer = setTimeout(refresh, 3000);
        }).catch(() => { run.disabled = false; });
    });
    refresh();
  }
})();

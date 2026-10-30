/* The brief: shared helpers, the subject asked about and the context as served (see brief/main.js). */
'use strict';
(() => {
  // Every file of the brief shares this namespace; `later` reaches a function a file further down defines,
  // once every file has loaded.
  const B = window.RefresherBrief = {};
  B.later = new Proxy({}, {get: (_, name) => (...args) => B[name](...args)});
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = String(text); if (cls) n.className = cls; return n; };
  const NS = 'http://www.w3.org/2000/svg';
  const svg = (tag, attrs, cls) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v); if (cls) n.setAttribute('class', cls); return n; };
  const plural = (n, word, many) => `${n} ${n === 1 ? word : (many || word + 's')}`;
  const short = (t, n) => t && t.length > n ? t.slice(0, n - 1) + '…' : (t || '');
  const day = iso => iso ? new Date(iso).toLocaleDateString('en', {day: 'numeric', month: 'short', year: 'numeric'}) : '';
  const params = new URLSearchParams(location.search);
  const subject = params.get('id') ? {id: params.get('id')} : {q: params.get('q') || ''};
  const query = new URLSearchParams(subject).toString();
  // A source opens beside the brief (?read=); the brief stays where you were. Every source link carries this URL,
  // so a middle click still opens it in a tab of its own.
  const readHref = id => `/brief/?${query}&read=${encodeURIComponent(id)}`;
  const ideaVar = n => `var(--idea-${n % 6})`;
  const ideaColour = n => getComputedStyle(document.documentElement).getPropertyValue(`--idea-${n % 6}`).trim() || '#234cc5';
  // The context as served, refilled in place by load() so every file holds the same object; the path of sources
  // you read (the breadcrumb), changed in place by go(); the claim lit in the source, and the source you read.
  const c = {}, path = [], st = {lit: null, current: null};
  const post = (path, body) => fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Refresher': '1'},
    body: JSON.stringify(body || {})}).then(async r => { const b = await r.json().catch(() => ({})); if (!r.ok) throw new Error(b.error || `HTTP ${r.status}`); return b; });

  $('export').href = '/api/brief.md?' + query;

  function sid(item) { return c.written.ids[item]; }
  function source(item) { return c.sources[item] || {}; }
  function sourceLabel(item) { const s = source(item); return [s.source, s.author && s.author !== s.source ? s.author : null, day(s.when)].filter(Boolean).join(' · '); }

  // "… [S1] … [+S3]" as text with citation links.
  function cited(text, cls) {
    const p = el('p', null, cls);
    const byId = Object.fromEntries(Object.entries(c.written.ids).map(([item, s]) => [s, item]));
    let last = 0;
    for (const m of text.matchAll(/\[(\+?)S(\d+)\]/g)) {
      p.append(document.createTextNode(text.slice(last, m.index)));
      const item = byId['S' + m[2]];
      const a = el('a', (m[1] ? '+' : '') + 'S' + m[2], 'cite' + (m[1] ? ' lent' : ''));
      if (item) { a.href = readHref(item); a.title = `${source(item).title || ''} — ${sourceLabel(item)}`; }
      p.append(a);
      last = m.index + m[0].length;
    }
    p.append(document.createTextNode(text.slice(last)));
    return p;
  }

  function chip(item, cls, label) {
    const a = el('a', `${sid(item) || ''} ${label || source(item).source || ''}`.trim(), 'chip ' + (cls || ''));
    a.href = readHref(item); a.title = source(item).title || '';
    return a;
  }

  Object.assign(B, {$, c, chip, cited, day, el, ideaColour, ideaVar, path, plural, post, query, readHref, short, sid,
    source, sourceLabel, st, subject, svg});
})();

/* A subject read cluster by cluster: shared helpers, the subject and its plan (see topic/main.js). */
'use strict';
(() => {
  // Every file of the topic page shares this namespace; `later` reaches a function a file further down
  // defines, once every file has loaded.
  const B = window.RefresherTopic = {};
  B.later = new Proxy({}, {get: (_, name) => (...args) => B[name](...args)});
  const {beside, extendTrail, markNow, refreshContext, renderBar, renderBrowse, renderMap, renderRail} = B.later;
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = String(text); if (cls) n.className = cls; return n; };
  const svg = (tag, attrs = {}) => { const n = document.createElementNS('http://www.w3.org/2000/svg', tag); for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, String(v)); return n; };
  const button = (text, action, cls) => { const b = el('button', text, cls); b.type = 'button'; b.addEventListener('click', action); return b; };
  const link = (text, href, cls) => { const a = el('a', text, cls); a.href = href; return a; };
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const day = iso => new Date(iso).toLocaleDateString('en', {day: 'numeric', month: 'short', year: 'numeric'});
  const short = (text, n) => text.length > n ? text.slice(0, n - 1).trimEnd() + '…' : text;
  const pct = x => `${Math.round(100 * x)}%`;
  const initials = name => name.split(/[\s.\-_]+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join('') || '?';
  const smooth = () => matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
  const params = new URLSearchParams(location.search);
  const subjectQuery = params.get('id') ? {id: params.get('id')} : {q: params.get('q') || ''};
  const subjectParams = new URLSearchParams(subjectQuery).toString();
  $('tab-brief').href = '/brief/?' + subjectParams;  // the same subject as a brief
  const GRAPH_CAP = 8;

  // The subject as served, refilled in place, so every file holds the same object. The state the files share: the
  // source being read, the map's scroll before it opened, the trail of sources walked, a name marked in the text, the
  // context section open, the source picked, where you are (cluster > source > Idea), the thread of links followed
  // and your place on it.
  const s = {}, st = {current: null, mapScroll: 0, trail: [], highlight: null, openCtx: 'connected', selected: null,
    where: {cluster: null, source: null, idea: null}, thread: [], pos: 0};
  const contexts = new Map(), openBlocks = new Set(), shown = new Set();
  const results = new Map();  // all results by grouping, emptied when a mark changes the plan
  const src = id => s.sources[id];
  const stateOf = id => s.marks[id] || 'unread';
  const cluster = id => s.clusters.find(c => c.id === id);
  const clusterName = id => cluster(id)?.title || 'other sources';
  const onSubject = c => c.band === 'main' || c.band === 'adjacent';

  // --- the plan: numbers stay put while you read; what you read is numbered first ---
  function planIds() { return s.plan.map(x => x.source); }
  function numbered() {  // source -> its number on the reading path: the sources you read, then the plan
    const read = Object.keys(s.marks).filter(id => s.sources[id] && s.marks[id] !== 'unread');
    const out = new Map(); [...read, ...planIds()].forEach(id => { if (!out.has(id)) out.set(id, out.size + 1); });
    return out;
  }
  const nextId = () => planIds()[0] || null;
  const share = id => s.share[id] || 0;
  function gainText(id) {
    const st = stateOf(id);
    if (st !== 'unread') return st;
    const n = share(id);
    return n >= 0.005 ? `+${pct(n)} of the subject` : '+0%';
  }

  async function setState(id, state) {
    const r = await fetch(`/api/source/${encodeURIComponent(id)}/state`, {method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Refresher': '1'}, body: JSON.stringify({state, ...subjectQuery})});
    const body = await r.json().catch(() => ({}));
    if (!r.ok) { $('cover-text').textContent = body.error || `Could not save (${r.status})`; return; }
    Object.assign(s, body); results.clear();  // plan numbers and order changed
    if (st.current) { extendTrail(); renderRail(); renderBar(); refreshContext(); if (beside()) { renderBrowse(); markNow(); } } else renderMap();
  }

  Object.assign(B, {$, GRAPH_CAP, button, cluster, clusterName, contexts, day, el, gainText, initials, link, nextId,
    numbered, onSubject, openBlocks, pct, planIds, plural, results, s, setState, short, shown, smooth,
    src, st, stateOf, subjectParams, subjectQuery, svg});
})();

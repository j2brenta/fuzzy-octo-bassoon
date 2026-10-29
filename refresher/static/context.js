/* The brief (/brief/?q=|id=): a top band that stays (title, keywords, one quiet line with the date range, versions,
   save, Markdown; a one-line banner on what arrived since the last saved version), then three slots that keep their
   jobs. Overview: the brief in the centre (major ideas, sources, further reading), a narrow panel with the mini map,
   people and organizations. Reading: opening a source slides the same idea cards into a compact rail, puts the source
   in the centre with what the brief takes tinted by idea, and turns the narrow panel to the source (the map in the
   same place, its people, what was extracted by idea, kind as a label). Further reading is a dialog there. The
   breadcrumb holds the path; Esc steps back. The panel ranks whom to listen to; any person or organization opens in a
   dialog with a mini map of who is tied to them, where a click opens the next. Values only enter textContent. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = String(text); if (cls) n.className = cls; return n; };
  const NS = 'http://www.w3.org/2000/svg';
  const svg = (tag, attrs, cls) => { const n = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs || {})) n.setAttribute(k, v); if (cls) n.setAttribute('class', cls); return n; };
  const plural = (n, word, many) => `${n} ${n === 1 ? word : (many || word + 's')}`;
  const day = iso => iso ? new Date(iso).toLocaleDateString('en', {day: 'numeric', month: 'short', year: 'numeric'}) : '';
  const params = new URLSearchParams(location.search);
  const subject = params.get('id') ? {id: params.get('id')} : {q: params.get('q') || ''};
  const query = new URLSearchParams(subject).toString();
  // A source opens beside the brief (?read=); the brief stays where you were. Every source link carries this URL,
  // so a middle click still opens it in a tab of its own.
  const readHref = id => `/brief/?${query}&read=${encodeURIComponent(id)}`;
  const ideaVar = n => `var(--idea-${n % 6})`;
  const ideaColour = n => getComputedStyle(document.documentElement).getPropertyValue(`--idea-${n % 6}`).trim() || '#234cc5';
  let c = null;  // the context as served
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

  function ideaCard(x, n) {
    const card = el('article', null, 'ce-idea'); card.id = 'idea-' + n; card.dataset.idea = x.id;
    card.style.setProperty('--idea', ideaVar(n - 1));
    const t = c.written.texts[x.id] || {};
    const head = el('div', null, 'ce-chips ce-idea-head');
    head.append(el('span', `IDEA ${n}`, 'ce-num'), el('span', '', 'ce-here'));
    const n_sources = Object.keys(x.sources).length;
    head.append(el('span', plural(n_sources, 'source'), 'chip muted'));
    if (x.support > 1) head.append(el('span', `${x.support} independent writers`, 'chip'));
    if (x.history.length > 1 || x.previously.length) {
      const b = el('button', `history · ${plural(x.history.length, 'step')}, ${day(x.history[0].when)} → ${day(x.history[x.history.length - 1].when)}`, 'ce-history-btn');
      b.type = 'button'; b.addEventListener('click', () => history(x, n)); head.append(b);
    }
    card.append(head, el('h3', x.title));
    const areas = (c.area?.clusters || []).filter(k => k.in_brief && k.ideas.includes(n - 1));
    if (areas.length) {
      const where = el('div', null, 'ce-chips');
      where.append(el('span', 'In', 'label'), ...areas.map(k => { const b = el('button', k.title.slice(0, 48), 'chip area-chip'); b.type = 'button';
        b.addEventListener('click', () => mapCluster(k.id)); return b; }));
      card.append(where);
    }
    card.append(t.paragraph ? cited(t.paragraph, 'para' + (t.written_by ? '' : ' plain')) : el('p', 'Not written yet.', 'para plain'));
    if (t.previously) { const pv = cited(t.previously, 'ce-previously'); pv.prepend(el('b', 'Previously. ')); card.append(pv); }
    else if (x.previously.length) {
      for (const p of x.previously) {
        const pv = el('p', null, 'ce-previously'); pv.append(el('b', 'Previously. '), document.createTextNode(`${p.old_statement} (${source(p.old_item).source || ''}, ${day(p.old_when)}) — replaced: ${p.rationale}`));
        card.append(pv);
      }
    }
    const read = el('div', null, 'ce-read');
    const a = el('a', source(x.base).title || 'the base source'); a.href = readHref(x.base);
    read.append(el('span', 'Read in depth', 'label'), el('span', sid(x.base), 'cite'), a,
      el('span', `${sourceLabel(x.base)} · ${source(x.base).minutes || 1} min`, 'small quiet'));
    card.append(read);
    const builders = Object.entries(x.sources).filter(([, r]) => r === 'builder').map(([i]) => i);
    const rewrites = Object.entries(x.sources).filter(([, r]) => r === 'rewrite').map(([i]) => i);
    const old = Object.entries(x.sources).filter(([, r]) => r === 'superseded').map(([i]) => i);
    const lent = (x.adjacent || []).map(a => a.item);
    const chips = el('div', null, 'ce-chips');
    if (builders.length) { chips.append(el('span', 'Built on by', 'label'), ...builders.map(i => chip(i))); }
    if (lent.length) { chips.append(el('span', 'Lent facts', 'label'), ...lent.map(i => chip(i, 'lent'))); }
    if (rewrites.length) { chips.append(el('span', 'Retold by', 'label'), ...rewrites.map(i => chip(i, 'muted'))); }
    if (old.length) { chips.append(el('span', 'Replaced', 'label'), ...old.map(i => chip(i, 'old'))); }
    if (chips.childNodes.length) card.append(chips);
    for (const k of x.conflicts) card.append(el('p', `Disputed: ${k.rationale}`, 'small chip warn'));
    card.addEventListener('mouseenter', () => light(x.id, true)); card.addEventListener('mouseleave', () => light(x.id, false));
    return card;
  }

  function light(id, on) {
    document.querySelectorAll(`[data-idea="${CSS.escape(id)}"]`).forEach(n => n.classList.toggle('lit', on));
  }

  function history(x, n) {
    $('history-title').textContent = `Idea ${n}: how it developed`;
    $('history-idea').textContent = x.title;
    $('history-steps').replaceChildren(...[...x.history].reverse().map(h => {
      const li = el('li', null, h.role === 'superseded' ? 'superseded' : '');
      const top = el('div', null, 'ce-chips');
      top.append(el('span', h.step, 'step'), el('span', day(h.when), 'small quiet'), chip(h.item, h.role === 'superseded' ? 'old' : ''));
      li.append(top, el('p', h.statement, 'st'));
      if (h.rationale) li.append(el('p', h.rationale, 'why'));
      return li;
    }));
    $('history').showModal();
  }

  function constellation(major) {
    const box = $('constellation');
    const W = 400, H = major.length > 3 ? 380 : 300;
    box.setAttribute('viewBox', `0 0 ${W} ${H}`);
    box.replaceChildren();
    const n = major.length, cx = W / 2, cy = H / 2;
    const R = n === 1 ? 0 : Math.min(W, H) / 2 - 70;
    const at = major.map((x, k) => ({x, px: cx + R * Math.cos(-Math.PI / 2 + 2 * Math.PI * k / n), py: cy + R * Math.sin(-Math.PI / 2 + 2 * Math.PI * k / n),
      r: Math.min(26 + 7 * Math.sqrt(Object.keys(x.sources).length), n === 1 ? 90 : 52)}));
    // Bridges: two ideas sharing a source.
    for (let a = 0; a < at.length; a++) for (let b = a + 1; b < at.length; b++) {
      const shared = Object.keys(at[a].x.sources).filter(i => i in at[b].x.sources);
      if (shared.length) box.append(svg('line', {x1: at[a].px, y1: at[a].py, x2: at[b].px, y2: at[b].py}, 'bridge'));
    }
    at.forEach(({x, px, py, r}, k) => {
      const g = svg('g', {tabindex: 0, role: 'link', 'aria-label': `Idea ${k + 1}: ${x.title}`}, 'idea'); g.dataset.idea = x.id;
      const ringEl = svg('circle', {cx: px, cy: py, r}, 'ring'); ringEl.style.stroke = ideaColour(k); g.append(ringEl);
      const members = Object.entries(x.sources);
      const builders = members.filter(([, role]) => role === 'builder'), rewrites = members.filter(([, role]) => role === 'rewrite'),
        old = members.filter(([, role]) => role === 'superseded');
      const ring = (list, rr, size, cls, offset) => list.forEach(([i], j) => {
        const ang = (offset || 0) + 2 * Math.PI * j / Math.max(list.length, 1);
        const node = svg('circle', {cx: px + rr * Math.cos(ang), cy: py + rr * Math.sin(ang), r: size}, cls);
        const tip = svg('title'); tip.textContent = `${sid(i)} ${source(i).title || ''}`; node.append(tip); g.append(node);
      });
      ring(builders, r * 0.55, 6, 'builder', 0.3);
      ring(rewrites, r * 0.8, 3, 'rewrite', 1.1);
      ring(old, r * 0.55, 5, 'old', 2.2);
      const lent = x.adjacent || [];
      lent.forEach((a, j) => {
        const ang = -Math.PI / 4 + j * 0.55, ox = px + (r + 14) * Math.cos(ang), oy = py + (r + 14) * Math.sin(ang);
        g.append(svg('line', {x1: px, y1: py, x2: ox, y2: oy}, 'tether'));
        const node = svg('circle', {cx: ox, cy: oy, r: 5}, 'lent');
        const tip = svg('title'); tip.textContent = `${sid(a.item)} lends: ${a.facts.map(f => f.statement).join(' · ')}`; node.append(tip); g.append(node);
      });
      const baseEl = svg('circle', {cx: px, cy: py, r: 10}, 'base'); baseEl.style.fill = ideaColour(k); g.append(baseEl);
      const s = svg('text', {x: px, y: py + 3, 'text-anchor': 'middle'}, 'sid'); s.textContent = sid(x.base) || ''; g.append(s);
      const num = svg('text', {x: px, y: py - r + 14, 'text-anchor': 'middle'}, 'n'); num.textContent = String(k + 1); g.append(num);
      const tip = svg('title'); tip.textContent = x.title; g.append(tip);
      const go = () => { const card = $('idea-' + (k + 1)); card.scrollIntoView({behavior: 'smooth', block: 'start'}); light(x.id, true); setTimeout(() => light(x.id, false), 1600); };
      g.addEventListener('click', go); g.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
      g.addEventListener('mouseenter', () => light(x.id, true)); g.addEventListener('mouseleave', () => light(x.id, false));
      box.append(g);
    });
  }

  function arrival(r, cls) {
    const box = el('div', null, 'ce-arrival ' + cls);
    const words = {'updates': 'Updates what you knew', 'contradicts': 'Contradicts what you knew', 'new idea': 'A new major idea',
      'new base': 'A new source to read', 'new authority': 'A new top voice', 'builds on': 'Builds on an idea you have',
      'lends facts': 'Adds facts', 'minor idea': 'A minor idea'};
    box.append(el('span', words[r.kind] || r.kind, 'what'));
    const h = el('h3'); const a = el('a', r.title); a.href = readHref(r.item); h.append(a); box.append(h);
    box.append(el('p', [r.source, day(r.when), r.idea_title ? `on “${r.idea_title}”` : null].filter(Boolean).join(' · '), 'small quiet'));
    if (r.why) box.append(el('p', r.why, 'small'));
    return box;
  }

  // One line under the top band, never in place of the ideas: what arrived since the last saved version, opened on demand.
  function since(thread) {
    const d = thread && thread.delta, box = $('since');
    if (!d) { box.hidden = true; return; }
    box.hidden = false;
    box.classList.toggle('critical', d.critical.length > 0);
    if (!d.arrived) { box.replaceChildren(el('p', `You're up to date with your saved sources since your checkpoint of ${day(d.since)}.`, 'ce-since-line')); return; }
    const noise = Object.values(d.noise).reduce((n, rows) => n + rows.length, 0);
    const det = el('details');
    det.append(el('summary', [`${plural(d.arrived, 'source')} since your checkpoint of ${day(d.since)}`, d.critical.length && `${d.critical.length} critical`,
      d.notes.length && plural(d.notes.length, 'note'), noise && `${noise} overlapping or less relevant`].filter(Boolean).join(' · ')));
    const reasons = {retells: ['retells what you know', 'retell what you know'],
      outdated: ['is an older view already replaced', 'are older views already replaced'],
      near: ['is only near the subject', 'are only near the subject']};
    const body = el('div', null, 'ce-since-body');
    body.append(...d.critical.map(r => arrival(r, 'critical')), ...d.notes.map(r => arrival(r, 'note')), ...Object.entries(d.noise).map(([k, rows]) => {
      const n = el('details', null, 'ce-noise');
      n.append(el('summary', `${plural(rows.length, 'source')} ${(reasons[k] || [k, k])[rows.length === 1 ? 0 : 1]}`));
      const ul = el('ul'); rows.forEach(r => { const li = el('li'); const a = el('a', r.title); a.href = readHref(r.item); li.append(a, document.createTextNode(` · ${r.source || ''}`)); ul.append(li); });
      n.append(ul); return n;
    }));
    det.append(body);
    box.replaceChildren(det);
  }

  // Saved versions of this brief, newest first, each opening in a dialog.
  function versions(thread) {
    const btn = $('versions'), list = $('versions-list');
    const n = thread ? thread.stamps.length : 0;
    $('versions-box').hidden = !n; list.hidden = true; btn.setAttribute('aria-expanded', 'false');
    if (!n) return;
    btn.textContent = `Versions (${n}) ▾`;
    fetch(`/api/threads/${encodeURIComponent(thread.thread.slug)}/snapshots`).then(r => r.json()).then(body => {
      const diffs = Object.fromEntries((body.diffs || []).map(d => [d.to, d]));
      list.replaceChildren(...[...body.snapshots].reverse().map(s => {
        const li = el('li');
        const b = el('button', `${day(s.at)} · ${plural(s.major.length, 'idea')} · ${plural(s.counts.sources, 'source')}`);
        b.type = 'button'; b.addEventListener('click', () => { toggleVersions(false); snapshot(s); }); li.append(b);
        const d = diffs[s.stamp];
        if (d) li.append(el('p', [d.added.length && `+${d.added.length} ideas`, d.dropped.length && `−${d.dropped.length}`,
          d.rebased.length && `${d.rebased.length} new base`, d.replaced.length && `${d.replaced.length} replaced`].filter(Boolean).join(' · ') || 'no change in the core', 'diff'));
        return li;
      }));
    }).catch(() => { $('versions-box').hidden = true; });
  }
  function toggleVersions(open) {
    $('versions-list').hidden = !open; $('versions').setAttribute('aria-expanded', String(open));
  }

  function snapshot(s) {
    $('snapshot-title').textContent = `Your version of ${day(s.at)}`;
    const body = $('snapshot-body'); body.replaceChildren();
    for (const x of s.major) {
      body.append(el('h3', x.title, 'serif'));
      body.append(el('p', (x.text && x.text.paragraph || '').replace(/\[\+?S\d+\]/g, '').trim(), 'quiet'));
    }
    $('snapshot').showModal();
  }


  // --- the area map: topics hold clusters, clusters hold source dots coloured by the idea they serve ---
  function areaElements(area, focus) {
    const els = [], shown = new Set();
    const keep = focus ? new Set([focus.cluster, ...area.links.filter(l => l.a === focus.cluster || l.b === focus.cluster).flatMap(l => [l.a, l.b])]) : null;
    const clustersShown = area.clusters.filter(k => !keep || keep.has(k.id));
    const topicOf = Object.fromEntries(area.topics.flatMap(t => t.clusters.map(k => [k, t.id])));
    for (const t of area.topics) if (clustersShown.some(k => topicOf[k.id] === t.id)) els.push({data: {id: t.id, label: t.title, kind: 'topic'}});
    for (const k of clustersShown) {
      els.push({data: {id: k.id, label: k.title.length > 44 ? k.title.slice(0, 43) + '…' : k.title, kind: 'cluster', parent: topicOf[k.id],
        beyond: k.in_brief ? 0 : 1, count: k.count}});
      for (const i of k.sources) {
        if (shown.has(i)) continue; shown.add(i);
        const src = area.sources[i];
        els.push({data: {id: 'src:' + i, item: i, parent: k.id, kind: 'source', label: sid(i) || '', colour: src.idea == null ? '' : ideaColour(src.idea),
          inbrief: src.idea == null ? 0 : 1, beyond: k.in_brief ? 0 : 1, title: src.title, here: focus && focus.item === i ? 1 : 0}});
      }
    }
    for (const k of clustersShown) for (const i of k.also || []) {  // a source that also feeds this cluster
      if (shown.has(i)) els.push({data: {id: `also:${i}:${k.id}`, source: 'src:' + i, target: k.id, kind: 'also'}});
    }
    const ids = new Set(els.filter(e => !e.data.source).map(e => e.data.id));
    for (const l of area.links) if (ids.has(l.a) && ids.has(l.b)) els.push({data: {id: `l:${l.a}:${l.b}`, source: l.a, target: l.b, kind: 'link', beyond: l.beyond ? 1 : 0}});
    return els;
  }
  function areaStyle() {
    const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return [
      {selector: 'node', style: {'label': 'data(label)', 'font-size': 14, 'color': css('--r-ink'), 'text-wrap': 'wrap', 'text-max-width': 140}},
      {selector: 'node[kind="topic"]', style: {'background-color': css('--r-graph-topic'), 'background-opacity': .25, 'border-color': css('--r-graph-topic-border'),
        'border-width': 1, 'text-valign': 'top', 'font-weight': 600, 'font-size': 15, 'padding': 14, 'shape': 'round-rectangle'}},
      {selector: 'node[kind="cluster"]', style: {'background-color': css('--r-graph-region'), 'background-opacity': .5, 'border-color': css('--r-graph-border'),
        'border-width': 1, 'text-valign': 'top', 'text-margin-y': -2, 'padding': 10, 'shape': 'round-rectangle'}},
      {selector: 'node[kind="cluster"]:childless', style: {'width': 120, 'height': 40, 'text-valign': 'center', 'text-margin-y': 0, 'font-size': 12, 'text-max-width': 112}},
      {selector: 'node[kind="cluster"][beyond=1]', style: {'opacity': .55, 'border-style': 'dashed', 'background-opacity': .2}},
      {selector: 'node[kind="source"]', style: {'width': 18, 'height': 18, 'shape': 'ellipse', 'background-color': css('--r-noise'), 'label': 'data(label)',
        'font-size': 10, 'color': 'white', 'text-valign': 'center', 'text-halign': 'center', 'font-weight': 700, 'border-width': 0}},
      {selector: 'node[kind="source"][inbrief=1]', style: {'background-color': 'data(colour)', 'width': 22, 'height': 22}},
      {selector: 'node[kind="source"][here=1]', style: {'border-width': 4, 'border-color': css('--r-coral'), 'width': 28, 'height': 28}},
      {selector: 'node.read', style: {'background-opacity': .45}},
      {selector: 'node.next', style: {'border-width': 3, 'border-color': css('--r-ink'), 'border-style': 'dotted'}},
      {selector: 'node.lit', style: {'border-width': 3, 'border-color': css('--r-brand')}},
      {selector: 'edge[kind="link"]', style: {'width': 1.4, 'line-color': css('--r-graph-link'), 'curve-style': 'bezier', 'opacity': .7}},
      {selector: 'edge[kind="link"][beyond=1]', style: {'line-style': 'dashed', 'opacity': .5}},
      {selector: 'edge[kind="also"]', style: {'width': 1, 'line-color': css('--r-graph-edge'), 'line-style': 'dotted', 'curve-style': 'bezier', 'opacity': .6}},
    ];
  }
  function drawArea(box, area, focus, onTap) {
    if (typeof cytoscape !== 'function') { box.replaceChildren(el('p', 'The map needs cytoscape.min.js.', 'small quiet')); return null; }
    const elements = areaElements(area, focus);
    // A fixed layout, not a force layout (compound nodes made cose run off the canvas): one block per topic, its
    // clusters in rows, blocks packed left to right as the box's shape allows; the brief's blocks first.
    const order = area.clusters.map(k => k.id);
    const clusterEls = elements.filter(e => e.data.kind === 'cluster');
    const blocks = new Map();
    for (const e of clusterEls) { const t = e.data.parent || '~'; if (!blocks.has(t)) blocks.set(t, []); blocks.get(t).push(e); }
    const blockOrder = [...blocks.entries()].sort((a, b) => Math.min(...a[1].map(e => e.data.beyond)) - Math.min(...b[1].map(e => e.data.beyond))
      || (a[0] === '~') - (b[0] === '~') || b[1].length - a[1].length);
    const W = Math.max(box.clientWidth, 200), H = Math.max(box.clientHeight, 160), CW = 170, CH = 120;
    const cols = Math.max(1, Math.min(clusterEls.length, Math.round(Math.sqrt(clusterEls.length * (W / CW) / (H / CH)))));
    const center = {};
    let x0 = 0, y0 = 0, shelf = 0;
    for (const [, list] of blockOrder) {
      list.sort((a, b) => (a.data.beyond - b.data.beyond) || order.indexOf(a.data.id) - order.indexOf(b.data.id));
      const bc = Math.min(cols, list.length), rows = Math.ceil(list.length / bc);
      if (x0 && x0 + bc > cols) { x0 = 0; y0 += shelf + 0.45; shelf = 0; }
      list.forEach((e, k) => { center[e.data.id] = {x: (x0 + k % bc) * CW, y: (y0 + Math.floor(k / bc)) * CH}; e.position = {...center[e.data.id]}; });
      x0 += bc + 0.3; shelf = Math.max(shelf, rows);
    }
    const per = {};
    for (const e of elements) if (e.data.kind === 'source') {
      const k = (per[e.data.parent] = (per[e.data.parent] || 0) + 1) - 1, o = center[e.data.parent] || {x: 0, y: 0};
      e.position = {x: o.x + ((k % 3) - 1) * 30, y: o.y + Math.floor(k / 3) * 30};
    }
    const cy = cytoscape({container: box, elements, style: areaStyle(), minZoom: .25, maxZoom: 1.3, boxSelectionEnabled: false,
      autoungrabify: true, layout: {name: 'preset', fit: true, padding: 16}});
    cy.fit(undefined, 16);
    cy.on('tap', 'node', e => onTap(e.target.data()));
    return cy;
  }
  function peek(box, data, area) {
    box.hidden = false; box.replaceChildren();
    const close = el('button', '×', 'ce-close'); close.type = 'button'; close.addEventListener('click', () => { box.hidden = true; });
    if (data.kind !== 'cluster') { box.hidden = true; return; }
    const k = area.clusters.find(x => x.id === data.id); if (!k) { box.hidden = true; return; }
    box.append(close, el('p', k.in_brief ? 'IN THE BRIEF' : 'BEYOND THE BRIEF' + (k.why ? ' · ' + k.why : ''), 'eyebrow'), el('h3', k.title, 'serif'),
      el('p', `${plural(k.count, 'source')} live here`, 'small quiet'));
    const ul = el('ul', null, 'ce-peek-list');
    for (const i of [...k.sources, ...(k.also || [])].slice(0, 6)) {
      const s = area.sources[i] || {}; const li = el('li'); const a = el('a', s.title || i); a.href = readHref(i);
      li.append(a, el('span', ' — ' + (s.thesis || '').slice(0, 120), 'small quiet')); ul.append(li);
    }
    box.append(ul);
    if (k.id.startsWith('cluster:')) { const b = el('a', 'Brief on this area ↗', 'ce-link'); b.href = '/brief/?id=' + encodeURIComponent(k.id); box.append(b); }
  }
  function focusCluster(id) {
    if (!areaCy) return;
    const n = areaCy.getElementById(id); if (!n.length) return;
    areaCy.animate({fit: {eles: n, padding: 40}}, {duration: 300}); n.addClass('lit'); setTimeout(() => n.removeClass('lit'), 1600);
  }
  function mapCluster(id) {  // from a keyword or an idea's "In" chip: the mini map shows where that cluster is
    if (mapMode === 'ideas') showMap('area');
    focusCluster(id);
    if (matchMedia('(max-width: 1000px)').matches) $('map-block').scrollIntoView({behavior: 'smooth'});
  }
  const docs = {};  // item -> /api/brief/source/<id>, once per page
  function loadDoc(item) {
    if (!docs[item]) docs[item] = fetch(`/api/brief/source/${encodeURIComponent(item)}?${query}`).then(async r => {
      const b = await r.json().catch(() => ({})); if (!r.ok) throw new Error(b.error || `HTTP ${r.status}`); return b; });
    return docs[item];
  }
  function narrow(text, start, end, statement) {
    const words = t => new Set((t.toLowerCase().match(/[\p{L}\p{N}]{4,}/gu) || []));
    const want = words(statement);
    let best = [start, end], score = 0;
    const re = /[^.!?…\n]+[.!?…]*/g, part = text.slice(start, end);
    for (let m; (m = re.exec(part));) {
      const got = words(m[0]); let n = 0; for (const w of got) if (want.has(w)) n++;
      const sc = n / Math.max(4, want.size);
      if (sc > score) { score = sc; best = [start + m.index, start + m.index + m[0].length]; }
    }
    return score >= 0.25 ? best : [start, end];
  }
  function setState(id, value) { return post(`/api/source/${encodeURIComponent(id)}/state`, {state: value, ...subject}); }
  function nextList(groups) {
    const out = [];
    let n = 0;
    for (const g of groups) {
      const sec = el('section', null, 'ce-next-group');
      if (g.colour != null) sec.style.setProperty('--idea', ideaVar(g.colour));
      sec.append(el('h3', g.group.length > 90 ? g.group.slice(0, 89) + '…' : g.group, 'ce-next-title'));
      const ol = el('ol', null, 'ce-next');
      for (const r of g.rows) {
        n += 1;
        const li = el('li', null, r.read ? 'read' : '');
        const a = el('a', r.title); a.href = readHref(r.item);
        li.append(el('span', String(n), 'n'), a, el('p', r.thesis, 'thesis'),
          el('p', [r.why, r.source, r.read ? (r.read === 'read' ? '✓ read' : 'skimmed') : null].filter(Boolean).join(' · '), 'why'));
        ol.append(li);
      }
      sec.append(ol);
      const extra = [g.more ? `${g.more} more here` : '', g.rewrites ? `${plural(g.rewrites, 'rewrite')} not listed` : ''].filter(Boolean).join(' · ');
      if (extra) sec.append(el('p', extra, 'small quiet'));
      out.push(sec);
    }
    return out.length ? out : [el('p', 'Nothing further on this subject yet.', 'small quiet')];
  }

  let areaCy = null, mapMode = 'area';
  function mapTap(area) { return data => data.kind === 'source' ? open(data.item) : peek($('peek'), data, area); }
  function showMap(which) {
    mapMode = which;
    for (const [id, mode] of [['show-area', 'area'], ['show-full', 'full'], ['show-ideas', 'ideas']]) $(id).setAttribute('aria-pressed', String(which === mode));
    $('area-box').hidden = which === 'ideas'; $('ideas-box').hidden = which !== 'ideas'; $('peek').hidden = true;
    if (which === 'ideas') return;
    const area = which === 'full' ? c.area_full : c.area;
    if (areaCy) areaCy.destroy();
    areaCy = drawArea($('area'), area, null, mapTap(area));
    if (path.length) markHere(path[path.length - 1]);
    $('map-note').textContent = which === 'full'
      ? `${plural(area.clusters.length, 'cluster')} you have; the brief’s ${area.clusters.filter(k => k.in_brief).length} lit`
      : `${plural(area.clusters.filter(k => k.in_brief).length, 'cluster')} of the brief, ${area.clusters.filter(k => !k.in_brief).length} beyond`;
  }

  // --- the narrow panel: about the brief (map, people, organizations), or about the source you read ---
  const RELATION = {wrote: 'wrote it', quoted: 'quoted', about: 'facts about them', mentioned: 'named'};
  // Each badge opens the person or organization (whoOpen); one the brief ranks carries its place.
  const rankOf = id => { const n = (c.people || []).filter(r => r.raw > 0).findIndex(r => r.id === id); return n < 0 ? null : n + 1; };
  function badges(box, rows, item) {
    box.replaceChildren(...rows.slice(0, 30).map(r => {
      const rel = item ? r.relation : Object.values(r.items || {})[0], n = rankOf(r.id);
      const b = el('button', (n ? `#${n} ` : '') + r.name, 'badge ' + (rel || '')); b.type = 'button';
      b.title = item ? (RELATION[rel] || '') : Object.entries(r.items || {}).map(([i, x]) => `${RELATION[x] || x} in ${sid(i) || source(i).source || ''}`).join('; ');
      b.addEventListener('click', () => whoOpen(r.id, true));
      return b;
    }));
    if (!rows.length) box.append(el('span', 'none named', 'small quiet'));
  }
  // The brief's ranking (refresher/authority.py): writers and those they credit, first in the panel; a channel
  // stands for a writer the source does not name, so it has no profile to open.
  function ranked() {
    const rows = (c.people || []).filter(r => r.raw > 0).slice(0, 6);
    $('ranked-box').hidden = !rows.length;
    $('ranked').replaceChildren(...rows.map(r => {
      const li = el('li'), name = el(r.kind === 'channel' ? 'span' : 'button', r.name, 'ce-ranked-name');
      if (r.kind !== 'channel') { name.type = 'button'; name.addEventListener('click', () => whoOpen(r.id, true)); }
      const bar = el('span', null, 'ce-bar'); bar.style.setProperty('--w', `${Math.round(r.authority * 100)}%`);
      li.append(name, bar, el('span', r.kind === 'channel' ? `${r.why} · channel` : r.why, 'small quiet'));
      return li;
    }));
    const shown = new Set(rows.map(r => r.id));
    return c.details.people.filter(r => !shown.has(r.id));
  }
  // A claim: its kind as a small label (ideas are the only grouping), coloured by the idea it feeds.
  function claimButton(cl, onClick) {
    const li = el('li', null, cl.superseded ? 'superseded' : '');
    const b = el('button', null, 'ce-claim'); b.type = 'button'; b.dataset.claim = cl.id;
    b.append(el('span', cl.mark && cl.mark.kind === 'lent' ? `lent · ${cl.kind || 'fact'}` : (cl.kind || 'fact'), 'ce-kind-label'), document.createTextNode(cl.statement));
    if (cl.idea != null) { b.style.setProperty('--idea', ideaVar(cl.idea)); b.classList.add('in-brief'); }
    b.addEventListener('click', onClick);
    li.append(b);
    return li;
  }
  function group(title, rows, open, colour) {
    const det = el('details', null, 'ce-group'); det.open = open;
    if (colour != null) det.style.setProperty('--idea', ideaVar(colour));
    det.append(el('summary', `${title} · ${rows.length}`));
    const ul = el('ul', null, 'ce-claims'); rows.forEach(r => ul.append(r)); det.append(ul);
    return det;
  }
  function briefSide() {
    const rest = ranked();
    $('people-label').textContent = $('ranked-box').hidden ? 'People' : 'Also named';
    badges($('people'), rest); badges($('orgs'), c.details.organizations);
    $('claims').replaceChildren();
  }
  function sourceSide(doc, claims) {
    $('ranked-box').hidden = true; $('people-label').textContent = 'People';
    badges($('people'), doc.people || [], doc.id); badges($('orgs'), doc.organizations || [], doc.id);
    // Grouped by the idea each claim feeds or lends a fact to; the rest, folded, as not in the brief.
    const groups = [];
    c.major.forEach((x, n) => {
      const mine = claims.filter(cl => cl.mark && cl.mark.idea === n);
      if (mine.length) groups.push(group(`Idea ${n + 1} · ${x.sources[doc.id] || 'lends facts'}`, mine.map(row), true, n));
    });
    const rest = claims.filter(cl => !cl.mark);
    if (rest.length) groups.push(group('Not in the brief', rest.map(row), !groups.length && rest.length <= 8));
    $('claims').replaceChildren(...groups);
    if (!groups.length) $('claims').append(el('p', 'No claims were extracted from this source.', 'small quiet'));
    function row(cl) {
      const li = claimButton({...cl, idea: cl.mark ? cl.mark.idea : null}, () => litClaim(cl.id, true));
      const b = li.querySelector('button');
      const on = () => { hover(cl.id, true); reveal(cl.id); }, off = () => { hover(cl.id, false); clearTimeout(revealing); };
      b.addEventListener('mouseenter', on); b.addEventListener('mouseleave', off);
      b.addEventListener('focus', on); b.addEventListener('blur', off);
      return li;
    }
  }
  // Hovering a claim brings its passage into view, after a pause, so passing over the list does not scroll the text.
  let revealing = null;
  function reveal(id) {
    clearTimeout(revealing);
    revealing = setTimeout(() => {
      const p = passesOf(id)[0]; if (!p) return;
      const r = p.getBoundingClientRect();
      if (r.top < 130 || r.bottom > innerHeight - 20) p.scrollIntoView({behavior: 'smooth', block: 'center'});
    }, 250);
  }
  function sourcesList() {
    const ids = c.written.ids, current = path[path.length - 1];
    const order = Object.keys(c.sources).sort((a, b) => (ids[a] ? +ids[a].slice(1) : 999) - (ids[b] ? +ids[b].slice(1) : 999) || a.localeCompare(b));
    for (const i of path) if (!order.includes(i)) order.push(i);  // sources outside the brief you opened
    $('sources').replaceChildren(...order.map(i => {
      const li = el('li', null, i === current ? 'current' : path.includes(i) ? 'on-path' : '');
      const a = el('a', source(i).title || (docs[i] ? '…' : i)); a.href = readHref(i);
      if (!source(i).title && docs[i]) docs[i].then(d => { a.textContent = d.title; }).catch(() => {});
      li.append(el('span', sid(i) || '·', 'sid'), a);
      const roles = el('span', null, 'roles');
      c.major.forEach((x, n) => { if (x.sources[i]) { const ch = el('span', `${x.sources[i]} · ${n + 1}`, 'chip role'); ch.style.setProperty('--idea', ideaVar(n)); roles.append(ch); } });
      li.append(roles); return li;
    }));
  }

  // --- reading: a path of sources (the breadcrumb); the brief slides into the rail, the source takes the centre ---
  let path = [], lit = null, overviewScroll = 0, current = null;
  const short = (t, n) => t && t.length > n ? t.slice(0, n - 1) + '…' : (t || '');
  function go(next, claim = null, push = true) {
    if (push) window.RefresherJourney?.position();
    if (!path.length && next.length) overviewScroll = window.scrollY;
    path = next.filter(Boolean); lit = claim;
    $('tab-sources').href = '/topic/?' + query + (path.length ? '&read=' + encodeURIComponent(path[path.length - 1]) : '');
    if (push) {
      const u = new URL(location.href);
      if (path.length) u.searchParams.set('read', path.join(',')); else u.searchParams.delete('read');
      window.history.pushState({read: path.join(',')}, '', u);
    }
    show();
  }
  function open(item, claim = null) {
    if (!path.length) return go([item], claim);
    if (item === path[path.length - 1]) return claim ? litClaim(claim, true) : null;
    const at = path.indexOf(item);
    go(at >= 0 ? path.slice(0, at + 1) : [...path, item], claim);
  }
  function back() { go(path.slice(0, -1)); }
  function layout() {
    // Three tracks, the brief | the source | the narrow panel, in percentages so the change between states animates:
    // the brief narrows into the rail as the source opens beside it; the narrow panel keeps its width (the map stays put).
    const grid = $('grid');
    if (matchMedia('(max-width: 1000px)').matches) { grid.style.gridTemplateColumns = ''; return; }
    const W = grid.clientWidth || 1200, GAP = 14, side = Math.min(360, Math.max(290, 0.24 * W)), rail = 240;
    const pc = px => (100 * px / W).toFixed(2) + '%';
    grid.style.gridTemplateColumns = path.length
      ? `${pc(rail)} ${pc(W - rail - side - 2 * GAP)} ${pc(side)}`
      : `${pc(W - side - 2 * GAP)} 0% ${pc(side)}`;
  }
  async function show() {
    const reading = path.length > 0;
    $('grid').classList.toggle('reading', reading);
    document.body.classList.toggle('ce-reading', reading);
    $('esc-hint').hidden = !reading;
    $('context-toggle').hidden = !reading;
    $('subject-start').hidden = reading || !Object.keys(c.sources).length;
    contextDrawer(false);
    layout();
    crumbs([]);
    sourcesList();
    if (!reading) {
      document.querySelectorAll('.ce-idea').forEach(card => { card.classList.remove('here'); card.querySelector('.ce-here').textContent = ''; });
      $('source').replaceChildren(); current = null;
      briefSide(); markHere(null);
      setTimeout(() => window.scrollTo(0, overviewScroll), 380);
      return;
    }
    const opening = path.join(',');
    const opened = await Promise.all(path.map(i => loadDoc(i).catch(e => ({error: e.message, id: i, title: i}))));
    if (opening !== path.join(',')) return;
    const doc = opened[opened.length - 1];
    current = doc;
    crumbs(opened);
    sourcesList();
    c.major.forEach((x, n) => {  // the ideas this source serves, marked where they stand
      const card = $('idea-' + (n + 1)); if (!card) return;
      const role = x.sources[doc.id] || ((x.adjacent || []).some(a => a.item === doc.id) ? 'lends facts' : '');
      card.classList.toggle('here', !!role); card.querySelector('.ce-here').textContent = role ? `here · ${role}` : '';
    });
    if (doc.error) { $('source').replaceChildren(el('p', doc.error, 'quiet')); return; }
    const claims = claimsOf(doc);
    sourcePanel(doc, claims);
    sourceSide(doc, claims);
    markHere(doc.id);
    window.scrollTo({top: 0});
    if (lit) requestAnimationFrame(() => litClaim(lit, true));
    window.RefresherJourney?.reading(doc.title);
  }
  function crumbs(opened) {
    const items = [];
    const root = el('li'); const rootLink = el('a', 'Home'); rootLink.href = '/'; root.append(rootLink); items.push(root);
    const home = el('li');
    if (path.length) { const a = el('a', `← Back to ${short(c.scope.title, 40)}`); a.href = '#'; a.addEventListener('click', e => { e.preventDefault(); go([]); }); home.append(a); }
    else home.append(el('strong', short(c.scope.title, 60)));
    items.push(home);
    opened.forEach((d, k) => {
      const li = el('li'), label = `${sid(path[k]) || ''} ${short(d.title || '', 34)}`.trim();
      if (k === opened.length - 1) li.append(el('strong', label));
      else { const a = el('a', label); a.href = '#'; a.addEventListener('click', e => { e.preventDefault(); go(path.slice(0, k + 1)); }); li.append(a); }
      items.push(li);
    });
    $('crumbs').replaceChildren(...items);
  }
  function markHere(item, cy = areaCy) {
    if (!cy) return;
    cy.nodes('[kind="source"]').forEach(n => n.data('here', n.data('item') === item ? 1 : 0));
  }
  // The mini map, larger: whatever it shows (this area, the full graph or the ideas), in a dialog to pan and zoom.
  // A source opens in the page and closes the dialog; a cluster peeks inside it.
  let bigCy = null;
  function zoomMap() {
    const dialog = $('map-dialog'), box = $('map-big');
    $('map-dialog-title').textContent = {area: 'This area', full: 'Full graph', ideas: 'Ideas'}[mapMode];
    $('map-dialog-note').textContent = mapMode === 'ideas' ? '' : $('map-note').textContent;
    $('peek-big').hidden = true;
    box.replaceChildren();
    dialog.showModal();
    if (mapMode === 'ideas') {
      const copy = $('constellation').cloneNode(true); copy.removeAttribute('id');
      copy.querySelectorAll('g.idea').forEach(g => g.addEventListener('click', () => {
        dialog.close(); $('constellation').querySelector(`g.idea[data-idea="${CSS.escape(g.dataset.idea)}"]`)?.dispatchEvent(new MouseEvent('click'));
      }));
      box.append(copy);
      return;
    }
    const area = mapMode === 'full' ? c.area_full : c.area;
    bigCy = drawArea(box, area, null, data => {
      if (data.kind === 'source') { dialog.close(); open(data.item); } else peek($('peek-big'), data, area);
    });
    markHere(path[path.length - 1], bigCy);
  }
  $('map-zoom').addEventListener('click', zoomMap);
  $('map-dialog').addEventListener('close', () => { if (bigCy) { bigCy.destroy(); bigCy = null; } });
  // The claims of a source, each at its best-matching sentence (the recorded passage is often a whole paragraph).
  function claimsOf(doc) {
    const by = {};
    for (const s of doc.spans) {
      if (by[s.claim]) continue;
      const [a, b] = narrow(doc.text, s.start, s.end, s.statement);
      by[s.claim] = {id: s.claim, start: a, end: b, statement: s.statement, kind: doc.kinds?.[s.claim] || 'fact', mark: doc.marked[s.claim] || null};
    }
    return Object.values(by).sort((x, y) => x.start - y.start || x.end - y.end);
  }
  // Plain text with claim sentences wrapped; what the brief takes is tinted by its idea (lent facts underlined) unless hidden.
  function sourceText(doc, claims) {
    const box = el('div', null, 'ce-dense');
    const text = doc.text || '';
    if (!text) { box.append(el('p', 'The text of this source is not in the vault.', 'quiet')); return box; }
    const cuts = [...new Set([0, text.length, ...claims.flatMap(x => [x.start, x.end])])].sort((a, b) => a - b);
    const squeeze = t => t.replace(/[ \t]*\n[ \t]*(\n[ \t]*)+/g, '\n');
    for (let k = 0; k + 1 < cuts.length; k++) {
      const [a, b] = [cuts[k], cuts[k + 1]], piece = squeeze(text.slice(a, b));
      const over = claims.filter(x => x.start < b && x.end > a);
      if (!over.length) { box.append(document.createTextNode(piece)); continue; }
      const top = over.find(x => x.mark && x.mark.kind === 'thesis') || over.find(x => x.mark) || over[0];
      const span = el('span', piece, 'pass' + (top.mark ? ' takes ' + top.mark.kind : ''));
      span.dataset.claims = over.map(x => x.id).join(' ');
      if (top.mark) span.style.setProperty('--idea', ideaVar(top.mark.idea));
      box.append(span);
    }
    return box;
  }
  const passesOf = id => [...document.querySelectorAll('#source .pass')].filter(p => p.dataset.claims.split(' ').includes(id));
  function hover(id, on) { passesOf(id).forEach(p => p.classList.toggle('hover', on)); }
  function litClaim(id, scroll) {
    lit = id;
    document.querySelectorAll('#source .pass.lit').forEach(p => p.classList.remove('lit'));
    document.querySelectorAll('#side .ce-claim.lit').forEach(b => b.classList.remove('lit'));
    const passes = passesOf(id);
    passes.forEach(p => p.classList.add('lit'));
    const b = document.querySelector(`#side .ce-claim[data-claim="${CSS.escape(id)}"]`);
    if (b) { b.classList.add('lit'); const d = b.closest('details'); if (d) d.open = true; }
    if (scroll && passes[0]) passes[0].scrollIntoView({behavior: 'smooth', block: 'center'});
  }
  // As the text scrolls, the narrow panel marks the claims whose sentences are on screen.
  let watcher = null;
  function follow(textBox) {
    if (watcher) watcher.disconnect();
    const seen = new Map();
    watcher = new IntersectionObserver(entries => {
      for (const e of entries) for (const id of e.target.dataset.claims.split(' ')) {
        const n = (seen.get(id) || 0) + (e.isIntersecting ? 1 : -1); seen.set(id, Math.max(0, n));
      }
      document.querySelectorAll('#side .ce-claim').forEach(b => b.classList.toggle('in-view', (seen.get(b.dataset.claim) || 0) > 0));
    }, {rootMargin: '-90px 0px -15% 0px'});
    textBox.querySelectorAll('.pass').forEach(p => watcher.observe(p));
  }
  let showTakes = true;  // what the brief takes is tinted by its idea unless you hide it
  function sourcePanel(doc, claims) {
    const box = $('source');
    const meta = [doc.source, doc.author && doc.author !== doc.source ? doc.author : null, day(doc.time)].filter(Boolean).join(' · ');
    const head = el('header', null, 'ce-source-head');
    head.append(el('p', meta, 'small quiet'), el('h1', doc.title, 'serif'));
    const tools = el('div', null, 'ce-chips');
    for (const r of doc.roles) { const ch = el('span', `${r.role} of idea ${r.idea + 1}`, 'chip role'); ch.style.setProperty('--idea', ideaVar(r.idea)); tools.append(ch); }
    const takes = el('button', showTakes ? 'Hide what the brief takes' : 'Show what the brief takes', 'ce-link-button'); takes.type = 'button';
    takes.addEventListener('click', () => { showTakes = !showTakes; text.classList.toggle('show-takes', showTakes); takes.textContent = showTakes ? 'Hide what the brief takes' : 'Show what the brief takes'; });
    const done = el('button', doc.read === 'read' ? '✓ Read' : 'Mark read', 'ce-link-button'); done.type = 'button';
    done.addEventListener('click', async () => {
      const value = doc.read === 'read' ? 'unread' : 'read'; done.disabled = true;
      try { await setState(doc.id, value); doc.read = value; done.textContent = value === 'read' ? '✓ Read · undo' : 'Mark read'; }
      catch (e) { $('notice').textContent = 'Reading status was not saved. ' + e.message; }
      finally { done.disabled = false; }
    });
    tools.append(takes, done);
    const explore = el('a', 'Explore connections →', 'ce-link');
    explore.href = '/atlas/?item=' + encodeURIComponent(doc.id) + '&return=' + encodeURIComponent(location.pathname + location.search);
    tools.append(explore);
    if (doc.url) { const a = el('a', 'Original ↗', 'ce-link'); a.href = doc.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; tools.append(a); }
    head.append(tools);
    const text = sourceText(doc, claims);
    text.classList.toggle('show-takes', showTakes);
    const count = doc.next.reduce((n, g) => n + g.rows.length, 0);
    const further = el('button', `↘ Go further: ${plural(count, 'related source')}`, 'ce-link-button ce-further'); further.type = 'button';
    further.addEventListener('click', furtherDialog);
    const ids = Object.keys(c.sources).sort((a,b) => Number((sid(a)||'S999').slice(1))-Number((sid(b)||'S999').slice(1))), index = ids.indexOf(doc.id), next = ids[index + 1];
    const actions = el('div', null, 'journey-actions');
    if (next) { const a = el('a', 'Next: ' + (source(next).title || 'source'), 'journey-primary'); a.href = readHref(next); actions.append(a); }
    box.replaceChildren(head, text, actions, further);
    follow(text);
  }
  // A person or organization, as the Atlas opens one: where they stand in this brief and why, their sources, what
  // is said about them, and a mini map of the organizations and people tied to them. A click on the map opens that
  // one in the same dialog; the back button walks the trail.
  const TIED = {wrote: 'wrote', quoted: 'quoted', about: 'about them', mentioned: 'named'};
  let whoTrail = [], whoCy = null;
  async function whoOpen(id, fresh) {
    if (fresh) whoTrail = [];
    if (whoTrail[whoTrail.length - 1] !== id) whoTrail.push(id);
    const dialog = $('who');
    if (!dialog.open) dialog.showModal();
    $('who-body').replaceChildren(el('p', 'Reading what you know of them…', 'small quiet'));
    let p;
    try {
      const r = await fetch(`/api/brief/entity?${query}&entity=${encodeURIComponent(id)}`);
      p = await r.json(); if (!r.ok) throw new Error(p.error || `HTTP ${r.status}`);
    } catch (e) { $('who-body').replaceChildren(el('p', e.message, 'small quiet')); return; }
    if (whoTrail[whoTrail.length - 1] !== id) return;  // another click came first
    const prev = whoTrail[whoTrail.length - 2];
    $('who-back').hidden = !prev; $('who-back').textContent = '← back';
    $('who-title').textContent = p.name;
    $('who-kind').textContent = p.kind === 'person' ? 'PERSON' : `ORGANIZATION · ${(p.type || '').toUpperCase()}`;
    const body = [], st = p.standing;
    const links = [p.role, p.affiliation].filter(Boolean).map(f => { const a = el('a', `${f.value} ↗`); a.href = f.url; a.target = '_blank'; a.rel = 'noopener'; return a; });
    if (links.length) { const line = el('p', null, 'ce-who-links'); line.append(...links); body.push(line); }
    const stand = el('div', null, 'ce-who-standing');
    if (st.rank) {
      stand.append(el('p', `#${st.rank} of ${st.of} to listen to in this brief`, 'ce-who-rank serif'),
        el('p', st.why + (st.reach ? ` · in ${plural(st.reach, 'source')} of the brief` : ''), 'small quiet'));
    } else stand.append(el('p', st.note, 'small'));
    const k = st.contribution;
    if (k) stand.append(el('p', k.importance == null
      ? 'Contribution score: unscored, no accepted statements attributed to them on these Ideas.'
      : `Contribution score ${k.importance.toFixed(2)} (#${k.rank}) · track record ${k.track_record.toFixed(2)} · ${k.evidence_level} evidence`, 'small quiet'));
    body.push(stand);
    // the mini map, then the same neighbours as buttons for the keyboard
    const map = el('div', null, 'ce-who-map'); map.setAttribute('role', 'img');
    map.setAttribute('aria-label', `${p.name} with the organizations and people tied to them`);
    const near = [...p.organizations, ...p.people];
    body.push(near.length ? map : el('p', 'No organizations or people tied to them yet.', 'small quiet'));
    for (const [title, rows] of [['Organizations', p.organizations], ['People', p.people]]) {
      if (!rows.length) continue;
      const row = el('p', null, 'ce-row'); row.append(el('span', title, 'label'));
      const box = el('span', null, 'ce-badges' + (title === 'Organizations' ? ' orgs' : ''));
      rows.forEach(t => { const b = el('button', (t.rank ? `#${t.rank} ` : '') + t.name, 'badge' + (t.in_brief ? '' : ' mentioned')); b.type = 'button';
        b.title = t.how.join('; '); b.addEventListener('click', () => whoOpen(t.id)); box.append(b); });
      row.append(box); body.push(row);
    }
    if (p.sources.length) {
      body.push(el('h3', 'Sources', 'ce-section'));
      const ul = el('ul', null, 'ce-who-list');
      p.sources.forEach(s => { const li = el('li', null, s.in_brief ? 'in-brief' : ''); const a = el('a', s.title || s.item); a.href = readHref(s.item);
        li.append(el('span', TIED[s.relation] || s.relation, 'ce-kind-label'), a,
          el('span', ` · ${[s.in_brief && sid(s.item), s.source, day(s.when)].filter(Boolean).join(' · ')}`, 'small quiet')); ul.append(li); });
      if (p.more_sources) ul.append(el('li', `and ${plural(p.more_sources, 'more source')}`, 'small quiet'));
      body.push(ul);
    }
    for (const [title, rows, more] of [['What they say', p.statements, 0], ['What is said about them', p.facts, p.more_facts]]) {
      if (!rows.length) continue;
      body.push(el('h3', title, 'ce-section'));
      const ul = el('ul', null, 'ce-who-list');
      rows.forEach(f => { const li = el('li', null, 'serif'); const a = el('a', sid(f.item) || 'source'); a.href = readHref(f.item); a.className = 'cite';
        li.append(document.createTextNode(f.statement + ' '), a); ul.append(li); });
      if (more) ul.append(el('li', `and ${plural(more, 'more fact')}`, 'small quiet'));
      body.push(ul);
    }
    $('who-body').replaceChildren(...body);
    if (whoCy) { whoCy.destroy(); whoCy = null; }
    if (near.length && typeof cytoscape === 'function') whoCy = whoMap(map, p);
  }
  function whoMap(box, p) {
    const css = v => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
    const label = t => (t.rank ? `#${t.rank} ` : '') + (t.name.length > 28 ? t.name.slice(0, 27) + '…' : t.name);
    // Organizations on an arc to the right, people to the left, both spread over the box's own shape.
    const W = Math.max(box.clientWidth, 560), H = Math.max(box.clientHeight, 280), rx = W / 2 - 70, ry = H / 2 - 20;
    const arc = (rows, right) => rows.map((t, k) => {
      const a = rows.length === 1 ? 0 : (-70 + 140 * k / (rows.length - 1)) * Math.PI / 180;
      return {t, x: W / 2 + (right ? 1 : -1) * rx * Math.cos(a), y: H / 2 + ry * Math.sin(a)};
    });
    const placed = [...arc(p.organizations, p.kind !== 'organization'), ...arc(p.people, p.kind === 'organization')];
    const elements = [{data: {id: p.id, label: label({...p, rank: p.standing.rank}), kind: p.kind, centre: 1}, position: {x: W / 2, y: H / 2}},
      ...placed.flatMap(({t, x, y}) => [
        {data: {id: t.id, label: label(t), kind: t.kind, faint: t.in_brief ? 0 : 1}, position: {x, y}},
        {data: {id: `${p.id}>${t.id}`, source: p.id, target: t.id, label: t.how.find(h => !h.endsWith('together')) || '', width: Math.min(4, 1 + t.weight / 2)}}])];
    const cy = cytoscape({container: box, elements, minZoom: .3, maxZoom: 1.6, boxSelectionEnabled: false, autoungrabify: true,
      style: [
        {selector: 'node', style: {'label': 'data(label)', 'text-valign': 'center', 'text-wrap': 'wrap', 'text-max-width': 110, 'font-size': 10,
          'shape': 'round-rectangle', 'width': 120, 'height': 24, 'background-color': css('--r-graph-person') || '#fbe2d8',
          'border-width': 1, 'border-color': css('--r-graph-person-outline') || '#c77560', 'color': css('--r-ink') || '#25251f'}},
        {selector: 'node[kind="organization"]', style: {'shape': 'hexagon', 'background-color': css('--r-graph-org') || '#e2eaf1',
          'border-color': css('--r-graph-org-outline') || '#5f7f99'}},
        {selector: 'node[centre=1]', style: {'border-width': 3, 'font-weight': 600, 'width': 140, 'height': 32}},
        {selector: 'node[faint=1]', style: {'opacity': .55, 'border-style': 'dashed'}},
        {selector: 'edge', style: {'width': 'data(width)', 'line-color': css('--r-graph-edge') || '#a4a8b6', 'curve-style': 'straight',
          'label': 'data(label)', 'font-size': 8, 'color': css('--r-muted') || '#67665e', 'text-background-color': css('--r-paper') || '#fffcf5',
          'text-background-opacity': 1, 'text-background-padding': 2}}],
      layout: {name: 'preset', fit: true, padding: 12}});
    cy.on('tap', 'node', e => { if (!e.target.data('centre')) whoOpen(e.target.id()); });
    cy.on('mouseover', 'node', e => { if (!e.target.data('centre')) box.style.cursor = 'pointer'; });
    cy.on('mouseout', 'node', () => { box.style.cursor = ''; });
    return cy;
  }
  $('who-back').addEventListener('click', () => { whoTrail.pop(); const id = whoTrail.pop(); if (id) whoOpen(id); });
  $('who').addEventListener('close', () => { if (whoCy) { whoCy.destroy(); whoCy = null; } whoTrail = []; });

  // While reading, further reading is a dialog: from the source you are on, then on the brief as a whole.
  function furtherDialog() {
    const body = $('further-from'); body.replaceChildren();
    if (current && !current.error) body.append(el('h3', `From ${sid(current.id) || ''} ${short(current.title || '', 60)}`.trim(), 'ce-section'), ...nextList(current.next));
    body.append(el('h3', 'On the brief', 'ce-section'), ...nextList(c.further || []));
    $('further-dialog').showModal();
  }
  $('further-open').addEventListener('click', furtherDialog);
  window.addEventListener('resize', () => { layout(); if (matchMedia('(min-width: 1001px)').matches) contextDrawer(false); });

  // A source link anywhere on the page: from the overview it starts a path; from a source it goes one step further.
  document.addEventListener('click', e => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
    const u = new URL(a.href, location.href);
    const same = (u.searchParams.get('q') || '') === (subject.q || '') && (u.searchParams.get('id') || '') === (subject.id || '');
    if (u.pathname !== '/brief/' || !u.searchParams.get('read') || !same) return;
    e.preventDefault();
    const box = a.closest('dialog'); if (box) box.close();
    const item = u.searchParams.get('read').split(',').pop();
    open(item);
  });
  window.addEventListener('popstate', () => { const r = new URLSearchParams(location.search).get('read'); go(r ? r.split(',') : [], null, false); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && path.length && !document.querySelector('dialog[open]')) { e.preventDefault(); back(); }
  });
  document.querySelectorAll('.ce-fold').forEach(b => b.addEventListener('click', () => {
    const open = b.getAttribute('aria-expanded') !== 'true';
    b.setAttribute('aria-expanded', String(open)); $(b.getAttribute('aria-controls')).hidden = !open;
  }));
  $('show-area').addEventListener('click', () => showMap('area'));
  $('show-full').addEventListener('click', () => showMap('full'));
  $('show-ideas').addEventListener('click', () => showMap('ideas'));

  function render() {
    document.title = `Refresher — ${c.scope.title}`;
    $('title').textContent = c.scope.kind === 'query' ? `“${c.scope.title}”` : c.scope.title;
    keywords();
    // The range is every item you have on it, earliest to now; choosing a narrower range comes later.
    const k = c.counts, dates = Object.values(c.sources).map(s => s.when).filter(Boolean).sort();
    $('meta').textContent = [`As of ${day(c.at)}`, dates.length ? `sources ${day(dates[0])} → now` : null,
      `${plural(k.ideas, 'idea')} from ${plural(k.sources, 'source')}`].filter(Boolean).join(' · ');
    $('meta').title = `${plural(k.rewrites, 'rewrite')} collapsed · ${k.superseded} superseded`;
    $('write').hidden = !c.written.missing;
    since(c.thread); versions(c.thread);
    $('follow-subject').textContent = c.thread ? 'Following this subject' : 'Follow subject';
    $('follow-subject').disabled = Boolean(c.thread);
    window.RefresherJourney?.subject(c.scope.title);
    const first = Object.keys(c.sources).sort((a,b) => (Number((sid(a)||'S999').slice(1))-Number((sid(b)||'S999').slice(1))))[0];
    if (first) {
      const resume = window.RefresherJourney?.data.resume;
      const previous = resume && window.RefresherJourney.safe(resume.href) && new URL(resume.href, location.origin);
      const same = previous?.pathname === '/brief/' && (previous.searchParams.get('id') || '') === (subject.id || '') && (previous.searchParams.get('q') || '') === (subject.q || '');
      const last = same && previous.searchParams.get('read')?.split(',').pop();
      $('subject-start').href = last && c.sources[last] ? resume.href : readHref(first);
      $('subject-start').textContent = last && c.sources[last] ? 'Continue reading →' : 'Start reading →';
      $('subject-start').hidden = false;
    }
    if (!c.thread?.stamps?.length) { $('since').hidden = false; $('since').replaceChildren(el('p', 'First overview — save a checkpoint to compare future changes.', 'ce-since-line')); }
    $('ideas').replaceChildren(...c.major.map((x, n) => ideaCard(x, n + 1)));
    $('ideas-note').textContent = plural(c.major.length, 'major idea');
    $('also-box').hidden = !c.also.length;
    $('also').replaceChildren(...c.also.map(x => { const li = el('li'); li.append(el('span', x.title), document.createTextNode(' '), chip(x.base, 'muted')); return li; }));
    $('further').replaceChildren(...nextList(c.further || []));
    constellation(c.major);
    $('top').hidden = false; $('grid').hidden = false;
    showMap((c.area?.clusters || []).filter(k => k.in_brief).length > 1 ? 'area' : 'full');
    $('notice').textContent = c.major.length ? '' : 'Nothing in your knowledge is on this subject yet.';
    const reading = new URLSearchParams(location.search).get('read');
    go(reading ? reading.split(',') : [], null, false);
  }

  function load() {
    return fetch('/api/brief?' + query).then(async r => {
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw Object.assign(new Error(body.error || `HTTP ${r.status}`), {named: body.named || []});
      c = body; render();
      return c;
    });
  }

  async function writeBrief(auto) {
    $('write').disabled = true; $('write').textContent = 'Writing…';
    $('notice').textContent = `Writing ${plural(c.written.missing, 'paragraph')} from your sources…`;
    try { await post('/api/brief/write', subject); await load(); }
    catch (e) { $('notice').textContent = auto ? `The theses stand in for the paragraphs: ${e.message}` : e.message; }
    finally { $('write').disabled = false; $('write').textContent = 'Write the brief'; }
  }

  // Keywords: what you asked, then the topics and named clusters the brief spans (each shows itself on the map; a
  // cluster still titled by a sentence of its source is left out). People and organizations are in the narrow panel.
  function keywords() {
    const box = $('keywords'); box.replaceChildren();
    if (c.scope.kind === 'query') box.append(el('span', c.scope.title, 'kw query'));
    const area = c.area || {clusters: [], topics: []}, mine = new Set(area.clusters.filter(k => k.in_brief).map(k => k.id));
    const named = [...(area.topics || []).filter(t => t.clusters.some(k => mine.has(k))), ...area.clusters.filter(k => k.in_brief)]
      .filter(k => k.title && k.title.length <= 40 && k.title.split(/\s+/).length <= 5 && !/[.!?:]$/.test(k.title));
    for (const k of named) {
      const b = el('button', k.title, 'kw area'); b.type = 'button';
      b.addEventListener('click', () => mapCluster(k.id)); box.append(b);
    }
    box.hidden = !box.childNodes.length;
  }

  // Save version: this brief as of now, kept with its date (a thread is started on the first save).
  $('save').addEventListener('click', async () => {
    const b = $('save'); b.disabled = true; b.textContent = 'Saving…';
    try {
      const slug = c.thread ? c.thread.thread.slug : (await post('/api/threads', {...subject, title: c.scope.title})).slug;
      const s = await post(`/api/threads/${encodeURIComponent(slug)}/stamp`, {});
      await load(); $('notice').textContent = `Saved this version, as of ${day(s.at)}.`;
    } catch (e) { $('notice').textContent = e.message; }
    finally { b.disabled = false; b.textContent = 'Save checkpoint'; }
  });
  $('follow-subject').addEventListener('click', async () => {
    const b = $('follow-subject'); b.disabled = true;
    try { await post('/api/threads', {...subject, title: c.scope.title}); await load(); $('notice').textContent = 'Following this subject. Save a checkpoint when you want to compare future changes.'; }
    catch (e) { b.disabled = false; $('notice').textContent = e.message; }
  });
  function contextDrawer(open) {
    $('side').classList.toggle('context-open', open); $('context-toggle').setAttribute('aria-expanded', String(open));
    if (open) { $('side').setAttribute('role', 'dialog'); $('side').setAttribute('aria-modal', 'true'); $('context-close').focus(); }
    else { $('side').removeAttribute('role'); $('side').removeAttribute('aria-modal'); }
  }
  $('context-toggle').addEventListener('click', () => contextDrawer(true));
  $('context-close').addEventListener('click', () => { contextDrawer(false); $('context-toggle').focus(); });
  document.addEventListener('keydown', e => {
    if (!$('side').classList.contains('context-open')) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); contextDrawer(false); $('context-toggle').focus(); }
    if (e.key === 'Tab') {
      const nodes = [...$('side').querySelectorAll('a[href],button:not([disabled]),[tabindex="0"]')].filter(n => n.getClientRects().length);
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }, true);
  $('versions').addEventListener('click', e => { e.stopPropagation(); toggleVersions($('versions-list').hidden); });
  document.addEventListener('click', e => { if (!e.target.closest('.ce-versions')) toggleVersions(false); });
  $('write').addEventListener('click', () => writeBrief(false));
  document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));

  if (!subject.id && !subject.q) { $('notice').textContent = 'Ask about a subject from the home page.'; return; }
  load().catch(error => {
    $('notice').textContent = '';
    $('notice').append(el('p', error.message, 'serif'));
    if (error.named?.length) {
      const list = el('ul');
      error.named.forEach(x => { const li = el('li'); const a = el('a', x.title); a.href = '/library/?source=' + encodeURIComponent(x.id); li.append(a, el('span', ` · ${x.source || ''} · names ${x.names.join(', ')}`, 'small quiet')); list.append(li); });
      $('notice').append(el('p', 'Sources that name what you asked about:', 'quiet'), list);
    }
  });
})();

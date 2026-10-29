/* The brief (/brief/?q=|id=): what survives to now on a subject (refresher/context.py), what arrived since your last
   stamp (refresher/threads.py), the constellation of major ideas and whom to listen to. Values only enter textContent. */
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
  let trail = [];  // the sources read here, in order
  let c = null;  // the context as served
  const post = (path, body) => fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Refresher': '1'},
    body: JSON.stringify(body || {})}).then(async r => { const b = await r.json().catch(() => ({})); if (!r.ok) throw new Error(b.error || `HTTP ${r.status}`); return b; });

  $('tab-sources').href = '/topic/?' + query;
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
    const head = el('div', null, 'ce-chips');
    head.append(el('span', `IDEA ${n}`, 'ce-num'));
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
        b.addEventListener('click', () => { showMap('area'); focusCluster(k.id); }); return b; }));
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

  function people(rows) {
    $('people').replaceChildren(...rows.map(r => {
      const li = el('li');
      const who = el('div', null, 'who');
      who.append(el('strong', r.name), el('span', r.kind === 'channel' ? 'channel' : (r.evidence_level || ''), 'small quiet'));
      const bars = el('div', null, 'bars');
      const bar = el('div', null, 'bar'); const fill = el('span'); fill.style.width = Math.round(100 * r.authority) + '%'; bar.append(fill);
      bar.title = `authority ${Math.round(100 * r.authority)} of 100`;
      bars.append(bar, el('span', `named in ${plural(r.reach, 'source')}`, 'reach'));
      li.append(who, bars, el('p', r.why + (r.also_named ? ` (also written ${r.also_named.join(', ')})` : ''), 'why'));
      return li;
    }));
    if (!rows.length) $('people').append(el('li', 'No writer here is known yet.', 'small quiet'));
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

  function since(thread) {
    const d = thread && thread.delta;
    if (!d) { $('since').hidden = true; return; }
    $('since').hidden = false;
    const noise = Object.values(d.noise).reduce((n, rows) => n + rows.length, 0);
    $('since-title').textContent = d.arrived
      ? `Since your stamp of ${day(d.since)}: ${plural(d.arrived, 'source')} arrived — ${d.critical.length} critical, ${plural(d.notes.length, 'note')}, ${noise} noise`
      : `Nothing new on this since your stamp of ${day(d.since)}.`;
    $('critical').replaceChildren(...d.critical.map(r => arrival(r, 'critical')));
    $('notes').replaceChildren(...d.notes.map(r => arrival(r, 'note')));
    const reasons = {retells: ['retells what you know', 'retell what you know'],
      outdated: ['is an older view already replaced', 'are older views already replaced'],
      near: ['is only near the subject', 'are only near the subject']};
    $('noise').replaceChildren(...Object.entries(d.noise).map(([k, rows]) => {
      const det = el('details', null, 'ce-noise');
      det.append(el('summary', `${plural(rows.length, 'source')} ${(reasons[k] || [k, k])[rows.length === 1 ? 0 : 1]}`));
      const ul = el('ul'); rows.forEach(r => { const li = el('li'); const a = el('a', r.title); a.href = readHref(r.item); li.append(a, document.createTextNode(` · ${r.source || ''}`)); ul.append(li); });
      det.append(ul); return det;
    }));
  }

  function stamps(thread) {
    if (!thread || !thread.stamps.length) { $('stamps-box').hidden = true; return; }
    $('stamps-box').hidden = false;
    fetch(`/api/threads/${encodeURIComponent(thread.thread.slug)}/snapshots`).then(r => r.json()).then(body => {
      const diffs = Object.fromEntries((body.diffs || []).map(d => [d.to, d]));
      $('stamps').replaceChildren(...[...body.snapshots].reverse().map(s => {
        const li = el('li');
        const b = el('button', `${day(s.at)} · ${plural(s.major.length, 'major idea')} · ${plural(s.counts.sources, 'source')}`);
        b.type = 'button'; b.addEventListener('click', () => snapshot(s)); li.append(b);
        const d = diffs[s.stamp];
        if (d) li.append(el('p', [d.added.length && `+${d.added.length} ideas`, d.dropped.length && `−${d.dropped.length}`,
          d.rebased.length && `${d.rebased.length} new base`, d.replaced.length && `${d.replaced.length} replaced`].filter(Boolean).join(' · ') || 'no change in the core', 'diff'));
        return li;
      }));
    }).catch(() => { $('stamps-box').hidden = true; });
  }

  function snapshot(s) {
    $('snapshot-title').textContent = `Your stamp of ${day(s.at)}`;
    const body = $('snapshot-body'); body.replaceChildren();
    for (const x of s.major) {
      body.append(el('h3', x.title, 'serif'));
      body.append(el('p', (x.text && x.text.paragraph || '').replace(/\[\+?S\d+\]/g, '').trim(), 'quiet'));
    }
    $('snapshot').showModal();
  }


  // --- the area map: topics hold clusters, clusters hold source dots coloured by the idea they serve ---
  let areaCy = null, miniCy = null;
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
    // A fixed grid, not a force layout (compound nodes made cose run off the canvas): the brief's clusters first,
    // grouped by topic, then the ones beyond; sources in rows of three inside their cluster.
    const order = area.clusters.map(k => k.id);
    const clusterEls = elements.filter(e => e.data.kind === 'cluster');
    // One block per topic (its clusters two by two, the brief's first), blocks stacked: a topic's region then
    // wraps only its own clusters. Clusters in no topic come last.
    const blocks = new Map();
    for (const e of clusterEls) { const t = e.data.parent || '~'; if (!blocks.has(t)) blocks.set(t, []); blocks.get(t).push(e); }
    const blockOrder = [...blocks.entries()].sort((a, b) => (a[0] === '~') - (b[0] === '~')
      || Math.min(...a[1].map(e => e.data.beyond)) - Math.min(...b[1].map(e => e.data.beyond)));
    const center = {};
    let y = 0;
    const cols = clusterEls.length > 2 ? 2 : clusterEls.length || 1;  // the panel is narrow: two columns read best
    for (const [, list] of blockOrder) {
      list.sort((a, b) => (a.data.beyond - b.data.beyond) || order.indexOf(a.data.id) - order.indexOf(b.data.id));
      list.forEach((e, k) => { center[e.data.id] = {x: (k % cols) * 165, y: y + Math.floor(k / cols) * 118}; e.position = {...center[e.data.id]}; });
      y += Math.ceil(list.length / cols) * 118 + 46;
    }
    const per = {};
    for (const e of elements) if (e.data.kind === 'source') {
      const k = (per[e.data.parent] = (per[e.data.parent] || 0) + 1) - 1, o = center[e.data.parent] || {x: 0, y: 0};
      e.position = {x: o.x + ((k % 3) - 1) * 30, y: o.y + Math.floor(k / 3) * 30};
    }
    const cy = cytoscape({container: box, elements, style: areaStyle(), minZoom: .3, maxZoom: 1.3, boxSelectionEnabled: false,
      autoungrabify: true, layout: {name: 'preset', fit: true, padding: 16}});
    cy.fit(undefined, 16);
    cy.on('tap', 'node', e => onTap(e.target.data()));
    return cy;
  }
  function focusCluster(id) {
    if (!areaCy) return;
    const n = areaCy.getElementById(id); if (!n.length) return;
    areaCy.animate({fit: {eles: n, padding: 30}}, {duration: 300}); n.addClass('lit'); setTimeout(() => n.removeClass('lit'), 1600);
  }
  function peek(box, data, area) {
    box.hidden = false; box.replaceChildren();
    const close = el('button', '×', 'ce-close'); close.type = 'button'; close.addEventListener('click', () => { box.hidden = true; });
    if (data.kind === 'source') {
      const s = area.sources[data.item] || {};
      box.append(close, el('p', s.role ? `${s.role} · ${s.source || ''}` : s.source || '', 'small quiet'), el('h3', s.title || '', 'serif'), el('p', s.thesis || '', 'small'));
      const a = el('a', 'Read this →', 'ce-button primary'); a.href = readHref(data.item); box.append(a);
      return;
    }
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
  function showMap(which) {
    $('show-area').setAttribute('aria-pressed', String(which === 'area')); $('show-ideas').setAttribute('aria-pressed', String(which === 'ideas'));
    $('area-box').hidden = which !== 'area'; $('ideas-box').hidden = which !== 'ideas';
    if (which === 'area' && areaCy) { areaCy.resize(); areaCy.fit(undefined, 12); }
  }

  // --- reading in place: clean text, the passages of the brief's ideas tinted, where you are, where to go next ---
  let inspecting = false;
  function readerText(doc) {
    const box = el('div', null, 'ce-text' + (inspecting ? ' inspect' : ''));
    const text = doc.text || '';
    if (!text) { box.append(el('p', 'The text of this source is not in the vault.', 'quiet')); return box; }
    const spans = doc.spans.filter(s => inspecting || doc.marked[s.claim]);
    const cuts = [...new Set([0, text.length, ...spans.flatMap(s => [s.start, s.end])])].sort((a, b) => a - b);
    for (let k = 0; k + 1 < cuts.length; k++) {
      const [a, b] = [cuts[k], cuts[k + 1]], piece = text.slice(a, b);
      const over = spans.filter(s => s.start < b && s.end > a);
      if (!over.length) { box.append(document.createTextNode(piece)); continue; }
      const m = over.map(s => doc.marked[s.claim]).find(Boolean);
      const span = el('span', piece, 'pass' + (m ? ' ' + m.kind : ' claim'));
      if (m) span.style.setProperty('--idea', ideaVar(m.idea));
      if (inspecting) span.title = over.map(s => '• ' + s.statement).join('\n');
      box.append(span);
    }
    return box;
  }
  function nextList(groups, marksOrder) {
    const out = [];
    let n = 0;
    for (const g of groups) {
      const sec = el('section', null, 'ce-next-group');
      if (g.colour != null) sec.style.setProperty('--idea', ideaVar(g.colour));
      sec.append(el('h3', g.group.length > 90 ? g.group.slice(0, 89) + '…' : g.group, 'ce-next-title'));
      const ol = el('ol', null, 'ce-next');
      for (const r of g.rows) {
        n += 1; marksOrder.push(r.item);
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
    return out.length ? out : [el('p', 'Nothing else on this subject yet.', 'small quiet')];
  }
  function setState(id, value) {
    return post(`/api/source/${encodeURIComponent(id)}/state`, {state: value, ...subject}).catch(() => {});
  }
  async function openReader(id, push) {
    if (push) { const u = new URL(location.href); u.searchParams.set('read', id); window.history.pushState({read: id}, '', u); }
    if (!trail.includes(id)) trail.push(id); else trail = trail.slice(0, trail.indexOf(id) + 1);
    $('grid').classList.add('reading'); document.body.classList.add('ce-reading');
    $('reader').hidden = false;
    $('reader-body').replaceChildren(el('p', 'Opening…', 'quiet'));
    const r = await fetch(`/api/brief/source/${encodeURIComponent(id)}?${query}`);
    const doc = await r.json().catch(() => ({}));
    if (!r.ok) { $('reader-body').replaceChildren(el('p', doc.error || 'Cannot open this source.', 'quiet')); return; }
    const body = $('reader-body');
    const meta = [doc.source, doc.author && doc.author !== doc.source ? 'by ' + doc.author : null, day(doc.time)].filter(Boolean).join(' · ');
    const roles = el('div', null, 'ce-chips');
    for (const role of doc.roles) { const ch = el('span', `${role.role} of idea ${role.idea + 1}`, 'chip role'); ch.style.setProperty('--idea', ideaVar(role.idea)); ch.title = role.title; roles.append(ch); }
    const tools = el('div', null, 'ce-reader-tools');
    const inspect = el('button', inspecting ? 'Hide claim marks' : 'Inspect claims', 'ce-button'); inspect.type = 'button';
    inspect.addEventListener('click', () => { inspecting = !inspecting; openReader(id, false); });
    const done = el('button', doc.read === 'read' ? '✓ Read' : 'Mark read', 'ce-button'); done.type = 'button';
    done.addEventListener('click', async () => { await setState(id, doc.read === 'read' ? 'unread' : 'read'); openReader(id, false); });
    tools.append(inspect, done);
    if (doc.url) { const a = el('a', 'Original ↗', 'ce-link'); a.href = doc.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; tools.append(a); }
    const theses = el('ul', null, 'ce-theses');
    doc.theses.forEach(t => theses.append(el('li', t.statement)));
    const legend = el('p', 'Tinted: what the brief takes from this source, in its idea’s colour; dotted: a fact it lends.', 'small quiet');
    body.replaceChildren(el('p', meta, 'eyebrow'), el('h1', doc.title, 'serif ce-reader-title'), roles, tools,
      el('h3', 'What it argues', 'ce-section'), theses, legend, readerText(doc));
    body.scrollTop = 0; $('reader').scrollTop = 0;
    // The trail: where you came from, a step back keeps it.
    $('trail').replaceChildren(...trail.map((i, k) => { const li = el('li'); const a = el('a', sid(i) || (c.area?.sources[i]?.title || i).slice(0, 18)); a.href = readHref(i);
      if (i === id) a.setAttribute('aria-current', 'step'); li.append(a); return li; }));
    const order = [];
    $('next').replaceChildren(...nextList(doc.next, order));
    if (miniCy) miniCy.destroy();
    const focus = {cluster: doc.here.cluster, item: id};
    const area = {...c.area, sources: {...c.area.sources}};
    if (!area.sources[id]) area.sources[id] = {title: doc.title, idea: null, thesis: doc.theses[0]?.statement || ''};
    if (!area.clusters.some(k => k.id === doc.here.cluster)) area.clusters = [...area.clusters, {id: doc.here.cluster, title: 'This source', in_brief: false, count: 1, sources: [id], also: []}];
    else area.clusters = area.clusters.map(k => k.id === doc.here.cluster && !k.sources.includes(id) ? {...k, sources: [...k.sources, id]} : k);
    $('mini-peek').hidden = true;
    miniCy = drawArea($('mini'), area, focus, data => peek($('mini-peek'), data, area));
    if (miniCy) {
      order.forEach((i, k) => { const n = miniCy.getElementById('src:' + i); if (n.length) { n.addClass('next'); n.data('label', String(k + 1)); } });
    }
    if (!doc.read) setState(id, 'skimmed');  // opened is at least skimmed
  }
  function closeReader(push) {
    if (push) { const u = new URL(location.href); u.searchParams.delete('read'); window.history.pushState({}, '', u); }
    $('grid').classList.remove('reading'); document.body.classList.remove('ce-reading');
    $('reader').hidden = true;
    if (areaCy) { areaCy.resize(); areaCy.fit(undefined, 12); }
  }
  document.addEventListener('click', e => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
    const u = new URL(a.href, location.href);
    const same = (u.searchParams.get('q') || '') === (subject.q || '') && (u.searchParams.get('id') || '') === (subject.id || '');
    if (u.pathname === '/brief/' && u.searchParams.get('read') && same) {
      e.preventDefault(); openReader(u.searchParams.get('read'), true);
    }
  });
  window.addEventListener('popstate', () => { const r = new URLSearchParams(location.search).get('read'); if (r) openReader(r, false); else closeReader(false); });
  $('reader-close').addEventListener('click', () => closeReader(true));
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('reader').hidden && !document.querySelector('dialog[open]')) closeReader(true); });
  $('show-area').addEventListener('click', () => showMap('area'));
  $('show-ideas').addEventListener('click', () => showMap('ideas'));

  function render() {
    document.title = `Refresher — ${c.scope.title}`;
    $('kind').textContent = c.scope.kind === 'query' ? 'What survives to now on what you asked' : 'What survives to now on a topic';
    $('title').textContent = c.scope.kind === 'query' ? `“${c.scope.title}”` : c.scope.title;
    const k = c.counts;
    $('meta').textContent = `As of ${day(c.at)} · ${plural(k.ideas, 'major idea')} from ${plural(k.sources, 'source')} · ${plural(k.rewrites, 'rewrite')} collapsed · ${k.superseded} superseded`;
    const thread = c.thread;
    $('follow').hidden = !!thread;
    $('stamp').hidden = !thread;
    $('write').hidden = !c.written.missing;
    since(thread); stamps(thread);
    $('ideas').replaceChildren(...c.major.map((x, n) => ideaCard(x, n + 1)));
    const lent = c.major.flatMap(x => (x.adjacent || []).map(a => ({...a, idea: x})));
    $('adjacent-box').hidden = !lent.length;
    $('adjacent').replaceChildren(...lent.map(a => {
      const li = el('li'); li.append(chip(a.item, 'lent', source(a.item).title), el('span', ` → ${a.idea.title.slice(0, 90)}`, 'small quiet'));
      const ul = el('ul', null, 'facts'); a.facts.forEach(f => ul.append(el('li', f.statement))); li.append(ul); return li;
    }));
    const rewrites = c.major.flatMap(x => Object.entries(x.sources).filter(([, r]) => r === 'rewrite').map(([i]) => ({i, x})));
    $('rewrites-box').hidden = !rewrites.length;
    $('rewrites').replaceChildren(...rewrites.map(({i, x}) => { const li = el('li'); li.append(chip(i, 'muted', source(i).title), el('span', ` retells ${sid(x.base)} ${source(x.base).source || ''}`, 'small quiet')); return li; }));
    $('also-box').hidden = !c.also.length;
    $('also').replaceChildren(...c.also.map(x => { const li = el('li'); li.append(el('span', x.title), document.createTextNode(' '), chip(x.base, 'muted')); return li; }));
    $('sources').replaceChildren(...Object.entries(c.written.ids).sort((a, b) => +a[1].slice(1) - +b[1].slice(1)).map(([i, s]) => {
      const li = el('li'); li.append(el('span', s, 'sid')); const a = el('a', source(i).title || i); a.href = readHref(i); li.append(a, el('span', ` · ${sourceLabel(i)}`, 'quiet')); return li;
    }));
    constellation(c.major); people(c.people || []);
    $('grid').hidden = false;
    if (areaCy) areaCy.destroy();
    $('peek').hidden = true;
    const multi = (c.area?.clusters || []).filter(k => k.in_brief).length > 1;
    showMap('area');  // laid out while visible, then the Ideas view when the subject sits in one cluster
    areaCy = c.area ? drawArea($('area'), c.area, null, data => peek($('peek'), data, c.area)) : null;
    if (!multi) showMap('ideas');
    const reading = new URLSearchParams(location.search).get('read');
    if (reading) openReader(reading, false);
    $('notice').textContent = c.major.length ? '' : 'Nothing in your knowledge is on this subject yet.';
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

  $('follow').addEventListener('click', async () => {
    $('follow').disabled = true;
    try { await post('/api/threads', {...subject, title: c.scope.title}); await load(); } catch (e) { $('notice').textContent = e.message; }
    finally { $('follow').disabled = false; }
  });
  $('stamp').addEventListener('click', async () => {
    $('stamp').disabled = true; $('stamp').textContent = 'Stamping…';
    try { await post(`/api/threads/${encodeURIComponent(c.thread.thread.slug)}/stamp`, {}); await load(); $('notice').textContent = 'Stamped: the next brief starts from here.'; }
    catch (e) { $('notice').textContent = e.message; }
    finally { $('stamp').disabled = false; $('stamp').textContent = 'Stamp: caught up through now'; }
  });
  $('write').addEventListener('click', () => writeBrief(false));
  document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));

  if (!subject.id && !subject.q) { $('notice').textContent = 'Ask about a subject from the home page.'; return; }
  load().then(x => { if (x.written.missing) writeBrief(true); }).catch(error => {
    $('notice').textContent = '';
    $('notice').append(el('p', error.message, 'serif'));
    if (error.named?.length) {
      const list = el('ul');
      error.named.forEach(x => { const li = el('li'); const a = el('a', x.title); a.href = readHref(x.id); li.append(a, el('span', ` · ${x.source || ''} · names ${x.names.join(', ')}`, 'small quiet')); list.append(li); });
      $('notice').append(el('p', 'Sources that name what you asked about:', 'quiet'), list);
    }
  });
})();

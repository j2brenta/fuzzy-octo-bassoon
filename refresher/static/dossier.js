/* A subject read cluster by cluster (refresher/subject.py, GET /api/subject).

   Map mode: a header with one primary action, an orientation zone (the graph of clusters beside the reading plan)
   and one block per cluster on the subject: its anchor, its perspectives, its noise as a count. Far sources are one
   line at the end. Every source is one row, the next one in the plan marked; every row carries one number, "+N% of
   the subject", its marginal share in coverage order. Under the title, what the subject is about: its most
   central Ideas and the clusters it sits in, since a topic's name alone says little.
   Reading mode (?read=<id>): on a wide screen the text opens in a pane beside the map or the list, which stay as
   they were, with the context (connected sources, people, organisations, Ideas) as a drawer; on a narrower one the
   plan becomes a rail on the left, the text takes the middle and the context the right. Esc closes it.
   Marking read or skimmed posts to /api/source/<id>/state, which returns the plan and coverage recomputed.
   All results (?view=list&group=…&scope=…&all=1): every source the subject finds in one table, grouped by home
   cluster, every cluster it sits in, role, trust, type or topic, with a column per cluster marking where it sits.
   Values from the vault only enter textContent. */
'use strict';
(() => {
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
  const GRAPH_CAP = 8;

  let s = null, current = null, mapScroll = 0, trail = [], highlight = null, openCtx = 'connected';
  const contexts = new Map(), openBlocks = new Set(), shown = new Set();
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
  function newIdeas(id) {  // in-scope Ideas of this source no source you read holds
    const covered = new Set(Object.keys(s.marks).filter(o => o !== id).flatMap(o => s.sources[o]?.ideas || []));
    return src(id).ideas.filter(i => !covered.has(i));
  }

  async function setState(id, state) {
    const r = await fetch(`/api/source/${encodeURIComponent(id)}/state`, {method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Refresher': '1'}, body: JSON.stringify({state, ...subjectQuery})});
    const body = await r.json().catch(() => ({}));
    if (!r.ok) { $('cover-text').textContent = body.error || `Could not save (${r.status})`; return; }
    Object.assign(s, body); results.clear();  // plan numbers and order changed
    if (current) { extendTrail(); renderRail(); renderBar(); refreshContext(); if (beside()) { renderBrowse(); markNow(); } } else renderMap();
  }

  // --- the graph of clusters: nodes are clusters, edges shared Ideas, the main cluster pinned at the centre ---
  function graphNodes() {
    const live = s.clusters.filter(c => c.ideas);
    const kept = live.slice(0, GRAPH_CAP - (live.length > GRAPH_CAP ? 1 : 0));
    const nodes = kept.map(c => ({id: c.id, title: c.title, band: c.band, ideas: c.ideas, members: [c.id]}));
    const rest = live.slice(kept.length);
    if (rest.length) nodes.push({id: 'other', title: plural(rest.length, 'other cluster'), band: 'far', ideas: rest.reduce((t, c) => t + c.ideas, 0), members: rest.map(c => c.id)});
    const at = new Map(nodes.flatMap(n => n.members.map(m => [m, n.id])));
    const edges = new Map();
    for (const e of s.edges) {
      const a = at.get(e.a), b = at.get(e.b); if (!a || !b || a === b) continue;
      const key = [a, b].sort().join('|'), old = edges.get(key);
      if (!old) edges.set(key, {a, b, count: e.count, label: e.label}); else old.count += e.count;
    }
    return {nodes, edges: [...edges.values()], at};
  }
  const layouts = new Map();
  function layout(nodes, edges, w, h) {
    // A small force layout: the main cluster pinned at the centre, edges as springs, every pair repelling. Seeded
    // by position in the list, so the same subject draws the same way every time.
    const key = `${w}x${h}`; if (layouts.has(key)) return layouts.get(key);
    const pos = new Map(nodes.map((n, k) => {
      const a = 2 * Math.PI * k / Math.max(1, nodes.length - 1);
      return [n.id, n.band === 'main' ? {x: 0, y: 0} : {x: Math.cos(a) * w * 0.32, y: Math.sin(a) * h * 0.32}];
    }));
    for (let step = 0; step < 300; step++) {
      const force = new Map(nodes.map(n => [n.id, {x: 0, y: 0}]));
      for (const a of nodes) for (const b of nodes) {
        if (a === b) continue;
        const p = pos.get(a.id), q = pos.get(b.id), dx = p.x - q.x, dy = p.y - q.y, d2 = Math.max(60, dx * dx + dy * dy);
        force.get(a.id).x += w * 7 * dx / d2; force.get(a.id).y += w * 7 * dy / d2;
      }
      for (const e of edges) {
        const p = pos.get(e.a), q = pos.get(e.b), dx = q.x - p.x, dy = q.y - p.y, d = Math.hypot(dx, dy) || 1, pull = (d - w / 4) * 0.05;
        force.get(e.a).x += pull * dx / d; force.get(e.a).y += pull * dy / d; force.get(e.b).x -= pull * dx / d; force.get(e.b).y -= pull * dy / d;
      }
      for (const n of nodes) {
        if (n.band === 'main') continue;
        const p = pos.get(n.id), f = force.get(n.id);
        p.x += Math.max(-6, Math.min(6, f.x - p.x * 0.01)); p.y += Math.max(-6, Math.min(6, f.y - p.y * 0.01));
        const mx = w / 2 - (w > 240 ? 56 : 18), my = h / 2 - (w > 240 ? 34 : 18);  // labels stay inside the box
        p.x = Math.max(-mx, Math.min(mx, p.x)); p.y = Math.max(-my + 8, Math.min(my, p.y));
      }
    }
    layouts.set(key, pos);
    return pos;
  }
  function graph(w, h, {labels = true, focus = null, open = null} = {}) {
    // Clusters as nodes, shared Ideas as edges. `open` (a cluster drawn on its own) opens in place into its sources:
    // the anchor at the centre, perspectives on a middle ring, noise on the outer one; filled where a source lives,
    // hollow where it is also here. Hovering a source draws dashed lines to the other clusters it sits in. Labels
    // sit outside every circle and never on each other (one that cannot be placed stays a tooltip), and the view
    // box is fitted to what is drawn, so no node is cut off.
    const {nodes, edges, at} = graphNodes();
    const root = svg('svg', {role: 'img', 'aria-label': `${plural(nodes.length, 'cluster')} and the Ideas they share`});
    if (!nodes.length) { root.setAttribute('viewBox', `${-w / 2} ${-h / 2} ${w} ${h}`); return root; }
    const pos = new Map([...layout(nodes, edges, w, h)].map(([k, p]) => [k, {...p}])), max = Math.max(...nodes.map(n => n.ideas));
    const big = open && at.get(open) === open && cluster(open) ? open : null, R = Math.min(w, h) * 0.34;
    const radius = n => n.id === big ? R : (labels ? 9 : 6) + (labels ? 13 : 8) * Math.sqrt(n.ideas / max);
    if (big) {  // make room: every other cluster moves out of the open one's way
      const c = pos.get(big);
      nodes.forEach((n, k) => {
        if (n.id === big) return;
        const p = pos.get(n.id), need = R + radius(n) + 16;
        let dx = p.x - c.x, dy = p.y - c.y, d = Math.hypot(dx, dy);
        if (d < 1) { const a = 2 * Math.PI * k / nodes.length; dx = Math.cos(a); dy = Math.sin(a); d = 1; }
        if (d < need) { p.x = c.x + dx / d * need; p.y = c.y + dy / d * need; }
      });
    }
    const here = at.get(src(focus || nextId() || '')?.cluster);
    const after = focus ? nextAfter(focus) : planIds()[1];
    const there = after ? at.get(src(after).cluster) : null;
    const top = Math.max(1, ...edges.map(e => e.count));
    const circles = [], wanted = [];  // obstacles, then the labels to place among them
    const edgeLayer = svg('g'), nodeLayer = svg('g'), hoverLayer = svg('g', {class: 'g-hover'}), labelLayer = svg('g');
    for (const e of edges) {
      const p = pos.get(e.a), q = pos.get(e.b), next = here && there && here !== there && [e.a, e.b].includes(here) && [e.a, e.b].includes(there);
      const line = svg('line', {x1: p.x, y1: p.y, x2: q.x, y2: q.y, class: 'g-edge' + (next ? ' next' : ''), 'stroke-width': (1 + 3 * e.count / top).toFixed(1)});
      const t = svg('title'); t.textContent = `${plural(e.count, 'shared Idea')}, most of all: ${s.ideas[e.label]?.title || ''}`; line.append(t); edgeLayer.append(line);
    }
    for (const n of nodes) {
      const p = pos.get(n.id), r = radius(n);
      const members = n.members.flatMap(m => Object.values(s.sources).filter(x => x.cluster === m && x.role !== 'noise'));
      const done = members.length && members.every(x => s.marks[x.id]);
      const g = svg('g', {class: `g-node ${n.band}${done ? ' done' : ''}${n.id === here ? ' here' : ''}${n.id === big ? ' open' : ''}`, tabindex: labels ? 0 : -1, role: 'button'});
      const t = svg('title'); t.textContent = `${n.title} · ${n.band} · ${plural(n.ideas, 'Idea')}`;
      g.append(t, svg('circle', {cx: p.x.toFixed(1), cy: p.y.toFixed(1), r: r.toFixed(1)}));
      circles.push({x: p.x, y: p.y, r, big: n.id === big});  // the open cluster is in the way of every label but its sources'
      if (labels && (n.band !== 'far' || n.id === big)) wanted.push({text: short(n.title, n.id === big ? 24 : 20), x: p.x, anchor: 'middle', cls: n.id === big ? 'open-label' : '', must: true,
        inside: n.id === big, ys: n.id === big ? [p.y - r + 24, p.y - r + 34] : [p.y + r + 11, p.y - r - 5, p.y + r + 22]});
      // a cluster is named below or above its circle; the open one at the top inside its own
      const go = () => { if (current && !beside()) closeReading(); if (n.id !== 'other') showBlock(n.id); else { shown.add('far'); renderFar(); $('far').scrollIntoView({behavior: smooth()}); } };
      g.addEventListener('click', go); g.addEventListener('keydown', e => e.key === 'Enter' && go());
      nodeLayer.append(g);
    }
    if (big) {
      const c = pos.get(big), rows = cluster(big).placements;
      const ring = (list, frac, r0) => list.map((row, k) => {
        // spread round the circle but for a gap at the top, which stays free for its name
        const gap = 1.22, a = -Math.PI / 2 + gap / 2 + (2 * Math.PI - gap) * (k + 0.5) / list.length;
        return {row, x: c.x + Math.cos(a) * R * frac, y: c.y + Math.sin(a) * R * frac, a, r: r0};
      });
      const anchor = rows.filter(r => r.role === 'anchor').map(row => ({row, x: c.x, y: c.y, a: Math.PI / 2, r: 7, centre: true}));
      const placed = [...anchor, ...ring(rows.filter(r => r.role === 'perspective'), 0.52, 5), ...ring(rows.filter(r => r.role === 'noise'), 0.84, 3)];
      for (const q of placed) {
        const id = q.row.source, x = src(id), g = svg('g', {class: `g-src ${q.row.role} ${q.row.home ? 'home' : 'also'}`, tabindex: 0, role: 'button'});
        const t = svg('title'); t.textContent = `${x.title} · ${q.row.role}${q.row.home ? '' : ` · also here, home: ${clusterName(x.cluster)}`}`;
        g.append(t, svg('circle', {cx: q.x.toFixed(1), cy: q.y.toFixed(1), r: q.r}));
        circles.push({x: q.x, y: q.y, r: q.r});
        if (labels && q.row.role !== 'noise') {
          const cos = Math.cos(q.a), side = q.centre ? 'middle' : cos > 0.25 ? 'start' : cos < -0.25 ? 'end' : 'middle';
          const lx = q.centre ? q.x : q.x + cos * (q.r + 4), ly = q.centre ? q.y + q.r + 11 : q.y + Math.sin(q.a) * (q.r + 4) + (side === 'middle' ? (Math.sin(q.a) > 0 ? 9 : -2) : 3);
          wanted.push({text: short(x.title, 22), x: lx, anchor: side, cls: 'src-label', ys: [ly, ly + 11, ly - 11, ly + 22]});
        }
        const others = () => x.places.filter(p => p.cluster !== big && at.has(p.cluster) && pos.has(at.get(p.cluster)));
        g.addEventListener('mouseenter', () => hoverLayer.replaceChildren(...others().map(p => { const o = pos.get(at.get(p.cluster));
          return svg('line', {x1: q.x, y1: q.y, x2: o.x, y2: o.y, class: `g-place ${p.home ? 'home' : 'also'}`}); })));
        g.addEventListener('mouseleave', () => hoverLayer.replaceChildren());
        const go = () => openSource(id, false, big);
        g.addEventListener('click', go); g.addEventListener('keydown', e => e.key === 'Enter' && go());
        nodeLayer.append(g);
      }
    }
    root.append(edgeLayer, nodeLayer, hoverLayer, labelLayer);
    // Labels are placed once the graph is on the page, where their real size is known: each tries its place, then a
    // step down or up; one that still touches a circle or another label is left out (its node keeps the tooltip).
    // Cluster names go first. Then the view box is fitted to everything drawn, so no node or label is cut off.
    root.settle = () => {
      const taken = [];
      const hits = (b, inside) => taken.some(o => b.x < o.x + o.width && o.x < b.x + b.width && b.y < o.y + o.height && o.y < b.y + b.height) ||
        circles.some(c => !(c.big && inside) && (Math.max(b.x, Math.min(c.x, b.x + b.width)) - c.x) ** 2 + (Math.max(b.y, Math.min(c.y, b.y + b.height)) - c.y) ** 2 < c.r * c.r);
      for (const lb of wanted.sort((a, b) => (b.must ? 1 : 0) - (a.must ? 1 : 0))) {
        const text = svg('text', {x: lb.x.toFixed(1), 'text-anchor': lb.anchor, class: lb.cls}); text.textContent = lb.text;
        labelLayer.append(text);
        const spot = lb.ys.find(y => { text.setAttribute('y', y.toFixed(1)); return !hits(text.getBBox(), lb.inside || lb.cls === 'src-label'); });
        if (spot === undefined) { text.remove(); continue; }
        taken.push(text.getBBox());
      }
      const b = [edgeLayer, nodeLayer, labelLayer].map(g => g.getBBox()).filter(x => x.width || x.height);
      const x0 = Math.min(...b.map(x => x.x)), y0 = Math.min(...b.map(x => x.y)), x1 = Math.max(...b.map(x => x.x + x.width)), y1 = Math.max(...b.map(x => x.y + x.height));
      const pad = 6, bw = Math.max(w * 0.6, x1 - x0 + 2 * pad), bh = Math.max(h * 0.6, y1 - y0 + 2 * pad), cx = (x0 + x1) / 2, cy = (y0 + y1) / 2;
      if (Number.isFinite(cx)) root.setAttribute('viewBox', `${(cx - bw / 2).toFixed(1)} ${(cy - bh / 2).toFixed(1)} ${bw.toFixed(1)} ${bh.toFixed(1)}`);
    };
    root.setAttribute('viewBox', `${-w / 2} ${-h / 2} ${w} ${h}`);
    return root;
  }

  // --- map mode ---
  function renderAbout() {
    // A topic's name says little, and its centre blends the clusters it is made of: say what each part is about,
    // the clusters on the subject with the Idea of each most relevant to it.
    const parts = s.clusters.filter(onSubject).slice(0, 4).map(c => {
      const best = Object.entries(s.ideas).filter(([, i]) => i.in_scope && i.cluster === c.id).sort((a, b) => b[1].relevance - a[1].relevance)[0];
      return best ? [c, short(best[1].title.replace(/\.$/, ''), 130)] : null;
    }).filter(Boolean);
    const box = $('about'); box.replaceChildren(); box.hidden = !parts.length;
    if (!parts.length) return;
    box.append(el('span', parts.length > 1 ? `About: ${parts.length} threads` : 'About', 'about-label'));
    for (const [c, idea] of parts) {
      const line = el('span', null, 'about-idea' + (c.band === 'main' ? ' main' : ''));
      line.append(el('b', c.title), ` — ${idea}`); box.append(line);
    }
  }
  function renderHead() {
    renderAbout();
    const live = Object.values(s.sources), onSubj = s.clusters.filter(onSubject);
    const noise = live.filter(x => x.role === 'noise').length;
    const far = live.filter(x => x.role !== 'noise' && cluster(x.cluster)?.band === 'far').length;
    $('counts').textContent = `${plural(live.length, 'source')} · ${plural(onSubj.length, 'cluster')} on the subject · ${far} far · ${noise} noise`;
    $('cover-bar').style.width = pct(s.coverage);
    const to = s.coverage >= 0.8 ? 'you have read 80% of it' : s.plan.length ? `about ${s.minutes} min to ${pct(Math.min(0.8, s.plan.at(-1).cumulative))}` : 'nothing left that adds to it';
    $('cover-text').replaceChildren(el('strong', `Covered ${pct(s.coverage)}`), ` · ${to}`);
    const next = nextId(), number = numbered();
    $('start').textContent = !next ? 'Nothing left to read' : Object.keys(s.marks).some(id => s.sources[id]) ? `Continue · #${number.get(next)}` : 'Start reading';
    $('start').disabled = !next;
  }
  function renderPlan() {
    const number = numbered();
    $('plan').replaceChildren(...s.plan.map(x => {
      const li = el('li'), b = button('', () => openSource(x.source));
      const bar = el('span', null, 'cum'), fill = el('span'); fill.style.width = pct(x.cumulative); bar.append(fill);
      b.append(el('span', `#${number.get(x.source)}`, 'n'), el('span', short(src(x.source).title, 64), 't'),
        el('span', short(clusterName(src(x.source).cluster), 24), 'tag'), bar, el('span', pct(x.cumulative), 'pct'));
      li.append(b); return li;
    }));
    $('plan-note').textContent = s.plan.length ? `${plural(s.plan.length, 'source')} to reach ${pct(s.plan.at(-1).cumulative)} of the subject.`
      : 'Nothing left in the plan: everything that adds to the subject is read.';
  }
  function meta(x) {
    const m = el('p', null, 'meta');
    [x.source, x.author, day(x.time), `${x.minutes} min`].filter(Boolean).forEach(t => m.append(el('span', t)));
    return m;
  }
  function signals(x) {  // depth as a bar and trust as a word: two signals, never merged
    const box = el('span', null, 'sig'); box.append(depthBar(x.depth, x.depth_inputs), el('span', x.trust, 'trust ' + x.trust)); return box;
  }
  const placementOf = (clusterId, id) => cluster(clusterId)?.placements.find(r => r.source === id);
  function row(id, number, role, place = null, next = false) {  // place: its placement in the block it is listed in
    const x = src(id), st = stateOf(id), also = place && !place.home;
    const r = el('div', null, `row s-${st}${also ? ' also-here' : ''}${next ? ' next' : ''}`); r.dataset.source = id;
    const open = () => openSource(id, false, place?.cluster);
    const head = el('div', null, 'row-head');
    const chev = button('▸', () => { const open = r.classList.toggle('open'); chev.textContent = open ? '▾' : '▸'; chev.setAttribute('aria-expanded', String(open)); }, 'chev');
    chev.setAttribute('aria-expanded', 'false'); chev.setAttribute('aria-label', 'Show the gist');
    const name = el('span', null, 'row-title'); name.append(button(x.title, open, 'title-btn'));
    if (next) name.prepend(el('span', 'next to read', 'next-tag'));
    if (also) name.append(el('span', `home: ${clusterName(x.cluster)}`, 'home-tag'));
    const shownRole = place ? place.role : x.role, label = el('span', null, 'role ' + shownRole);
    if (place) label.append(el('i', null, `mark ${place.home ? 'home' : 'also'} ${shownRole}`));
    label.append(role || shownRole);
    head.append(el('span', null, `dot s-${st}`), el('span', number ? `#${number}` : '', 'n'), name,
      shownRole === 'noise' ? el('span', null, 'sig') : signals(x), label, el('span', gainText(id), 'gain'), chev);
    const body = el('div', null, 'row-body');
    body.append(meta(x), el('p', x.gist || x.hook, 'gist-text'));
    if (x.repost_of) body.append(el('p', `A repost of ${short(src(x.repost_of)?.title || 'another source', 60)}.`, 'small quiet'));
    const acts = el('div', null, 'card-foot'); acts.append(button('Read ▸', open));
    acts.append(st === 'unread' ? button('Skim', () => setState(id, 'skimmed')) : button('Unmark', () => setState(id, 'unread')));
    body.append(acts);
    r.append(head, body);
    return r;
  }
  function renderBlocks() {
    const number = numbered(), next = nextId(), blocks = s.clusters.filter(onSubject);
    const box = $('blocks'); box.replaceChildren();
    blocks.forEach((c, k) => {
      const open = openBlocks.has(c.id), sec = el('section', null, `block ${c.band}${open ? ' open' : ''}`); sec.id = 'block-' + c.id;
      const head = el('header'), h = el('h2'), toggle = button(c.title, () => { open ? openBlocks.delete(c.id) : openBlocks.add(c.id); renderBlocks(); if (!open) setWhere({cluster: c.id, source: null, idea: null}); }, 'block-toggle');
      toggle.setAttribute('aria-expanded', String(open)); h.append(toggle);
      const facts = el('p', null, 'block-facts');
      facts.append(el('span', c.band, 'band ' + c.band), el('span', `${plural(c.placements.length, 'source')} · ${plural(c.ideas, 'Idea')}`));
      if (c.also_here.length) facts.append(el('span', `${c.also_here.length} also here from other clusters`));
      if (c.band === 'adjacent' && c.shares_with_main) facts.append(el('span', `shares ${plural(c.shares_with_main, 'Idea')} with main`));
      head.append(h, facts); sec.append(head);
      // Every source is one row; the next one in the plan is marked where it lives.
      const piece = id => { const place = placementOf(c.id, id); return row(id, number.get(id), null, place, id === next && !!place?.home); };
      if (c.anchor) sec.append(piece(c.anchor));
      if (open) {
        c.perspectives.forEach(id => sec.append(piece(id)));
        if (c.noise.length) {
          const key = 'noise:' + c.id;
          sec.append(button(`${plural(c.noise.length, 'noise source')} · ${shown.has(key) ? 'hide' : 'show'}`,
            () => { shown.has(key) ? shown.delete(key) : shown.add(key); renderBlocks(); }, 'link-btn more-line'));
          if (shown.has(key)) c.noise.forEach(id => sec.append(row(id, null, null, placementOf(c.id, id))));
        }
      } else if (c.perspectives.length || c.noise.length) {
        sec.append(button(`${[c.perspectives.length && plural(c.perspectives.length, 'perspective'), c.noise.length && plural(c.noise.length, 'noise source')].filter(Boolean).join(' · ')} · open`,
          () => { openBlocks.add(c.id); renderBlocks(); }, 'link-btn more-line'));
      }
      box.append(sec);
      const after = blocks[k + 1], edge = after && s.edges.find(e => [e.a, e.b].includes(c.id) && [e.a, e.b].includes(after.id));
      if (edge) {
        const t = el('p', null, 'transition');
        t.append(`connects to ${after.title} through `, el('q', short(s.ideas[edge.label].title.replace(/\.$/, ''), 110)));
        box.append(t);
      }
    });
    if (!blocks.length) box.append(el('p', 'No cluster is on this subject yet.', 'quiet'));
    markNow();
  }
  function renderFar() {
    const far = Object.values(s.sources).filter(x => !(cluster(x.cluster) && onSubject(cluster(x.cluster))) && !s.no_ideas.includes(x.id));
    const none = s.no_ideas.length, box = $('far'); box.replaceChildren();
    if (!far.length && !none) return;
    const number = numbered(), open = shown.has('far');
    const line = el('p', null, 'far-line');
    line.append([far.length && `${plural(far.length, 'source')} from other clusters near this subject`, none && `${none} with no Ideas extracted`].filter(Boolean).join(' · ') + ' · ',
      button(open ? 'hide' : 'show', () => { open ? shown.delete('far') : shown.add('far'); renderFar(); }, 'link-btn'));
    box.append(line);
    if (!open) return;
    const ranked = far.map(x => [x, x.shares.filter(y => number.has(y.source)).sort((a, b) => b.count - a.count)[0]])
      .sort((a, b) => (b[1]?.count || 0) - (a[1]?.count || 0));
    for (const [x, best] of ranked) box.append(row(x.id, null, best ? `shares ${plural(best.count, 'Idea')} with #${number.get(best.source)}` : clusterName(x.cluster)));
    for (const id of s.no_ideas) box.append(row(id, null, 'no Ideas extracted'));
  }
  function renderMap() {
    renderHead(); renderPlan(); renderBlocks(); renderFar();
    drawGraph();
    const main = s.clusters.find(c => c.band === 'main');
    $('graph-caption').textContent = main ? `${main.title} at the centre. Solid is main, tinted adjacent, an outline far; a line is a shared Idea.` : '';
  }
  function drawGraph() {  // the map's graph, the cluster you are in opened into its sources
    const g = graph(300, 250, {open: where.cluster || s.clusters.find(c => c.band === 'main')?.id});
    $('graph').replaceChildren(g); g.settle();
  }
  function showBlock(id) {
    openBlocks.add(id); renderBlocks(); setWhere({cluster: id, source: null, idea: null});
    const b = $('block-' + id); if (b) { b.scrollIntoView({behavior: smooth(), block: 'start'}); b.classList.remove('flash'); void b.offsetWidth; b.classList.add('flash'); }
  }

  // --- all results: every source found in one table, grouped (GET /api/subject/results) ---
  const GROUPS = [['cluster_home', 'Cluster, home only'], ['cluster_all', 'Cluster, every place'], ['role', 'Role'],
    ['trust', 'Trust'], ['type', 'Type'], ['topic', 'Topic']];
  const results = new Map();
  let selected = null;
  function listState() {
    const u = new URLSearchParams(location.search);
    return {group: GROUPS.some(g => g[0] === u.get('group')) ? u.get('group') : 'cluster_home', scope: u.get('scope') || '', all: u.get('all') === '1'};
  }
  function setList(change) {  // the grouping, topic and filter live in the URL, so a reload keeps them
    const url = new URL(location.href);
    for (const [k, v] of Object.entries(change)) { if (v) url.searchParams.set(k, v); else url.searchParams.delete(k); }
    history.replaceState(history.state, '', url); renderList();
  }
  async function loadResults(st) {
    const key = JSON.stringify(st);
    if (!results.has(key)) {
      const q = new URLSearchParams({...subjectQuery, group_by: st.group, only_matches: st.all ? '0' : '1'});
      if (st.scope) q.set('scope', st.scope);
      const r = await fetch('/api/subject/results?' + q), body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
      results.set(key, body);
    }
    return results.get(key);
  }
  const roleCounts = roles => ['anchor', 'perspective', 'noise'].filter(k => roles[k]).map(k => plural(roles[k], k)).join(' · ');
  function depthBar(depth, inputs) {
    const box = el('span', null, 'depth'); box.setAttribute('role', 'img');
    const why = inputs ? `${inputs.words.toLocaleString('en')} words · ${plural(inputs.ideas, 'Idea')} · ${inputs.specific} specific` : '';
    box.setAttribute('aria-label', `depth ${depth} of 5${why ? ': ' + why : ''}`); box.title = why;
    for (let i = 1; i <= 5; i++) box.append(el('i', null, i <= depth ? 'on' : ''));
    return box;
  }
  function placeMark(source, c, p, current) {  // filled at home, hollow elsewhere; its size says the role there
    const b = button('', () => focusPlacement(c.id, source), `mark ${p.home ? 'home' : 'also'} ${p.role}${current ? ' this' : ''}`);
    b.setAttribute('aria-label', `${c.title}: ${p.role}, ${p.home ? 'its home' : 'also here'}. Show it on the map`); b.title = `${c.title} · ${p.role}${p.home ? ' · home' : ' · also here'}`;
    return b;
  }
  function resultRow(row, r) {
    const x = src(row.source), number = numbered().get(row.source);
    const tr = el('tr', null, `r-row${row.match ? '' : ' unmatched'}${row.source === selected ? ' selected' : ''}`);
    tr.dataset.source = row.source; tr.tabIndex = 0;
    const open = () => { selected = row.source; openSource(row.source); };
    tr.addEventListener('click', e => { if (!e.target.closest('.mark')) open(); });
    tr.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target === tr) open(); });
    const cell = (child, cls) => { const td = el('td', null, cls); if (child != null) td.append(child); return td; };
    const title = el('td', null, 'c-title');
    title.append(el('span', x.title, 't'), el('span', [x.source, x.author, day(x.time)].filter(Boolean).join(' · '), 'sub'));
    const role = el('td', null, 'c-role'); role.append(el('span', row.role, 'role ' + row.role));
    if (row.placement && !row.placement.home) role.append(el('span', 'also here', 'also'));
    const terms = row.matched_terms.length ? row.matched_terms.join(', ') : row.matched_ideas ? plural(row.matched_ideas, 'Idea') : '—';
    tr.append(cell(el('span', null, `dot s-${stateOf(row.source)}`), 'c-dot'), cell(number ? `#${number}` : '', 'c-n'), title, role,
      cell(depthBar(row.depth, row.depth_inputs), 'c-depth'), cell(el('span', row.trust, 'trust ' + row.trust), 'c-trust'),
      cell(row.type, 'c-type'), cell(terms, 'c-terms'));
    for (const c of r.clusters) {
      const p = row.places.find(q => q.cluster === c.id), td = el('td', null, 'c-cluster');
      if (p) td.append(placeMark(row.source, c, p, r.group_by === 'cluster_all' && row.group === c.id));
      tr.append(td);
    }
    return tr;
  }
  async function renderList() {
    const st = listState();
    $('group-by').value = st.group; $('scope').value = st.scope; $('only').value = st.all ? '0' : '1';
    let r;
    try { r = await loadResults(st); } catch (error) { $('list-head').textContent = error.message; return; }
    if (JSON.stringify(listState()) !== JSON.stringify(st)) return;  // another choice was made while this loaded
    const h = r.header, subj = h.kind === 'query' ? `“${h.subject}”` : h.subject;
    const head = $('list-head'); head.replaceChildren(el('strong', `${h.matching} of ${plural(h.total, 'source')}`), ` match ${subj}`);
    if (h.placements) head.append(` · ${plural(h.placements, 'place')} across clusters`);
    if (st.all && h.rows > h.matching) head.append(` · ${h.total - h.matching} more below, outside the subject`);
    const cols = 8 + r.clusters.length;
    $('slices').replaceChildren(...r.slices.map(sl => {
      const b = button('', () => document.querySelector(`tbody[data-group="${CSS.escape(sl.group)}"]`)?.scrollIntoView({behavior: smooth(), block: 'start'}), 'slice');
      b.style.flex = String(sl.count); b.title = `${sl.title} · ${sl.count} · ${roleCounts(sl.roles)}`;
      b.append(el('span', sl.title, 's-name'), el('span', sl.count, 's-count')); return b;
    }));
    const table = $('results'), thead = el('thead'), tr = el('tr');
    for (const [label, cls] of [['', 'c-dot'], ['#', 'c-n'], ['Source', 'c-title'], ['Role', 'c-role'], ['Depth', 'c-depth'],
      ['Trust', 'c-trust'], ['Type', 'c-type'], ['Matches', 'c-terms']]) tr.append(el('th', label, cls));
    for (const c of r.clusters) { const th = el('th', null, `c-cluster ${c.band}`); th.title = `${c.title} · ${c.band}`; th.append(el('span', short(c.title, 24))); tr.append(th); }
    thead.append(tr); table.replaceChildren(thead);
    for (const sl of r.slices) {
      const body = el('tbody'); body.dataset.group = sl.group;
      const g = el('tr', null, 'group'), th = el('th'); th.colSpan = cols;
      th.append(el('span', sl.title, 'g-title'), el('span', `${plural(sl.count, r.group_by === 'cluster_all' ? 'place' : 'source')} · ${roleCounts(sl.roles)}`, 'g-counts'));
      g.append(th); body.append(g);
      r.rows.filter(x => x.group === sl.group).forEach(x => body.append(resultRow(x, r)));
      table.append(body);
    }
    if (!r.rows.length) table.append(el('caption', 'Nothing matches here. Show all sources, or another topic.', 'quiet'));
    const pick = selected && table.querySelector(`tr[data-source="${CSS.escape(selected)}"]`);
    if (pick && !current) requestAnimationFrame(() => pick.scrollIntoView({block: 'center'}));
    markNow();
  }
  function setView(view) {
    const url = new URL(location.href);
    if (view === 'list') url.searchParams.set('view', 'list'); else url.searchParams.delete('view');
    if (!beside()) for (const k of ['read', 'in', 'thread', 'pos']) url.searchParams.delete(k);  // beside, the text stays open
    url.hash = '';
    history.pushState({}, '', url); route();
  }
  function focusPlacement(clusterId, source) {  // a mark in a cluster column: the map, that cluster open, that source picked
    selected = source; setView('map');
    const c = cluster(clusterId); if (!c) return;
    if (source && c.noise.includes(source)) shown.add('noise:' + clusterId);
    requestAnimationFrame(() => {  // after the map restored its scroll position
      showBlock(clusterId); setWhere({cluster: clusterId, source, idea: null});
      if (!source) return;
      const row = $('block-' + clusterId)?.querySelector(`[data-source="${CSS.escape(source)}"]`);
      if (!row) return;
      document.querySelectorAll('.picked').forEach(n => n.classList.remove('picked'));
      row.classList.add('picked', 'flash'); row.scrollIntoView({behavior: smooth(), block: 'center'});
    });
  }
  $('group-by').append(...GROUPS.map(([v, label]) => { const o = el('option', label); o.value = v; return o; }));
  $('group-by').addEventListener('change', e => setList({group: e.target.value === 'cluster_home' ? '' : e.target.value}));
  $('scope').addEventListener('change', e => setList({scope: e.target.value}));
  $('only').addEventListener('change', e => setList({all: e.target.value === '0' ? '1' : ''}));
  $('view-map').addEventListener('click', () => setView('map'));
  $('view-list').addEventListener('click', () => setView('list'));

  // --- the depth rail: All > subject > cluster > source > Idea, with counts and position (GET /api/path) ---
  let where = {cluster: null, source: null, idea: null};
  const paths = new Map();
  function setWhere(change) {
    const moved = 'cluster' in change && change.cluster !== where.cluster;
    where = {...where, ...change}; renderPath();
    if (moved && !current && !$('map-body').hidden) drawGraph();
  }
  function pathKey() { const q = new URLSearchParams(subjectQuery); for (const [k, v] of Object.entries(where)) if (v) q.set(k, v); return q.toString(); }
  async function renderPath() {
    const key = pathKey();
    if (!paths.has(key)) {
      const r = await fetch('/api/path?' + key), body = await r.json().catch(() => ({}));
      if (!r.ok) return;
      paths.set(key, body.levels);
    }
    if (key !== pathKey()) return;  // the selection moved on while this loaded
    const levels = paths.get(key), last = levels.length - 1;
    $('path-list').replaceChildren(...levels.map((lv, n) => {
      const li = el('li', null, 'lv-' + lv.level), name = lv.level === 'subject' && lv.kind === 'query' ? `“${lv.name}”`
        : lv.level === 'idea' ? `Idea §${lv.position}` : lv.level === 'all' ? lv.name : short(lv.name, 44);
      const facts = [lv.position && lv.level !== 'idea' ? `${lv.position} of ${lv.of}` : lv.level === 'idea' ? `of ${lv.of}` : null,
        lv.count != null && lv.noun ? plural(lv.count, lv.noun) : null].filter(Boolean).join(' · ');
      const go = {all: () => { location.href = '/'; }, subject: () => { selected = null; setView('map'); requestAnimationFrame(() => window.scrollTo({top: 0})); },
        cluster: () => focusPlacement(lv.id, null), source: () => openSource(lv.id, false, where.cluster),
        idea: () => showIdea(lv.id)}[lv.level];
      const b = button('', go, 'lv'); b.title = lv.name;
      b.append(el('span', name, 'lv-name')); if (facts) b.append(el('small', facts));
      if (n === last) b.setAttribute('aria-current', 'location');
      li.append(b); return li;
    }));
    $('path').hidden = false;
  }
  function showIdea(ideaId) {  // the passage behind an Idea of the source being read
    const m = [...document.querySelectorAll('#r-text mark.idea-mark')].find(n => n.dataset.ideas.split(' ').includes(ideaId));
    if (m) { m.scrollIntoView({block: 'center', behavior: smooth()}); m.classList.remove('flash'); void m.offsetWidth; m.classList.add('flash'); }
  }

  // --- reading mode ---
  function extendTrail() {  // the path you walk: what you read, then the plan; detours sit after the source they left
    for (const id of planIds()) if (!trail.includes(id)) trail.push(id);
  }
  // The thread: the sources you pulled one link at a time. Following a link adds a station after the one you are
  // on (from an earlier station that starts a branch: the stations after it are let go); an earlier station takes
  // you back along it; opening a source any other way starts a new thread. It lives in the URL (&thread=&pos=).
  let thread = [], pos = 0;
  function openSource(id, detour = false, inCluster = null, via = null) {  // inCluster: the cluster it was opened from
    if (!s.sources[id]) return;
    selected = id;
    if (via === 'station') pos = thread.indexOf(id);
    else if (via === 'link') { thread = [...(thread.length ? thread.slice(0, pos + 1) : [current].filter(Boolean)), id]; pos = thread.length - 1; }
    else { thread = [id]; pos = 0; }
    if (!current) mapScroll = window.scrollY;
    if (!trail.length) trail = [...Object.keys(s.marks).filter(x => s.sources[x]), ...planIds()];
    if (!trail.includes(id)) {
      const at = current ? trail.indexOf(current) : -1;
      if (detour && at >= 0) trail.splice(at + 1, 0, id); else trail.push(id);
    }
    extendTrail();
    const url = new URL(location.href); url.searchParams.set('read', id); url.hash = '';
    if (inCluster && inCluster !== src(id).cluster) url.searchParams.set('in', inCluster); else url.searchParams.delete('in');
    if (thread.length > 1) { url.searchParams.set('thread', thread.join(',')); url.searchParams.set('pos', String(pos)); }
    else { url.searchParams.delete('thread'); url.searchParams.delete('pos'); }
    if (current) history.replaceState({read: id}, '', url); else history.pushState({read: id}, '', url);
    showReading(id);
  }
  function closeReading() {
    const url = new URL(location.href);
    for (const k of ['read', 'in', 'thread', 'pos']) url.searchParams.delete(k);
    history.pushState({}, '', url); route();
  }
  // On a wide screen the text opens beside the map or the list, which stay where they were; on a narrower one
  // reading mode takes the page, with the plan as a rail.
  const wide = matchMedia('(min-width: 1100px)');
  const beside = () => wide.matches;
  function renderBrowse() {  // the map or the list, whichever the URL says
    const list = new URLSearchParams(location.search).get('view') === 'list';
    $('ideas-view').hidden = true; $('map').hidden = false; $('list').hidden = !list; $('map-body').hidden = list;
    $('view-map').setAttribute('aria-pressed', String(!list)); $('view-list').setAttribute('aria-pressed', String(list));
    renderHead();
    if (list) renderList(); else renderMap();
    if (!current) setWhere(list ? {cluster: null, source: selected, idea: null} : {source: null, idea: null});
  }
  function markNow() {  // the source being read, marked wherever the map or the list shows it
    document.querySelectorAll('#map .now').forEach(n => n.classList.remove('now'));
    if (current) document.querySelectorAll(`#map [data-source="${CSS.escape(current)}"]`).forEach(n => n.classList.add('now'));
  }
  function route() {
    const read = new URLSearchParams(location.search).get('read'), ideasView = location.hash === '#ideas';
    if (read && s.sources[read]) {
      const u = new URLSearchParams(location.search), ids = (u.get('thread') || '').split(',').filter(x => s.sources[x]), at = Number(u.get('pos'));
      if (ids.length > 1 && ids[at] === read) { thread = ids; pos = at; } else if (thread[pos] !== read) { thread = [read]; pos = 0; }
      if (!trail.length) trail = [...Object.keys(s.marks).filter(x => s.sources[x]), ...planIds()];
      if (!trail.includes(read)) trail.push(read);
      if (beside()) renderBrowse();
      if (read !== current || !beside() || $('reading').hidden) showReading(read); else markNow();
      return;
    }
    const wasBeside = $('stage').classList.contains('side');
    current = null; $('stage').classList.remove('side'); $('reading').hidden = true; $('ideas-view').hidden = !ideasView; $('map').hidden = ideasView;
    toggleDrawer(false);
    if (ideasView) { renderIdeas(); return; }
    renderBrowse(); markNow();
    if (!wasBeside) requestAnimationFrame(() => window.scrollTo({top: mapScroll}));  // beside, the map never moved
  }
  async function showReading(id) {
    const side = beside();
    current = id; $('stage').classList.toggle('side', side); $('reading').hidden = false; $('pop').hidden = true;
    $('ideas-view').hidden = true; $('map').hidden = !side;
    if (side) markNow();
    const x = src(id), number = numbered();
    const opened = new URLSearchParams(location.search).get('in'), there = opened && opened !== x.cluster ? placementOf(opened, id) : null;
    $('r-eyebrow').textContent = [number.get(id) ? `#${number.get(id)}` : null, there ? `${clusterName(opened)}, also here` : clusterName(x.cluster),
      there ? there.role : x.role].filter(Boolean).join(' · ');
    $('r-title').textContent = x.title;
    $('r-meta').textContent = [x.source, x.author, day(x.time), `${x.minutes} min`, gainText(id)].filter(Boolean).join(' · ');
    $('r-gist-text').textContent = (x.gist || x.hook) + (x.summary === 'lead' ? ' (its opening lines: no summary yet)' : '');
    $('r-gist').open = !s.marks[id];
    $('r-text').replaceChildren(el('p', 'Opening the text…', 'quiet')); $('ctx-body').replaceChildren(); $('r-tools').replaceChildren();
    renderRail(); renderBar(); renderThread();
    if (side) $('reading').scrollTop = 0; else window.scrollTo({top: 0});
    const from = new URLSearchParams(location.search).get('in');
    setWhere({cluster: from && cluster(from) ? from : x.cluster, source: id, idea: null});
    try {
      await loadContext(id);
      if (current !== id) return;
      highlight = null; renderText(contexts.get(id)); renderContext(contexts.get(id));
      if (from && from !== x.cluster) showPassage(contexts.get(id), from);
    } catch (error) { $('r-text').replaceChildren(el('p', error.message, 'quiet')); }
  }
  async function loadContext(id, again = false) {
    if (contexts.has(id) && !again) return;
    const r = await fetch(`/api/source/${encodeURIComponent(id)}/context?${subjectParams}`);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
    contexts.set(id, body);
  }
  async function refreshContext() {  // what is new to you changed: the Ideas section says so
    const id = current; contexts.clear();
    try { await loadContext(id); if (current === id) { renderContext(contexts.get(id)); $('r-meta').textContent = [src(id).source, src(id).author, day(src(id).time), `${src(id).minutes} min`, gainText(id)].filter(Boolean).join(' · '); } } catch (e) { /* the old context stays */ }
  }
  let translated = true;
  function renderText(ctx) {
    const tools = $('r-tools'); tools.replaceChildren();
    if (ctx.translation.length) {
      const t = button(translated ? 'Translation · show the original' : 'Original · show the translation', () => { translated = !translated; renderText(ctx); });
      t.setAttribute('aria-pressed', String(translated)); tools.append(t);
    }
    if (highlight) tools.append(button(`Marked: ${highlight} · clear`, () => { highlight = null; renderText(ctx); }));
    if (ctx.url && /^https?:/.test(ctx.url)) { const a = link('Open the original ↗', ctx.url); a.target = '_blank'; a.rel = 'noopener noreferrer'; tools.append(a); }
    $('r-text').replaceWith(textBox(ctx));
  }
  function textBox(ctx) {
    // The whole source; each passage behind an Idea of this source gets a quiet underline that opens what else says it.
    const box = el('div', null, 'text'); box.id = 'r-text';
    if (!ctx.text) { box.append(el('p', 'The text of this source is not in the vault.', 'quiet')); return box; }
    const ideaOf = new Map(ctx.ideas.map(i => [i.id, i]));
    const spans = ctx.spans.filter(x => x.idea && ideaOf.has(x.idea));
    const name = highlight ? new RegExp(`(${highlight.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi') : null;
    const write = (target, str) => {
      if (!name) { target.append(document.createTextNode(str)); return; }
      str.split(name).forEach((part, k) => target.append(k % 2 ? el('mark', part, 'name-mark') : document.createTextNode(part)));
    };
    const piece = (str, a, b, cls) => {
      const hits = spans.filter(x => x.start < b && x.end > a);
      if (!hits.length) { if (cls) { const n = el('span', null, cls); write(n, str); box.append(n); } else write(box, str); return; }
      const ids = [...new Set(hits.map(x => x.idea))];
      const m = el('mark', null, 'idea-mark' + (ids.some(i => ideaOf.get(i).new && ideaOf.get(i).in_scope) ? ' new' : ''));
      m.dataset.start = a; m.dataset.end = b;
      write(m, str); m.dataset.ideas = ids.join(' '); m.tabIndex = 0; m.setAttribute('role', 'button');
      const open = e => { popover(m, ids.map(i => ideaOf.get(i)), e); setWhere({idea: ids[0]}); };
      m.addEventListener('click', open); m.addEventListener('keydown', e => e.key === 'Enter' && open(e));
      box.append(m);
    };
    if (translated && ctx.translation.length) {
      let at = 0;
      for (const line of ctx.translation) {
        const gap = ctx.text.slice(at, line.start);
        if (gap.trim()) piece(gap, at, line.start, 'untranslated'); else if (at) box.append('\n'.repeat(Math.max(1, (gap.match(/\n/g) || []).length)));
        piece(line.text, line.start, line.end); at = line.end;
      }
      if (ctx.text.slice(at).trim()) piece(ctx.text.slice(at), at, ctx.text.length, 'untranslated');
    } else {
      const cuts = [...new Set([0, ctx.text.length, ...spans.flatMap(x => [x.start, x.end])])].filter(x => x >= 0 && x <= ctx.text.length).sort((a, b) => a - b);
      for (let i = 0; i + 1 < cuts.length; i++) piece(ctx.text.slice(cuts[i], cuts[i + 1]), cuts[i], cuts[i + 1]);
    }
    return box;
  }
  function popover(anchor, list, e) {
    e?.stopPropagation();
    const pop = $('pop'); pop.replaceChildren();
    for (const i of list) {
      const box = el('div', null, 'pop-idea');
      box.append(el('p', i.title, 'pop-title'), el('p', i.in_scope ? (i.new ? 'New to you' : 'Already covered by what you read') : 'Not part of this subject', 'small quiet'));
      if (i.also.length) {
        const p = el('p', 'Also in: ', 'small');
        i.also.slice(0, 4).forEach((o, n) => { if (n) p.append(', '); p.append(button(short(src(o)?.title || o, 50), () => openSource(o, true), 'link-btn')); });
        if (i.also.length > 4) p.append(` and ${i.also.length - 4} more`);
        box.append(p);
      } else box.append(el('p', 'No other source says it.', 'small quiet'));
      pop.append(box);
    }
    const r = anchor.getBoundingClientRect(), host = $('reading').getBoundingClientRect();
    pop.style.left = `${Math.max(8, Math.min(r.left - host.left, host.width - 348))}px`; pop.style.top = `${r.bottom - host.top + $('reading').scrollTop + 6}px`;
    pop.hidden = false;
  }
  document.addEventListener('click', e => { if (!$('pop').hidden && !$('pop').contains(e.target)) $('pop').hidden = true; });

  // A link's word as seen from the source being read: what this source does to the other, or the other to it.
  const WORDS = {disputes: ['disputes', 'disputed by'], retells: ['retells', 'retold by'], based_on: ['based on', 'basis for'],
    supports: ['supports', 'supported by'], updates: ['updates', 'updated by'], builds_on: ['builds on', 'built on by'],
    cites: ['cites', 'cited by'], same_author: ['same author', 'same author'], shares: ['shares an Idea', 'shares an Idea']};
  const linkWord = l => (WORDS[l.type] || [l.word, l.word])[l.direction === 'earlier' ? 0 : 1];
  function linkBetween(a, b) { return s.links.find(l => (l.from === a && l.to === b) || (l.from === b && l.to === a)); }
  function renderThread() {
    const box = $('thread'); box.hidden = thread.length < 2;
    if (thread.length < 2) { box.replaceChildren(); return; }
    box.replaceChildren(el('span', 'Thread', 'th-label'), ...thread.flatMap((id, k) => {
      const b = button('', () => { if (k !== pos) openSource(id, false, null, 'station'); }, 'station' + (k === pos ? ' here' : '') + (k > pos ? ' ahead' : ''));
      b.append(el('span', k + 1, 'st-n'), el('span', short(src(id).title, 34), 'st-t')); b.title = src(id).title;
      if (k === pos) b.setAttribute('aria-current', 'step');
      const link = k ? linkBetween(thread[k - 1], id) : null;  // the word from the station before
      const word = link ? (WORDS[link.type] || [link.word, link.word])[link.from === thread[k - 1] ? 0 : 1] : '→';
      return k ? [el('span', word, 'st-link'), b] : [b];
    }));
  }
  function showPassage(ctx, clusterId) {  // the passage of the first Idea that put this source in that cluster
    const place = ctx.placements.find(p => p.cluster === clusterId);
    if (!place?.passage) return;
    const [start] = place.passage, marks = [...document.querySelectorAll('#r-text mark.idea-mark')];
    const m = marks.find(n => +n.dataset.start <= start && start < +n.dataset.end) || marks.find(n => n.dataset.ideas.split(' ').some(i => place.ideas.includes(i)));
    if (!m) return;
    requestAnimationFrame(() => { m.scrollIntoView({block: 'center', behavior: 'auto'}); m.classList.remove('flash'); void m.offsetWidth; m.classList.add('flash'); });
  }
  function renderRail() {
    const number = numbered(), list = trail.filter(id => s.sources[id]);
    $('rail-plan').replaceChildren(...list.map(id => {
      const li = el('li'), b = button('', () => openSource(id));
      b.append(el('span', null, `dot s-${stateOf(id)}`), el('span', number.get(id) ? `#${number.get(id)}` : '·', 'n'), el('span', short(src(id).title, 46), 't'));
      if (id === current) b.setAttribute('aria-current', 'true');
      if (!number.has(id)) b.classList.add('detour');
      li.append(b); return li;
    }));
    const mini = graph(200, 150, {labels: false, focus: current}); $('mini').replaceChildren(mini); mini.settle();
    $('strip').replaceChildren(...list.map(id => {
      const b = button('', () => openSource(id), 'strip-dot' + (id === current ? ' here' : ''));
      b.append(el('span', null, `dot s-${stateOf(id)}`)); if (id === current) b.append(el('span', `#${number.get(id) || '·'} ${short(src(id).title, 36)}`));
      b.title = src(id).title; return b;
    }));
  }
  function nextAfter(id) {
    const list = trail.filter(x => s.sources[x]), at = list.indexOf(id);
    return list.slice(at + 1).find(x => !s.marks[x]) || planIds().find(x => x !== id) || null;
  }
  function renderBar() {
    const id = current, bar = $('r-bar'); bar.replaceChildren();
    const marks = el('div', null, 'marks');
    for (const [st, label] of [['read', '✓ Mark read'], ['skimmed', '◐ Skim']]) {
      const b = button(label, () => setState(id, stateOf(id) === st ? 'unread' : st)); b.setAttribute('aria-pressed', String(stateOf(id) === st)); marks.append(b);
    }
    bar.append(marks);
    const next = nextAfter(id);
    if (next) {
      const number = numbered(), b = button('', () => openSource(next), 'next'), cross = src(next).cluster !== src(id).cluster;
      const edge = cross && s.edges.find(e => [e.a, e.b].includes(src(id).cluster) && [e.a, e.b].includes(src(next).cluster));
      b.append(el('small', cross ? `Next cluster: ${clusterName(src(next).cluster)}` + (edge ? `, through “${short(s.ideas[edge.label].title.replace(/\.$/, ''), 60)}”` : '') : 'Next'),
        `${number.get(next) ? '#' + number.get(next) + ' ' : ''}${short(src(next).title, 60)} ▸`);
      bar.append(b);
    } else bar.append(el('span', 'Nothing left in the plan.', 'small quiet'));
    const keys = el('p', null, 'keys');
    keys.append('Keys: ', el('kbd', 'J'), '/', el('kbd', 'K'), ' along the plan · ', el('kbd', 'R'), ' read · ', el('kbd', 'S'), ' skim · ', el('kbd', 'Esc'), ' map');
    bar.append(keys);
  }

  // --- the context panel: one section open at a time, each item saying how it relates to this source ---
  function section(key, title, count, chips, body) {
    const det = el('details', null, 'ctx-sec'); det.dataset.key = key; det.open = openCtx === key;
    const sum = el('summary'), top = el('span', null, 'chips');
    chips.slice(0, 3).forEach(c => top.append(el('span', short(c, 24), 'chip muted')));
    sum.append(el('b', title), el('span', count, 'count'), top);
    det.append(sum, body);
    det.addEventListener('toggle', () => {
      if (!det.open) return;
      openCtx = key; document.querySelectorAll('.ctx-sec').forEach(o => { if (o !== det) o.open = false; });
    });
    return det;
  }
  function renderContext(ctx) {
    const body = $('ctx-body'); body.replaceChildren();
    // Connected: typed links, grouped by type, each with the reason the data gives and whether it goes deeper;
    // following one adds a station to the thread. Retellings are a count, never an option.
    const conn = el('div'), byType = new Map();
    for (const l of ctx.links) { const w = linkWord(l); if (!byType.has(w)) byType.set(w, []); byType.get(w).push(l); }
    for (const [word, list] of byType) {
      conn.append(el('p', word, 'ctx-label'));
      for (const l of list) {
        const b = button('', () => openSource(l.source, true, null, 'link'), 'ctx-row link-row');
        const why = el('span', short(l.label.replace(/\.$/, ''), 120), 'why');
        b.append(el('span', null, `dot s-${stateOf(l.source)}`), el('span', short(src(l.source).title, 70), 't'),
          el('span', short(clusterName(src(l.source).cluster), 30), 'tag'), why);
        if (l.deeper) b.append(el('span', 'deeper', 'deeper'));
        if (l.retold.length) b.append(el('span', `retold by ${l.retold.length}`, 'retold'));
        conn.append(b);
      }
    }
    if (ctx.retold_by.length) {
      const r = el('p', `Retold by ${ctx.retold_by.length}`, 'retold-line');
      r.title = ctx.retold_by.map(i => src(i)?.title || i).join('\n'); conn.append(r);
    }
    if (!ctx.links.length) conn.append(el('p', 'Nothing links this source to another yet.', 'small quiet'));
    body.append(section('connected', 'Connected', ctx.links.length, [...byType.keys()], conn));

    const ppl = el('div');
    for (const p of ctx.people) {
      const b = button('', () => {
        if (highlight !== p.name) { highlight = p.name; renderText(ctx); document.querySelector('.name-mark')?.scrollIntoView({block: 'center', behavior: smooth()}); return; }
        if (b.nextSibling?.classList?.contains('person')) b.nextSibling.remove(); else b.after(person(p));
      }, 'ctx-row');
      b.append(el('span', initials(p.name), 'av'), el('span', p.name, 't'), el('span', [p.role, p.affiliation].filter(Boolean).join(', '), 'tag'),
        el('span', `${p.relation}${p.elsewhere ? ` · in ${plural(p.elsewhere, 'other source')}` : ''}`, 'why'));
      b.title = 'Click to mark them in the text; again for their card';
      ppl.append(b);
    }
    if (!ctx.people.length) ppl.append(el('p', 'No person is named in it.', 'small quiet'));
    body.append(section('people', 'People', ctx.people.length, ctx.people.map(p => p.name), ppl));

    const org = el('div');
    for (const o of ctx.orgs) {
      const b = button('', () => { highlight = highlight === o.name ? null : o.name; renderText(ctx); document.querySelector('.name-mark')?.scrollIntoView({block: 'center', behavior: smooth()}); }, 'ctx-row');
      b.append(el('span', initials(o.name), 'av org'), el('span', o.name, 't'), el('span', o.type && o.type !== 'other' ? o.type.replace('_', ' ') : '', 'tag'),
        el('span', `${o.relation}${o.elsewhere ? ` · in ${plural(o.elsewhere, 'other source')}` : ''}`, 'why'));
      org.append(b);
    }
    if (!ctx.orgs.length) org.append(el('p', 'No organisation is named in it.', 'small quiet'));
    body.append(section('orgs', 'Organisations', ctx.orgs.length, ctx.orgs.map(o => o.name), org));

    const ideas = el('div'), scoped = ctx.ideas.filter(i => i.in_scope);
    for (const [label, list] of [['New to you', scoped.filter(i => i.new)], ['Already covered', scoped.filter(i => !i.new)], ['Outside this subject', ctx.ideas.filter(i => !i.in_scope)]]) {
      if (!list.length) continue;
      ideas.append(el('p', label, 'ctx-label'));
      for (const i of list) ideas.append(button(i.title, () => { showIdea(i.id); setWhere({idea: i.id}); }, 'ctx-idea'));
    }
    body.append(section('ideas', 'Ideas in this source', ctx.ideas.length, scoped.filter(i => i.new).map(i => i.title), ideas));
    body.append(link('Check in Atlas ↗', '/atlas/?item=' + encodeURIComponent(ctx.id), 'atlas-link'));
    $('ctx-open').textContent = `${ctx.people.length} ${ctx.people.length === 1 ? 'person' : 'people'} · ${ctx.links.length} connected`;
  }
  function person(p) {
    const box = el('div', null, 'person');
    if (p.role || p.affiliation) box.append(el('p', [p.role, p.affiliation].filter(Boolean).join(', ')));
    if (p.domains.length) box.append(el('p', 'Works on: ' + p.domains.join(' · '), 'small'));
    p.said.forEach(t => box.append(el('p', '“' + t + '”', 'small quiet')));
    if (!box.childElementCount) box.append(el('p', 'Nothing more is known of them yet.', 'small quiet'));
    return box;
  }
  function toggleDrawer(open) { $('ctx').classList.toggle('open', open); $('ctx-open').setAttribute('aria-expanded', String(open)); }
  $('ctx-open').addEventListener('click', () => toggleDrawer(!$('ctx').classList.contains('open')));
  $('ctx-close').addEventListener('click', () => toggleDrawer(false));

  // --- All Ideas, grouped by cluster ---
  let dossier = null;
  async function renderIdeas() {
    const box = $('sections');
    if (!dossier) {
      box.replaceChildren(el('p', 'Gathering the Ideas…', 'quiet'));
      const r = await fetch('/api/dossier?' + new URLSearchParams(subjectQuery.id ? {topic: subjectQuery.id} : {q: subjectQuery.q}));
      dossier = r.ok ? await r.json().catch(() => null) : null;
      if (!dossier) { box.replaceChildren(el('p', 'Could not gather the Ideas.', 'quiet')); return; }
    }
    const rows = dossier.ideas.filter(r => ['core', 'supporting', 'fading'].includes(r.tier));
    const sameWhy = new Set(rows.map(r => r.why)).size === 1;  // a line every entry shares says nothing on each
    const groups = new Map();
    for (const r of rows) { const c = s.ideas[r.id]?.cluster, key = cluster(c) ? c : 'other'; if (!groups.has(key)) groups.set(key, []); groups.get(key).push(r); }
    const rank = key => key === 'other' ? 99 : s.clusters.indexOf(cluster(key));
    box.replaceChildren(el('h1', `${plural(rows.length, 'Idea')} on ${s.scope.title}`, 'ideas-title'));
    if (sameWhy && rows.length > 1) box.append(el('p', `Every one of them: ${rows[0].why}.`, 'small quiet'));
    for (const [key, list] of [...groups].sort((a, b) => rank(a[0]) - rank(b[0]))) {
      const sec = el('section', null, 'sec'), head = el('header'), h = el('h2', key === 'other' ? 'Other clusters' : clusterName(key));
      head.append(h); if (cluster(key)) head.append(el('span', cluster(key).band, 'band ' + cluster(key).band)); sec.append(head);
      const ul = el('div', null, 'ideas');
      for (const r of list) {
        const c = el('article', null, `idea tier-${r.tier}`), m = el('div', null, 'meta');
        m.append(el('span', r.tier, 'chip' + (r.tier === 'core' ? '' : ' muted')), el('span', plural(r.sources, 'source'), 'chip muted'));
        if (r.contested) m.append(el('span', 'contested', 'chip warn'));
        c.append(el('h3', r.title), m);
        if (!sameWhy) c.append(el('p', r.why, 'small quiet'));
        const srcs = el('div', null, 'src-list');
        r.items.slice(0, 4).forEach(i => { if (s.sources[i]) srcs.append(button(short(s.sources[i].title, 50), () => openSource(i), 'src')); });
        c.append(srcs); ul.append(c);
      }
      sec.append(ul); box.append(sec);
    }
  }
  $('ideas-back').addEventListener('click', e => { e.preventDefault(); history.pushState({}, '', location.pathname + location.search); route(); });
  window.addEventListener('hashchange', route);

  // --- keys and history ---
  $('start').addEventListener('click', () => { const n = nextId(); if (n) openSource(n); });
  $('back').addEventListener('click', e => { e.preventDefault(); closeReading(); });
  $('pane-close').addEventListener('click', closeReading);
  wide.addEventListener('change', () => { if (current) route(); });
  window.addEventListener('popstate', route);
  document.addEventListener('keydown', e => {
    if (e.metaKey || e.ctrlKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || !current) return;
    const key = e.key.toLowerCase(), list = trail.filter(x => s.sources[x]), at = list.indexOf(current);
    if (e.key === 'Escape') { if (!$('pop').hidden) $('pop').hidden = true; else if ($('ctx').classList.contains('open')) toggleDrawer(false); else closeReading(); }
    else if (key === 'j' && list[at + 1]) openSource(list[at + 1]);
    else if (key === 'k' && at > 0) openSource(list[at - 1]);
    else if (key === 'r') setState(current, 'read');
    else if (key === 's') setState(current, 'skimmed');
  });

  fetch('/api/subject?' + subjectParams).then(async r => {
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
    s = body;
    document.title = `Refresher — ${s.scope.title}`;
    $('kind').textContent = {topic: 'A topic you accepted', cluster: 'A cluster of close Ideas', query: 'What you asked about'}[s.scope.kind];
    $('title').textContent = s.scope.kind === 'query' ? `“${s.scope.title}”` : s.scope.title;
    const main = s.clusters.find(c => c.band === 'main'); if (main) openBlocks.add(main.id);
    $('scope').append(...s.topics.map(t => { const o = el('option', short(t.title, 48)); o.value = t.id; return o; }));
    $('notice').hidden = true;
    route();
  }).catch(error => {
    $('notice').textContent = '';
    $('notice').append(el('p', error.message, 'serif'), el('p', 'Try another subject, or pick one of your topics.', 'quiet'), link('← All topics', '/'));
  });
})();

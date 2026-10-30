/* A subject: the graph of clusters, the main cluster pinned at the centre, one cluster opened into its sources. */
'use strict';
(() => {
  const B = window.RefresherTopic;
  const {$, GRAPH_CAP, cluster, clusterName, nextId, planIds, plural, s, short, shown, smooth, src, st, svg} = B;
  const {beside, closeReading, nextAfter, openSource, renderFar, showBlock} = B.later;
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
      const go = () => { if (st.current && !beside()) closeReading(); if (n.id !== 'other') showBlock(n.id); else { shown.add('far'); renderFar(); $('far').scrollIntoView({behavior: smooth()}); } };
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

  Object.assign(B, {graph});
})();

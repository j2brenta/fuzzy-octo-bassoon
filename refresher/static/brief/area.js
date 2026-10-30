/* The brief: the area map (topics hold clusters, clusters hold source dots), small and in a dialog. */
'use strict';
(() => {
  const B = window.RefresherBrief;
  const {$, c, el, ideaColour, path, plural, readHref, sid} = B;
  const {open} = B.later;
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
  Object.assign(B, {mapCluster, markHere, showMap});
})();

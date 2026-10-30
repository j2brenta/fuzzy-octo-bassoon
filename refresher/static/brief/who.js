/* The brief: a person or organization in a dialog, with a mini map of who is tied to them. */
'use strict';
(() => {
  const B = window.RefresherBrief;
  const {$, day, el, plural, query, readHref, sid} = B;
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

  Object.assign(B, {whoOpen});
})();

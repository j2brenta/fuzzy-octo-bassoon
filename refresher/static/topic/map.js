/* A subject, map mode: the header, the reading plan, one block per cluster, far sources at the end. */
'use strict';
(() => {
  const B = window.RefresherTopic;
  const {$, button, cluster, clusterName, day, el, gainText, graph, nextId, numbered, onSubject, openBlocks,
    pct, plural, s, setState, short, shown, smooth, src, st, stateOf} = B;
  const {depthBar, markNow, openSource, setWhere} = B.later;
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
    box.append(el('span', parts.length > 1 ? `About: ${parts.length} clusters` : 'About', 'about-label'));
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
    $('counts').textContent = `${plural(live.length, 'source')} · ${plural(onSubj.length, 'cluster')} on the subject · ${far} far · ${noise} overlapping or less relevant`;
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
          sec.append(button(`${plural(c.noise.length, 'overlapping source')} · ${shown.has(key) ? 'hide' : 'show'}`,
            () => { shown.has(key) ? shown.delete(key) : shown.add(key); renderBlocks(); }, 'link-btn more-line'));
          if (shown.has(key)) c.noise.forEach(id => sec.append(row(id, null, null, placementOf(c.id, id))));
        }
      } else if (c.perspectives.length || c.noise.length) {
        sec.append(button(`${[c.perspectives.length && plural(c.perspectives.length, 'perspective'), c.noise.length && plural(c.noise.length, 'overlapping source')].filter(Boolean).join(' · ')} · open`,
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
    const g = graph(300, 250, {open: st.where.cluster || s.clusters.find(c => c.band === 'main')?.id});
    $('graph').replaceChildren(g); g.settle();
  }
  function showBlock(id) {
    openBlocks.add(id); renderBlocks(); setWhere({cluster: id, source: null, idea: null});
    const b = $('block-' + id); if (b) { b.scrollIntoView({behavior: smooth(), block: 'start'}); b.classList.remove('flash'); void b.offsetWidth; b.classList.add('flash'); }
  }

  Object.assign(B, {drawGraph, placementOf, renderFar, renderHead, renderMap, showBlock});
})();

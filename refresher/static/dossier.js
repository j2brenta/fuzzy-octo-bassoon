/* A topic's dossier: the brief on the left, the Atlas embedded on the right and linked both ways. The Atlas is the
   same page as /atlas/, opened with ?embed and driven through its window.atlas API (same origin). Values from the
   dossier only enter textContent. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = String(text); if (cls) n.className = cls; return n; };
  const button = (text, action, cls) => { const b = el('button', text, cls); b.type = 'button'; b.addEventListener('click', action); return b; };
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const day = iso => new Date(iso).toLocaleDateString('en', {day: 'numeric', month: 'short', year: 'numeric'});
  const smooth = () => matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth';
  const TIERS = {core: 'core', supporting: 'supporting', fading: 'fading', replaced: 'replaced', noise: 'noise'};
  const params = new URLSearchParams(location.search);
  const query = new URLSearchParams();
  if (params.get('id')) query.set('topic', params.get('id')); else query.set('q', params.get('q') || '');
  if (params.get('at')) query.set('at', params.get('at'));

  let d = null, atlas = null, steering = false;
  const cards = new Map(), ideaOfClaim = new Map();

  // --- the brief ---
  function section(id, n, title, intro) {
    const s = el('section', null, 'sec'); s.id = id; s.setAttribute('aria-labelledby', id + '-h');
    const head = el('header'), text = el('div'), h = el('h2', title); h.id = id + '-h';
    text.append(h); if (intro) text.append(el('p', intro));
    head.append(el('span', n, 'num'), text); s.append(head); return s;
  }
  function source(itemId, label) {
    const s = d.sources[itemId] || {title: itemId};
    const b = button(label || s.title, () => openSource(itemId), 'src');
    b.title = [s.source, s.author, s.time && day(s.time)].filter(Boolean).join(' · ');
    return b;
  }
  function weight(importance, top) {
    const box = el('span', null, 'weight'); box.setAttribute('aria-label', `importance ${importance.toFixed(2)}`);
    const filled = top > 0 ? Math.max(1, Math.round(5 * importance / top)) : 0;
    for (let i = 0; i < 5; i++) box.append(el('i', null, i < filled ? 'on' : ''));
    return box;
  }
  function ideaCard(r, top) {
    const card = el('article', null, `idea tier-${r.tier}`); card.id = 'idea-' + r.id; card.dataset.idea = r.id;
    const head = el('div', null, 'idea-top'), h = el('h3');
    h.append(button(r.title, () => steer(() => atlas?.selectNode(r.current[0] || r.claims[0]))));
    head.append(el('span', r.rank, 'rank'), h);
    const meta = el('div', null, 'meta');
    meta.append(weight(r.importance, top), el('span', r.tier, 'chip' + (r.tier === 'core' ? '' : ' muted')),
      el('span', r.status, 'chip muted'), el('span', plural(r.sources, 'source'), 'chip muted'));
    if (r.band !== 'fresh') meta.append(el('span', r.band, 'chip new'));
    if (r.contested) meta.append(el('span', 'contested', 'chip warn'));
    if (r.provisional) meta.append(el('span', 'joined provisionally', 'chip muted'));
    head.append(meta);
    const body = el('div', null, 'body');
    for (const steps of r.was) {
      const was = el('p', null, 'was'); was.append('Said before: ');
      steps.forEach((s, i) => { if (i) was.append(el('span', '→', 'arrow')); was.append(el('s', s.statement), ` (${day(s.time)})`); });
      was.append(el('span', '→ now as above', 'arrow')); body.append(was);
    }
    for (const x of r.replaced_by) {
      const p = el('p', 'Replaced by ', 'was');
      p.append(x.in_scope ? button(x.title, () => reveal(x.id), 'src') : el('strong', x.title)); body.append(p);
    }
    const why = el('details', null, 'why'), parts = el('div', null, 'parts');
    why.append(el('summary', `Why #${r.rank}`), el('p', r.why));
    for (const [k, label] of [['support', 'independent sources'], ['trust', 'trust in the contributors'], ['central', 'closeness to the subject'],
      ['firm', 'firmed by the review'], ['fresh', 'freshness now']]) {
      const bar = el('span', null, 'bar'), fill = el('span'); fill.style.width = `${Math.round(100 * Math.min(1, r.parts[k]))}%`; bar.append(fill);
      parts.append(el('span', label, 'small quiet'), bar, el('span', r.parts[k].toFixed(2), 'small'));
    }
    if (r.parts.user) parts.append(el('span', 'your votes', 'small quiet'), el('span'), el('span', r.parts.user.toFixed(2), 'small'));
    why.append(parts, el('p', `importance ${r.importance.toFixed(2)} = freshness × (0.35 sources + 0.25 trust + 0.25 closeness + 0.15 firm) + your votes`));
    body.append(why);
    const list = el('div', null, 'src-list'); r.items.slice(0, 6).forEach(i => list.append(source(i)));
    if (r.items.length > 6) list.append(el('span', `and ${r.items.length - 6} more`, 'small quiet'));
    body.append(list); head.append(body); card.append(head);
    card.addEventListener('mouseenter', () => light(r.id)); card.addEventListener('mouseleave', () => light(null));
    cards.set(r.id, card); r.claims.forEach(c => ideaOfClaim.set(c, r.id));
    return card;
  }
  function render() {
    document.title = `Refresher — ${d.scope.title}`;
    $('kind').textContent = {topic: 'Dossier · a topic you accepted', cluster: 'Dossier · a cluster of close Ideas', query: 'Dossier · what you asked about'}[d.scope.kind];
    $('title').textContent = d.scope.kind === 'query' ? `“${d.scope.title}”` : d.scope.title;
    const c = d.counts;
    $('meta').textContent = `As of ${day(d.at)} · ${plural(c.ideas, 'Idea')} from ${plural(c.items, 'source')}`;
    $('tier-bar').replaceChildren(...Object.keys(TIERS).filter(t => c[t]).map(t => { const s = el('span', null, 't-' + t); s.style.flex = String(c[t]); return s; }));
    $('legend').replaceChildren(...Object.keys(TIERS).map(t => { const s = el('span'); s.append(el('i', null, 't-' + t), `${c[t]} ${t}`); return s; }));
    const top = Math.max(0, ...d.ideas.map(r => r.importance));
    const shown = d.ideas.filter(r => ['core', 'supporting', 'fading'].includes(r.tier));
    const hidden = d.ideas.filter(r => ['replaced', 'noise'].includes(r.tier));
    const parts = [];

    const view = section('view', '1', 'The current view', 'Ranked by how many independent sources say it, how far you trust them, how close it sits to the subject and how fresh it is.');
    const core = el('div', null, 'ideas'); shown.filter(r => r.tier === 'core').forEach(r => core.append(ideaCard(r, top)));
    view.append(core);
    const rest = shown.filter(r => r.tier !== 'core');
    if (rest.length) {
      const more = el('details', null, 'more'); more.open = rest.length <= 8;
      more.append(el('summary', `${plural(rest.length, 'more Idea')}: supporting and fading`));
      const list = el('div', null, 'ideas'); rest.forEach(r => list.append(ideaCard(r, top))); more.append(list); view.append(more);
    }
    if (!shown.length) view.append(el('p', 'Nothing current here: every Idea was replaced or is noise.', 'quiet'));
    parts.push([view, shown.length]);

    const changed = section('changed', '2', 'What changed', 'Claims a newer one replaced, oldest first. The newer word ranks; the older one stays for the record.');
    const changes = el('ol', null, 'changes');
    for (const chain of d.changed) {
      const li = el('li', null, 'change');
      chain.steps.forEach((s, i) => {
        const now = i === chain.steps.length - 1, row = el('div', null, 'step ' + (now ? 'now' : 'old')), text = el('div');
        text.append(el('p', s.statement)); const where = el('div', null, 'where'); where.append(`${day(s.time)} · `, source(s.item));
        text.append(where); row.append(el('span', now ? 'now' : 'was', 'label'), text); li.append(row);
      });
      if (chain.steps.at(-1).idea && cards.has(chain.steps.at(-1).idea)) li.append(button('Show the Idea ↑', () => reveal(chain.steps.at(-1).idea), 'src'));
      changes.append(li);
    }
    changed.append(d.changed.length ? changes : el('p', 'Nothing has been replaced yet.', 'quiet'));
    parts.push([changed, d.changed.length]);

    const contested = section('contested', '3', 'Contested', 'Sources that disagree. Neither is dropped; read both.');
    for (const pair of d.contested) {
      const box = el('div', null, 'pair');
      for (const [side, s] of [['a', pair.a], ['b', pair.b]]) {
        const card = el('div', null, 'side'); card.append(el('p', s.statement));
        const where = el('div', null, 'small quiet'); where.append(`${day(s.time)} · `, source(s.item)); card.append(where);
        box.append(card); if (side === 'a') box.append(el('span', '⟷', 'vs'));
      }
      contested.append(box); if (pair.rationale) contested.append(el('p', pair.rationale, 'pair-why'));
    }
    if (!d.contested.length) contested.append(el('p', 'No conflicts between your sources here.', 'quiet'));
    parts.push([contested, d.contested.length]);

    const read = section('read', '4', 'Read in order', 'The fewest sources that cover the most important Ideas, weighted by how far you trust their authors; originals before reposts.');
    const list = el('ol', null, 'reading');
    for (const r of d.reading) {
      const li = el('li', null, 'read'), body = el('div'), h = el('h3');
      h.append(button(r.title, () => openSource(r.item)));
      body.append(h, el('p', [r.source, r.author, day(r.time)].filter(Boolean).join(' · '), 'where'), el('p', r.reason, 'reason'));
      if (r.url && /^https?:/.test(r.url)) { const a = el('a', 'Open the original ↗', 'small'); a.href = r.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; body.append(a); }
      li.append(el('span', r.rank, 'n'), body); list.append(li);
    }
    read.append(d.reading.length ? list : el('p', 'No source to recommend yet.', 'quiet'));
    if (d.also.length) {
      const more = el('details', null, 'more'); more.append(el('summary', `Also (${d.also.length}), oldest first`));
      const box = el('div', null, 'src-list'); d.also.forEach(r => box.append(source(r.item))); more.append(box); read.append(more);
    }
    parts.push([read, d.reading.length]);

    const near = d.nearby, nearby = section('nearby', '5', 'Nearby', 'Where this subject leads: linked topics and clusters, and the people in it.');
    const groups = [['Linked', near.linked, r => r.evidence ? 'backed by relations or shared Ideas' : 'similar'],
      ['Clusters inside', near.clusters, r => plural(r.ideas, 'Idea') + ' here'], ['In topics', near.topics, r => plural(r.ideas, 'Idea') + ' here']];
    for (const [label, rows, sub] of groups) {
      if (!rows.length) continue;
      const box = el('div', null, 'pills');
      rows.forEach(r => { const a = el('a', null, 'pill'); a.href = '/topic/?id=' + encodeURIComponent(r.id); a.append(r.title, el('small', sub(r))); box.append(a); });
      nearby.append(el('p', label, 'label-row'), box);
    }
    if (near.people.length) {
      const box = el('div', null, 'pills');
      near.people.forEach(p => { const b = button('', () => steer(() => atlas?.selectPerson(p.id, p.score)), 'pill person');
        b.append(p.name, el('small', `${p.importance == null && p.about ? `${p.about} sourced fact${p.about === 1 ? '' : 's'}` : `${p.importance == null ? 'unscored' : 'contribution rank #' + p.score.rank} · stated ${p.stated} · ${p.score.evidence_level} evidence`}`)); box.append(b); });
      nearby.append(el('p', 'People', 'label-row'), box);
    }
    const nearCount = near.linked.length + near.clusters.length + near.topics.length + near.people.length;
    if (!nearCount) nearby.append(el('p', 'Nothing linked yet.', 'quiet'));
    parts.push([nearby, nearCount]);

    const noise = section('noise', '6', 'Replaced and noise', 'Kept for the record: Ideas a newer one overtook, reposts and short-lived claims that expired.');
    if (hidden.length) {
      const more = el('details', null, 'more'); more.id = 'noise-list';
      more.append(el('summary', `Show ${plural(hidden.length, 'Idea')}`));
      const box = el('div', null, 'ideas'); hidden.forEach(r => box.append(ideaCard(r, top))); more.append(box); noise.append(more);
    } else noise.append(el('p', 'No noise here.', 'quiet'));
    parts.push([noise, hidden.length]);

    $('sections').replaceChildren(...parts.map(p => p[0]));
    $('toc').replaceChildren(...parts.map(([s, n]) => { const a = el('a', s.querySelector('h2').textContent); a.href = '#' + s.id; a.append(el('span', n)); return a; }));
    $('notice').hidden = true; $('head').hidden = false; $('split').hidden = false;
    watchScroll();
  }

  // --- the link between the brief and the map ---
  function light(ideaId) {
    cards.forEach((c, id) => c.classList.toggle('lit', id === ideaId));
    if (atlas) atlas.spotlight(ideaId ? [ideaId] : []);
  }
  function steer(action) { steering = true; try { action(); } finally { steering = false; } }
  function openSource(itemId) {  // a source picked in the brief: unfold the map if it is folded and show the source's text
    if (narrow.matches && $('pane').classList.contains('closed')) $('wide').click();
    steer(() => { atlas?.selectItem(itemId); atlas?.showDetail(); });
  }
  function reveal(ideaId) {
    const card = cards.get(ideaId); if (!card) return;
    const fold = card.closest('details'); if (fold) fold.open = true;
    card.scrollIntoView({behavior: smooth(), block: 'center'});
    card.classList.remove('flash'); void card.offsetWidth; card.classList.add('flash');
  }
  function ideaFor(kind, id) {  // the highest-ranked Idea in the brief behind what was picked on the map
    if (kind === 'claim') return ideaOfClaim.get(id);
    const ranked = d.ideas;
    if (kind === 'item') return ranked.find(r => r.items.includes(id))?.id;
    if (kind === 'group') {
      const cluster = atlas.data.clusters?.find(c => c.id === id), topic = atlas.data.topics.find(t => t.id === id);
      const ids = new Set(cluster ? cluster.ideas : topic ? [...topic.ideas, ...topic.attached] : []);
      return ranked.find(r => ids.has(r.id))?.id;
    }
    return ideaOfClaim.get(id) || ranked.find(r => r.items.includes(id))?.id;
  }
  function connect() {
    const frame = $('atlas');
    try { atlas = frame.contentWindow.atlas || null; } catch (e) { atlas = null; }
    if (!atlas || !d) return;
    atlas.scope(Object.keys(d.sources).filter(i => !d.sources[i].hidden));
    atlas.onselect = (kind, id) => { if (steering) return; const idea = ideaFor(kind, id); if (idea) reveal(idea); };
  }
  function watchScroll() {
    if (!('IntersectionObserver' in window)) return;
    // The card crossing the middle of the screen lights up on the map, unless the pointer is on another one.
    const seen = new IntersectionObserver(entries => {
      const hit = entries.find(e => e.isIntersecting); if (!hit || document.querySelector('.idea:hover')) return;
      light(hit.target.dataset.idea);
    }, {rootMargin: '-45% 0px -50% 0px'});
    cards.forEach(c => seen.observe(c));
  }
  window.addEventListener('message', e => { if (e.origin === location.origin && e.data?.type === 'atlas-ready') connect(); });
  $('atlas').addEventListener('load', connect);
  // On a narrow screen the map is a band above the brief, folded until asked for; on a wide one it can take the page.
  const narrow = matchMedia('(max-width: 1100px)');
  function label() {
    const open = narrow.matches ? !$('pane').classList.contains('closed') : document.body.classList.contains('wide');
    $('wide').setAttribute('aria-pressed', String(open));
    $('wide').textContent = narrow.matches ? (open ? 'Hide the map' : 'Show the map') : open ? '← Back to the brief' : '⤢ Wide map';
  }
  if (narrow.matches) $('pane').classList.add('closed');
  narrow.addEventListener('change', label); label();
  $('wide').addEventListener('click', () => {
    if (narrow.matches) $('pane').classList.toggle('closed'); else document.body.classList.toggle('wide');
    label();
    if (atlas) setTimeout(() => { atlas.cy.resize(); atlas.cy.fit(atlas.cy.elements(), 34); }, 50);
  });

  fetch('/api/dossier?' + query).then(async r => {
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
    d = body; render(); connect();
  }).catch(error => {
    $('notice').textContent = '';
    $('notice').append(el('p', error.message, 'serif'), el('p', 'Try another subject, or pick one of your topics.', 'quiet'));
    const back = el('a', '← All topics'); back.href = '/'; $('notice').append(back);
  });
})();

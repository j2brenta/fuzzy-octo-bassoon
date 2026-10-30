/* The brief: the major ideas as cards, their history and their constellation. */
'use strict';
(() => {
  const B = window.RefresherBrief;
  const {$, c, chip, cited, day, el, ideaColour, ideaVar, plural, readHref, sid, source, sourceLabel, svg} = B;
  const {mapCluster} = B.later;
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

  Object.assign(B, {constellation, ideaCard});
})();

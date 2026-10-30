/* A subject, reading mode: the text with the passages behind its Ideas marked, the thread, the rail and the bar. */
'use strict';
(() => {
  const B = window.RefresherTopic;
  const {$, button, clusterName, el, graph, link, numbered, openSource, planIds, s, setState, setWhere,
    short, src, st, stateOf} = B;
  let translated = true;
  function renderText(ctx) {
    const tools = $('r-tools'); tools.replaceChildren();
    if (ctx.translation.length) {
      const t = button(translated ? 'Translation · show the original' : 'Original · show the translation', () => { translated = !translated; renderText(ctx); });
      t.setAttribute('aria-pressed', String(translated)); tools.append(t);
    }
    if (st.highlight) tools.append(button(`Marked: ${st.highlight} · clear`, () => { st.highlight = null; renderText(ctx); }));
    if (ctx.url && /^https?:/.test(ctx.url)) { const a = link('Open the original ↗', ctx.url); a.target = '_blank'; a.rel = 'noopener noreferrer'; tools.append(a); }
    $('r-text').replaceWith(textBox(ctx));
  }
  function textBox(ctx) {
    // The whole source; each passage behind an Idea of this source gets a quiet underline that opens what else says it.
    const box = el('div', null, 'text'); box.id = 'r-text';
    if (!ctx.text) { box.append(el('p', 'The text of this source is not in the vault.', 'quiet')); return box; }
    const ideaOf = new Map(ctx.ideas.map(i => [i.id, i]));
    const spans = ctx.spans.filter(x => x.idea && ideaOf.has(x.idea));
    const name = st.highlight ? new RegExp(`(${st.highlight.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, 'gi') : null;
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
    const box = $('thread'); box.hidden = st.thread.length < 2;
    if (st.thread.length < 2) { box.replaceChildren(); return; }
    box.replaceChildren(el('span', 'Thread', 'th-label'), ...st.thread.flatMap((id, k) => {
      const b = button('', () => { if (k !== st.pos) openSource(id, false, null, 'station'); }, 'station' + (k === st.pos ? ' here' : '') + (k > st.pos ? ' ahead' : ''));
      b.append(el('span', k + 1, 'st-n'), el('span', short(src(id).title, 34), 'st-t')); b.title = src(id).title;
      if (k === st.pos) b.setAttribute('aria-current', 'step');
      const link = k ? linkBetween(st.thread[k - 1], id) : null;  // the word from the station before
      const word = link ? (WORDS[link.type] || [link.word, link.word])[link.from === st.thread[k - 1] ? 0 : 1] : '→';
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
    const number = numbered(), list = st.trail.filter(id => s.sources[id]);
    $('rail-plan').replaceChildren(...list.map(id => {
      const li = el('li'), b = button('', () => openSource(id));
      b.append(el('span', null, `dot s-${stateOf(id)}`), el('span', number.get(id) ? `#${number.get(id)}` : '·', 'n'), el('span', short(src(id).title, 46), 't'));
      if (id === st.current) b.setAttribute('aria-current', 'true');
      if (!number.has(id)) b.classList.add('detour');
      li.append(b); return li;
    }));
    const mini = graph(200, 150, {labels: false, focus: st.current}); $('mini').replaceChildren(mini); mini.settle();
    $('strip').replaceChildren(...list.map(id => {
      const b = button('', () => openSource(id), 'strip-dot' + (id === st.current ? ' here' : ''));
      b.append(el('span', null, `dot s-${stateOf(id)}`)); if (id === st.current) b.append(el('span', `#${number.get(id) || '·'} ${short(src(id).title, 36)}`));
      b.title = src(id).title; return b;
    }));
  }
  function nextAfter(id) {
    const list = st.trail.filter(x => s.sources[x]), at = list.indexOf(id);
    return list.slice(at + 1).find(x => !s.marks[x]) || planIds().find(x => x !== id) || null;
  }
  function renderBar() {
    const id = st.current, bar = $('r-bar'); bar.replaceChildren();
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

  Object.assign(B, {linkWord, nextAfter, renderBar, renderRail, renderText, renderThread, showPassage});
})();

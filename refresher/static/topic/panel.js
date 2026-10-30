/* A subject, reading mode: the context panel, one section open at a time. */
'use strict';
(() => {
  const B = window.RefresherTopic;
  const {$, button, clusterName, el, initials, link, linkWord, openSource, plural, renderText, setWhere,
    short, showIdea, smooth, src, st, stateOf} = B;
  // --- the context panel: one section open at a time, each item saying how it relates to this source ---
  function section(key, title, count, chips, body) {
    const det = el('details', null, 'ctx-sec'); det.dataset.key = key; det.open = st.openCtx === key;
    const sum = el('summary'), top = el('span', null, 'chips');
    chips.slice(0, 3).forEach(c => top.append(el('span', short(c, 24), 'chip muted')));
    sum.append(el('b', title), el('span', count, 'count'), top);
    det.append(sum, body);
    det.addEventListener('toggle', () => {
      if (!det.open) return;
      st.openCtx = key; document.querySelectorAll('.ctx-sec').forEach(o => { if (o !== det) o.open = false; });
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
        if (st.highlight !== p.name) { st.highlight = p.name; renderText(ctx); document.querySelector('.name-mark')?.scrollIntoView({block: 'center', behavior: smooth()}); return; }
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
      const b = button('', () => { st.highlight = st.highlight === o.name ? null : o.name; renderText(ctx); document.querySelector('.name-mark')?.scrollIntoView({block: 'center', behavior: smooth()}); }, 'ctx-row');
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
    body.append(link('Explore connections →', '/atlas/?item=' + encodeURIComponent(ctx.id) + '&return=' + encodeURIComponent(location.pathname + location.search), 'atlas-link'));
    $('ctx-open').textContent = 'Source details';
  }
  function person(p) {
    const box = el('div', null, 'person');
    if (p.role || p.affiliation) box.append(el('p', [p.role, p.affiliation].filter(Boolean).join(', ')));
    if (p.domains.length) box.append(el('p', 'Works on: ' + p.domains.join(' · '), 'small'));
    p.said.forEach(t => box.append(el('p', '“' + t + '”', 'small quiet')));
    if (!box.childElementCount) box.append(el('p', 'Nothing more is known of them yet.', 'small quiet'));
    return box;
  }
  function toggleDrawer(open) {
    $('ctx').classList.toggle('open', open); $('ctx-open').setAttribute('aria-expanded', String(open));
    if (open) { $('ctx').setAttribute('role','dialog'); $('ctx').setAttribute('aria-modal','true'); $('ctx-close').focus(); }
    else { $('ctx').removeAttribute('role'); $('ctx').removeAttribute('aria-modal'); if ($('ctx').contains(document.activeElement)) $('ctx-open').focus(); }
  }
  document.addEventListener('keydown', e => {
    if (e.key !== 'Tab' || !$('ctx').classList.contains('open')) return;
    const focusable = [...$('ctx').querySelectorAll('a[href],button:not([disabled]),[tabindex="0"]')].filter(n => n.getClientRects().length);
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  });
  $('evidence-toggle').addEventListener('click',()=>{const show=$('reading').classList.toggle('show-evidence');$('evidence-toggle').setAttribute('aria-pressed',String(show));});
  $('ctx-open').addEventListener('click', () => toggleDrawer(!$('ctx').classList.contains('open')));
  $('ctx-close').addEventListener('click', () => toggleDrawer(false));

  Object.assign(B, {renderContext, toggleDrawer});
})();

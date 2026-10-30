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
   Values from the vault only enter textContent.
   The page is the files of static/topic/, loaded in order by dossier.html: core.js (helpers, the subject and the
   state the files share) first, this one (All Ideas, keys, loading) last; each adds its functions to
   window.RefresherTopic. */
'use strict';
(() => {
  const B = window.RefresherTopic;
  const {$, button, closeReading, cluster, clusterName, el, link, nextId, openBlocks, openSource, plural,
    route, s, setState, short, st, subjectParams, subjectQuery, toggleDrawer} = B;
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

  window.addEventListener('popstate', route);
  document.addEventListener('keydown', e => {
    if (e.metaKey || e.ctrlKey || e.altKey || /^(INPUT|TEXTAREA|SELECT)$/.test(e.target.tagName) || !st.current) return;
    const key = e.key.toLowerCase(), list = st.trail.filter(x => s.sources[x]), at = list.indexOf(st.current);
    if (e.key === 'Escape') { if (!$('pop').hidden) $('pop').hidden = true; else if ($('ctx').classList.contains('open')) toggleDrawer(false); else closeReading(); }
    else if (key === 'j' && list[at + 1]) openSource(list[at + 1]);
    else if (key === 'k' && at > 0) openSource(list[at - 1]);
    else if (key === 'r') setState(st.current, 'read');
    else if (key === 's') setState(st.current, 'skimmed');
  });

  const slow = setTimeout(() => { $('notice').textContent = 'Still working: reading what you asked about can take a few seconds…'; }, 5000);
  fetch('/api/subject?' + subjectParams).then(async r => {
    clearTimeout(slow);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(body.error || `HTTP ${r.status}`), {named: body.named || []});
    Object.assign(s, body);
    document.title = `Refresher — ${s.scope.title}`;
    window.RefresherJourney?.subject(s.scope.title);
    $('kind').textContent = {topic: 'A topic you accepted', cluster: 'A cluster of close Ideas', query: 'What you asked about'}[s.scope.kind]
      + (s.scope.note ? ` · ${s.scope.note}` : '');
    $('title').textContent = s.scope.kind === 'query' ? `“${s.scope.title}”` : s.scope.title;
    const main = s.clusters.find(c => c.band === 'main'); if (main) openBlocks.add(main.id);
    $('scope').append(...s.topics.map(t => { const o = el('option', short(t.title, 48)); o.value = t.id; return o; }));
    $('notice').hidden = true;
    route();
  }).catch(error => {
    clearTimeout(slow);
    $('notice').textContent = '';
    $('notice').classList.add('subject-empty');
    $('notice').append(el('h1', 'No matching subject'), el('p', error.message, 'quiet'));
    const requestedSource = new URLSearchParams(location.search).get('read');
    if (requestedSource && /^[\w-]{1,200}$/.test(requestedSource)) {
      $('notice').append(el('p', 'You can still open the saved article without this subject filter.', 'quiet'),
        link('Read saved article →', '/library/?source=' + encodeURIComponent(requestedSource), 'journey-primary'));
    }
    if (error.named?.length) {  // no Idea matches, but sources name who or what was asked about
      const list = el('ul', null, 'named-list');
      error.named.forEach(x => { const li = el('li'); li.append(link(x.title, '/read/?read=' + encodeURIComponent(x.id)), el('span', ` · ${x.source || ''} · names ${x.names.join(', ')}`, 'small quiet')); list.append(li); });
      $('notice').append(el('p', `No Idea matches, but ${error.named.length === 1 ? 'one source names' : error.named.length + ' sources name'} what you asked about. What they say is about them (their profile), which a topic does not rank:`, 'quiet'), list);
    }
    $('notice').append(el('p', 'Try another subject, or pick one of your topics.', 'quiet'), link('← All topics', '/'));
  });
  Object.assign(B, {renderIdeas});
})();

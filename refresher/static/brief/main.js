/* The brief (/brief/?q=|id=): a top band that stays (title, keywords, one quiet line with the date range, versions,
   save, Markdown; a one-line banner on what arrived since the last saved version), then three slots that keep their
   jobs. Overview: the brief in the centre (major ideas, sources, further reading), a narrow panel with the mini map,
   people and organizations. Reading: opening a source slides the same idea cards into a compact rail, puts the source
   in the centre with what the brief takes tinted by idea, and turns the narrow panel to the source (the map in the
   same place, its people, what was extracted by idea, kind as a label). Further reading is a dialog there. The
   breadcrumb holds the path; Esc steps back. The panel ranks whom to listen to; any person or organization opens in a
   dialog with a mini map of who is tied to them, where a click opens the next. Values only enter textContent.
   The page is the files of static/brief/, loaded in order by context.html: core.js (helpers and the shared state)
   first, this one (the top band, loading, the page's own events) last; each adds its functions to
   window.RefresherBrief. */
'use strict';
(() => {
  const B = window.RefresherBrief;
  const {$, back, c, chip, constellation, day, el, go, ideaCard, ideaVar, layout, mapCluster, open, path,
    plural, post, query, readHref, short, showMap, sid, since, st, subject, toggleVersions, versions} = B;
  function nextList(groups) {
    const out = [];
    let n = 0;
    for (const g of groups) {
      const sec = el('section', null, 'ce-next-group');
      if (g.colour != null) sec.style.setProperty('--idea', ideaVar(g.colour));
      sec.append(el('h3', g.group.length > 90 ? g.group.slice(0, 89) + '…' : g.group, 'ce-next-title'));
      const ol = el('ol', null, 'ce-next');
      for (const r of g.rows) {
        n += 1;
        const li = el('li', null, r.read ? 'read' : '');
        const a = el('a', r.title); a.href = readHref(r.item);
        li.append(el('span', String(n), 'n'), a, el('p', r.thesis, 'thesis'),
          el('p', [r.why, r.source, r.read ? (r.read === 'read' ? '✓ read' : 'skimmed') : null].filter(Boolean).join(' · '), 'why'));
        ol.append(li);
      }
      sec.append(ol);
      const extra = [g.more ? `${g.more} more here` : '', g.rewrites ? `${plural(g.rewrites, 'rewrite')} not listed` : ''].filter(Boolean).join(' · ');
      if (extra) sec.append(el('p', extra, 'small quiet'));
      out.push(sec);
    }
    return out.length ? out : [el('p', 'Nothing further on this subject yet.', 'small quiet')];
  }

  // While reading, further reading is a dialog: from the source you are on, then on the brief as a whole.
  function furtherDialog() {
    const body = $('further-from'); body.replaceChildren();
    const current = st.current;
    if (current && !current.error) body.append(el('h3', `From ${sid(current.id) || ''} ${short(current.title || '', 60)}`.trim(), 'ce-section'), ...nextList(current.next));
    body.append(el('h3', 'On the brief', 'ce-section'), ...nextList(c.further || []));
    $('further-dialog').showModal();
  }
  $('further-open').addEventListener('click', furtherDialog);
  window.addEventListener('resize', () => { layout(); if (matchMedia('(min-width: 1001px)').matches) contextDrawer(false); });

  // A source link anywhere on the page: from the overview it starts a path; from a source it goes one step further.
  document.addEventListener('click', e => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey) return;
    const u = new URL(a.href, location.href);
    const same = (u.searchParams.get('q') || '') === (subject.q || '') && (u.searchParams.get('id') || '') === (subject.id || '');
    if (u.pathname !== '/brief/' || !u.searchParams.get('read') || !same) return;
    e.preventDefault();
    const box = a.closest('dialog'); if (box) box.close();
    const item = u.searchParams.get('read').split(',').pop();
    open(item);
  });
  window.addEventListener('popstate', () => { const r = new URLSearchParams(location.search).get('read'); go(r ? r.split(',') : [], null, false); });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && path.length && !document.querySelector('dialog[open]')) { e.preventDefault(); back(); }
  });
  document.querySelectorAll('.ce-fold').forEach(b => b.addEventListener('click', () => {
    const open = b.getAttribute('aria-expanded') !== 'true';
    b.setAttribute('aria-expanded', String(open)); $(b.getAttribute('aria-controls')).hidden = !open;
  }));
  $('show-area').addEventListener('click', () => showMap('area'));
  $('show-full').addEventListener('click', () => showMap('full'));
  $('show-ideas').addEventListener('click', () => showMap('ideas'));

  function render() {
    document.title = `Refresher — ${c.scope.title}`;
    $('title').textContent = c.scope.kind === 'query' ? `“${c.scope.title}”` : c.scope.title;
    keywords();
    // The range is every item you have on it, earliest to now; choosing a narrower range comes later.
    const k = c.counts, dates = Object.values(c.sources).map(s => s.when).filter(Boolean).sort();
    $('meta').textContent = [`As of ${day(c.at)}`, dates.length ? `sources ${day(dates[0])} → now` : null,
      `${plural(k.ideas, 'idea')} from ${plural(k.sources, 'source')}`].filter(Boolean).join(' · ');
    $('meta').title = `${plural(k.rewrites, 'rewrite')} collapsed · ${k.superseded} superseded`;
    $('write').hidden = !c.written.missing;
    since(c.thread); versions(c.thread);
    $('follow-subject').textContent = c.thread ? 'Following this subject' : 'Follow subject';
    $('follow-subject').disabled = Boolean(c.thread);
    window.RefresherJourney?.subject(c.scope.title);
    const first = Object.keys(c.sources).sort((a,b) => (Number((sid(a)||'S999').slice(1))-Number((sid(b)||'S999').slice(1))))[0];
    if (first) {
      const resume = window.RefresherJourney?.data.resume;
      const previous = resume && window.RefresherJourney.safe(resume.href) && new URL(resume.href, location.origin);
      const same = previous?.pathname === '/brief/' && (previous.searchParams.get('id') || '') === (subject.id || '') && (previous.searchParams.get('q') || '') === (subject.q || '');
      const last = same && previous.searchParams.get('read')?.split(',').pop();
      $('subject-start').href = last && c.sources[last] ? resume.href : readHref(first);
      $('subject-start').textContent = last && c.sources[last] ? 'Continue reading →' : 'Start reading →';
      $('subject-start').hidden = false;
    }
    if (!c.thread?.stamps?.length) { $('since').hidden = false; $('since').replaceChildren(el('p', 'First overview — save a checkpoint to compare future changes.', 'ce-since-line')); }
    $('ideas').replaceChildren(...c.major.map((x, n) => ideaCard(x, n + 1)));
    $('ideas-note').textContent = plural(c.major.length, 'major idea');
    $('also-box').hidden = !c.also.length;
    $('also').replaceChildren(...c.also.map(x => { const li = el('li'); li.append(el('span', x.title), document.createTextNode(' '), chip(x.base, 'muted')); return li; }));
    $('further').replaceChildren(...nextList(c.further || []));
    constellation(c.major);
    $('top').hidden = false; $('grid').hidden = false;
    showMap((c.area?.clusters || []).filter(k => k.in_brief).length > 1 ? 'area' : 'full');
    $('notice').textContent = c.major.length ? '' : 'Nothing in your knowledge is on this subject yet.';
    const reading = new URLSearchParams(location.search).get('read');
    go(reading ? reading.split(',') : [], null, false);
  }

  function load() {
    return fetch('/api/brief?' + query).then(async r => {
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw Object.assign(new Error(body.error || `HTTP ${r.status}`), {named: body.named || []});
      for (const k of Object.keys(c)) delete c[k];
      Object.assign(c, body); render();
      return c;
    });
  }

  async function writeBrief(auto) {
    $('write').disabled = true; $('write').textContent = 'Writing…';
    $('notice').textContent = `Writing ${plural(c.written.missing, 'paragraph')} from your sources…`;
    try { await post('/api/brief/write', subject); await load(); }
    catch (e) { $('notice').textContent = auto ? `The theses stand in for the paragraphs: ${e.message}` : e.message; }
    finally { $('write').disabled = false; $('write').textContent = 'Write the brief'; }
  }

  // Keywords: what you asked, then the topics and named clusters the brief spans (each shows itself on the map; a
  // cluster still titled by a sentence of its source is left out). People and organizations are in the narrow panel.
  function keywords() {
    const box = $('keywords'); box.replaceChildren();
    if (c.scope.kind === 'query') box.append(el('span', c.scope.title, 'kw query'));
    const area = c.area || {clusters: [], topics: []}, mine = new Set(area.clusters.filter(k => k.in_brief).map(k => k.id));
    const named = [...(area.topics || []).filter(t => t.clusters.some(k => mine.has(k))), ...area.clusters.filter(k => k.in_brief)]
      .filter(k => k.title && k.title.length <= 40 && k.title.split(/\s+/).length <= 5 && !/[.!?:]$/.test(k.title));
    for (const k of named) {
      const b = el('button', k.title, 'kw area'); b.type = 'button';
      b.addEventListener('click', () => mapCluster(k.id)); box.append(b);
    }
    box.hidden = !box.childNodes.length;
  }

  // Save version: this brief as of now, kept with its date (a thread is started on the first save).
  $('save').addEventListener('click', async () => {
    const b = $('save'); b.disabled = true; b.textContent = 'Saving…';
    try {
      const slug = c.thread ? c.thread.thread.slug : (await post('/api/threads', {...subject, title: c.scope.title})).slug;
      const s = await post(`/api/threads/${encodeURIComponent(slug)}/stamp`, {});
      await load(); $('notice').textContent = `Saved this version, as of ${day(s.at)}.`;
    } catch (e) { $('notice').textContent = e.message; }
    finally { b.disabled = false; b.textContent = 'Save checkpoint'; }
  });
  $('follow-subject').addEventListener('click', async () => {
    const b = $('follow-subject'); b.disabled = true;
    try { await post('/api/threads', {...subject, title: c.scope.title}); await load(); $('notice').textContent = 'Following this subject. Save a checkpoint when you want to compare future changes.'; }
    catch (e) { b.disabled = false; $('notice').textContent = e.message; }
  });
  function contextDrawer(open) {
    $('side').classList.toggle('context-open', open); $('context-toggle').setAttribute('aria-expanded', String(open));
    if (open) { $('side').setAttribute('role', 'dialog'); $('side').setAttribute('aria-modal', 'true'); $('context-close').focus(); }
    else { $('side').removeAttribute('role'); $('side').removeAttribute('aria-modal'); }
  }
  $('context-toggle').addEventListener('click', () => contextDrawer(true));
  $('context-close').addEventListener('click', () => { contextDrawer(false); $('context-toggle').focus(); });
  document.addEventListener('keydown', e => {
    if (!$('side').classList.contains('context-open')) return;
    if (e.key === 'Escape') { e.preventDefault(); e.stopImmediatePropagation(); contextDrawer(false); $('context-toggle').focus(); }
    if (e.key === 'Tab') {
      const nodes = [...$('side').querySelectorAll('a[href],button:not([disabled]),[tabindex="0"]')].filter(n => n.getClientRects().length);
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }
  }, true);
  $('versions').addEventListener('click', e => { e.stopPropagation(); toggleVersions($('versions-list').hidden); });
  document.addEventListener('click', e => { if (!e.target.closest('.ce-versions')) toggleVersions(false); });
  $('write').addEventListener('click', () => writeBrief(false));
  document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => b.closest('dialog').close()));

  if (!subject.id && !subject.q) { $('notice').textContent = 'Ask about a subject from the home page.'; return; }
  load().catch(error => {
    $('notice').textContent = '';
    $('notice').append(el('p', error.message, 'serif'));
    if (error.named?.length) {
      const list = el('ul');
      error.named.forEach(x => { const li = el('li'); const a = el('a', x.title); a.href = '/library/?source=' + encodeURIComponent(x.id); li.append(a, el('span', ` · ${x.source || ''} · names ${x.names.join(', ')}`, 'small quiet')); list.append(li); });
      $('notice').append(el('p', 'Sources that name what you asked about:', 'quiet'), list);
    }
  });
  Object.assign(B, {contextDrawer, furtherDialog});
})();

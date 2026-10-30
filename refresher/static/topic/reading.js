/* A subject, reading mode: the depth rail, the thread you followed, the route between map, list and text. */
'use strict';
(() => {
  const B = window.RefresherTopic;
  const {$, button, cluster, clusterName, contexts, day, drawGraph, el, focusPlacement, gainText, numbered,
    placementOf, planIds, plural, renderHead, renderList, renderMap, s, setView, short, smooth, src,
    st, subjectParams, subjectQuery} = B;
  const {renderBar, renderContext, renderIdeas, renderRail, renderText, renderThread, showPassage,
    toggleDrawer} = B.later;
  // --- the depth rail: All > subject > cluster > source > Idea, with counts and position (GET /api/path) ---
  const paths = new Map();
  function setWhere(change) {
    const moved = 'cluster' in change && change.cluster !== st.where.cluster;
    st.where = {...st.where, ...change}; renderPath();
    if (moved && !st.current && !$('map-body').hidden) drawGraph();
  }
  function pathKey() { const q = new URLSearchParams(subjectQuery); for (const [k, v] of Object.entries(st.where)) if (v) q.set(k, v); return q.toString(); }
  async function renderPath() {
    const key = pathKey();
    if (!paths.has(key)) {
      const r = await fetch('/api/path?' + key), body = await r.json().catch(() => ({}));
      if (!r.ok) return;
      paths.set(key, body.levels);
    }
    if (key !== pathKey()) return;  // the selection moved on while this loaded
    const levels = paths.get(key), last = levels.length - 1;
    $('path-list').replaceChildren(...levels.map((lv, n) => {
      const li = el('li', null, 'lv-' + lv.level), name = lv.level === 'subject' && lv.kind === 'query' ? `“${lv.name}”`
        : lv.level === 'idea' ? `Idea §${lv.position}` : lv.level === 'all' ? lv.name : short(lv.name, 44);
      const facts = [lv.position && lv.level !== 'idea' ? `${lv.position} of ${lv.of}` : lv.level === 'idea' ? `of ${lv.of}` : null,
        lv.count != null && lv.noun ? plural(lv.count, lv.noun) : null].filter(Boolean).join(' · ');
      const go = {all: () => { location.href = '/'; }, subject: () => { st.selected = null; setView('map'); requestAnimationFrame(() => window.scrollTo({top: 0})); },
        cluster: () => focusPlacement(lv.id, null), source: () => openSource(lv.id, false, st.where.cluster),
        idea: () => showIdea(lv.id)}[lv.level];
      const b = button('', go, 'lv'); b.title = lv.name;
      b.append(el('span', name, 'lv-name')); if (facts) b.append(el('small', facts));
      if (n === last) b.setAttribute('aria-current', 'location');
      li.append(b); return li;
    }));
    $('path').hidden = false;
  }
  function showIdea(ideaId) {  // the passage behind an Idea of the source being read
    const m = [...document.querySelectorAll('#r-text mark.idea-mark')].find(n => n.dataset.ideas.split(' ').includes(ideaId));
    if (m) { m.scrollIntoView({block: 'center', behavior: smooth()}); m.classList.remove('flash'); void m.offsetWidth; m.classList.add('flash'); }
  }

  // --- reading mode ---
  function extendTrail() {  // the path you walk: what you read, then the plan; detours sit after the source they left
    for (const id of planIds()) if (!st.trail.includes(id)) st.trail.push(id);
  }
  // The thread: the sources you pulled one link at a time. Following a link adds a station after the one you are
  // on (from an earlier station that starts a branch: the stations after it are let go); an earlier station takes
  // you back along it; opening a source any other way starts a new thread. It lives in the URL (&thread=&pos=).
  function openSource(id, detour = false, inCluster = null, via = null) {  // inCluster: the cluster it was opened from
    if (!s.sources[id]) return;
    window.RefresherJourney?.position();
    st.selected = id;
    if (via === 'station') st.pos = st.thread.indexOf(id);
    else if (via === 'link') { st.thread = [...(st.thread.length ? st.thread.slice(0, st.pos + 1) : [st.current].filter(Boolean)), id]; st.pos = st.thread.length - 1; }
    else { st.thread = [id]; st.pos = 0; }
    if (!st.current) st.mapScroll = window.scrollY;
    if (!st.trail.length) st.trail = [...Object.keys(s.marks).filter(x => s.sources[x]), ...planIds()];
    if (!st.trail.includes(id)) {
      const at = st.current ? st.trail.indexOf(st.current) : -1;
      if (detour && at >= 0) st.trail.splice(at + 1, 0, id); else st.trail.push(id);
    }
    extendTrail();
    const url = new URL(location.href); url.searchParams.set('read', id); url.hash = '';
    if (inCluster && inCluster !== src(id).cluster) url.searchParams.set('in', inCluster); else url.searchParams.delete('in');
    if (st.thread.length > 1) { url.searchParams.set('thread', st.thread.join(',')); url.searchParams.set('pos', String(st.pos)); }
    else { url.searchParams.delete('thread'); url.searchParams.delete('pos'); }
    history.pushState({read: id}, '', url);
    showReading(id);
  }
  function closeReading() {
    window.RefresherJourney?.position();
    const url = new URL(location.href);
    for (const k of ['read', 'in', 'thread', 'pos']) url.searchParams.delete(k);
    history.pushState({}, '', url); route();
  }
  // Opening a source gives the article the page; the map remains in the browsing view.
  const beside = () => false; // Reading has one focused layout at every screen size.
  function renderBrowse() {  // the map or the list, whichever the URL says
    const list = new URLSearchParams(location.search).get('view') === 'list';
    $('ideas-view').hidden = true; $('map').hidden = false; $('list').hidden = !list; $('map-body').hidden = list;
    $('view-map').setAttribute('aria-pressed', String(!list)); $('view-list').setAttribute('aria-pressed', String(list));
    renderHead();
    if (list) renderList(); else renderMap();
    if (!st.current) setWhere(list ? {cluster: null, source: st.selected, idea: null} : {source: null, idea: null});
  }
  function markNow() {  // the source being read, marked wherever the map or the list shows it
    document.querySelectorAll('#map .now').forEach(n => n.classList.remove('now'));
    if (st.current) document.querySelectorAll(`#map [data-source="${CSS.escape(st.current)}"]`).forEach(n => n.classList.add('now'));
  }
  function route() {
    const read = new URLSearchParams(location.search).get('read'), ideasView = location.hash === '#ideas';
    if (read && s.sources[read]) {
      const u = new URLSearchParams(location.search), ids = (u.get('thread') || '').split(',').filter(x => s.sources[x]), at = Number(u.get('pos'));
      if (ids.length > 1 && ids[at] === read) { st.thread = ids; st.pos = at; } else if (st.thread[st.pos] !== read) { st.thread = [read]; st.pos = 0; }
      if (!st.trail.length) st.trail = [...Object.keys(s.marks).filter(x => s.sources[x]), ...planIds()];
      if (!st.trail.includes(read)) st.trail.push(read);
      if (beside()) renderBrowse();
      if (read !== st.current || !beside() || $('reading').hidden) showReading(read); else markNow();
      return;
    }
    const wasBeside = $('stage').classList.contains('side');
    document.body.classList.remove('source-reading-mode');
    st.current = null; $('stage').classList.remove('side'); $('reading').hidden = true; $('ideas-view').hidden = !ideasView; $('map').hidden = ideasView;
    toggleDrawer(false);
    if (ideasView) { renderIdeas(); return; }
    renderBrowse(); markNow();
    if (!wasBeside) requestAnimationFrame(() => window.scrollTo({top: st.mapScroll}));  // beside, the map never moved
  }
  async function showReading(id) {
    const side = beside();
    document.body.classList.add('source-reading-mode');
    $('tab-brief').href = '/brief/?' + subjectParams + '&read=' + encodeURIComponent(id);
    st.current = id; $('stage').classList.toggle('side', side); $('reading').hidden = false; $('pop').hidden = true;
    $('ideas-view').hidden = true; $('map').hidden = !side;
    if (side) markNow();
    const x = src(id), number = numbered();
    const opened = new URLSearchParams(location.search).get('in'), there = opened && opened !== x.cluster ? placementOf(opened, id) : null;
    $('r-eyebrow').textContent = [number.get(id) ? `#${number.get(id)}` : null, there ? `${clusterName(opened)}, also here` : clusterName(x.cluster),
      there ? there.role : x.role].filter(Boolean).join(' · ');
    $('r-title').textContent = x.title;
    $('r-title').setAttribute('tabindex','-1');
    $('r-meta').textContent = [x.source, x.author, day(x.time), `${x.minutes} min`, gainText(id)].filter(Boolean).join(' · ');
    $('r-gist-text').textContent = (x.gist || x.hook) + (x.summary === 'lead' ? ' (its opening lines: no summary yet)' : '');
    $('r-gist').open = false;
    $('r-gist').hidden = x.summary === 'lead' || !(x.gist || x.hook);
    $('reading').classList.remove('show-evidence');
    $('evidence-toggle').setAttribute('aria-pressed','false');
    $('r-text').replaceChildren(el('p', 'Opening the text…', 'quiet')); $('ctx-body').replaceChildren(); $('r-tools').replaceChildren();
    renderRail(); renderBar(); renderThread();
    if (side) $('reading').scrollTop = 0; else window.scrollTo({top: 0});
    const from = new URLSearchParams(location.search).get('in');
    setWhere({cluster: from && cluster(from) ? from : x.cluster, source: id, idea: null});
    try {
      await loadContext(id);
      if (st.current !== id) return;
      st.highlight = null; renderText(contexts.get(id)); renderContext(contexts.get(id));
      if (from && from !== x.cluster) showPassage(contexts.get(id), from);
      window.RefresherJourney?.subject(s.scope?.title || document.title.replace('Refresher — ', ''));
      window.RefresherJourney?.reading(x.title);
    } catch (error) { $('r-text').replaceChildren(el('p', error.message, 'quiet')); }
  }
  async function loadContext(id, again = false) {
    if (contexts.has(id) && !again) return;
    const r = await fetch(`/api/source/${encodeURIComponent(id)}/context?${subjectParams}`);
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
    contexts.set(id, body);
  }
  async function refreshContext() {  // what is new to you changed: the Ideas section says so
    const id = st.current; contexts.clear();
    try { await loadContext(id); if (st.current === id) { renderContext(contexts.get(id)); $('r-meta').textContent = [src(id).source, src(id).author, day(src(id).time), `${src(id).minutes} min`, gainText(id)].filter(Boolean).join(' · '); } } catch (e) { /* the old context stays */ }
  }
  Object.assign(B, {beside, closeReading, extendTrail, markNow, openSource, refreshContext, renderBrowse, route,
    setWhere, showIdea});
})();

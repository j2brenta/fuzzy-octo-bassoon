/* A subject, all results: every source found in one table, grouped (GET /api/subject/results). */
'use strict';
(() => {
  const B = window.RefresherTopic;
  const {$, button, cluster, day, el, numbered, plural, results, short, showBlock, shown, smooth, src, st,
    stateOf, subjectQuery} = B;
  const {beside, markNow, openSource, route, setWhere} = B.later;
  // --- all results: every source found in one table, grouped (GET /api/subject/results) ---
  const GROUPS = [['cluster_home', 'Cluster, home only'], ['cluster_all', 'Cluster, every place'], ['role', 'Role'],
    ['trust', 'Trust'], ['type', 'Type'], ['topic', 'Topic']];
  function listState() {
    const u = new URLSearchParams(location.search);
    return {group: GROUPS.some(g => g[0] === u.get('group')) ? u.get('group') : 'cluster_home', scope: u.get('scope') || '', all: u.get('all') === '1'};
  }
  function setList(change) {  // the grouping, topic and filter live in the URL, so a reload keeps them
    const url = new URL(location.href);
    for (const [k, v] of Object.entries(change)) { if (v) url.searchParams.set(k, v); else url.searchParams.delete(k); }
    history.replaceState(history.state, '', url); renderList();
  }
  async function loadResults(st) {
    const key = JSON.stringify(st);
    if (!results.has(key)) {
      const q = new URLSearchParams({...subjectQuery, group_by: st.group, only_matches: st.all ? '0' : '1'});
      if (st.scope) q.set('scope', st.scope);
      const r = await fetch('/api/subject/results?' + q), body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
      results.set(key, body);
    }
    return results.get(key);
  }
  const roleCounts = roles => ['anchor', 'perspective', 'noise'].filter(k => roles[k]).map(k => plural(roles[k], k === 'noise' ? 'overlapping source' : k)).join(' · ');
  function depthBar(depth, inputs) {
    const box = el('span', null, 'depth'); box.setAttribute('role', 'img');
    const why = inputs ? `${inputs.words.toLocaleString('en')} words · ${plural(inputs.ideas, 'Idea')} · ${inputs.specific} specific` : '';
    box.setAttribute('aria-label', `depth ${depth} of 5${why ? ': ' + why : ''}`); box.title = why;
    for (let i = 1; i <= 5; i++) box.append(el('i', null, i <= depth ? 'on' : ''));
    return box;
  }
  function placeMark(source, c, p, here) {  // filled at home, hollow elsewhere; its size says the role there
    const b = button('', () => focusPlacement(c.id, source), `mark ${p.home ? 'home' : 'also'} ${p.role}${here ? ' this' : ''}`);
    b.setAttribute('aria-label', `${c.title}: ${p.role}, ${p.home ? 'its home' : 'also here'}. Show it on the map`); b.title = `${c.title} · ${p.role}${p.home ? ' · home' : ' · also here'}`;
    return b;
  }
  function resultRow(row, r) {
    const x = src(row.source), number = numbered().get(row.source);
    const tr = el('tr', null, `r-row${row.match ? '' : ' unmatched'}${row.source === st.selected ? ' selected' : ''}`);
    tr.dataset.source = row.source; tr.tabIndex = 0;
    const open = () => { st.selected = row.source; openSource(row.source); };
    tr.addEventListener('click', e => { if (!e.target.closest('.mark')) open(); });
    tr.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target === tr) open(); });
    const cell = (child, cls) => { const td = el('td', null, cls); if (child != null) td.append(child); return td; };
    const title = el('td', null, 'c-title');
    title.append(el('span', x.title, 't'), el('span', [x.source, x.author, day(x.time)].filter(Boolean).join(' · '), 'sub'));
    const role = el('td', null, 'c-role'); role.append(el('span', row.role, 'role ' + row.role));
    if (row.placement && !row.placement.home) role.append(el('span', 'also here', 'also'));
    const terms = row.matched_terms.length ? row.matched_terms.join(', ') : row.matched_ideas ? plural(row.matched_ideas, 'Idea') : '—';
    tr.append(cell(el('span', null, `dot s-${stateOf(row.source)}`), 'c-dot'), cell(number ? `#${number}` : '', 'c-n'), title, role,
      cell(depthBar(row.depth, row.depth_inputs), 'c-depth'), cell(el('span', row.trust, 'trust ' + row.trust), 'c-trust'),
      cell(row.type, 'c-type'), cell(terms, 'c-terms'));
    for (const c of r.clusters) {
      const p = row.places.find(q => q.cluster === c.id), td = el('td', null, 'c-cluster');
      if (p) td.append(placeMark(row.source, c, p, r.group_by === 'cluster_all' && row.group === c.id));
      tr.append(td);
    }
    return tr;
  }
  async function renderList() {
    const st = listState();
    $('group-by').value = st.group; $('scope').value = st.scope; $('only').value = st.all ? '0' : '1';
    let r;
    try { r = await loadResults(st); } catch (error) { $('list-head').textContent = error.message; return; }
    if (JSON.stringify(listState()) !== JSON.stringify(st)) return;  // another choice was made while this loaded
    const h = r.header, subj = h.kind === 'query' ? `“${h.subject}”` : h.subject;
    const head = $('list-head'); head.replaceChildren(el('strong', `${h.matching} of ${plural(h.total, 'source')}`), ` match ${subj}`);
    if (h.placements) head.append(` · ${plural(h.placements, 'place')} across clusters`);
    if (st.all && h.rows > h.matching) head.append(` · ${h.total - h.matching} more below, outside the subject`);
    const cols = 8 + r.clusters.length;
    $('slices').replaceChildren(...r.slices.map(sl => {
      const b = button('', () => document.querySelector(`tbody[data-group="${CSS.escape(sl.group)}"]`)?.scrollIntoView({behavior: smooth(), block: 'start'}), 'slice');
      b.style.flex = String(sl.count); b.title = `${sl.title} · ${sl.count} · ${roleCounts(sl.roles)}`;
      b.append(el('span', sl.title, 's-name'), el('span', sl.count, 's-count')); return b;
    }));
    const table = $('results'), thead = el('thead'), tr = el('tr');
    for (const [label, cls] of [['', 'c-dot'], ['#', 'c-n'], ['Source', 'c-title'], ['Role', 'c-role'], ['Depth', 'c-depth'],
      ['Trust', 'c-trust'], ['Type', 'c-type'], ['Matches', 'c-terms']]) tr.append(el('th', label, cls));
    for (const c of r.clusters) { const th = el('th', null, `c-cluster ${c.band}`); th.title = `${c.title} · ${c.band}`; th.append(el('span', short(c.title, 24))); tr.append(th); }
    thead.append(tr); table.replaceChildren(thead);
    for (const sl of r.slices) {
      const body = el('tbody'); body.dataset.group = sl.group;
      const g = el('tr', null, 'group'), th = el('th'); th.colSpan = cols;
      th.append(el('span', sl.title, 'g-title'), el('span', `${plural(sl.count, r.group_by === 'cluster_all' ? 'place' : 'source')} · ${roleCounts(sl.roles)}`, 'g-counts'));
      g.append(th); body.append(g);
      r.rows.filter(x => x.group === sl.group).forEach(x => body.append(resultRow(x, r)));
      table.append(body);
    }
    if (!r.rows.length) table.append(el('caption', 'Nothing matches here. Show all sources, or another topic.', 'quiet'));
    const pick = st.selected && table.querySelector(`tr[data-source="${CSS.escape(st.selected)}"]`);
    if (pick && !st.current) requestAnimationFrame(() => pick.scrollIntoView({block: 'center'}));
    markNow();
  }
  function setView(view) {
    const url = new URL(location.href);
    if (view === 'list') url.searchParams.set('view', 'list'); else url.searchParams.delete('view');
    if (!beside()) for (const k of ['read', 'in', 'thread', 'pos']) url.searchParams.delete(k);  // beside, the text stays open
    url.hash = '';
    history.pushState({}, '', url); route();
  }
  function focusPlacement(clusterId, source) {  // a mark in a cluster column: the map, that cluster open, that source picked
    st.selected = source; setView('map');
    const c = cluster(clusterId); if (!c) return;
    if (source && c.noise.includes(source)) shown.add('noise:' + clusterId);
    requestAnimationFrame(() => {  // after the map restored its scroll position
      showBlock(clusterId); setWhere({cluster: clusterId, source, idea: null});
      if (!source) return;
      const row = $('block-' + clusterId)?.querySelector(`[data-source="${CSS.escape(source)}"]`);
      if (!row) return;
      document.querySelectorAll('.picked').forEach(n => n.classList.remove('picked'));
      row.classList.add('picked', 'flash'); row.scrollIntoView({behavior: smooth(), block: 'center'});
    });
  }
  $('group-by').append(...GROUPS.map(([v, label]) => { const o = el('option', label); o.value = v; return o; }));
  $('group-by').addEventListener('change', e => setList({group: e.target.value === 'cluster_home' ? '' : e.target.value}));
  $('scope').addEventListener('change', e => setList({scope: e.target.value}));
  $('only').addEventListener('change', e => setList({all: e.target.value === '0' ? '1' : ''}));
  $('view-map').addEventListener('click', () => setView('map'));
  $('view-list').addEventListener('click', () => setView('list'));

  Object.assign(B, {depthBar, focusPlacement, renderList, setView});
})();

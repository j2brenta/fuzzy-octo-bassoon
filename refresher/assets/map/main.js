/* Refresher Atlas: modes, filters and the public window.atlas; runs last. */
'use strict';
(() => {
  const B = window.RefresherAtlas;
  if (!B) return;  // an empty Atlas wires nothing
  const {$, button, claims, clearDetail, clusters, cy, data, date, el, embedded, filteredItems, fitGraph,
    highlight, items, params, people, plural, reading, renderComparison, renderGraph, renderProposals,
    renderTopics, scoreSubject, selectEdge, selectGroup, selectItem, selectLink, selectNode,
    selectPerson, setLang, state, stories, updateTimelineLink} = B;
  function setMode() {
    for (const other of ['documents', 'claims', 'topics', 'people']) $('mode-' + other).setAttribute('aria-pressed', String(other === state.mode));
    // Claims is a drill-down from a cluster, not a view of everything: its button shows only while you are in it.
    $('mode-claims').hidden = state.mode !== 'claims';
    $('mode-claims').textContent = state.drill ? `Claims · ${(clusters.get(state.drill)?.title || '').slice(0, 40)}` : 'Claims';
  }
  function drill(clusterId) {  // a cluster's claims, the only way into the Claims view
    const c = clusters.get(clusterId); if (!c) return;
    state.drill = clusterId; state.only = new Set(c.items); state.mode = 'claims'; state.ego = false; state.selection = null;
    clearDetail(); setMode(); refresh();
  }
  function refresh() {
    $('people-subject').hidden = state.mode !== 'people';
    $('people-score-note').hidden = state.mode !== 'people';
    $('people-tools').hidden = state.mode !== 'people' || !data.served;
    $('graph-geometry').hidden = state.mode === 'people';
    $('review-filter').hidden = state.mode === 'people';
    $('people-subject').value = scoreSubject();
    renderTopics(); const visible = filteredItems(), allowed = new Set(visible.map(i => i.id));
    const selectedItem = claims.get(state.selection)?.item || state.selection;
    if ((selectedItem && !people.has(selectedItem) && !allowed.has(selectedItem)) || state.detailItems.some(id => !allowed.has(id))) {
      state.selection = null; state.ego = false; clearDetail();
    }
    state.comparison = state.comparison.filter(id => claims.has(id));
    const topic = data.topics.find(t => t.id === state.topic)?.title || (state.topic === 'unassigned' ? 'No topic yet' : 'All topics');
    const story = stories.get(state.story)?.title || 'All Ideas';
    const scope = `${topic} / ${story.slice(0, 60)} · ${visible.length} items · quality: ${$('quality-filter').value || 'any'} · freshness: ${$('band-filter').value || 'any'} · from: ${$('date-filter').value || 'any date'}`;
    const scoringLabel = state.mode === 'people' ? `Scoring subject: ${data.people_subjects?.find(s => s.id === scoreSubject())?.title || 'All subjects'} · ` : '';
    $('reading-scope-text').textContent = scoringLabel + scope; $('compare-scope').textContent = 'Same slice: ' + scope;
    renderGraph(visible); renderComparison(); updateTimelineLink();
    if (reading && !state.view) clearDetail();  // the sources of the new slice
  }
  // Reading, a tap on the map peeks: the evidence and the text you were in stay until you choose to go there.
  function peekNode(id) {
    let box = $('atlas-peek');
    if (!box) { box = el('div', null, 'atlas-peek'); box.id = 'atlas-peek'; $('graph').parentElement.append(box); }
    const item = items.get(id), claim = claims.get(id), person = people.get(id), group = clusters.get(id) || data.topics.find(t => t.id === id);
    const kind = item ? 'SOURCE' : claim ? 'CLAIM' : person ? (person.kind === 'person' ? 'PERSON' : 'ORGANIZATION') : group ? (clusters.has(id) ? 'CLUSTER' : 'TOPIC') : '';
    if (!kind) { box.hidden = true; return; }
    const title = item ? item.title : claim ? claim.statement : person ? person.name : group.title;
    const sub = item ? [item.source, date(item), item.hook].filter(Boolean).join(' · ')
      : group ? plural((clusters.get(id)?.items || data.items.filter(i => i.topics.includes(id))).length, 'source') : '';
    const close = button('×', () => { box.hidden = true; }, 'peek-close');
    const go = button(item ? 'Read this →' : 'Show in the evidence →', () => { box.hidden = true; selectNode(id); }, 'pill');
    box.replaceChildren(close, el('p', kind, 'eyebrow'), el('h3', title, 'detail-title'), sub ? el('p', sub, 'detail-meta') : '', go);
    box.hidden = false;
  }
  cy.on('tap', 'node', e => {
    if (['region', 'cluster-region'].includes(e.target.data('kind'))) return;
    if (reading) peekNode(e.target.id()); else selectNode(e.target.id());
  });
  cy.on('tap', 'edge', e => e.target.data('kind') === 'LINK' ? selectLink(e.target.data())
    : ['WROTE', 'CONTRIBUTES'].includes(e.target.data('kind')) ? selectPerson(e.target.data('source')) : selectEdge(e.target.data()));
  $('people-subject').addEventListener('change', e => {state.peopleSubject = e.target.value; state.topic = ''; state.story = ''; $('story-filter').value = ''; state.selection = null; clearDetail(); refresh();});
  $('story-filter').addEventListener('change', e => {state.story = e.target.value; state.ego = false; refresh();});
  ['quality-filter', 'band-filter', 'date-filter', 'hide-reposts', 'review-filter'].forEach(id => $(id).addEventListener('change', refresh));
  $('show-decided').addEventListener('change', renderProposals);
  $('lang-switch').hidden = !data.items.some(i => i.translation?.length);
  $('lang-translation').title = `Machine translation into ${data.language}, for reading only`;
  for (const lang of ['original', 'translation']) $('lang-' + lang).addEventListener('click', () => setLang(lang));
  for (const lang of ['original', 'translation']) $('lang-' + lang).setAttribute('aria-pressed', String(lang === state.lang));
  $('search').addEventListener('input', refresh);
  for (const mode of ['documents', 'claims', 'topics', 'people']) $('mode-' + mode).addEventListener('click', () => {
    if (mode === state.mode) return;
    if (state.drill) { state.drill = null; state.only = null; }  // leaving a cluster's claims: back to everything
    state.mode = mode; state.ego = false; state.selection = null; clearDetail(); setMode(); refresh();
  });
  $('zoom-in').addEventListener('click', () => cy.zoom({level: cy.zoom() * 1.25, renderedPosition: {x: cy.width() / 2, y: cy.height() / 2}}));
  $('zoom-out').addEventListener('click', () => cy.zoom({level: cy.zoom() / 1.25, renderedPosition: {x: cy.width() / 2, y: cy.height() / 2}}));
  $('fit').addEventListener('click', () => fitGraph());
  $('ego').addEventListener('click', () => {state.ego = true; renderGraph(filteredItems());});
  $('clear-focus').addEventListener('click', () => {state.ego = false; renderGraph(filteredItems());});
  $('clear-compare').addEventListener('click', () => {state.comparison = []; renderComparison();});
  $('reset').addEventListener('click', () => {
    state.topic = ''; state.peopleSubject = ''; state.story = ''; state.ego = false; state.selection = null;
    clearDetail();
    for (const id of ['story-filter', 'quality-filter', 'band-filter', 'date-filter', 'search', 'review-filter']) $(id).value = '';
    $('hide-reposts').checked = false; refresh();
  });
  // An Idea lit up without selecting it: its claims, its cluster or its sources, whichever the mode draws.
  function spotlight(ideaIds) {
    if (!ideaIds?.length) return highlight();
    const ids = ideaIds.flatMap(i => { const s = stories.get(i); if (!s) return [];
      return state.mode === 'claims' ? s.claims : state.mode === 'topics' ? [s.cluster].filter(Boolean) : s.items; });
    const shown = cy.collection(ids.map(id => cy.getElementById(id)).filter(n => n.length));
    cy.elements().removeClass('dim highlight');
    if (!shown.length) return;
    const near = shown.union(shown.ancestors());
    cy.elements().not(near).addClass('dim'); near.addClass('highlight');
  }
  function scope(itemIds) {
    state.only = itemIds ? new Set(itemIds) : null; state.selection = null; state.ego = false; clearDetail(); refresh();
  }
  function showDetail() {  // a pick made from outside (the dossier): its evidence from the top, even when it sits below the map
    $('detail').scrollTop = 0; document.querySelector('.evidence-column').scrollIntoView({block: 'start'});
  }
  if (data.served) window.peopleReview.backfill($('people-tools'));
  for (const [key, id] of Object.entries({quality:'quality-filter', freshness:'band-filter', from:'date-filter', search:'search'})) {
    if (params.has(key)) $(id).value = params.get(key);
  }
  $('hide-reposts').checked = params.get('hide_reposts') === '1';
  if (params.get('topic') === 'unassigned' || data.topics.some(t => t.id === params.get('topic'))) state.topic = params.get('topic') || '';
  setMode(); renderProposals(); refresh();
  // Arriving from a topic's reader or Idea finder: the slice around what was picked, not the whole corpus.
  if (params.get('idea') && stories.has(params.get('idea'))) { state.story = params.get('idea'); $('story-filter').value = state.story; refresh(); }
  if (params.get('cluster') && clusters.has(params.get('cluster'))) drill(params.get('cluster'));
  if (params.get('item') && items.has(params.get('item'))) {
    selectItem(params.get('item')); state.ego = true; renderGraph(filteredItems()); showDetail();
  }
  if (params.get('claim')) selectNode(params.get('claim'));
  if (reading) {
    $('reader').hidden = false;
    if (items.has(params.get('read'))) selectItem(params.get('read'));
  }
  window.atlas = {cy, state, data, refresh, selectItem, selectNode, selectEdge, selectLink, selectPerson, selectGroup, filteredItems,
    spotlight, scope, showDetail, onselect: null};
  if (embedded && window.parent !== window) window.parent.postMessage({type: 'atlas-ready'}, location.origin);
  Object.assign(B, {drill, refresh, setMode, showDetail});
})();

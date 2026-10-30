/* Refresher Atlas: the map's Cytoscape instance, its style, and what each node and topic region is. */
'use strict';
(() => {
  const B = window.RefresherAtlas;
  if (!B) return;  // an empty Atlas wires nothing
  const {$, button, claims, clusters, data, el, people, state, stories} = B;
  const {refresh} = B.later;
  // --- topics, timeline and map ---
  function renderTopics() {
    $('topics').replaceChildren();
    const loose = [...clusters.values()].filter(c => !c.topics.length).length;
    const topics = [{id: '', title: 'All topics', count: data.items.length, groups: clusters.size},
      ...data.topics.map(t => ({...t, count: data.items.filter(i => i.topics.includes(t.id)).length, groups: t.clusters.length})),
      {id: 'unassigned', title: 'No topic yet', count: data.items.filter(i => !i.topics.length).length, groups: loose}];
    for (const topic of topics) {
      const node = button('', () => {state.topic = topic.id; state.peopleSubject = ''; state.story = ''; $('story-filter').value = ''; state.ego = false; refresh();}, 'topic');
      node.setAttribute('aria-pressed', String(state.topic === topic.id));
      const extra = (topic.groups ? ` · ${topic.groups} clusters` : '') + (topic.attached?.length ? ` · ${topic.attached.length} provisional` : '');
      node.append(el('strong', topic.title), el('span', `${topic.count} items${extra}`, 'topic-count')); $('topics').append(node);
    }
  }
  function itemBands(item) { return item.claims.map(id => claims.get(id).band); }
  function filteredItems() {
    const query = $('search').value.toLocaleLowerCase();
    return data.items.filter(item => (!state.only || state.only.has(item.id)) &&
      (!state.topic || (state.topic === 'unassigned' ? !item.topics.length : item.topics.includes(state.topic))) &&
      (!state.story || item.stories.includes(state.story)) &&
      (!$('quality-filter').value || item.quality === $('quality-filter').value) &&
      (!$('band-filter').value || itemBands(item).includes($('band-filter').value)) &&
      (!$('date-filter').value || (item.time && item.time.slice(0, 10) >= $('date-filter').value)) &&
      (!$('hide-reposts').checked || !item.repost_of) &&
      (!query || (item.title + ' ' + item.source + ' ' + (item.author || '')).toLocaleLowerCase().includes(query))
    ).sort((a, b) => (b.time || '').localeCompare(a.time || '') || a.id.localeCompare(b.id));
  }
  // Colours with a meaning come from the shared shell (shell.css), so the map and the other pages agree.
  const token = (name, fallback) => getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  const accepted = token('--r-accepted', '#234cc5');
  const cy = cytoscape({container: $('graph'), elements: [], layout: {name: 'preset'},
    minZoom: .25, maxZoom: 2.5, wheelSensitivity: .2, boxSelectionEnabled: false,
    style: [
      {selector: 'node', style: {'label': 'data(label)', 'shape': 'round-rectangle', 'background-color': token('--r-paper', '#fffcf5'),
        'border-width': 1.2, 'border-color': token('--r-graph-border', '#a9b7e6'), 'width': 185, 'height': 64,
        'font-size': 13, 'color': token('--r-ink', '#25251f'), 'text-wrap': 'wrap', 'text-max-width': 170,
        'text-valign': 'center', 'text-halign': 'center', 'text-overflow-wrap': 'anywhere', 'padding': 5}},
      {selector: 'node[kind="claim"]', style: {'background-color': token('--r-soft', '#edf0fd'), 'border-color': token('--r-graph-link', '#8b9ed9')}},
      {selector: 'node[superseded=1]', style: {'border-style': 'dashed', 'color': token('--r-graph-muted', '#777268'), 'background-color': token('--r-graph-inactive', '#f1ede3')}},
      {selector: ':parent', style: {'shape': 'round-rectangle', 'background-color': token('--r-graph-topic', '#f3e6ad'), 'background-opacity': .28,
        'border-color': token('--r-graph-topic-border', '#ddcc90'), 'border-width': 1, 'padding': 18, 'text-valign': 'top', 'text-margin-y': -9,
        'font-size': 12, 'font-weight': 600, 'text-max-width': 200}},
      {selector: 'node[kind="cluster-region"]', style: {'background-color': token('--r-graph-region', '#dce3fb'), 'background-opacity': .35,
        'border-color': token('--r-graph-border', '#a9b7e6'), 'border-style': 'dashed', 'padding': 10, 'font-size': 10, 'font-weight': 500}},
      {selector: 'node[kind="cluster"]', style: {'background-color': token('--r-soft', '#edf0fd'), 'border-color': token('--r-graph-link', '#8b9ed9'),
        'width': 'data(size)', 'height': 'data(size)', 'shape': 'ellipse', 'font-size': 11, 'text-max-width': 150}},
      {selector: 'node[kind="topic"]', style: {'background-color': token('--r-graph-topic', '#f3e6ad'), 'border-color': token('--r-graph-topic-outline', '#c8aa45')}},
      {selector: 'node[kind="person"]', style: {'shape': 'round-rectangle', 'width': 180, 'height': 64,
        'background-color': token('--r-graph-person', '#fbe2d8'), 'border-color': token('--r-graph-person-outline', '#c77560'), 'font-size': 12, 'text-max-width': 166}},
      {selector: 'node[entity]', style: {'width': 150, 'height': 40, 'text-max-width': 136}},  // a name only: a smaller box
      {selector: 'node[entity="org"]', style: {'shape': 'hexagon', 'width': 164, 'height': 46, 'text-max-width': 128,
        'background-color': token('--r-graph-org', '#e2eaf1'), 'border-color': token('--r-graph-org-outline', '#5f7f99')}},
      {selector: 'edge[kind="WROTE"]', style: {'width': 'data(width)', 'line-color': token('--r-graph-person-outline', '#c77560'), 'target-arrow-color': token('--r-graph-person-outline', '#c77560'),
        'target-arrow-shape': 'triangle', 'curve-style': 'bezier', 'opacity': .75}},
      {selector: 'node[pending=1]', style: {'border-color': token('--r-graph-pending-outline', '#bb8035'), 'background-color': token('--r-graph-pending', '#fff0cf')}},
      {selector: 'edge', style: {'width': 1.3, 'line-color': token('--r-graph-edge', '#a4a8b6'), 'target-arrow-color': token('--r-graph-arrow', '#8b92ac'),
        'target-arrow-shape': 'triangle', 'curve-style': 'unbundled-bezier', 'control-point-distances': 120, 'control-point-weights': .5, 'arrow-scale': .75,
        'label': 'data(label)', 'font-size': 8, 'color': token('--r-muted', '#67665e'), 'text-rotation': 'autorotate',
        'text-background-color': token('--r-bg', '#f6f0e4'), 'text-background-opacity': .95, 'text-background-padding': 3}},
      {selector: 'edge[kind="SUPERSEDES"]', style: {'line-color': token('--r-supersedes', '#4c6e91'), 'target-arrow-color': token('--r-supersedes', '#4c6e91'), 'width': 1.8}},
      {selector: 'edge[kind="CONTRADICTS"]', style: {'line-color': token('--r-conflict', '#b74b36'), 'target-arrow-color': token('--r-conflict', '#b74b36'), 'width': 1.8}},
      {selector: 'edge[kind="ABSTAIN"]', style: {'line-style': 'dotted', 'line-color': token('--r-graph-noise', '#b5b1a6'), 'target-arrow-shape': 'none', 'color': token('--r-graph-muted', '#777268')}},
      {selector: 'edge[kind="LINK"]', style: {'width': 'data(width)', 'target-arrow-shape': 'none', 'curve-style': 'bezier',
        'line-color': token('--r-graph-link', '#8b9ed9'), 'opacity': .8}},
      {selector: 'edge[kind="LINK"][level="topic"]', style: {'line-color': token('--r-graph-topic-link', '#b39a46'), 'line-style': 'solid'}},
      {selector: 'edge[kind="LINK"][evidence=0]', style: {'line-style': 'dashed'}},
      {selector: 'edge[basis="accepted"]', style: {'line-color': accepted, 'target-arrow-color': accepted, 'width': 2.2}},
      {selector: 'edge[basis="proposal"]', style: {'line-style': 'dashed', 'line-color': token('--r-proposed', '#bd8039'), 'target-arrow-color': token('--r-proposed', '#bd8039')}},
      {selector: '.dim', style: {'opacity': .2}},
      {selector: 'node.highlight', style: {'border-width': 3, 'border-color': accepted}},
      {selector: 'edge.highlight', style: {'width': 3, 'line-color': accepted, 'target-arrow-color': accepted}},
      {selector: ':selected', style: {'border-color': accepted, 'border-width': 3}}
    ]});
  function fitGraph() {
    cy.fit(cy.elements(), 34);
    // A lone person should stay at normal text size rather than fill the canvas.
    if (state.mode === 'people' && cy.zoom() > 1) {cy.zoom(1); cy.center();}
  }
  const topicTitle = id => id?.startsWith('about:') ? 'About ' + (people.get(id.slice(6))?.name || 'someone')
    : data.topics.find(t => t.id === id)?.title || 'No topic yet';
  const groupOf = new Map();  // node -> topic region: an item by its Ideas' topics, a claim by its own Idea's
  data.items.forEach(i => { groupOf.set(i.id, i.topics[0] || 'unassigned');
    i.claims.forEach(c => { const claim = claims.get(c);
      // A profile fact has no Idea: it sits with the person or organization it is about.
      groupOf.set(c, !claim.idea && claim.subject ? 'about:' + claim.subject
        : stories.get(claim.idea)?.topics[0] || i.topics[0] || 'unassigned'); }); });
  const clusterOf = id => stories.get(claims.get(id)?.idea)?.cluster || '';  // a claim's cluster, '' for none
  const linkLabel = l => l.relations.length ? `${l.relations.length} relation${l.relations.length > 1 ? 's' : ''}`
    : l.shared.length ? `${l.shared.length} shared Ideas` : `similar ${l.similarity.toFixed(2)}`;
  const order = new Map(data.items.map((i, n) => [i.id, n]));
  Object.assign(B, {clusterOf, cy, filteredItems, fitGraph, groupOf, linkLabel, order, renderTopics, topicTitle});
})();

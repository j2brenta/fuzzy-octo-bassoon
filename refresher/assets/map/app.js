/* Refresher Atlas. Ported from Victor's News Atlas viewer (feat/newsmap-sidecar-v06): coordinated topics, timeline,
   map, evidence and comparison. Imported values only enter textContent and canvas labels, never HTML. */
'use strict';
(() => {
  const data = JSON.parse(document.getElementById('map-data').textContent);
  const $ = id => document.getElementById(id);
  const state = {topic: '', story: '', mode: 'documents', selection: null, ego: false, comparison: [], detailItems: []};
  const items = new Map(data.items.map(x => [x.id, x]));
  const claims = new Map(data.claims.map(x => [x.id, x]));
  const stories = new Map(data.stories.map(x => [x.id, x]));
  const clusters = new Map((data.clusters || []).map(x => [x.id, x]));
  const links = data.links || [];
  const kinds = {merge_ideas: 'Merge two Ideas', topic: 'New topic', merge_people: 'Same person',
    relation: 'Confirm a relation'};
  const basisWords = {auto: 'automatic', accepted: 'accepted by you', proposal: 'proposed, waiting for you'};
  const contextFields = {subject: 'Subject', version: 'Version', metric: 'Metric', dataset: 'Dataset',
    unit: 'Unit', conditions: 'Conditions', time_attribution: 'Time / attribution', unknowns: 'Unknown'};
  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  }
  function button(text, action, cls) {
    const node = el('button', text, cls); node.type = 'button'; node.addEventListener('click', action); return node;
  }
  function date(item) {
    if (!item.time) return 'Date unknown';
    const d = new Date(item.time);
    return (Number.isNaN(d.valueOf()) ? item.time : d.toLocaleDateString('en', {day: 'numeric', month: 'short', year: 'numeric'})) + ' · ' + item.time_basis;
  }
  function external(url) {
    const a = el('a', 'Open the source ↗ (external site)');
    a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.referrerPolicy = 'no-referrer'; return a;
  }
  function quote(anchor) {
    const box = el('blockquote', null, 'quote');
    if (anchor.prefix) box.append(el('span', anchor.prefix, 'context'));
    box.append(el('mark', anchor.quote));
    if (anchor.suffix) box.append(el('span', anchor.suffix, 'context'));
    if (anchor.translation) {
      const t = el('p', null, 'translation');
      t.append(el('span', 'Machine translation, for reading only: ', 'locator'), el('span', anchor.translation));
      box.append(t);
    }
    box.append(el('div', `Passage ${anchor.span} · characters [${anchor.start_char}, ${anchor.end_char}) of the original`, 'locator'));
    return box;
  }
  function band(claim) { return el('span', `${claim.band} ${claim.freshness}`, 'band ' + claim.band); }

  $('snapshot-date').textContent = new Date(data.snapshot_at).toLocaleString('en');
  $('scope-badge').textContent = data.served ? 'Served locally' : 'Read-only file';
  for (const [key, title] of [['items', 'items'], ['ideas', 'Ideas'], ['clusters', 'clusters'], ['topics', 'topics'], ['waiting', 'waiting for you']]) {
    const stat = el('div', null, 'stat'); stat.append(el('strong', data.counts[key]), el('span', title)); $('stats').append(stat);
  }
  for (const story of data.stories.filter(s => s.items.length > 1)) {
    const opt = el('option', story.title.slice(0, 70) + ` (${story.items.length})`); opt.value = story.id; $('story-filter').append(opt);
  }

  // --- proposals: what the hourly review suggests; decided only through `refresher serve` ---
  function renderProposals() {
    const all = $('show-decided').checked;
    const shown = data.proposals.filter(p => all || p.status === 'proposed');
    $('proposals').replaceChildren();
    $('proposal-note').textContent = data.served
      ? 'Accept applies the change at once; a decision on changed evidence is refused and you see the current version.'
      : 'This file is read-only. Run `uv run refresher serve` to decide these here.';
    if (!shown.length) $('proposals').append(el('p', all ? 'The review has proposed nothing yet.' : 'Nothing waits for you.', 'empty-proposals'));
    for (const p of shown) $('proposals').append(proposalCard(p));
  }
  function proposalCard(p) {
    const card = el('article', null, 'proposal' + (p.status === 'proposed' ? ' waiting' : ''));
    card.append(el('span', kinds[p.kind] || p.kind, 'kind'));
    const head = p.kind === 'topic' ? p.title : p.kind === 'relation' ? `${p.claims[0]?.statement.slice(0, 60)}… ${p.type.toLowerCase()} an earlier claim`
      : p.kind === 'merge_people' ? p.people.join(' = ') : 'These say the same thing';
    card.append(el('h3', head));
    if (p.claims.length) {
      const list = el('ul');
      for (const c of p.claims) {
        const li = el('li');
        li.append(c.item ? button(c.statement, () => selectNode(c.id)) : el('span', c.statement));
        list.append(li);
      }
      card.append(list);
    }
    card.append(el('p', p.rationale, 'why'));
    const chips = el('div', null, 'chips');
    chips.append(el('span', p.status, 'chip' + (p.status === 'stale' ? ' warn' : '')));
    p.decisions.slice(-1).forEach(d => chips.append(el('span', `${d.action} · ${new Date(d.at).toLocaleString('en')}`, 'chip')));
    card.append(chips);
    if (p.reason) card.append(el('p', p.reason, 'why'));
    if (p.kind === 'merge_ideas' && p.claims.length === 2 && p.claims.every(c => c.item)) {
      card.append(button('Compare side by side ↓', () => {state.comparison = p.claims.map(c => c.id); renderComparison();
        $('comparison').scrollIntoView({behavior: 'smooth', block: 'start'});}));
    }
    if (data.served && ['proposed', 'accepted', 'rejected'].includes(p.status)) {
      const actions = el('div', null, 'actions');
      const why = el('input'); why.placeholder = 'Your reason (optional)'; why.setAttribute('aria-label', 'Your reason');
      why.id = 'why-' + p.id;
      const feedback = el('p', '', 'feedback');
      for (const action of ['accept', 'reject', 'defer']) {
        if (p.status !== 'proposed' && action === 'defer') continue;
        actions.append(button(action[0].toUpperCase() + action.slice(1), () => decide(p, action, why.value, feedback, actions), action));
      }
      actions.prepend(why);
      card.append(actions, feedback);
    }
    return card;
  }
  async function decide(p, action, rationale, feedback, actions) {
    actions.querySelectorAll('button').forEach(b => {b.disabled = true;});
    feedback.className = 'feedback'; feedback.textContent = 'Saving…';
    try {
      const response = await fetch('api/proposals/' + encodeURIComponent(p.id), {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Refresher': '1'},
        body: JSON.stringify({action, token: p.token, rationale})});
      const body = await response.json().catch(() => ({}));
      if (response.ok) { feedback.textContent = `${action}ed; rebuilding the map…`; location.reload(); return; }
      feedback.className = 'feedback error';
      feedback.textContent = body.error || `Not saved (HTTP ${response.status}).`;
      feedback.append(' ', button('Look again', () => location.reload()));
    } catch (error) {
      feedback.className = 'feedback error'; feedback.textContent = 'The server did not answer. Is `refresher serve` still running?';
    }
    actions.querySelectorAll('button').forEach(b => {b.disabled = false;});
  }

  // --- topics, timeline and map ---
  function renderTopics() {
    $('topics').replaceChildren();
    const loose = [...clusters.values()].filter(c => !c.topics.length).length;
    const topics = [{id: '', title: 'All topics', count: data.items.length, groups: clusters.size},
      ...data.topics.map(t => ({...t, count: data.items.filter(i => i.topics.includes(t.id)).length, groups: t.clusters.length})),
      {id: 'unassigned', title: 'No topic yet', count: data.items.filter(i => !i.topics.length).length, groups: loose}];
    for (const topic of topics) {
      const node = button('', () => {state.topic = topic.id; state.story = ''; $('story-filter').value = ''; state.ego = false; refresh();}, 'topic');
      node.setAttribute('aria-pressed', String(state.topic === topic.id));
      const extra = (topic.groups ? ` · ${topic.groups} clusters` : '') + (topic.attached?.length ? ` · ${topic.attached.length} provisional` : '');
      node.append(el('strong', topic.title), el('span', `${topic.count} items${extra}`, 'topic-count')); $('topics').append(node);
    }
  }
  function itemBands(item) { return item.claims.map(id => claims.get(id).band); }
  function filteredItems() {
    const query = $('search').value.toLocaleLowerCase();
    return data.items.filter(item =>
      (!state.topic || (state.topic === 'unassigned' ? !item.topics.length : item.topics.includes(state.topic))) &&
      (!state.story || item.stories.includes(state.story)) &&
      (!$('quality-filter').value || item.quality === $('quality-filter').value) &&
      (!$('band-filter').value || itemBands(item).includes($('band-filter').value)) &&
      (!$('date-filter').value || (item.time && item.time.slice(0, 10) >= $('date-filter').value)) &&
      (!$('hide-reposts').checked || !item.repost_of) &&
      (!query || (item.title + ' ' + item.source + ' ' + (item.author || '')).toLocaleLowerCase().includes(query))
    ).sort((a, b) => (b.time || '').localeCompare(a.time || '') || a.id.localeCompare(b.id));
  }
  const cy = cytoscape({container: $('graph'), elements: [], layout: {name: 'preset'},
    minZoom: .25, maxZoom: 2.5, wheelSensitivity: .2, boxSelectionEnabled: false,
    style: [
      {selector: 'node', style: {'label': 'data(label)', 'shape': 'round-rectangle', 'background-color': '#fdfcf7',
        'border-width': 1.2, 'border-color': '#8fa79a', 'width': 185, 'height': 64,
        'font-size': 13, 'color': '#233a35', 'text-wrap': 'wrap', 'text-max-width': 170,
        'text-valign': 'center', 'text-halign': 'center', 'text-overflow-wrap': 'anywhere', 'padding': 5}},
      {selector: 'node[kind="claim"]', style: {'background-color': '#e1ece4', 'border-color': '#7d9f8c'}},
      {selector: 'node[superseded=1]', style: {'border-style': 'dashed', 'color': '#7b8a84', 'background-color': '#eef0ea'}},
      {selector: ':parent', style: {'shape': 'round-rectangle', 'background-color': '#d8e4d2', 'background-opacity': .28,
        'border-color': '#c8d6c2', 'border-width': 1, 'padding': 18, 'text-valign': 'top', 'text-margin-y': -9,
        'font-size': 12, 'font-weight': 600, 'text-max-width': 200}},
      {selector: 'node[kind="cluster-region"]', style: {'background-color': '#c9d9e0', 'background-opacity': .35,
        'border-color': '#a9bfc9', 'border-style': 'dashed', 'padding': 10, 'font-size': 10, 'font-weight': 500}},
      {selector: 'node[kind="cluster"]', style: {'background-color': '#e3edf1', 'border-color': '#7f9eab',
        'width': 'data(size)', 'height': 'data(size)', 'shape': 'ellipse', 'font-size': 11, 'text-max-width': 150}},
      {selector: 'node[kind="topic"]', style: {'background-color': '#d8e4d2', 'border-color': '#93ad8c'}},
      {selector: 'node[pending=1]', style: {'border-color': '#b88959', 'background-color': '#f4eadc'}},
      {selector: 'edge', style: {'width': 1.3, 'line-color': '#9baaa1', 'target-arrow-color': '#81958a',
        'target-arrow-shape': 'triangle', 'curve-style': 'unbundled-bezier', 'control-point-distances': 120, 'control-point-weights': .5, 'arrow-scale': .75,
        'label': 'data(label)', 'font-size': 8, 'color': '#55655e', 'text-rotation': 'autorotate',
        'text-background-color': '#f3f5ef', 'text-background-opacity': .95, 'text-background-padding': 3}},
      {selector: 'edge[kind="SUPERSEDES"]', style: {'line-color': '#4c6e91', 'target-arrow-color': '#4c6e91', 'width': 1.8}},
      {selector: 'edge[kind="CONTRADICTS"]', style: {'line-color': '#b5553c', 'target-arrow-color': '#b5553c', 'width': 1.8}},
      {selector: 'edge[kind="ABSTAIN"]', style: {'line-style': 'dotted', 'line-color': '#a7b0aa', 'target-arrow-shape': 'none', 'color': '#7b8a84'}},
      {selector: 'edge[kind="LINK"]', style: {'width': 'data(width)', 'target-arrow-shape': 'none', 'curve-style': 'bezier',
        'line-color': '#8fa9b4', 'opacity': .8}},
      {selector: 'edge[kind="LINK"][level="topic"]', style: {'line-color': '#6e9a64', 'line-style': 'solid'}},
      {selector: 'edge[kind="LINK"][evidence=0]', style: {'line-style': 'dashed'}},
      {selector: 'edge[basis="accepted"]', style: {'line-color': '#176a61', 'target-arrow-color': '#176a61', 'width': 2.2}},
      {selector: 'edge[basis="proposal"]', style: {'line-style': 'dashed', 'line-color': '#be8656', 'target-arrow-color': '#be8656'}},
      {selector: '.dim', style: {'opacity': .2}},
      {selector: 'node.highlight', style: {'border-width': 3, 'border-color': '#176a61'}},
      {selector: 'edge.highlight', style: {'width': 3, 'line-color': '#176a61', 'target-arrow-color': '#176a61'}},
      {selector: ':selected', style: {'border-color': '#176a61', 'border-width': 3}}
    ]});
  const topicTitle = id => data.topics.find(t => t.id === id)?.title || 'No topic yet';
  const groupOf = new Map();  // node -> topic region: an item by its Ideas' topics, a claim by its own Idea's
  data.items.forEach(i => { groupOf.set(i.id, i.topics[0] || 'unassigned');
    i.claims.forEach(c => groupOf.set(c, stories.get(claims.get(c).idea)?.topics[0] || i.topics[0] || 'unassigned')); });
  const clusterOf = id => stories.get(claims.get(id)?.idea)?.cluster || '';  // a claim's cluster, '' for none
  const linkLabel = l => l.relations.length ? `${l.relations.length} relation${l.relations.length > 1 ? 's' : ''}`
    : l.shared.length ? `${l.shared.length} shared Ideas` : `similar ${l.similarity.toFixed(2)}`;
  const order = new Map(data.items.map((i, n) => [i.id, n]));
  function graphSlice(visible) {
    const allowedItems = new Set(visible.map(x => x.id));
    let nodes, edges;
    const filter = $('review-filter').value;
    if (state.mode === 'documents') {
      nodes = visible.map(i => ({id: i.id, kind: 'document', label: i.title.slice(0, 110), pending: Number(i.quality !== 'accepted')}));
      edges = data.item_edges.filter(e => allowedItems.has(e.source) && allowedItems.has(e.target));
    } else if (state.mode === 'topics') {
      const inScope = c => state.topic === 'unassigned' ? !c.topics.length : !state.topic || c.topics.includes(state.topic);
      nodes = [...clusters.values()].filter(c => inScope(c) && c.items.some(i => allowedItems.has(i))).map(c => ({id: c.id, kind: 'cluster',
        label: `${c.title}\n${c.ideas.length} Ideas · ${c.sources.length} source${c.sources.length > 1 ? 's' : ''}`,
        size: 46 + 9 * Math.min(c.ideas.length, 12), topic: c.topics[0] || ''}));
      const shownTopics = new Set(nodes.map(n => n.topic).filter(Boolean));
      if (state.topic && state.topic !== 'unassigned') shownTopics.add(state.topic);
      const ids = new Set([...nodes.map(n => n.id), ...shownTopics]);
      edges = links.filter(l => ids.has(l.source) && ids.has(l.target)).map(l => ({...l, kind: 'LINK', label: linkLabel(l),
        width: Math.min(9, 1 + 2 * l.weight), evidence: Number(l.relations.length + l.shared.length > 0)}));
      return {nodes, edges, total: nodes.length, allNodes: nodes, allEdges: edges, topics: [...shownTopics].sort()};
    } else {
      nodes = data.claims.filter(c => allowedItems.has(c.item)).map(c => ({id: c.id, kind: 'claim', label: c.statement.slice(0, 110),
        pending: Number(c.quality !== 'accepted'), superseded: Number(c.superseded)}));
      const n = new Set(nodes.map(x => x.id));
      edges = data.edges.filter(e => n.has(e.source) && n.has(e.target));
    }
    if (filter) edges = edges.filter(e => e.basis === filter);
    if (state.ego && state.selection) {
      const adjacent = new Set([state.selection]);
      edges.filter(e => e.source === state.selection || e.target === state.selection).forEach(e => {adjacent.add(e.source); adjacent.add(e.target);});
      nodes = nodes.filter(n => adjacent.has(n.id));
    }
    const allIds = new Set(nodes.map(n => n.id));
    const total = nodes.length, allNodes = [...nodes], allEdges = edges.filter(e => allIds.has(e.source) && allIds.has(e.target));
    if (state.selection) nodes.sort((a, b) => Number(b.id === state.selection) - Number(a.id === state.selection));
    nodes = nodes.slice(0, data.limits.primary_nodes);
    const shown = new Set(nodes.map(n => n.id));
    return {nodes, edges: edges.filter(e => shown.has(e.source) && shown.has(e.target)), total, allNodes, allEdges};
  }
  function renderTimeline(visible) {
    $('timeline').replaceChildren(); $('list-count').textContent = `${visible.length} items`;
    for (const item of visible) {
      const active = state.selection === item.id || claims.get(state.selection)?.item === item.id;
      const b = button('', () => selectItem(item.id), 'item-button' + (active ? ' active' : ''));
      b.dataset.item = item.id;
      const meta = [item.source, item.author, item.quality !== 'accepted' ? item.quality : null, item.repost_of ? 'repost' : null].filter(Boolean).join(' · ');
      b.append(el('span', date(item), 'item-date'), el('span', item.title, 'item-title'), el('span', meta, 'item-source'));
      $('timeline').append(b);
    }
    if (!visible.length) $('timeline').append(el('p', 'No items match these filters.', 'quiet'));
  }
  function renderTopicGraph(slice) {
    // Topics hold their clusters; links join clusters (and whole topics). The layout settles from a grid.
    cy.batch(() => {cy.elements().remove(); cy.add([
      ...slice.topics.map(t => ({group: 'nodes', data: {id: t, kind: 'topic', label: topicTitle(t)}})),
      ...slice.nodes.map((n, i) => ({group: 'nodes', data: {...n, parent: n.topic || undefined},
        position: {x: (i % 6) * 200, y: Math.floor(i / 6) * 170}})),
      ...slice.edges.map(e => ({group: 'edges', data: {...e}}))]);});
    cy.nodes().unlock();
    cy.layout({name: 'cose', animate: false, randomize: false, nodeDimensionsIncludeLabels: true, padding: 30,
      idealEdgeLength: e => 150 / Math.max(1, e.data('weight')), nodeRepulsion: () => 9000, numIter: 1500}).run();
    cy.nodes().lock(); cy.fit(cy.elements(), 34);
  }
  function renderGraph(visible) {
    const slice = graphSlice(visible);
    if (state.mode === 'topics') renderTopicGraph(slice);
    else {
    const shownGroups = [...new Set(slice.nodes.map(n => groupOf.get(n.id) || 'unassigned'))].sort();
    const compact = new Map(), subregions = new Map(); let groupX = 0;
    const rank = id => order.get(claims.get(id)?.item || id) ?? 0;
    const nested = state.mode === 'claims';  // claims sit in their cluster's region inside the topic's
    for (const topic of shownGroups) {
      const members = slice.nodes.filter(n => (groupOf.get(n.id) || 'unassigned') === topic).sort((a, b) => rank(a.id) - rank(b.id) || a.id.localeCompare(b.id));
      const blocks = new Map();
      members.forEach(n => { const key = nested ? clusterOf(n.id) : ''; blocks.set(key, [...(blocks.get(key) || []), n]); });
      for (const [key, block] of [...blocks].sort((a, b) => (a[0] === '') - (b[0] === '') || a[0].localeCompare(b[0]))) {
        const columns = Math.max(1, Math.ceil(block.length / 6));
        block.forEach((n, i) => compact.set(n.id, {x: groupX + (i % columns) * 235, y: Math.floor(i / columns) * 110}));
        if (key) subregions.set(`sub-${topic}-${key}`, {topic, cluster: key, count: block.length});
        groupX += columns * 235 + (key ? 45 : 25);
      }
    }
    const parentOf = n => { const key = state.mode === 'claims' ? clusterOf(n.id) : '', g = groupOf.get(n.id) || 'unassigned';
      return key ? `sub-${g}-${key}` : 'region-' + g; };
    cy.batch(() => {cy.elements().remove(); cy.add([
      ...shownGroups.map(t => ({group: 'nodes', data: {id: 'region-' + t, kind: 'region',
        label: topicTitle(t) + '\n' + slice.nodes.filter(n => groupOf.get(n.id) === t).length + ' nodes'}})),
      ...[...subregions].map(([id, sub]) => ({group: 'nodes', data: {id, kind: 'cluster-region', parent: 'region-' + sub.topic,
        label: (clusters.get(sub.cluster)?.title || 'Cluster') + ` · ${sub.count}`}})),
      ...slice.nodes.map(n => ({group: 'nodes', data: {...n, parent: parentOf(n)}, position: compact.get(n.id)})),
      ...slice.edges.map(e => ({group: 'edges', data: {...e}}))]);});
    cy.nodes().lock(); cy.fit(cy.elements(), 34);
    }
    $('map-empty').hidden = slice.nodes.length > 0;
    $('map-count').textContent = `${slice.nodes.length} nodes · ${slice.edges.length} relations`;
    $('hidden-count').textContent = slice.total > slice.nodes.length ? `${slice.total - slice.nodes.length} nodes hidden; narrow the filters or focus.` : 'Every node in this slice';
    $('map-title').textContent = {documents: 'Sources and how their claims relate', claims: 'Claims in their clusters and topics',
      topics: 'Clusters, topics and the links between them'}[state.mode];
    $('graph-list').replaceChildren();
    slice.allNodes.forEach(n => $('graph-list').append(button(n.label, () => selectNode(n.id))));
    const nodeLabel = id => items.get(id)?.title || claims.get(id)?.statement || clusters.get(id)?.title || topicTitle(id);
    slice.allEdges.forEach(e => $('graph-list').append(button(`${nodeLabel(e.source)} → ${e.label} (${basisWords[e.basis]}) → ${nodeLabel(e.target)}`,
      () => e.kind === 'LINK' ? selectLink(e) : selectEdge(e))));
    highlight();
  }
  function highlight() {
    cy.elements().removeClass('dim highlight');
    const selected = state.selection && cy.getElementById(state.selection);
    if (selected && selected.length) {
      const near = selected.closedNeighborhood().union(selected.closedNeighborhood().ancestors());
      cy.elements().not(near).addClass('dim'); near.addClass('highlight');
    }
    $('ego').disabled = !selected || !selected.length;
    $('clear-focus').hidden = !state.ego;
  }
  function claimCard(claim) {
    const box = el('article', null, 'claim-card' + (claim.superseded ? ' superseded' : ''));
    const text = el('p', claim.statement); text.append(band(claim)); box.append(text);
    const facts = [claim.kind, claim.durability, claim.superseded ? 'superseded' : null, claim.noise ? 'noise' : null].filter(Boolean);
    box.append(el('p', facts.join(' · '), 'small quiet'));
    const story = claim.idea && stories.get(claim.idea);
    if (story && story.claims.length > 1) {
      box.append(el('p', `Idea: ${story.title} (${story.status}, ${story.claims.length} claims)`, 'small'));
      if (story.chain.length) { const chain = el('ol', null, 'chain'); story.chain.forEach(s => chain.append(el('li', s))); box.append(chain); }
    }
    const compare = button(state.comparison.includes(claim.id) ? '✓ In comparison' : 'Add to comparison', () => toggleCompare(claim.id));
    compare.dataset.compare = claim.id;
    box.append(compare);
    claim.anchors.forEach(a => box.append(quote(a)));
    if (!claim.anchors.length) box.append(el('p', 'No passage is recorded for this claim.', 'small quiet'));
    return box;
  }
  function selectItem(id) {
    const item = items.get(id); if (!item) return;
    state.detailItems = [id];
    state.selection = state.mode === 'claims' && item.claims.length ? item.claims[0] : id;
    if (!cy.getElementById(state.selection).length) renderGraph(filteredItems());
    $('detail').replaceChildren(el('h3', item.title, 'detail-title'));
    const chips = el('div', null, 'chips');
    for (const text of [item.quality, item.repost_of ? 'repost' : 'first source', ...item.topics.map(topicTitle)]) {
      chips.append(el('span', text, 'chip' + (item.quality !== 'accepted' && text === item.quality ? ' warn' : '')));
    }
    $('detail').append(chips, el('p', [item.source, item.author ? 'by ' + item.author : null, date(item)].filter(Boolean).join('\n'), 'detail-meta'));
    if (item.quality !== 'accepted') $('detail').append(el('p', 'Ingestion has not accepted this item yet; check its claims before relying on them.', 'chip warn'));
    if (item.repost_of && items.has(item.repost_of)) $('detail').append(button('Repost of: ' + items.get(item.repost_of).title.slice(0, 60), () => selectItem(item.repost_of)));
    if (item.url) $('detail').append(external(item.url));
    $('detail').append(el('div', 'Note: ' + item.note, 'locator'));
    $('detail').append(el('h3', 'Claims and their passages', 'detail-section'));
    item.claims.forEach(cid => $('detail').append(claimCard(claims.get(cid))));
    if (!item.claims.length) $('detail').append(el('p', 'No claims were extracted.', 'quiet'));
    renderTimeline(filteredItems()); highlight();
  }
  function ideaButton(ideaId) {
    const story = stories.get(ideaId); if (!story) return el('li', ideaId);
    const li = el('li'); li.append(button(story.title, () => selectNode(story.claims[0]))); return li;
  }
  function selectGroup(id) {
    const c = clusters.get(id), t = data.topics.find(x => x.id === id); if (!c && !t) return;
    state.selection = id;
    state.detailItems = [];
    $('detail').replaceChildren(el('h3', c ? c.title : t.title, 'detail-title'));
    if (c) {
      $('detail').append(el('p', `Cluster · ${c.ideas.length} Ideas from ${c.sources.length} source${c.sources.length > 1 ? 's' : ''} · found by similarity, recomputed with the graph`, 'detail-meta'));
      if (c.topics.length) $('detail').append(el('p', 'In: ' + c.topics.map(topicTitle).join(', '), 'small'));
      else if (c.sources.length < 2) $('detail').append(el('p', 'One source so far: a second one can make it part of a topic.', 'chip'));
    } else {
      $('detail').append(el('p', `Topic you accepted · ${t.ideas.length} Ideas, ${t.attached.length} joined provisionally, ${t.clusters.length} clusters`, 'detail-meta'));
    }
    $('detail').append(el('h3', 'Ideas', 'detail-section'));
    const list = el('ul'); (c ? c.ideas : [...t.ideas, ...t.attached]).forEach(i => list.append(ideaButton(i))); $('detail').append(list);
    const mine = links.filter(l => l.source === id || l.target === id);
    if (mine.length) {
      $('detail').append(el('h3', c ? 'Linked clusters' : 'Linked topics', 'detail-section'));
      mine.forEach(l => { const other = l.source === id ? l.target : l.source;
        $('detail').append(button(`${clusters.get(other)?.title || topicTitle(other)} · ${linkLabel(l)} · weight ${l.weight}`, () => selectLink(l))); });
    }
    highlight();
  }
  function selectLink(l) {
    const name = id => clusters.get(id)?.title || topicTitle(id);
    $('detail').replaceChildren(el('h3', `${name(l.source)} ↔ ${name(l.target)}`, 'detail-title'),
      el('p', `${l.level === 'topic' ? 'Topics' : 'Clusters'} linked automatically · weight ${l.weight} · centroid similarity ${l.similarity.toFixed(2)}`, 'detail-meta'));
    if (l.relations.length) {
      $('detail').append(el('h3', 'Relations that cross them', 'detail-section'));
      l.relations.forEach(r => { $('detail').append(el('p', `${r.said} — ${r.label} — ${r.about}`, 'small'));
        [r.source, r.target].filter(id => claims.has(id)).forEach(id => $('detail').append(claimCard(claims.get(id)))); });
      const pair = [l.relations[0].source, l.relations[0].target];
      if (pair.every(id => claims.has(id))) { state.comparison = pair; renderComparison(); }
    }
    if (l.shared.length) {
      $('detail').append(el('h3', 'Ideas in both', 'detail-section'));
      const list = el('ul'); l.shared.forEach(i => list.append(ideaButton(i))); $('detail').append(list);
    }
    if (!l.relations.length && !l.shared.length) $('detail').append(el('p', 'Linked by similarity alone: no relation crosses them yet.', 'small quiet'));
    state.selection = l.source; highlight();
  }
  function selectNode(id) {
    if (clusters.has(id) || data.topics.some(t => t.id === id)) return selectGroup(id);
    if (items.has(id)) return selectItem(id);
    if (!claims.has(id)) return;
    if (state.mode !== 'claims') { state.mode = 'claims'; setMode(); refresh(); }
    state.selection = id;
    state.detailItems = [claims.get(id).item];
    if (!cy.getElementById(id).length) renderGraph(filteredItems());
    const c = claims.get(id), item = items.get(c.item);
    $('detail').replaceChildren(el('h3', 'Claim', 'detail-title'), el('p', `${item.source} · ${date(item)}`, 'detail-meta'), claimCard(c),
      button('Open the item', () => selectItem(c.item)));
    renderTimeline(filteredItems()); highlight();
  }
  function selectEdge(edge) {
    const ends = [edge.source, edge.target];
    state.detailItems = ends.map(id => claims.get(id)?.item || (items.has(id) ? id : null)).filter(Boolean);
    $('detail').replaceChildren(el('h3', edge.label, 'detail-title'), el('p', `${edge.kind} · ${basisWords[edge.basis]}`, 'detail-meta'));
    if (edge.rationale) $('detail').append(el('p', edge.rationale));
    if (edge.context) $('detail').append(contextTable(edge.context, 'The relator\u2019s reading of this pair'));
    if (edge.said) $('detail').append(el('p', 'Later: ' + edge.said, 'small'), el('p', 'Earlier: ' + edge.about, 'small'));
    const pair = claims.has(edge.source) ? ends : [edge.said, edge.about].map(s => data.claims.find(c => c.statement === s && ends.includes(c.item))?.id);
    pair.filter(Boolean).forEach(id => $('detail').append(claimCard(claims.get(id))));
    if (pair.every(Boolean)) {
      state.comparison = pair; renderComparison();
      $('detail').append(button('Go to the comparison ↓', () => $('comparison').scrollIntoView({behavior: 'smooth', block: 'start'})));
    }
    if (edge.proposal) $('detail').append(el('p', 'The review asked you about this relation; see Waiting for you above.', 'chip warn'));
    state.selection = edge.source; renderTimeline(filteredItems()); highlight();
  }
  function contextTable(context, caption) {
    const table = el('table', null, 'context-table');
    if (caption) table.append(el('caption', caption, 'small quiet'));
    for (const [key, label] of Object.entries(contextFields)) {
      const row = el('tr'); row.append(el('th', label), el('td', context[key] || 'Not stated')); table.append(row);
    }
    return table;
  }
  function toggleCompare(id) {
    if (state.comparison.includes(id)) state.comparison = state.comparison.filter(x => x !== id);
    else state.comparison = [...state.comparison.slice(-1), id];
    renderComparison();
    document.querySelectorAll('[data-compare]').forEach(b => {b.textContent = state.comparison.includes(b.dataset.compare) ? '✓ In comparison' : 'Add to comparison';});
  }
  function renderComparison() {
    $('comparison').replaceChildren();
    const pair = state.comparison;
    const relation = data.edges.find(e => pair.includes(e.source) && pair.includes(e.target) && e.source !== e.target);
    for (let i = 0; i < 2; i++) {
      if (!pair[i] || !claims.has(pair[i])) { $('comparison').append(el('div', `Pick the ${i ? 'second' : 'first'} claim in the evidence panel`, 'compare-card empty')); continue; }
      const c = claims.get(pair[i]), item = items.get(c.item), card = el('article', null, 'compare-card');
      card.append(el('p', `${item.source} · ${date(item)}`, 'eyebrow'), el('h3', c.statement));
      if (relation) card.append(el('p', `${relation.label} · ${basisWords[relation.basis]}`, 'chip'));
      const story = c.idea && stories.get(c.idea);
      const table = el('table', null, 'context-table');
      for (const [label, value] of [['Author', item.author || 'Unknown'], ['Source', item.source], ['Kind', c.kind],
          ['Durability', c.durability], ['Freshness', `${c.band} (${c.freshness})`], ['Idea', story ? story.title : 'None'],
          ['Superseded', c.superseded ? 'Yes' : 'No'], ['Quality', c.quality]]) {
        const row = el('tr'); row.append(el('th', label), el('td', value)); table.append(row);
      }
      card.append(table);
      if (relation?.context && i === 0) card.append(contextTable(relation.context, 'What the pair shares and where it differs (from the relator)'));
      c.anchors.forEach(a => card.append(quote(a))); $('comparison').append(card);
    }
  }
  function clearDetail() {
    state.detailItems = [];
    $('detail').replaceChildren(el('p', 'Pick an item or a relation in this slice.', 'quiet'));
  }
  function setMode() {
    for (const other of ['documents', 'claims', 'topics']) $('mode-' + other).setAttribute('aria-pressed', String(other === state.mode));
  }
  function refresh() {
    renderTopics(); const visible = filteredItems(), allowed = new Set(visible.map(i => i.id));
    const selectedItem = claims.get(state.selection)?.item || state.selection;
    if ((selectedItem && !allowed.has(selectedItem)) || state.detailItems.some(id => !allowed.has(id))) {
      state.selection = null; state.ego = false; clearDetail();
    }
    state.comparison = state.comparison.filter(id => claims.has(id));
    const topic = data.topics.find(t => t.id === state.topic)?.title || (state.topic === 'unassigned' ? 'No topic yet' : 'All topics');
    const story = stories.get(state.story)?.title || 'All Ideas';
    const scope = `${topic} / ${story.slice(0, 60)} · ${visible.length} items · quality: ${$('quality-filter').value || 'any'} · freshness: ${$('band-filter').value || 'any'} · from: ${$('date-filter').value || 'any date'}`;
    $('reading-scope').textContent = scope; $('compare-scope').textContent = 'Same slice: ' + scope;
    renderTimeline(visible); renderGraph(visible); renderComparison();
  }
  cy.on('tap', 'node', e => { if (!['region', 'cluster-region'].includes(e.target.data('kind'))) selectNode(e.target.id()); });
  cy.on('tap', 'edge', e => e.target.data('kind') === 'LINK' ? selectLink(e.target.data()) : selectEdge(e.target.data()));
  $('story-filter').addEventListener('change', e => {state.story = e.target.value; state.ego = false; refresh();});
  ['quality-filter', 'band-filter', 'date-filter', 'hide-reposts', 'review-filter'].forEach(id => $(id).addEventListener('change', refresh));
  $('show-decided').addEventListener('change', renderProposals);
  $('search').addEventListener('input', refresh);
  for (const mode of ['documents', 'claims', 'topics']) $('mode-' + mode).addEventListener('click', () => {
    state.mode = mode; state.ego = false; state.selection = null; clearDetail(); setMode(); refresh();
  });
  $('zoom-in').addEventListener('click', () => cy.zoom({level: cy.zoom() * 1.25, renderedPosition: {x: cy.width() / 2, y: cy.height() / 2}}));
  $('zoom-out').addEventListener('click', () => cy.zoom({level: cy.zoom() / 1.25, renderedPosition: {x: cy.width() / 2, y: cy.height() / 2}}));
  $('fit').addEventListener('click', () => cy.fit(cy.elements(), 34));
  $('ego').addEventListener('click', () => {state.ego = true; renderGraph(filteredItems());});
  $('clear-focus').addEventListener('click', () => {state.ego = false; renderGraph(filteredItems());});
  $('clear-compare').addEventListener('click', () => {state.comparison = []; renderComparison();});
  $('reset').addEventListener('click', () => {
    state.topic = ''; state.story = ''; state.ego = false; state.selection = null;
    clearDetail();
    for (const id of ['story-filter', 'quality-filter', 'band-filter', 'date-filter', 'search', 'review-filter']) $(id).value = '';
    $('hide-reposts').checked = false; refresh();
  });
  renderProposals(); refresh();
  window.atlas = {cy, state, data, refresh, selectItem, selectNode, selectEdge, selectLink, filteredItems};
})();

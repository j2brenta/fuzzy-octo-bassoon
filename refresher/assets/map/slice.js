/* Refresher Atlas: the slice of the graph the mode and filters draw, laid out on the map, and what is lit. */
'use strict';
(() => {
  const B = window.RefresherAtlas;
  if (!B) return;  // an empty Atlas wires nothing
  const {$, basisWords, button, claims, clusterOf, clusters, cy, data, embedded, fitGraph, groupOf, items,
    linkLabel, links, order, params, people, peopleSummary, rankedPeople, reviewing, scoreSubject,
    state, topicTitle} = B;
  const {selectEdge, selectLink, selectNode, selectPerson} = B.later;
  function graphSlice(visible) {
    const allowedItems = new Set(visible.map(x => x.id));
    let nodes, edges;
    const filter = $('review-filter').value;
    if (state.mode === 'people') {
      const rows = rankedPeople(visible).slice(0, data.limits.primary_nodes);
      nodes = rows.map(r => {
        const person = people.get(r.person_id);
        return {id:r.person_id, kind:'person', label:`${person.name}\n${peopleSummary(person)}`,
          pending:Number([...(person.statements || []), ...person.facts].every(id => claims.get(id)?.quality !== 'accepted'))};
      });
      edges = []; const subjectNodes = new Map();
      const subjects = (data.people_subjects || []).filter(s => !scoreSubject() || s.id === scoreSubject());
      for (const r of rows) {
        if (r.importance == null) continue;
        for (const s of subjects) {
          const scoped = data.people_scores[s.id]?.find(x => x.person_id === r.person_id);
          if (!scoped?.idea_count || nodes.length + subjectNodes.size >= data.limits.primary_nodes) continue;
          subjectNodes.set(s.id, {id:s.id, kind:s.kind, size:75, label:s.title});
          edges.push({id:`contributes>${r.person_id}>${s.id}`, source:r.person_id, target:s.id, kind:'CONTRIBUTES',
            label:`${scoped.idea_count} Ideas`, width:1 + Math.min(scoped.idea_count, 5), basis:'auto'});
        }
      }
      nodes.push(...subjectNodes.values());
      return {nodes, edges, total:nodes.length, allNodes:nodes, allEdges:edges, topics:[]};
    } else if (state.mode === 'documents') {
      nodes = visible.map(i => ({id: i.id, kind: 'document', label: i.title.slice(0, 110), pending: Number(i.quality !== 'accepted')}));
      edges = data.item_edges.filter(e => allowedItems.has(e.source) && allowedItems.has(e.target));
    } else if (state.mode === 'topics') {
      const inScope = c => state.topic === 'unassigned' ? !c.topics.length : !state.topic || c.topics.includes(state.topic);
      nodes = [...clusters.values()].filter(c => inScope(c) && c.items.some(i => allowedItems.has(i))).map(c => ({id: c.id, kind: 'cluster',
        label: `${c.title}\n${c.ideas.length} Ideas · ${c.items.length} post${c.items.length > 1 ? 's' : ''}`,
        size: 46 + 9 * Math.min(c.ideas.length, 12), topic: c.topics[0] || ''}));
      const shownTopics = new Set(nodes.map(n => n.topic).filter(Boolean));
      if (state.topic && state.topic !== 'unassigned') shownTopics.add(state.topic);
      const ids = new Set([...nodes.map(n => n.id), ...shownTopics]);
      edges = links.filter(l => ids.has(l.source) && ids.has(l.target)).map(l => ({...l, kind: 'LINK', label: linkLabel(l),
        width: Math.min(9, 1 + 2 * l.weight), evidence: Number(l.relations.length + l.shared.length > 0)}));
      // People and organizations, by name only (what is said about them is in the evidence when picked), and
      // "wrote" edges to the clusters their posts feed. An organization has its own shape.
      for (const person of people.values()) {
        const fed = Object.entries(person.clusters).filter(([cid]) => ids.has(cid));
        const facts = person.facts.filter(c => allowedItems.has(claims.get(c)?.item));
        if (!fed.length && !facts.length) continue;
        nodes.push({id: person.id, kind: 'person', entity: person.kind === 'organization' ? 'org' : 'person', label: person.name});
        const verb = person.items.length ? 'wrote' : 'in their posts';
        fed.forEach(([cid, n]) => edges.push({id: `wrote>${person.id}>${cid}`, source: person.id, target: cid, kind: 'WROTE',
          label: `${verb} · ${n} Ideas`, width: Math.min(7, 1 + n / 2), basis: 'auto'}));
      }
      return {nodes, edges, total: nodes.length, allNodes: nodes, allEdges: edges, topics: [...shownTopics].sort()};
    } else {
      const held = state.drill ? new Set(clusters.get(state.drill)?.ideas || []) : null;  // a cluster's own claims, not all its posts'
      nodes = data.claims.filter(c => allowedItems.has(c.item) && (!held || held.has(c.idea))).map(c => ({id: c.id, kind: 'claim', label: c.statement.slice(0, 110),
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
  function peopleLayout(count, width) {
    const cols = Math.max(1, Math.min(count, Math.floor(width / 240)));
    return {name:'grid', cols, rows:Math.max(1, Math.ceil(count / cols)), avoidOverlap:true,
      avoidOverlapPadding:32, nodeDimensionsIncludeLabels:true, fit:false, animate:false, padding:30};
  }
  function renderTopicGraph(slice) {
    // Topics hold their clusters; links join clusters (and whole topics). The layout settles from a grid.
    cy.batch(() => {cy.elements().remove(); cy.add([
      ...slice.topics.map(t => ({group: 'nodes', data: {id: t, kind: 'topic', label: topicTitle(t)}})),
      ...slice.nodes.map((n, i) => ({group: 'nodes', data: {...n, parent: n.kind === 'cluster' && n.topic ? n.topic : undefined},
        position: {x: (i % 6) * 200, y: Math.floor(i / 6) * 170}})),
      ...slice.edges.map(e => ({group: 'edges', data: {...e}}))]);});
    cy.nodes().unlock();
    cy.layout(state.mode === 'people' ? peopleLayout(slice.nodes.length, cy.width()) :
      {name: 'cose', animate: false, randomize: false, nodeDimensionsIncludeLabels: true, padding: 30,
      // "wrote" edges carry no weight; a NaN length would leave every position empty.
      idealEdgeLength: e => 150 / Math.max(1, Number(e.data('weight')) || 1), nodeRepulsion: () => 9000, numIter: 1500}).run();
    cy.nodes().lock(); fitGraph();
  }
  function renderGraph(visible) {
    const slice = graphSlice(visible);
    if (state.mode === 'topics' || state.mode === 'people') renderTopicGraph(slice);
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
    cy.nodes().lock(); fitGraph();
    }
    $('map-empty').hidden = slice.nodes.length > 0;
    $('map-count').textContent = `${slice.nodes.length} nodes · ${slice.edges.length} relations`;
    $('hidden-count').textContent = slice.total > slice.nodes.length ? `${slice.total - slice.nodes.length} nodes hidden; narrow the filters or focus.` : 'Every node in this slice';
    $('map-title').textContent = {documents: 'Sources and how their claims relate', claims: 'Claims in their clusters and topics',
      topics: 'Clusters, topics and the links between them', people:'People and their evidence'}[state.mode];
    $('graph-list').replaceChildren();
    slice.allNodes.forEach(n => $('graph-list').append(button(n.label, () => selectNode(n.id))));
    const nodeLabel = id => items.get(id)?.title || claims.get(id)?.statement || clusters.get(id)?.title
      || people.get(id)?.name || topicTitle(id);
    slice.allEdges.forEach(e => $('graph-list').append(button(`${nodeLabel(e.source)} → ${e.label} (${basisWords[e.basis]}) → ${nodeLabel(e.target)}`,
      () => e.kind === 'LINK' ? selectLink(e) : ['WROTE', 'CONTRIBUTES'].includes(e.kind) ? selectPerson(e.source) : selectEdge(e))));
    highlight();
  }
  // The slice on the timeline (it lives in the Library): same topic, filters and selected source, and a way back here.
  function updateTimelineLink() {
    const link = $('open-timeline');
    const q = new URLSearchParams();
    const fields = {topic: state.topic, idea: state.story, cluster: state.drill || '', mode: state.mode,
      quality: $('quality-filter').value, freshness: $('band-filter').value, from: $('date-filter').value,
      search: $('search').value, hide_reposts: $('hide-reposts').checked ? '1' : '', return: params.get('return') || ''};
    const selected = items.has(state.selection) ? state.selection : claims.get(state.selection)?.item;
    if (selected) fields.item = selected;
    for (const [key, value] of Object.entries(fields)) if (value) q.set(key, value);
    q.set('via', 'atlas');
    link.href = '/timeline/?' + q;
    link.hidden = embedded || reviewing || !data.served;
  }
  function highlight() {
    updateTimelineLink();
    cy.elements().removeClass('dim highlight');
    const selected = state.selection && cy.getElementById(state.selection);
    if (selected && selected.length) {
      const near = selected.closedNeighborhood().union(selected.closedNeighborhood().ancestors());
      cy.elements().not(near).addClass('dim'); near.addClass('highlight');
    }
    $('ego').disabled = !selected || !selected.length;
    $('clear-focus').hidden = !state.ego;
  }
  Object.assign(B, {highlight, renderGraph, updateTimelineLink});
})();

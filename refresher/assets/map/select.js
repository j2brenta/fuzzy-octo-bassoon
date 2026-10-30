/* Refresher Atlas: the evidence for a source, a cluster or topic, or a link between them. */
'use strict';
(() => {
  const B = window.RefresherAtlas;
  if (!B) return;  // an empty Atlas wires nothing
  const {$, button, claimCard, claimRow, claims, clusterPill, clusters, clustersOf, cy, data, date, el,
    embedded, external, filteredItems, flow, highlight, itemPill, items, linkLabel, links, peopleOf,
    pill, plural, reading, relatedSources, renderGraph, reviewing, section, state, stories, textBlock,
    topicPill, topicTitle} = B;
  const {drill, openReader, renderComparison, selectNode} = B.later;
  function show(render, ...args) {
    state.view = [render, args];
    const kind = {selectItem: 'item', selectGroup: 'group', selectLink: 'link', selectPerson: 'person', selectNode: 'claim',
      selectEdge: 'edge'}[render.name];
    if (window.atlas?.onselect) window.atlas.onselect(kind, kind === 'link' || kind === 'edge' ? args[0].source : args[0]);
  }
  function selectItem(id, focus) {
    const item = items.get(id); if (!item) return; show(selectItem, id, focus);
    state.detailItems = [id];
    state.selection = state.mode === 'claims' && item.claims.length ? focus || item.claims[0] : id;
    if (!reviewing && !cy.getElementById(state.selection).length) renderGraph(filteredItems());  // Review hides the map
    const ideaIds = item.stories, clusterIds = clustersOf(item), related = relatedSources(item);
    const chips = el('div', null, 'chips');
    chips.append(el('span', item.quality, 'chip' + (item.quality !== 'accepted' ? ' warn' : '')), el('span', item.repost_of ? 'repost' : 'first source', 'chip'));
    $('detail').replaceChildren(el('p', 'SOURCE', 'eyebrow'), el('h3', item.title, 'detail-title'),
      el('p', [item.source, item.author ? 'by ' + item.author : null, date(item)].filter(Boolean).join(' · '), 'detail-meta'), chips);
    if (item.quality !== 'accepted') $('detail').append(el('p', 'Ingestion has not accepted this item yet; check its claims before relying on them.', 'chip warn'));
    if (item.url) $('detail').append(external(item.url));
    // The text first: it is what a source is opened for; where it leads follows. Reading, the text is in the middle.
    if (reading) {
      if (item.gist || item.hook) $('detail').append(el('h3', 'Gist', 'detail-section'), el('p', item.gist || item.hook, 'gist-preview'));
      openReader(item, focus);
    } else $('detail').append(el('h3', 'Text', 'detail-section'), textBlock(item, focus));
    $('detail').append(el('h3', 'Where it leads', 'detail-section'),
      flow([[item.claims.length, 'claim'], [ideaIds.length, 'Idea'], [clusterIds.length, 'cluster'], [item.topics.length, 'topic']]),
      ...section('Topics', item.topics.map(topicPill)), ...section('Clusters', clusterIds.map(clusterPill)),
      ...section('People', peopleOf([id])), ...section('Organizations', peopleOf([id], 'organization')),
      ...section('Related sources', related.slice(0, 8).map(([i, why]) => itemPill(i, why)), related.length > 8 ? `and ${related.length - 8} more` : ''));
    $('detail').append(el('h3', `Claims (${item.claims.length})`, 'detail-section'));
    const list = el('div', null, 'claim-list'); item.claims.forEach(cid => list.append(claimRow(claims.get(cid)))); $('detail').append(list);
    if (!item.claims.length) $('detail').append(el('p', 'No claims were extracted.', 'quiet'));
    $('detail').append(el('p', 'Obsidian note: ' + item.note, 'locator'));
    highlight();
  }
  function ideaButton(ideaId) {
    const story = stories.get(ideaId); if (!story) return el('li', ideaId);
    const li = el('li'); li.append(button(story.title, () => selectNode(story.claims[0]))); return li;
  }
  function groupPosts(itemIds) {
    return [...section(`Sources (${itemIds.length})`, itemIds.slice(0, 12).map(i => itemPill(i, items.get(i)?.source)),
      itemIds.length > 12 ? `and ${itemIds.length - 12} more` : ''), ...section('People', peopleOf(itemIds)),
      ...section('Organizations', peopleOf(itemIds, 'organization'))];
  }
  function selectGroup(id) {
    const c = clusters.get(id), t = data.topics.find(x => x.id === id); if (!c && !t) return; show(selectGroup, id);
    state.selection = id;
    state.detailItems = [];
    $('detail').replaceChildren(el('p', c ? 'CLUSTER' : 'TOPIC', 'eyebrow'), el('h3', c ? c.title : t.title, 'detail-title'));
    if (data.served && !embedded) {  // the app ranks what this group holds and what to read first
      const a = el('a', 'Open its dossier: ranked, what changed, what to read first ↗'); a.href = '/topic/?id=' + encodeURIComponent(id);
      $('detail').append(a);
    }
    const ideaIds = c ? c.ideas : [...t.ideas, ...t.attached];
    const postIds = c ? c.items : data.items.filter(i => i.topics.includes(id)).map(i => i.id);
    if (c) {
      $('detail').append(button(`See its ${c.ideas.length > 1 ? 'claims' : 'claim'}: the Claims view of this cluster ↓`, () => drill(id), 'chip'));
      const how = c.theme ? 'a theme the model gave Ideas no cluster held'
        : 'found by similarity' + (c.assigned.length ? `; the model placed ${c.assigned.length} more here` : '');
      $('detail').append(el('p', `${plural(c.ideas.length, 'Idea')} from ${plural(c.items.length, 'post')} · ${how}`, 'detail-meta'));
      $('detail').append(...section('In topics', c.topics.map(topicPill)));
      if (!c.topics.length && c.items.length < 2) $('detail').append(el('p', 'One post so far: a second one can make it part of a topic.', 'chip'));
    } else {
      $('detail').append(el('p', `Topic you accepted · ${plural(t.ideas.length, 'Idea')}, ${t.attached.length} joined provisionally`, 'detail-meta'),
        ...section('Clusters', t.clusters.map(clusterPill)));
    }
    $('detail').append(...groupPosts(postIds));
    const mine = links.filter(l => l.source === id || l.target === id);
    $('detail').append(...section(c ? 'Linked clusters' : 'Linked topics', mine.map(l => { const other = l.source === id ? l.target : l.source;
      return pill(clusters.get(other)?.title || topicTitle(other), () => selectLink(l), clusters.has(other) ? 'cluster' : 'topic', `${linkLabel(l)} · weight ${l.weight}`); })));
    $('detail').append(el('h3', `Ideas (${ideaIds.length})`, 'detail-section'));
    const list = el('ul', null, 'idea-list'); ideaIds.forEach(i => list.append(ideaButton(i))); $('detail').append(list);
    highlight();
  }
  function selectLink(l) {
    show(selectLink, l);
    const name = id => clusters.get(id)?.title || topicTitle(id);
    $('detail').replaceChildren(el('p', l.level === 'topic' ? 'TOPICS LINKED' : 'CLUSTERS LINKED', 'eyebrow'), el('h3', `${name(l.source)} ↔ ${name(l.target)}`, 'detail-title'),
      el('p', `Linked automatically · weight ${l.weight} · centroid similarity ${l.similarity.toFixed(2)}`, 'detail-meta'));
    const pillOf = id => clusters.has(id) ? clusterPill(id) : topicPill(id);
    $('detail').append(...section('The two sides', [pillOf(l.source), pillOf(l.target)]));
    if (l.relations.length) {
      $('detail').append(el('h3', 'Relations that cross them', 'detail-section'));
      l.relations.forEach(r => { $('detail').append(el('p', `${r.said} — ${r.label} — ${r.about}`, 'small'));
        [r.source, r.target].filter(id => claims.has(id)).forEach(id => $('detail').append(claimCard(claims.get(id)))); });
      const pair = [l.relations[0].source, l.relations[0].target];
      if (pair.every(id => claims.has(id))) { state.comparison = pair; renderComparison(); }
    }
    if (l.shared.length) {
      $('detail').append(el('h3', 'Ideas in both', 'detail-section'));
      const list = el('ul', null, 'idea-list'); l.shared.forEach(i => list.append(ideaButton(i))); $('detail').append(list);
    }
    if (!l.relations.length && !l.shared.length) $('detail').append(el('p', 'Linked by similarity alone: no relation crosses them yet.', 'small quiet'));
    state.selection = l.source; highlight();
  }
  Object.assign(B, {selectGroup, selectItem, selectLink, show});
})();

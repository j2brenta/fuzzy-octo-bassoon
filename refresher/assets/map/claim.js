/* Refresher Atlas: the evidence for a person or organization, a claim, or a relation. */
'use strict';
(() => {
  const B = window.RefresherAtlas;
  if (!B) return;  // an empty Atlas wires nothing
  const {$, band, basisWords, button, claimCard, claimRow, claims, clusterPill, clusters, cy, data, date,
    el, external, filteredItems, flow, highlight, itemPill, items, orgType, people, personPill, pill,
    pills, plural, quote, reading, renderGraph, reviewing, scoreSubject, scoreText, section,
    selectGroup, selectItem, show, state, stories, tiePills, topicPill} = B;
  const {contextTable, refresh, renderComparison, setMode, toggleCompare} = B.later;
  function selectPerson(id, scopedScore = null) {
    const person = people.get(id); if (!person) return; show(selectPerson, id, scopedScore);
    state.selection = id; state.detailItems = [...person.items, ...person.about_in, ...(person.statements || []).map(c => claims.get(c)?.item)].filter(Boolean);
    $('detail').replaceChildren(el('p', person.kind === 'person' ? 'PERSON' : 'ORGANIZATION · ' + orgType(person).toUpperCase(), 'eyebrow'), el('h3', person.name, 'detail-title'),
      el('p', `${plural(person.facts.length, 'fact')} about them · ${plural(person.items.length, 'post')} by them, ${person.about_in.length} about them`, 'detail-meta'));
    if (person.kind === 'person') {
      if (!person.statements?.length && person.facts.length) $('detail').append(el('p','Sourced facts about this person are saved here. No statements are attributed to them, so they receive no speaker contribution score.','small quiet'));
      else if (!person.statements?.length) $('detail').append(el('p','Mention only: no statements are attributed to this person. They are excluded from the People graph and ranking.','small quiet'));
      else if (person.identity_url) {const a=external(person.identity_url); a.textContent='Profile identity checked by you ↗'; $('detail').append(a);}
      else $('detail').append(el('p','Unresolved name group: use speaker review on individual statements to separate same-name people by profile URL.','small quiet'));
      const rank = scopedScore || data.people_scores?.[scoreSubject()]?.find(r => r.person_id === id);
      if (rank && person.statements?.length) {
        $('detail').append(el('h3', `Contribution: ${scoreText(rank)}`, 'detail-section'),
          el('p', `${rank.evidence_level} evidence · ${rank.source_count} accepted source items · ${rank.idea_count} distinct Ideas`, 'detail-meta'));
        const parts = el('div', null, 'chips');
        for (const [label, value] of [['Subject coverage', rank.subject_relevance], ['Track record', rank.track_record], ['Professional relevance', rank.professional_relevance]]) parts.append(el('span', `${label}: ${value == null ? 'unknown (neutral)' : value.toFixed(2)}`, 'chip'));
        $('detail').append(parts);
        rank.rationale.forEach(reason => $('detail').append(el('p', reason, 'score-reason')));
        if (rank.claim_ids.length) { const list = el('div', null, 'claim-list'); rank.claim_ids.forEach(c => claims.has(c) && list.append(claimRow(claims.get(c), true))); $('detail').append(el('h3', 'Accepted statements behind the score', 'detail-section'), list); }
        for (const source of rank.profile_sources) { const a = external(source.url); a.textContent = `${source.value} ↗`; $('detail').append(a); }
      }
      for (const field of [person.role, person.affiliation].filter(Boolean)) {const a = external(field.url); a.textContent = `${field.value} ↗`; $('detail').append(a);}
      const pending = (person.statements || []).filter(c => claims.get(c)?.quality !== 'accepted');
      if (pending.length) $('detail').append(el('p', `${pending.length} attributed statements await acceptance and do not affect the score.`, 'chip warn'));
    }
    const named = data.items.filter(i => i.mentions.some(m => m.id === id) && !person.items.includes(i.id) && !person.about_in.includes(i.id)).map(i => i.id);
    const fed = Object.entries(person.clusters).sort((a, b) => b[1] - a[1]);
    const topicIds = [...new Set(fed.flatMap(([cid]) => clusters.get(cid)?.topics || []))];
    $('detail').append(...section(person.kind === 'person' ? 'Organizations' : 'People', tiePills(person)),
      ...section('Wrote', person.items.map(i => itemPill(i, items.get(i)?.source))),
      ...section('Posts that tell about them', person.about_in.map(i => itemPill(i, items.get(i)?.source))),
      ...section('Posts that name them', named.map(i => itemPill(i, items.get(i)?.source))),
      ...section('Topics', topicIds.map(topicPill)),
      ...section('Clusters of those posts', fed.map(([cid, n]) => pill(clusters.get(cid)?.title || cid, () => selectGroup(cid), 'cluster', plural(n, 'Idea')))));
    if (person.facts.length) {
      $('detail').append(el('h3', 'What is said about them', 'detail-section'));
      const list = el('div', null, 'claim-list');
      person.facts.forEach(c => claims.has(c) && list.append(claimRow(claims.get(c), true))); $('detail').append(list);
    }
    highlight();
  }
  function selectNode(id) {
    if (people.has(id)) return selectPerson(id);
    if (clusters.has(id) || data.topics.some(t => t.id === id)) return selectGroup(id);
    if (items.has(id)) return selectItem(id);
    if (!claims.has(id)) return;
    show(selectNode, id);
    if (state.mode !== 'claims' && !reading && !reviewing) { state.mode = 'claims'; setMode(); refresh(); }  // reading, the map stays
    state.selection = id;
    state.detailItems = [claims.get(id).item];
    if (!reviewing && !cy.getElementById(id).length) renderGraph(filteredItems());
    const c = claims.get(id), item = items.get(c.item), story = c.idea && stories.get(c.idea);
    const cluster = story?.cluster, topicIds = [...new Set([...(story?.topics || []), ...(clusters.get(cluster)?.topics || [])])];
    const title = el('h3', c.statement, 'detail-title' + (c.superseded ? ' superseded' : '')); title.append(band(c));
    $('detail').replaceChildren(el('p', 'CLAIM', 'eyebrow'), title,
      el('p', [c.kind, c.durability, c.superseded ? 'superseded' : null, c.noise ? 'noise' : null, c.quality !== 'accepted' ? c.quality : null].filter(Boolean).join(' · '), 'detail-meta'));
    if (c.speaker && people.has(c.speaker)) $('detail').append(pill(`Stated by ${people.get(c.speaker).name}`, () => selectPerson(c.speaker), 'person', c.attribution?.kind === 'quoted' ? 'Quoted speaker; distinct from the document author' : 'Source author'));
    else $('detail').append(el('p', 'Speaker attribution unknown; this statement gives no person scoring credit.', 'small quiet'));
    if (data.served) window.peopleReview.mount($('detail'), c);
    $('detail').append(el('p', c.attribution_reviewed ? 'Speaker checked by you.' : 'Speaker is model-inferred and has not been checked by you.', 'small quiet'));
    if (c.evidence_origin) {const a=external(c.evidence_origin); a.textContent='Primary evidence origin checked by you ↗'; $('detail').append(a);}
    // Where it grows from, then what it grows into.
    $('detail').append(el('h3', 'Grows from', 'detail-section'),
      pills([itemPill(c.item, [item.source, item.author, date(item)].filter(Boolean).join(' · '))]));
    c.anchors.forEach(a => $('detail').append(quote(a)));
    if (!c.anchors.length) $('detail').append(el('p', 'No passage is recorded for this claim.', 'small quiet'));
    $('detail').append(button('Show it in the whole post', () => selectItem(c.item, id), 'text-button'));
    $('detail').append(el('h3', 'Grows into', 'detail-section'),
      flow([[1, 'claim'], [story ? 1 : 0, 'Idea'], [cluster ? 1 : 0, 'cluster'], [topicIds.length, 'topic']]));
    if (story) {
      $('detail').append(el('p', `Idea: ${story.title}`, 'idea-line'), el('p', `${story.status} · ${plural(story.claims.length, 'claim')} from ${plural(story.items.length, 'post')}`, 'small quiet'));
      if (story.chain.length) { const chain = el('ol', null, 'chain'); story.chain.forEach(s => chain.append(el('li', s))); $('detail').append(chain); }
    } else if (c.subject) $('detail').append(el('p', 'A fact about someone: it joins their profile, not an Idea.', 'small quiet'));
    $('detail').append(...section('Cluster', cluster ? [clusterPill(cluster)] : []), ...section('Topics', topicIds.map(topicPill)),
      ...section('About', c.subject && people.has(c.subject) ? [personPill(c.subject)] : []));
    const elsewhere = story ? story.claims.filter(o => o !== id && claims.has(o)) : [];
    if (elsewhere.length) {
      $('detail').append(el('h3', 'The same Idea elsewhere', 'detail-section'));
      const list = el('div', null, 'claim-list'); elsewhere.forEach(o => list.append(claimRow(claims.get(o), true))); $('detail').append(list);
    }
    const rel = data.edges.filter(e => e.source === id || e.target === id);
    const short = text => text.length > 90 ? text.slice(0, 89) + '…' : text;
    $('detail').append(...section('Relations', rel.map(e => { const out = e.source === id, other = claims.get(out ? e.target : e.source);
      return pill(out ? `This ${e.label}: ${short(other.statement)}` : `${short(other.statement)} — ${e.label} this`,
        () => selectEdge(e), 'relation', basisWords[e.basis]); })));
    const compare = button(state.comparison.includes(id) ? '✓ In comparison' : 'Add to comparison', () => toggleCompare(id));
    compare.dataset.compare = id; compare.classList.add('pill'); $('detail').append(pills([compare]));
    highlight();
  }
  function selectEdge(edge) {
    show(selectEdge, edge);
    const ends = [edge.source, edge.target];
    state.detailItems = ends.map(id => claims.get(id)?.item || (items.has(id) ? id : null)).filter(Boolean);
    $('detail').replaceChildren(el('p', 'RELATION', 'eyebrow'), el('h3', edge.label, 'detail-title'), el('p', `${edge.kind} · ${basisWords[edge.basis]}`, 'detail-meta'));
    if (edge.rationale) $('detail').append(el('p', edge.rationale));
    if (edge.context) $('detail').append(contextTable(edge.context, 'The relator’s reading of this pair'));
    if (edge.said) $('detail').append(el('p', 'Later: ' + edge.said, 'small'), el('p', 'Earlier: ' + edge.about, 'small'));
    const pair = claims.has(edge.source) ? ends : [edge.said, edge.about].map(s => data.claims.find(c => c.statement === s && ends.includes(c.item))?.id);
    pair.filter(Boolean).forEach(id => $('detail').append(claimCard(claims.get(id))));
    if (pair.every(Boolean)) {
      state.comparison = pair; renderComparison();
      $('detail').append(button('Go to the comparison ↓', () => $('comparison').scrollIntoView({behavior: 'smooth', block: 'start'})));
    }
    if (edge.basis === 'proposal' && data.served && !reviewing) {  // basis 'proposal': still waiting for you
      const decide = el('a', 'The review asked you about this relation: decide it in Review →', 'chip warn'); decide.href = '/review/';
      $('detail').append(decide);
    } else if (edge.basis === 'proposal') $('detail').append(el('p', reviewing ? 'The review asks you about this relation: decide it among the proposals.'
      : 'The review asked you about this relation; see Updates & suggestions below.', 'chip warn'));
    state.selection = edge.source; highlight();
  }
  Object.assign(B, {selectEdge, selectNode, selectPerson});
})();

/* Refresher Atlas: the evidence panel's building blocks: pills, the text with its claims marked, claim rows. */
'use strict';
(() => {
  const B = window.RefresherAtlas;
  if (!B) return;  // an empty Atlas wires nothing
  const {band, button, claims, clusters, data, date, el, items, people, plural, quote, reading, state,
    stories, topicTitle} = B;
  const {selectGroup, selectItem, selectNode, selectPerson, toggleCompare} = B.later;
  // --- evidence: one view per selection; the text in one language at a time, and where it leads ---
  const translated = item => state.lang === 'translation' && item && item.translation.length > 0;
  function pill(text, action, kind, sub) {
    const node = button('', action, 'pill ' + kind); node.append(el('span', text, 'pill-text'));
    if (sub) node.append(el('span', sub, 'pill-sub')); return node;
  }
  function pills(nodes, more) {
    const wrap = el('div', null, 'pills'); wrap.append(...nodes);
    if (more) wrap.append(el('span', more, 'small quiet'));
    return wrap;
  }
  const section = (head, nodes, more) => nodes.length ? [el('h3', head, 'detail-section'), pills(nodes, more)] : [];
  function flow(steps) {  // post → claims → Ideas → clusters → topics: how far an item has grown
    const row = el('div', null, 'flow'); row.setAttribute('aria-label', steps.map(([n, w]) => `${n} ${w}`).join(', then '));
    steps.forEach(([n, word], i) => { if (i) row.append(el('span', '→', 'flow-arrow'));
      const step = el('span', null, 'flow-step' + (n ? '' : ' none')); step.append(el('strong', n), el('span', n === 1 ? word : word + 's')); row.append(step); });
    return row;
  }
  const itemPill = (id, sub) => {  // in the reading layout with its gist as a preview
    const node = pill(items.get(id)?.title || id, () => selectItem(id), 'source', sub), gist = reading && (items.get(id)?.gist || items.get(id)?.hook);
    if (gist) node.append(el('span', gist, 'pill-gist'));
    return node;
  };
  const clusterPill = id => pill(clusters.get(id)?.title || id, () => selectGroup(id), 'cluster',
    clusters.has(id) ? plural(clusters.get(id).ideas.length, 'Idea') : null);
  const topicPill = id => pill(topicTitle(id), () => selectGroup(id), 'topic');
  const personPill = (id, sub) => pill(people.get(id)?.name || id, () => selectPerson(id), 'person', sub);
  const orgType = o => o.type ? o.type.replace('_', ' ') : 'organization';
  function peopleOf(itemIds, kind = 'person') {  // person (or organization) -> what they are to these posts: wrote, named, or told about
    const roles = new Map(), names = new Map(), add = (id, role) => roles.set(id, new Set([...(roles.get(id) || []), role]));
    for (const p of people.values()) if (p.kind === kind) itemIds.forEach(i => { if (p.items.includes(i)) add(p.id, 'wrote');
      if (p.about_in.includes(i)) add(p.id, 'told about'); });
    itemIds.forEach(i => (items.get(i)?.mentions || []).filter(m => (m.kind || 'person') === kind)
      .forEach(m => { names.set(m.id, m.name); add(m.id, 'named'); }));
    // Someone only named, with nothing said about them, has no profile to open.
    return [...roles].sort((a, b) => people.has(b[0]) - people.has(a[0])).map(([id, r]) => people.has(id)
      ? personPill(id, [kind === 'organization' ? orgType(people.get(id)) : null, ...r].filter(Boolean).join(', '))
      : pill(names.get(id), () => {}, 'person bare', 'named, no profile'));
  }
  function tiePills(profile) {  // a person's organizations, or an organization's people, with how they are tied
    return (profile.ties || []).map(t => pill(people.get(t.id)?.name || t.id, () => selectPerson(t.id), 'person',
      [profile.kind === 'person' ? t.label : t.label + ' it', t.item ? null : 'from the web'].filter(Boolean).join(' · ')));
  }
  function clustersOf(item) { return [...new Set(item.stories.map(s => stories.get(s)?.cluster).filter(Boolean))]; }
  function relatedSources(item) {  // other posts, with the strongest reason they are close to this one
    const why = new Map(), add = (id, reason) => id !== item.id && items.has(id) && !why.has(id) && why.set(id, reason);
    if (item.repost_of) add(item.repost_of, 'repost of it');
    data.item_edges.forEach(e => { if (e.source === item.id) add(e.target, e.label); if (e.target === item.id) add(e.source, e.label + ' this'); });
    data.items.forEach(other => other.repost_of === item.id && add(other.id, 'reposts this'));
    item.stories.forEach(s => (stories.get(s)?.items || []).forEach(i => add(i, 'same Idea')));
    clustersOf(item).forEach(c => (clusters.get(c)?.items || []).forEach(i => add(i, 'cluster: ' + clusters.get(c).title)));
    return [...why];
  }
  function sourceText(item, focus) {
    // The whole post with each claim's passage marked; in Translation, the translated lines stand in for the original.
    const box = el('div', null, 'source-text');
    if (!item.text) { box.append(el('p', 'The text of this item is not in the vault.', 'quiet')); return box; }
    const spans = item.claims.flatMap(cid => claims.get(cid).anchors.map(a => [a.start_char, a.end_char, cid]));
    const covering = (s, e) => [...new Set(spans.filter(([a, b]) => a < e && b > s).map(x => x[2]))];
    const piece = (text, s, e, cls) => {
      const ids = covering(s, e);
      // Reading, the text is text: claim marks only when you ask to inspect them.
      if (!ids.length || (reading && !state.inspect)) { box.append(cls ? el('span', text, cls) : document.createTextNode(text)); return; }
      const mark = el('mark', text, ids.includes(focus) ? 'on' : null);
      mark.dataset.claims = ids.join(' '); mark.tabIndex = 0; mark.setAttribute('role', 'button');
      mark.title = ids.map(id => '• ' + claims.get(id).statement).join('\n') + '\n\nOpen the claim';
      const open = () => selectNode(ids.includes(focus) ? focus : ids[0]);
      mark.addEventListener('click', open); mark.addEventListener('keydown', e => e.key === 'Enter' && open());
      box.append(mark);
    };
    if (translated(item)) {
      let at = 0;
      for (const line of item.translation) {
        const gap = item.text.slice(at, line.start);
        if (gap.trim()) piece(gap, at, line.start, 'untranslated'); else if (at) box.append('\n'.repeat(Math.max(1, (gap.match(/\n/g) || []).length)));
        piece(line.text, line.start, line.end); at = line.end;
      }
      if (item.text.slice(at).trim()) piece(item.text.slice(at), at, item.text.length, 'untranslated');
    } else {
      const cuts = [...new Set([0, item.text.length, ...spans.flatMap(([a, b]) => [a, b])])]
        .filter(x => x >= 0 && x <= item.text.length).sort((a, b) => a - b);
      for (let i = 0; i + 1 < cuts.length; i++) piece(item.text.slice(cuts[i], cuts[i + 1]), cuts[i], cuts[i + 1]);
    }
    return box;
  }
  function textBlock(item, focus) {
    const box = sourceText(item, focus), wrap = el('div', null, 'text-block');
    const note = translated(item) ? `Machine translation into ${data.language}, for reading only; claims were judged on the original.`
      : 'Original text. Marked passages are where claims come from: hover to read the claim, click to open it.';
    wrap.append(el('p', note, 'small quiet'), box);
    if (item.translation.length && !translated(item)) wrap.append(el('p', `A translation into ${data.language} is available: switch Translation above.`, 'small quiet'));
    const more = button('Show the whole text', () => { box.classList.toggle('open');
      more.textContent = box.classList.contains('open') ? 'Show less' : 'Show the whole text'; }, 'text-button');
    wrap.append(more);
    requestAnimationFrame(() => { more.hidden = box.scrollHeight <= box.clientHeight + 4;
      const on = box.querySelector('mark.on'); if (on) box.scrollTop = Math.max(0, on.offsetTop - box.clientHeight / 3); });
    return wrap;
  }
  function claimRow(claim, showSource) {
    // One line per claim: hover marks its passage in the text, click opens it with its passage and its Idea.
    const row = button('', () => selectNode(claim.id), 'claim-row' + (claim.superseded ? ' superseded' : ''));
    const text = el('span', claim.statement, 'claim-statement'); text.append(band(claim)); row.append(text);
    const story = claim.idea && stories.get(claim.idea), cluster = story?.cluster && clusters.get(story.cluster);
    const where = [claim.kind, story && story.claims.length > 1 ? `Idea of ${story.claims.length} claims` : null,
      cluster ? cluster.title : null, showSource ? items.get(claim.item)?.source : null].filter(Boolean);
    row.append(el('span', where.join(' · '), 'claim-where'));
    const marks = on => document.querySelectorAll(`#detail mark[data-claims~="${claim.id}"]`).forEach(m => m.classList.toggle('on', on));
    row.addEventListener('mouseenter', () => marks(true)); row.addEventListener('mouseleave', () => marks(false));
    row.addEventListener('focus', () => marks(true)); row.addEventListener('blur', () => marks(false));
    return row;
  }
  function claimCard(claim) {  // a claim with its passage, where the passage is the point (relations, comparisons)
    const box = el('article', null, 'claim-card' + (claim.superseded ? ' superseded' : ''));
    const text = el('p', claim.statement); text.append(band(claim)); box.append(text);
    const item = items.get(claim.item);
    box.append(button(`from ${item.source} · ${date(item)}`, () => selectNode(claim.id), 'text-button from'));
    claim.anchors.forEach(a => box.append(quote(a)));
    if (!claim.anchors.length) box.append(el('p', 'No passage is recorded for this claim.', 'small quiet'));
    const compare = button(state.comparison.includes(claim.id) ? '✓ In comparison' : 'Add to comparison', () => toggleCompare(claim.id));
    compare.dataset.compare = claim.id; box.append(compare);
    return box;
  }
  Object.assign(B, {claimCard, claimRow, clusterPill, clustersOf, flow, itemPill, orgType, peopleOf, personPill, pill,
    pills, relatedSources, section, sourceText, textBlock, tiePills, topicPill, translated});
})();

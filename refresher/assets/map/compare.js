/* Refresher Atlas: the side-by-side comparison, and the reading layout's text column. */
'use strict';
(() => {
  const B = window.RefresherAtlas;
  if (!B) return;  // an empty Atlas wires nothing
  const {$, basisWords, button, claims, contextFields, cy, data, date, el, external, filteredItems,
    itemPill, items, people, pills, plural, quote, reading, selectItem, sourceText, state, stories,
    translated} = B;
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
      for (const [label, value] of [['Writer', item.author || 'Unknown'], ['Speaker', people.get(c.speaker)?.name || 'Unknown'], ['Source', item.source], ['Kind', c.kind],
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
    state.detailItems = []; state.view = null;
    if (reading) {  // nothing picked: every source of the slice, newest first, its gist a preview
      const list = filteredItems().slice().sort((a, b) => b.time.localeCompare(a.time));
      $('detail').replaceChildren(el('p', 'SOURCES', 'eyebrow'), el('p', `${plural(list.length, 'source')} in this slice: pick one to read it`, 'detail-meta'),
        pills(list.map(i => itemPill(i.id, [i.source, i.author, date(i)].filter(Boolean).join(' · ')))));
      return;
    }
    $('detail').replaceChildren(el('p', 'Pick an item or a relation in this slice.', 'quiet'));
  }
  // --- the reading layout's middle column: the whole text of the source picked, its claims marked ---
  let readerItem = null, readerFocus = null;
  function openReader(item, focus) {
    readerItem = item; readerFocus = focus;
    const node = cy.getElementById(item.id);  // the map is narrow here: bring the source into its middle
    if (node.length) cy.animate({center: {eles: node}}, {duration: 250});
    const url = new URL(location.href); url.searchParams.set('read', item.id); history.replaceState(history.state, '', url);
    const box = $('reader'), tools = el('div', null, 'reader-tools');
    if (item.translation.length) tools.append(button(translated(item) ? 'Translation · show the original' : 'Original · show the translation',
      () => setLang(translated(item) ? 'original' : 'translation')));
    if (item.url) tools.append(external(item.url));
    tools.append(button(state.inspect ? 'Hide claim marks' : 'Inspect claims', () => { state.inspect = !state.inspect; openReader(readerItem, readerFocus); }));
    const text = sourceText(item, focus); text.classList.add('open', 'reader-text');
    box.replaceChildren(el('p', [item.source, date(item)].filter(Boolean).join(' · '), 'eyebrow'), el('h1', item.title, 'reader-title'),
      el('p', [item.author ? 'by ' + item.author : null, plural(item.claims.length, 'claim'), item.repost_of ? 'a repost' : null].filter(Boolean).join(' · '), 'detail-meta'),
      tools, translated(item) ? el('p', `Machine translation into ${data.language}, for reading only; claims were judged on the original.`, 'small quiet') : '', text);
    box.scrollTop = 0;
    const on = text.querySelector('mark.on'); if (on) requestAnimationFrame(() => on.scrollIntoView({block: 'center'}));
  }
  function setLang(lang) {
    state.lang = lang;
    try { localStorage.setItem('atlas-lang', lang); } catch (e) { /* the choice lasts for this page only */ }
    for (const other of ['original', 'translation']) $('lang-' + other).setAttribute('aria-pressed', String(other === lang));
    if (state.view) state.view[0](...state.view[1]);
    if (reading && readerItem && state.view?.[0] !== selectItem) openReader(readerItem, readerFocus);
    renderComparison();
  }
  Object.assign(B, {clearDetail, contextTable, openReader, renderComparison, setLang, toggleCompare});
})();

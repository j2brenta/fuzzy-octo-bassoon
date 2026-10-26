/* Offline coordinated views. Imported values only enter textContent/canvas labels. */
'use strict';
(() => {
  const data = JSON.parse(document.getElementById('newsmap-data').textContent);
  const $ = id => document.getElementById(id);
  const state = {topic: '', story: '', mode: 'documents', selection: null, ego: false, comparison: [], comparisonEdge: null, detailEdge: null, edgePage: 0, nodePage: 0, detailItems: []};
  const items = new Map(data.items.map(x => [x.id, x]));
  const claims = new Map(data.claims.map(x => [x.id, x]));
  const resources = new Map(data.resources.map(x => [x.id, x]));
  const identities = new Map(data.identities.map(x => [x.id, x]));
  const decisions = new Map(data.decisions.map(x => [x.id, x]));
  const words = {author_hint: 'Автор указан в source metadata', forwarder_hint: 'Переславший материал',
    extracted_mention: 'Извлечённое упоминание', source_record: 'Запись источника',
    publication: 'публикация', capture: 'захват', not_disclosed: 'дата не раскрыта'};
  const contextFields = {subject: 'Предмет', version: 'Версия', metric: 'Метрика', dataset: 'Датасет',
    unit: 'Единица', conditions: 'Условия', time_attribution: 'Время / атрибуция', unknowns: 'Неизвестно'};
  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  }
  function button(text, action, cls) {
    const node = el('button', text, cls); node.type = 'button'; node.addEventListener('click', action); return node;
  }
  function instant(item) {
    // Require an explicit offset; never guess the browser's local timezone.
    return typeof item.time === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(item.time) ? Date.parse(item.time) : NaN;
  }
  function utcDay(item) {
    const t = instant(item); return Number.isFinite(t) ? new Date(t).toISOString().slice(0, 10) : null;
  }
  function compareTime(a, b) {
    const x = instant(a), y = instant(b);
    return (Number.isFinite(x) ? x : -Infinity) - (Number.isFinite(y) ? y : -Infinity) || a.id.localeCompare(b.id);
  }
  function date(item) {
    if (!item.time) return 'Дата не раскрыта';
    const t = instant(item);
    return (Number.isFinite(t) ? new Date(t).toLocaleDateString('ru', {day: 'numeric', month: 'short', timeZone: 'UTC'}) + ' UTC' : 'Некорректная дата: ' + item.time) + ' · ' + words[item.time_basis];
  }
  function external(url) {
    const a = el('a', 'Открыть источник ↗ (внешний сайт)');
    a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.referrerPolicy = 'no-referrer'; return a;
  }
  function quote(anchor) {
    const box = el('blockquote', null, 'quote');
    if (anchor.prefix) box.append(el('span', anchor.prefix, 'context'));
    box.append(el('mark', anchor.quote));
    if (anchor.suffix) box.append(el('span', anchor.suffix, 'context'));
    const locator = `Фрагмент ${anchor.span || 'ссылки'} · [${anchor.start_char}, ${anchor.end_char}) · ${anchor.coordinate_unit}`;
    box.append(el('div', locator, 'locator'));
    if (anchor.text_sha256) box.append(el('div', 'text SHA256 ' + anchor.text_sha256, 'locator'));
    return box;
  }
  $('snapshot-date').textContent = new Date(data.snapshot_at).toLocaleString('ru');
  $('scope-badge').textContent = data.scope === 'private_local' ? 'Частный' : 'Разрешённый экспорт';
  for (const [key, title] of [['items', 'сохранённых ревизий'], ['stories', 'историй'], ['edges', 'явных связей']]) {
    const stat = el('div', null, 'stat'); stat.append(el('strong', data.counts[key]), el('span', title)); $('stats').append(stat);
  }
  for (const story of data.stories) {
    const opt = el('option', story.title + ` (${story.items.length})`); opt.value = story.id; $('story-filter').append(opt);
  }
  function renderTopics() {
    $('topics').replaceChildren();
    const topics = [{id: '', title: 'Все темы', count: data.items.length}, ...data.topics,
      {id: 'unassigned', title: 'Без темы', count: data.items.filter(i => !i.topics.length).length}];
    for (const topic of topics) {
      const node = button('', () => {state.topic = topic.id; state.story = ''; $('story-filter').value = ''; state.ego = false; refresh();}, 'topic');
      node.setAttribute('aria-pressed', String(state.topic === topic.id));
      if (topic.definition) node.title = topic.definition;
      node.append(el('strong', topic.title), el('span', `${topic.count} материалов`, 'topic-count')); $('topics').append(node);
    }
  }
  function filteredItems() {
    const query = $('search').value.toLocaleLowerCase();
    return data.items.filter(item =>
      (!state.topic || (state.topic === 'unassigned' ? !item.topics.length : item.topics.includes(state.topic))) &&
      (!state.story || (state.story === 'unassigned' ? !item.stories.length : item.stories.includes(state.story))) &&
      (!$('quality-filter').value || item.quality === $('quality-filter').value) &&
      (!$('role-filter').value || item.roles.some(id => identities.get(id).role === $('role-filter').value)) &&
      (!$('date-filter').value || (utcDay(item) && utcDay(item) >= $('date-filter').value)) &&
      (data.scope === 'public_allowlist' || $('historical').checked || item.selected) &&
      (!query || (item.title + ' ' + item.source).toLocaleLowerCase().includes(query))
    ).sort((a, b) => compareTime(b, a));
  }
  const cy = cytoscape({container: $('graph'), elements: [], layout: {name: 'preset'},
    minZoom: .25, maxZoom: 2.5, wheelSensitivity: .2, boxSelectionEnabled: false,
    style: [
      {selector: 'node', style: {'label': 'data(label)', 'shape': 'round-rectangle', 'background-color': '#fdfcf7',
        'border-width': 1.2, 'border-color': '#8fa79a', 'width': 185, 'height': 64,
        'font-size': 13, 'color': '#233a35', 'text-wrap': 'wrap', 'text-max-width': 170,
        'text-valign': 'center', 'text-halign': 'center', 'text-overflow-wrap': 'anywhere', 'padding': 5}},
      {selector: 'node[kind="resource"]', style: {'shape': 'round-rectangle', 'background-color': '#e8ece7',
        'border-style': 'dashed', 'width': 152, 'height': 44, 'font-size': 9, 'text-max-width': 140}},
      {selector: 'node[kind="claim"]', style: {'background-color': '#e1ece4', 'border-color': '#7d9f8c'}},
      {selector: ':parent', style: {'shape': 'round-rectangle', 'background-color': '#d8e4d2', 'background-opacity': .28,
        'border-color': '#c8d6c2', 'border-width': 1, 'padding': 18, 'text-valign': 'top', 'text-margin-y': -9,
        'font-size': 12, 'font-weight': 600, 'text-max-width': 200}},
      {selector: 'node[pending=1]', style: {'border-color': '#b88959', 'background-color': '#f4eadc'}},
      {selector: 'edge', style: {'width': 1.3, 'line-color': '#9baaa1', 'target-arrow-color': '#81958a',
        'target-arrow-shape': 'triangle', 'curve-style': 'unbundled-bezier', 'control-point-distances': 235, 'control-point-weights': .5, 'arrow-scale': .75,
        'label': 'data(label)', 'font-size': 8, 'color': '#55655e', 'text-rotation': 'autorotate',
        'text-background-color': '#f3f5ef', 'text-background-opacity': .95, 'text-background-padding': 3}},
      {selector: 'edge[basis="reviewed"],edge[basis="review_record"]', style: {'line-color': '#176a61', 'target-arrow-color': '#176a61', 'width': 2}},
      {selector: 'edge[basis="proposal"]', style: {'line-style': 'dashed', 'line-color': '#be8656', 'target-arrow-color': '#be8656'}},
      {selector: '.dim', style: {'opacity': .2}},
      {selector: 'node.highlight', style: {'border-width': 3, 'border-color': '#176a61'}},
      {selector: 'edge.highlight', style: {'width': 3, 'line-color': '#176a61', 'target-arrow-color': '#176a61'}},
      {selector: ':selected', style: {'border-color': '#176a61', 'border-width': 3}}
    ]});
  const positions = new Map();
  const groups = [...new Set(data.items.map(i => i.topics[0] || 'unassigned'))].sort();
  const groupOf = new Map(), groupSizes = new Map();
  groups.forEach((topic, column) => {
    const members = data.items.filter(i => (i.topics[0] || 'unassigned') === topic)
      .sort(compareTime);
    members.forEach((item, i) => {positions.set(item.id, {x: column * 260, y: i * 105}); groupOf.set(item.id, topic);});
    const cc = data.claims.filter(c => members.some(i => i.id === c.item));
    cc.forEach((c, i) => {positions.set(c.id, {x: column * 260, y: i * 105}); groupOf.set(c.id, topic);});
    groupSizes.set(topic, members.length);
  });
  const resourceCounts = new Map();
  data.resources.forEach(resource => {
    const match = data.edges.find(e => e.source === resource.id && e.kind === 'URL_MATCH');
    const referring = data.edges.find(e => e.target === resource.id && items.has(e.source));
    const topic = groupOf.get(match?.target || referring?.source) || groups[0] || 'unassigned';
    const extra = resourceCounts.get(topic) || 0; resourceCounts.set(topic, extra + 1);
    groupOf.set(resource.id, topic);
    positions.set(resource.id, {x: Math.max(0, groups.indexOf(topic)) * 260, y: ((groupSizes.get(topic) || 0) + extra) * 105});
  });
  function graphSlice(visible) {
    const allowedItems = new Set(visible.map(x => x.id));
    let nodes, edges;
    if (state.mode === 'documents') {
      nodes = visible.map(i => ({id: i.id, kind: 'document', label: i.title.slice(0, 125), pending: Number(i.quality !== 'accepted')}));
      edges = data.edges.filter(e => ['REFERS_TO', 'URL_MATCH', 'CAPTURED_FROM', 'CAPTURE_RECORD_MATCH'].includes(e.kind) &&
        (!items.has(e.source) || allowedItems.has(e.source)) && (!items.has(e.target) || allowedItems.has(e.target)));
      const used = new Set(edges.flatMap(e => [e.source, e.target]));
      nodes.push(...data.resources.filter(r => used.has(r.id)).map(r => ({id: r.id, kind: 'resource', label: r.label.slice(0, 110)})));
      const identityEdges = data.edges.filter(e => e.kind === 'IDENTITY_LINK' &&
        allowedItems.has(identities.get(e.source)?.item) && allowedItems.has(identities.get(e.target)?.item));
      const linked = new Set(identityEdges.flatMap(e => [e.source,e.target]));
      nodes.push(...data.identities.filter(i => linked.has(i.id)).map(i => ({id:i.id,kind:'identity',label:Object.values(i.hint).join(' · ')})));
      edges.push(...identityEdges);
    } else {
      nodes = data.claims.filter(c => allowedItems.has(c.item)).map(c => ({id: c.id, kind: 'claim', label: c.statement.slice(0, 125), pending: Number(c.quality !== 'accepted')}));
      const n = new Set(nodes.map(x => x.id));
      edges = data.edges.filter(e => n.has(e.source) && n.has(e.target));
      if ($('review-filter').value === 'active') edges = edges.filter(e => e.active_reviewed);
      if ($('review-filter').value === 'proposal') edges = edges.filter(e => e.basis === 'proposal');
      if ($('review-filter').value === 'model') edges = edges.filter(e => e.active_model_assessed);
      if ($('review-filter').value === 'history') edges = edges.filter(e => ['review_history','assessment_history','review_record'].includes(e.basis));
    }
    if (state.ego && state.selection) {
      const adjacent = new Set([state.selection]);
      edges.filter(e => e.source === state.selection || e.target === state.selection).forEach(e => {adjacent.add(e.source); adjacent.add(e.target);});
      nodes = nodes.filter(n => adjacent.has(n.id));
    }
    const allIds = new Set(nodes.map(n => n.id));
    const total = nodes.length, allNodes = [...nodes], allEdges = edges.filter(e => allIds.has(e.source) && allIds.has(e.target)).sort((a,b) => a.id.localeCompare(b.id));
    if (state.selection) nodes.sort((a, b) => Number(b.id === state.selection) - Number(a.id === state.selection));
    const selectedEdge = data.edges.find(e => e.id === state.detailEdge);
    if (selectedEdge) nodes.sort((a,b) => Number([selectedEdge.source,selectedEdge.target].includes(b.id)) - Number([selectedEdge.source,selectedEdge.target].includes(a.id)));
    nodes = nodes.slice(0, Math.min(20, data.limits.primary_nodes));
    const shown = new Set(nodes.map(n => n.id));
    const admitted = allEdges.filter(e => shown.has(e.source) && shown.has(e.target));
    return {nodes, edges: admitted.slice(state.edgePage * 60, state.edgePage * 60 + 60), total, allNodes, allEdges};
  }
  function renderTimeline(visible) {
    $('timeline').replaceChildren(); $('list-count').textContent = `${visible.length} материалов`;
    for (const item of visible) {
      const b = button('', () => selectItem(item.id), 'item-button' + (state.selection === item.id || claims.get(state.selection)?.item === item.id ? ' active' : ''));
      b.dataset.item = item.id;
      b.append(el('span', date(item), 'item-date'), el('span', item.title, 'item-title'), el('span', item.source + ' · ' + item.quality + (item.selected === null ? ' · раскрытая история, актуальность не раскрыта' : item.selected ? '' : ' · историческая ревизия'), 'item-source'));
      $('timeline').append(b);
    }
    if (!visible.length) $('timeline').append(el('p', 'Нет материалов с такими фильтрами.', 'quiet'));
  }
  function renderGraph(visible) {
    const slice = graphSlice(visible);
    const shownGroups = [...new Set(slice.nodes.map(n => groupOf.get(n.id) || 'unassigned'))].sort();
    const compact = new Map(); let groupX = 0;
    for (const topic of shownGroups) {
      const members = slice.nodes.filter(n => (groupOf.get(n.id) || 'unassigned') === topic)
        .sort((a,b) => (positions.get(a.id)?.y || 0) - (positions.get(b.id)?.y || 0) || a.id.localeCompare(b.id));
      const columns = Math.max(1, Math.ceil(members.length / 6));
      members.forEach((n,i) => compact.set(n.id, {x: groupX + (i % columns) * 235, y: Math.floor(i / columns) * 110}));
      groupX += columns * 235 + 25;
    }
    cy.batch(() => {cy.elements().remove(); cy.add([
      ...shownGroups.map(t => ({group: 'nodes', data: {id: 'region-' + t, kind: 'region',
        label: (data.topics.find(x => x.id === t)?.title || 'Без темы') + '\n' + slice.nodes.filter(n => groupOf.get(n.id) === t).length + ' узлов'}})),
      ...slice.nodes.map(n => ({group: 'nodes', data: {...n, parent: 'region-' + (groupOf.get(n.id) || 'unassigned')}, position: compact.get(n.id)})),
      ...slice.edges.map(e => ({group: 'edges', data: e}))]);});
    cy.nodes().lock(); cy.fit(cy.elements(), 34);
    $('map-empty').hidden = slice.nodes.length > 0;
    $('map-count').textContent = `${slice.nodes.length} узлов · ${slice.edges.length} связей`;
    $('hidden-count').textContent = slice.total > slice.nodes.length ? `Скрыто ${slice.total - slice.nodes.length} узлов; уточните фильтр или фокус.` : 'Все узлы этого среза';
    $('hidden-count').textContent += ` · Вне карты ${slice.allEdges.length - slice.edges.length} связей; доступны страницами в списке.`;
    $('map-title').textContent = state.mode === 'documents' ? 'Карта источников и ссылок' : 'Утверждения и интерпретации';
    $('graph-list').replaceChildren();
    slice.allNodes.slice(state.nodePage * 20, state.nodePage * 20 + 20).forEach(n => $('graph-list').append(button(n.label, () => selectNode(n.id))));
    const nodeLabel = id => items.get(id)?.title || claims.get(id)?.statement || resources.get(id)?.label || (identities.has(id) ? Object.values(identities.get(id).hint).join(' · ') : id);
    slice.allEdges.slice(state.edgePage * 60, state.edgePage * 60 + 60).forEach(e => $('graph-list').append(button(nodeLabel(e.source) + ' → ' + e.label + ' (' + e.basis + ') → ' + nodeLabel(e.target), () => selectEdge(e))));
    for (const [field, total, size, label] of [['nodePage',slice.allNodes.length,20,'узлов'],['edgePage',slice.allEdges.length,60,'связей']]) {
      if (state[field] > 0) $('graph-list').append(button('Предыдущая страница ' + label, () => {state[field]--; renderGraph(filteredItems());}));
      if ((state[field] + 1) * size < total) $('graph-list').append(button('Следующая страница ' + label, () => {state[field]++; renderGraph(filteredItems());}));
    }
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
    const box = el('article', null, 'claim-card');
    box.append(el('p', claim.statement));
    const compare = button(state.comparison.includes(claim.id) ? '✓ В сравнении' : 'Добавить к сравнению', () => toggleCompare(claim.id));
    compare.dataset.compare = claim.id;
    box.append(compare);
    if (!claim.complete_context) box.append(el('p', 'Раскрыта только часть контекста.', 'small quiet'));
    claim.anchors.forEach(a => box.append(quote(a)));
    return box;
  }
  function selectItem(id) {
    const item = items.get(id); if (!item) return;
    state.detailEdge = null; state.detailItems = [id];
    state.selection = state.mode === 'claims' && item.claims.length ? item.claims[0] : id;
    if (!cy.getElementById(state.selection).length) renderGraph(filteredItems());
    $('detail').replaceChildren(el('h3', item.title, 'detail-title'));
    const chips = el('div', null, 'chips');
    for (const text of [item.quality, item.admission, item.selected === null ? 'Разрешено раскрытие; текущая применимость не раскрыта' : item.selected ? 'Выбранная версия' : 'Невыбранная ревизия']) {
      chips.append(el('span', text, 'chip' + (item.quality !== 'accepted' ? ' warn' : '')));
    }
    $('detail').append(chips, el('p', `${item.source}\n${date(item)}\n${item.availability}`, 'detail-meta'));
    if (item.quality !== 'accepted') $('detail').append(el('p', 'Материал доступен для чтения, но не для одобрения активной смысловой связи.', 'chip warn'));
    $('detail').append(el('div', 'Ревизия: ' + item.revision, 'locator'));
    if (item.first_observed_batch) $('detail').append(el('div', `Впервые наблюдалось в импорте ${item.first_observed_batch}; последнее наблюдение ${item.last_observed_batch}.`, 'detail-meta'));
    if (item.raw_basis) $('detail').append(el('p', item.raw_basis, 'small quiet'));
    if (item.evidence_observation_reason) $('detail').append(el('p', item.evidence_observation_reason, 'small quiet'));
    if (item.url) $('detail').append(external(item.url));
    $('detail').append(el('h3', 'Роли и атрибуция', 'detail-section'));
    item.roles.forEach(rid => {const r = identities.get(rid); $('detail').append(el('p', (words[r.role] || r.role) + ': ' + Object.values(r.hint).join(' · '), 'role'));});
    $('detail').append(el('p', 'Одинаковое имя не объединяет людей. Независимость источников неизвестна.', 'small quiet'));
    if (item.subjects.length) $('detail').append(el('p', 'Ручной subject/version: ' + item.subjects.map(s => s.value.subject + ' / ' + s.value.version).join('; '), 'small'));
    $('detail').append(el('h3', 'Утверждения и все цитируемые фрагменты', 'detail-section'));
    item.claims.forEach(cid => $('detail').append(claimCard(claims.get(cid))));
    if (!item.claims.length) $('detail').append(el('p', 'Нет доступных проверенных фрагментов. Это не подтверждение утверждений.', 'quiet'));
    renderTimeline(filteredItems()); highlight();
  }
  function selectNode(id) {
    if (items.has(id)) return selectItem(id);
    state.detailEdge = null; state.selection = id;
    state.detailItems = claims.has(id) ? [claims.get(id).item] : [];
    if (!cy.getElementById(id).length) renderGraph(filteredItems());
    if (claims.has(id)) {
      const c = claims.get(id); $('detail').replaceChildren(el('h3', 'Точное утверждение', 'detail-title'),
        el('p', items.get(c.item).source + ' · ' + c.quality, 'detail-meta'), claimCard(c));
    } else if (identities.has(id)) {
      const identity = identities.get(id);
      $('detail').replaceChildren(el('h3', Object.values(identity.hint).join(' · ')), el('p', identity.role + ': ' + identity.basis), button('Открыть материал', () => selectItem(identity.item)));
      state.detailItems = [identity.item];
    } else if (resources.has(id)) {
      const r = resources.get(id); $('detail').replaceChildren(el('h3', 'URL-ресурс', 'detail-title'), el('p', r.label, 'detail-meta'),
        el('p', 'Ссылка не равна подтверждению. Совпадение URL не доказывает совпадение версий документа.', 'quiet'));
      if (r.url) $('detail').append(external(r.url));
    }
    renderTimeline(filteredItems()); highlight();
  }
  function selectEdge(edge) {
    // Resolve the immutable exported display edge; never accept a substitute pair.
    edge = data.edges.find(e => e.id === edge.id);
    if (!edge || !graphSlice(filteredItems()).allEdges.some(e => e.id === edge.id)) return;
    state.detailEdge = edge.id;
    state.detailItems = [edge.source, edge.target].map(id => claims.get(id)?.item || identities.get(id)?.item || (items.has(id) ? id : null)).filter(Boolean);
    $('detail').replaceChildren(el('h3', edge.label, 'detail-title'), el('p', edge.kind + ' · ' + edge.basis, 'detail-meta'), el('p', edge.rationale));
    if (edge.assessment) $('detail').append(el('p', edge.assessment + (edge.provenance ? ' · ' + edge.provenance.origin : ''), 'chip'));
    if (edge.actor) $('detail').append(el('p', `Review: ${edge.review_status} · ${edge.actor} · ${edge.at}`, 'small quiet'));
    for (const axis of ['blocked', 'historical', 'needs_recheck']) {
      if (edge[axis]?.length) $('detail').append(el('p', axis + ': ' + edge[axis].join('; '), 'chip warn'));
    }
    edge.anchors?.forEach(a => $('detail').append(quote(a)));
    if (claims.has(edge.source) && claims.has(edge.target)) {
      state.comparison = [edge.source, edge.target]; state.comparisonEdge = {id: edge.id, mode: state.mode}; renderComparison();
      $('detail').append(button('Перейти к сравнению ↓', () => $('comparison').scrollIntoView({behavior: 'smooth', block: 'start'})));
    }
    if (edge.decision) {
      const d = decisions.get(edge.decision);
      if (d) $('detail').append(el('p', (d.assessment || 'Решение') + ': ' + d.actor + ' · ' + d.at + (d.authority_id ? ' · CLI ID ' + d.authority_id : ''), 'small'));
    }
    state.selection = edge.source; renderTimeline(filteredItems()); highlight();
  }
  function toggleCompare(id) {
    state.comparisonEdge = null;
    if (state.comparison.includes(id)) state.comparison = state.comparison.filter(x => x !== id);
    else state.comparison = [...state.comparison.slice(-1), id];
    renderComparison();
    document.querySelectorAll('[data-compare]').forEach(b => {b.textContent = state.comparison.includes(b.dataset.compare) ? '✓ В сравнении' : 'Добавить к сравнению';});
  }
  function renderComparison() {
    $('comparison').replaceChildren();
    const pair = state.comparison;
    const selected = state.comparisonEdge;
    const relation = selected?.mode === state.mode && data.edges.find(e => e.id === selected.id && e.source === pair[0] && e.target === pair[1]);
    if (relation) {
      const shared = el('section', null, 'shared-context');
      shared.append(el('p', `${relation.label} · ${relation.review_status} · ${relation.actor} · ${relation.source} → ${relation.target}`, 'chip'));
      const table = el('table', null, 'context-table');
      for (const [k,label] of Object.entries(contextFields)) {
        const row = el('tr'); row.append(el('th',label),el('td',relation.context?.[k] || 'Не указано')); table.append(row);
      }
      shared.append(table,el('p','Общий контекст выбранного решения для этой пары цитат; не отдельные условия каждого утверждения.','small quiet'));
      $('comparison').append(shared);
    }
    for (let i = 0; i < 2; i++) {
      if (!pair[i]) { $('comparison').append(el('div', `Выберите ${i ? 'второе' : 'первое'} утверждение в панели оснований`, 'compare-card empty')); continue; }
      const c = claims.get(pair[i]), item = items.get(c.item), card = el('article', null, 'compare-card');
      card.append(el('p', `${item.source} · ${date(item)}`, 'eyebrow'), el('h3', c.statement));
      if (!relation) card.append(el('p', 'Нейтральное сравнение. Сопоставимость не установлена; выберите конкретную связь.', 'small quiet'));
      c.anchors.forEach(a => card.append(quote(a))); $('comparison').append(card);
    }
  }
  function clearDetail() {
    state.detailItems = []; state.detailEdge = null;
    $('detail').replaceChildren(el('p', 'Выберите материал или связь в текущем срезе.', 'quiet'));
  }
  function refresh() {
    state.edgePage = 0; state.nodePage = 0;
    renderTopics(); const visible = filteredItems(), allowed = new Set(visible.map(i => i.id));
    const selectedItem = claims.get(state.selection)?.item || state.selection;
    if ((selectedItem && !allowed.has(selectedItem) && !resources.has(state.selection)) || state.detailItems.some(id => !allowed.has(id))) {
      state.selection = null; state.ego = false; clearDetail();
    }
    if (resources.has(state.selection)) {state.selection = null; clearDetail();}
    state.comparison = state.comparison.filter(id => allowed.has(claims.get(id)?.item));
    const eligibleEdges = new Set(graphSlice(visible).allEdges.map(e => e.id));
    if (state.detailEdge && !eligibleEdges.has(state.detailEdge)) clearDetail();
    if (state.comparisonEdge && (state.comparisonEdge.mode !== state.mode || !eligibleEdges.has(state.comparisonEdge.id))) state.comparisonEdge = null;
    const topic = data.topics.find(t => t.id === state.topic)?.title || (state.topic === 'unassigned' ? 'Без темы' : 'Все темы');
    const story = data.stories.find(s => s.id === state.story)?.title || (state.story === 'unassigned' ? 'Без истории' : 'Все истории');
    const scope = `${topic} / ${story} · ${visible.length} материалов · quality: ${$('quality-filter').value || 'все'} · роль: ${$('role-filter').value || 'все'} · с даты: ${$('date-filter').value || 'любая'}`;
    $('reading-scope').textContent = scope; $('compare-scope').textContent = 'Тот же срез: ' + scope;
    $('review-filter').disabled = state.mode !== 'claims';
    renderTimeline(visible); renderGraph(visible); renderComparison();
  }
  cy.on('tap', 'node', e => selectNode(e.target.id()));
  cy.on('tap', 'edge', e => selectEdge(e.target.data()));
  $('story-filter').addEventListener('change', e => {state.story = e.target.value; state.ego = false; refresh();});
  ['quality-filter', 'role-filter', 'date-filter', 'historical', 'review-filter'].forEach(id => $(id).addEventListener('change', refresh));
  $('search').addEventListener('input', refresh);
  for (const mode of ['documents', 'claims']) $('mode-' + mode).addEventListener('click', () => {
    state.mode = mode; state.ego = false; state.selection = null; clearDetail();
    for (const other of ['documents', 'claims']) $('mode-' + other).setAttribute('aria-pressed', String(other === mode)); refresh();
  });
  $('zoom-in').addEventListener('click', () => cy.zoom({level: cy.zoom() * 1.25, renderedPosition: {x: cy.width()/2, y: cy.height()/2}}));
  $('zoom-out').addEventListener('click', () => cy.zoom({level: cy.zoom() / 1.25, renderedPosition: {x: cy.width()/2, y: cy.height()/2}}));
  $('fit').addEventListener('click', () => cy.fit(cy.elements(), 34));
  $('ego').addEventListener('click', () => {state.ego = true; renderGraph(filteredItems());});
  $('clear-focus').addEventListener('click', () => {state.ego = false; renderGraph(filteredItems());});
  $('clear-compare').addEventListener('click', () => {state.comparison = []; state.comparisonEdge = null; renderComparison();});
  $('reset').addEventListener('click', () => {
    state.topic = ''; state.story = ''; state.ego = false; state.selection = null;
    clearDetail();
    for (const id of ['story-filter', 'quality-filter', 'role-filter', 'date-filter', 'search', 'review-filter']) $(id).value = '';
    $('historical').checked = false; refresh();
  });
  refresh(); renderComparison();
  window.newsmap = {cy, state, data, refresh, selectItem, selectNode, selectEdge, filteredItems};
})();

/* Refresher Atlas. Ported from Victor's News Atlas viewer (feat/newsmap-sidecar-v06): coordinated topics, timeline,
   map, evidence and comparison. Imported values only enter textContent and canvas labels, never HTML.
   The page is this file and the ones beside it, loaded in order by index.html (graphview.ATLAS_SCRIPTS): this one
   reads the data and the layout and holds the shared helpers and state; main.js, last, wires the controls and
   publishes window.atlas. Each adds its functions to window.RefresherAtlas. */
'use strict';
(() => {
  const data = JSON.parse(document.getElementById('map-data').textContent);
  const $ = id => document.getElementById(id);
  const state = {topic: '', story: '', mode: 'documents', selection: null, ego: false, comparison: [], detailItems: [],
    lang: 'original', view: null, peopleSubject: '', inspect: false};
  try { if (localStorage.getItem('atlas-lang') === 'translation') state.lang = 'translation'; } catch (e) { /* no storage: original */ }
  // Embedded in a dossier (/topic/): the map and the evidence only, over the dossier's sources (atlas.scope).
  const params = new URLSearchParams(location.search);
  const back = document.getElementById('return-to-reading');
  if (back && params.get('return')) {
    try { const u = new URL(params.get('return'), location.origin);
      if (u.origin === location.origin && ['/brief/', '/topic/', '/library/', '/timeline/'].includes(u.pathname)) { back.href = u.pathname + u.search; back.hidden = false; }
    } catch { /* invalid return path */ }
  }
  const embedded = params.has('embed');
  if (embedded) document.body.classList.add('embedded');
  // Review (/review/): this page with the proposals in front and the evidence beside them, no map or filters. Served,
  // Explore leaves the proposals to Review; opened from disk (wiki/graph/) there is no Review, so they stay here.
  const reviewing = location.pathname === '/review/' && !embedded;
  if (reviewing) {
    document.body.classList.add('review-layout');
    document.title = 'Refresher Review';
    document.querySelector('.app-brand .page').textContent = 'Review';
    $('graph-heading').textContent = 'Review';
    document.querySelector('.graph-summary .quiet').textContent = 'What the hourly review suggests: relations a later '
      + 'source changed, Ideas that say the same thing, new topics, and people to merge or split. Pick a claim to see '
      + 'where it comes from.';
  } else if (data.served && !embedded && location.hash === '#review') {  // an old link to the proposals on Explore
    location.replace('/review/');
    return;
  } else if (data.served) $('review').hidden = true;
  if (!embedded && data.items.length === 0) {
    $('atlas-content').hidden = true;
    $('atlas-empty').hidden = false;
    document.querySelector('.header-note').hidden = true;
    return;
  }
  // Every file of the Atlas shares this namespace; `later` reaches a function a file further down defines, once
  // every file has loaded. An empty Atlas, or one sent on to Review, never makes it, and the other files stop.
  const B = window.RefresherAtlas = {};
  B.later = new Proxy({}, {get: (_, name) => (...args) => B[name](...args)});
  // The reading layout (/read/ = ?layout=read): the evidence on the left, the text of the source picked in the middle,
  // the map on the right. No timeline, and no source text in the evidence: a source there is its gist, and picking
  // it opens its text in the middle.
  const reading = params.get('layout') === 'read' && !embedded;
  if (reading) document.body.classList.add('read-layout');
  if (['documents', 'claims', 'topics', 'people'].includes(params.get('mode'))) state.mode = params.get('mode');
  state.only = null;  // item ids, or null for all
  const items = new Map(data.items.map(x => [x.id, x]));
  const claims = new Map(data.claims.map(x => [x.id, x]));
  const stories = new Map(data.stories.map(x => [x.id, x]));
  const clusters = new Map((data.clusters || []).map(x => [x.id, x]));
  const links = data.links || [];
  // Profiles: people and organizations, told apart by kind; an organization also has a type.
  const people = new Map([...(data.people || []), ...(data.organizations || [])].map(x => [x.id, x]));
  for (const subject of data.people_subjects || []) {
    const option = el('option', `${subject.title}${subject.kind === 'cluster' ? ' (provisional cluster)' : ''}`);
    option.value = subject.id; $('people-subject').append(option);
  }
  const scoreSubject = () => state.peopleSubject || (state.topic !== 'unassigned' ? state.topic : '') || '';
  function rankedPeople(visible) {
    const allowed = new Set(visible.map(i => i.id));
    return (data.people_scores?.[scoreSubject()] || []).filter(r => {
      const person = people.get(r.person_id);
      const stated = (person?.statements || []).some(id => {
        const claim = claims.get(id);
        return allowed.has(claim?.item) && (!scoreSubject() || r.claim_ids.includes(id));
      });
      const about = (person?.facts || []).some(id => {
        const claim = claims.get(id), item = items.get(claim?.item);
        return allowed.has(claim?.item) && (!scoreSubject() || item?.topics.includes(scoreSubject()) ||
          item?.stories.some(id => stories.get(id)?.cluster === scoreSubject()));
      });
      return stated || about;
    });
  }
  function peopleSummary(person) {
    const stated = person?.statements?.length || 0, facts = person?.facts?.length || 0;
    return [stated ? `${stated} attributed statement${stated === 1 ? '' : 's'}` : '',
      facts ? `${facts} sourced fact${facts === 1 ? '' : 's'}` : ''].filter(Boolean).join(' · ');
  }
  const scoreText = score => score?.importance == null ? (people.get(score?.person_id)?.facts?.length ? 'Facts about them' : 'Unscored') : `Rank #${score.rank}`;
  const kinds = {merge_ideas: 'Merge two Ideas', topic: 'New topic', merge_people: 'Same person',
    split_person: 'Several people', relation: 'Confirm a relation'};
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
    try { if (!['http:', 'https:'].includes(new URL(url).protocol)) return el('span', 'Source URL unavailable'); } catch { return el('span', 'Source URL unavailable'); }
    const a = el('a', 'Open the source ↗ (external site)');
    a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; a.referrerPolicy = 'no-referrer'; return a;
  }
  function quote(anchor) {  // the passage in the language picked above the evidence, never both at once
    if (reading) return document.createDocumentFragment();  // the text is in the middle column, not the evidence
    const box = el('blockquote', null, 'quote');
    box.title = `Passage ${anchor.span} · characters [${anchor.start_char}, ${anchor.end_char}) of the original`;
    if (state.lang === 'translation' && anchor.translation) {
      box.append(el('mark', anchor.translation), el('div', `Machine translation into ${data.language}, for reading only`, 'locator'));
      return box;
    }
    if (anchor.prefix) box.append(el('span', anchor.prefix, 'context'));
    box.append(el('mark', anchor.quote));
    if (anchor.suffix) box.append(el('span', anchor.suffix, 'context'));
    return box;
  }
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  function band(claim) { return el('span', `${claim.band} ${claim.freshness}`, 'band ' + claim.band); }

  $('snapshot-date').textContent = new Date(data.snapshot_at).toLocaleString('en');
  $('scope-badge').textContent = data.served ? 'Served locally' : 'Read-only file';
  for (const [key, title] of [['items', 'items'], ['ideas', 'Ideas'], ['clusters', 'clusters'], ['topics', 'topics'], ['waiting', 'waiting for you']]) {
    const stat = el('div', null, 'stat'); stat.append(el('strong', data.counts[key]), el('span', title)); $('stats').append(stat);
  }
  for (const story of data.stories.filter(s => s.items.length > 1)) {
    const opt = el('option', story.title.slice(0, 70) + ` (${story.items.length})`); opt.value = story.id; $('story-filter').append(opt);
  }

  Object.assign(B, {$, band, basisWords, button, claims, clusters, contextFields, data, date, el, embedded, external,
    items, kinds, links, params, people, peopleSummary, plural, quote, rankedPeople, reading,
    reviewing, scoreSubject, scoreText, state, stories});
})();

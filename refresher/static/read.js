/* Read (/read/): the Atlas without its timeline. Left, every source with its gist (GET /api/sources), filtered by a
   topic and a search; middle, the text of the one you pick with its claims marked (GET /api/document); right, the
   Atlas embedded (?embed: its map and the evidence for what you pick), scoped to the sources listed and linked both
   ways through its window.atlas API; a claim clicked in the text lights up the sources that hold its Idea. The topic and the source live in the URL (?topic=&read=). Values from the vault
   only enter textContent. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = String(text); if (cls) n.className = cls; return n; };
  const button = (text, action, cls) => { const b = el('button', text, cls); b.type = 'button'; b.addEventListener('click', action); return b; };
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const day = iso => new Date(iso).toLocaleDateString('en', {day: 'numeric', month: 'short', year: 'numeric'});

  let data = null, atlas = null, steering = false, current = null, translated = true;
  const docs = new Map();
  const params = () => new URLSearchParams(location.search);
  const topic = () => data.topics.some(t => t.id === params().get('topic')) ? params().get('topic') : '';
  const setUrl = change => {
    const url = new URL(location.href);
    for (const [k, v] of Object.entries(change)) { if (v) url.searchParams.set(k, v); else url.searchParams.delete(k); }
    history.replaceState(null, '', url);
  };
  const stateOf = id => data.marks[id] || 'unread';
  function shown() {  // the sources the topic and the search leave, newest first
    const t = topic(), q = $('find').value.trim().toLowerCase();
    return data.sources.filter(x => (!t || x.topics.includes(t)) &&
      (!q || [x.title, x.source, x.author, x.gist, x.hook].some(v => v && v.toLowerCase().includes(q))));
  }

  // --- topics and the list of sources with their gists ---
  function renderTopics() {
    const t = topic(), chip = (id, title, count) => {
      const b = button('', () => { setUrl({topic: id}); renderTopics(); renderList(); scopeMap(); }, 'chip' + (t === id ? ' on' : ''));
      b.setAttribute('aria-pressed', String(t === id)); b.append(el('span', title, 'c-title'), el('span', count, 'c-count')); return b;
    };
    $('topics').replaceChildren(chip('', 'All sources', data.sources.length), ...data.topics.map(x => chip(x.id, x.title, x.count)));
    $('topics').hidden = false;
  }
  function renderList() {
    const list = shown();
    $('count').textContent = plural(list.length, 'source');
    $('sources').replaceChildren(...list.map(x => {
      const li = el('li'), b = button('', () => open(x.id), 'src' + (x.id === current ? ' current' : ''));
      b.dataset.source = x.id;
      const head = el('span', null, 'src-head');
      head.append(el('span', null, `dot s-${stateOf(x.id)}`), el('span', x.title, 'src-title'));
      b.append(head, el('span', [x.source, x.author, day(x.time), `${x.minutes} min`].filter(Boolean).join(' · '), 'src-meta'),
        el('span', x.gist || x.hook || 'No text to summarise.', 'src-gist'));
      if (x.summary === 'lead') b.append(el('span', 'its opening lines: no summary yet', 'src-note'));
      if (x.repost_of) b.append(el('span', 'a repost', 'src-note'));
      li.append(b); return li;
    }));
    if (!list.length) $('sources').append(el('li', 'No source matches.', 'quiet'));
  }

  function keepInView(row) {  // scroll the list column only, never the page
    const col = row.closest('.col'), r = row.getBoundingClientRect(), c = col.getBoundingClientRect();
    if (r.top < c.top) col.scrollTop -= c.top - r.top + 8; else if (r.bottom > c.bottom) col.scrollTop += r.bottom - c.bottom + 8;
  }

  // --- the text of the picked source, its claims marked; a mark shows that claim's evidence on the map ---
  async function open(id, {fromMap = false} = {}) {
    const x = data.sources.find(s => s.id === id); if (!x) return;
    current = id; setUrl({read: id});
    document.querySelectorAll('.src.current').forEach(n => n.classList.remove('current'));
    const row = document.querySelector(`.src[data-source="${CSS.escape(id)}"]`);
    if (row) { row.classList.add('current'); keepInView(row); }
    $('doc-empty').hidden = true; $('doc').hidden = false;
    $('d-eyebrow').textContent = [x.source, x.ideas ? plural(x.ideas, 'Idea') : null].filter(Boolean).join(' · ');
    $('d-title').textContent = x.title;
    $('d-meta').textContent = [x.author, day(x.time), `${x.minutes} min`].filter(Boolean).join(' · ');
    $('d-text').replaceChildren(el('p', 'Opening the text…', 'quiet')); $('d-tools').replaceChildren();
    document.querySelector('.text-col').scrollTop = 0;
    if (!fromMap && atlas) { steering = true; try { atlas.selectItem(id); } finally { steering = false; } }
    try {
      if (!docs.has(id)) {
        const r = await fetch('/api/document?id=' + encodeURIComponent(id)), body = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
        docs.set(id, body);
      }
      if (current === id) renderDoc(docs.get(id), x);
    } catch (error) { $('d-text').replaceChildren(el('p', error.message, 'quiet')); }
  }
  function renderDoc(doc, x) {
    const tools = $('d-tools'); tools.replaceChildren();
    const st = stateOf(x.id);
    tools.append(button(st === 'read' ? '✓ Read · unmark' : '✓ Mark read', () => mark(x.id, st === 'read' ? 'unread' : 'read'), st === 'read' ? 'on' : ''));
    if (doc.translation.length) {
      const t = button(translated ? 'Translation · show the original' : 'Original · show the translation', () => { translated = !translated; renderDoc(doc, x); });
      t.setAttribute('aria-pressed', String(translated)); tools.append(t);
    }
    if (doc.url && /^https?:/.test(doc.url)) { const a = el('a', 'Open the original ↗'); a.href = doc.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; tools.append(a); }
    $('d-text').replaceWith(textBox(doc));
  }
  function textBox(doc) {  // the whole source; a passage behind a claim is underlined and shows its evidence on the map
    const box = el('div', null, 'd-text'); box.id = 'd-text';
    if (!doc.text) { box.append(el('p', 'The text of this source is not in the vault.', 'quiet')); return box; }
    const spans = doc.spans;
    const piece = (str, a, b, cls) => {
      const hits = spans.filter(s => s.start < b && s.end > a);
      if (!hits.length) { box.append(cls ? el('span', str, cls) : document.createTextNode(str)); return; }
      const m = el('mark', str, 'claim-mark'); m.tabIndex = 0; m.setAttribute('role', 'button');
      m.title = hits.map(h => h.statement).join('\n') + '\nClick: light up the other sources that say it, on the map.';
      // The map stays on the sources: what a claim adds there is which other sources hold its Idea.
      const go = () => { if (atlas) atlas.spotlight(hits.map(h => h.idea).filter(Boolean)); };
      m.addEventListener('click', go); m.addEventListener('keydown', e => e.key === 'Enter' && go());
      box.append(m);
    };
    if (translated && doc.translation.length) {
      let at = 0;
      for (const line of doc.translation) {
        const gap = doc.text.slice(at, line.start);
        if (gap.trim()) piece(gap, at, line.start, 'untranslated'); else if (at) box.append('\n'.repeat(Math.max(1, (gap.match(/\n/g) || []).length)));
        piece(line.text, line.start, line.end); at = line.end;
      }
      if (doc.text.slice(at).trim()) piece(doc.text.slice(at), at, doc.text.length, 'untranslated');
    } else {
      const cuts = [...new Set([0, doc.text.length, ...spans.flatMap(s => [s.start, s.end])])].filter(c => c >= 0 && c <= doc.text.length).sort((a, b) => a - b);
      for (let i = 0; i + 1 < cuts.length; i++) piece(doc.text.slice(cuts[i], cuts[i + 1]), cuts[i], cuts[i + 1]);
    }
    return box;
  }
  async function mark(id, state) {
    const r = await fetch('/api/reading', {method: 'POST', headers: {'Content-Type': 'application/json', 'X-Refresher': '1'},
      body: JSON.stringify({item: id, state})});
    if (!r.ok) return;
    if (state === 'unread') delete data.marks[id]; else data.marks[id] = state;
    renderList(); if (current === id) renderDoc(docs.get(id), data.sources.find(s => s.id === id));
  }

  // --- the map: the embedded Atlas, scoped to the sources listed; picking a source there opens its text here ---
  function scopeMap() { if (atlas) atlas.scope(shown().map(x => x.id)); }
  function connect() {
    try { atlas = $('atlas').contentWindow.atlas || null; } catch (e) { atlas = null; }
    if (!atlas || !data) return;
    scopeMap();
    if (current) { steering = true; try { atlas.selectItem(current); } finally { steering = false; } }
    atlas.onselect = (kind, id) => {
      if (steering) return;
      const item = kind === 'item' ? id : kind === 'claim' ? atlas.data.claims.find(c => c.id === id)?.item : null;
      if (item && item !== current && data.sources.some(s => s.id === item)) open(item, {fromMap: true});
    };
  }
  window.addEventListener('message', e => { if (e.origin === location.origin && e.data?.type === 'atlas-ready') connect(); });
  $('atlas').addEventListener('load', connect);
  $('find').addEventListener('input', () => { renderList(); scopeMap(); });

  fetch('/api/sources').then(async r => {
    const body = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
    data = body;
    $('notice').hidden = true; $('desk').hidden = false;
    renderTopics(); renderList(); connect();
    const read = params().get('read');
    if (read && data.sources.some(s => s.id === read)) open(read);
  }).catch(error => { $('notice').textContent = error.message; });
})();

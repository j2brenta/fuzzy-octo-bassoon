/* The brief: the source you read, its text with what the brief takes tinted by idea. */
'use strict';
(() => {
  const B = window.RefresherBrief;
  const {$, c, day, el, ideaVar, plural, post, query, readHref, sid, source, st, subject} = B;
  const {furtherDialog} = B.later;
  const docs = {};  // item -> /api/brief/source/<id>, once per page
  function loadDoc(item) {
    if (!docs[item]) docs[item] = fetch(`/api/brief/source/${encodeURIComponent(item)}?${query}`).then(async r => {
      const b = await r.json().catch(() => ({})); if (!r.ok) throw new Error(b.error || `HTTP ${r.status}`); return b; });
    return docs[item];
  }
  function narrow(text, start, end, statement) {
    const words = t => new Set((t.toLowerCase().match(/[\p{L}\p{N}]{4,}/gu) || []));
    const want = words(statement);
    let best = [start, end], score = 0;
    const re = /[^.!?…\n]+[.!?…]*/g, part = text.slice(start, end);
    for (let m; (m = re.exec(part));) {
      const got = words(m[0]); let n = 0; for (const w of got) if (want.has(w)) n++;
      const sc = n / Math.max(4, want.size);
      if (sc > score) { score = sc; best = [start + m.index, start + m.index + m[0].length]; }
    }
    return score >= 0.25 ? best : [start, end];
  }
  function setState(id, value) { return post(`/api/source/${encodeURIComponent(id)}/state`, {state: value, ...subject}); }
  // The claims of a source, each at its best-matching sentence (the recorded passage is often a whole paragraph).
  function claimsOf(doc) {
    const by = {};
    for (const s of doc.spans) {
      if (by[s.claim]) continue;
      const [a, b] = narrow(doc.text, s.start, s.end, s.statement);
      by[s.claim] = {id: s.claim, start: a, end: b, statement: s.statement, kind: doc.kinds?.[s.claim] || 'fact', mark: doc.marked[s.claim] || null};
    }
    return Object.values(by).sort((x, y) => x.start - y.start || x.end - y.end);
  }
  // Plain text with claim sentences wrapped; what the brief takes is tinted by its idea (lent facts underlined) unless hidden.
  function sourceText(doc, claims) {
    const box = el('div', null, 'ce-dense');
    const text = doc.text || '';
    if (!text) { box.append(el('p', 'The text of this source is not in the vault.', 'quiet')); return box; }
    const cuts = [...new Set([0, text.length, ...claims.flatMap(x => [x.start, x.end])])].sort((a, b) => a - b);
    const squeeze = t => t.replace(/[ \t]*\n[ \t]*(\n[ \t]*)+/g, '\n');
    for (let k = 0; k + 1 < cuts.length; k++) {
      const [a, b] = [cuts[k], cuts[k + 1]], piece = squeeze(text.slice(a, b));
      const over = claims.filter(x => x.start < b && x.end > a);
      if (!over.length) { box.append(document.createTextNode(piece)); continue; }
      const top = over.find(x => x.mark && x.mark.kind === 'thesis') || over.find(x => x.mark) || over[0];
      const span = el('span', piece, 'pass' + (top.mark ? ' takes ' + top.mark.kind : ''));
      span.dataset.claims = over.map(x => x.id).join(' ');
      if (top.mark) span.style.setProperty('--idea', ideaVar(top.mark.idea));
      box.append(span);
    }
    return box;
  }
  const passesOf = id => [...document.querySelectorAll('#source .pass')].filter(p => p.dataset.claims.split(' ').includes(id));
  function hover(id, on) { passesOf(id).forEach(p => p.classList.toggle('hover', on)); }
  function litClaim(id, scroll) {
    st.lit = id;
    document.querySelectorAll('#source .pass.lit').forEach(p => p.classList.remove('lit'));
    document.querySelectorAll('#side .ce-claim.lit').forEach(b => b.classList.remove('lit'));
    const passes = passesOf(id);
    passes.forEach(p => p.classList.add('lit'));
    const b = document.querySelector(`#side .ce-claim[data-claim="${CSS.escape(id)}"]`);
    if (b) { b.classList.add('lit'); const d = b.closest('details'); if (d) d.open = true; }
    if (scroll && passes[0]) passes[0].scrollIntoView({behavior: 'smooth', block: 'center'});
  }
  // As the text scrolls, the narrow panel marks the claims whose sentences are on screen.
  let watcher = null;
  function follow(textBox) {
    if (watcher) watcher.disconnect();
    const seen = new Map();
    watcher = new IntersectionObserver(entries => {
      for (const e of entries) for (const id of e.target.dataset.claims.split(' ')) {
        const n = (seen.get(id) || 0) + (e.isIntersecting ? 1 : -1); seen.set(id, Math.max(0, n));
      }
      document.querySelectorAll('#side .ce-claim').forEach(b => b.classList.toggle('in-view', (seen.get(b.dataset.claim) || 0) > 0));
    }, {rootMargin: '-90px 0px -15% 0px'});
    textBox.querySelectorAll('.pass').forEach(p => watcher.observe(p));
  }
  let showTakes = true;  // what the brief takes is tinted by its idea unless you hide it
  function sourcePanel(doc, claims) {
    const box = $('source');
    const meta = [doc.source, doc.author && doc.author !== doc.source ? doc.author : null, day(doc.time)].filter(Boolean).join(' · ');
    const head = el('header', null, 'ce-source-head');
    head.append(el('p', meta, 'small quiet'), el('h1', doc.title, 'serif'));
    const tools = el('div', null, 'ce-chips');
    for (const r of doc.roles) { const ch = el('span', `${r.role} of idea ${r.idea + 1}`, 'chip role'); ch.style.setProperty('--idea', ideaVar(r.idea)); tools.append(ch); }
    const takes = el('button', showTakes ? 'Hide what the brief takes' : 'Show what the brief takes', 'ce-link-button'); takes.type = 'button';
    takes.addEventListener('click', () => { showTakes = !showTakes; text.classList.toggle('show-takes', showTakes); takes.textContent = showTakes ? 'Hide what the brief takes' : 'Show what the brief takes'; });
    const done = el('button', doc.read === 'read' ? '✓ Read' : 'Mark read', 'ce-link-button'); done.type = 'button';
    done.addEventListener('click', async () => {
      const value = doc.read === 'read' ? 'unread' : 'read'; done.disabled = true;
      try { await setState(doc.id, value); doc.read = value; done.textContent = value === 'read' ? '✓ Read · undo' : 'Mark read'; }
      catch (e) { $('notice').textContent = 'Reading status was not saved. ' + e.message; }
      finally { done.disabled = false; }
    });
    tools.append(takes, done);
    const explore = el('a', 'Explore connections →', 'ce-link');
    explore.href = '/atlas/?item=' + encodeURIComponent(doc.id) + '&return=' + encodeURIComponent(location.pathname + location.search);
    tools.append(explore);
    if (doc.url) { const a = el('a', 'Original ↗', 'ce-link'); a.href = doc.url; a.target = '_blank'; a.rel = 'noopener noreferrer'; tools.append(a); }
    head.append(tools);
    const text = sourceText(doc, claims);
    text.classList.toggle('show-takes', showTakes);
    const count = doc.next.reduce((n, g) => n + g.rows.length, 0);
    const further = el('button', `↘ Go further: ${plural(count, 'related source')}`, 'ce-link-button ce-further'); further.type = 'button';
    further.addEventListener('click', furtherDialog);
    const ids = Object.keys(c.sources).sort((a,b) => Number((sid(a)||'S999').slice(1))-Number((sid(b)||'S999').slice(1))), index = ids.indexOf(doc.id), next = ids[index + 1];
    const actions = el('div', null, 'journey-actions');
    if (next) { const a = el('a', 'Next: ' + (source(next).title || 'source'), 'journey-primary'); a.href = readHref(next); actions.append(a); }
    box.replaceChildren(head, text, actions, further);
    follow(text);
  }
  Object.assign(B, {claimsOf, docs, hover, litClaim, loadDoc, passesOf, sourcePanel});
})();

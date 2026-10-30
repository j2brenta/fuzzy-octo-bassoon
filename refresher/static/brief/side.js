/* The brief: the narrow panel, about the brief (people, organizations) or about the source you read. */
'use strict';
(() => {
  const B = window.RefresherBrief;
  const {$, c, docs, el, hover, ideaVar, litClaim, passesOf, path, readHref, sid, source} = B;
  const {whoOpen} = B.later;
  // --- the narrow panel: about the brief (map, people, organizations), or about the source you read ---
  const RELATION = {wrote: 'wrote it', quoted: 'quoted', about: 'facts about them', mentioned: 'named'};
  // Each badge opens the person or organization (whoOpen); one the brief ranks carries its place.
  const rankOf = id => { const n = (c.people || []).filter(r => r.raw > 0).findIndex(r => r.id === id); return n < 0 ? null : n + 1; };
  function badges(box, rows, item) {
    box.replaceChildren(...rows.slice(0, 30).map(r => {
      const rel = item ? r.relation : Object.values(r.items || {})[0], n = rankOf(r.id);
      const b = el('button', (n ? `#${n} ` : '') + r.name, 'badge ' + (rel || '')); b.type = 'button';
      b.title = item ? (RELATION[rel] || '') : Object.entries(r.items || {}).map(([i, x]) => `${RELATION[x] || x} in ${sid(i) || source(i).source || ''}`).join('; ');
      b.addEventListener('click', () => whoOpen(r.id, true));
      return b;
    }));
    if (!rows.length) box.append(el('span', 'none named', 'small quiet'));
  }
  // The brief's ranking (refresher/authority.py): writers and those they credit, first in the panel; a channel
  // stands for a writer the source does not name, so it has no profile to open.
  function ranked() {
    const rows = (c.people || []).filter(r => r.raw > 0).slice(0, 6);
    $('ranked-box').hidden = !rows.length;
    $('ranked').replaceChildren(...rows.map(r => {
      const li = el('li'), name = el(r.kind === 'channel' ? 'span' : 'button', r.name, 'ce-ranked-name');
      if (r.kind !== 'channel') { name.type = 'button'; name.addEventListener('click', () => whoOpen(r.id, true)); }
      const bar = el('span', null, 'ce-bar'); bar.style.setProperty('--w', `${Math.round(r.authority * 100)}%`);
      li.append(name, bar, el('span', r.kind === 'channel' ? `${r.why} · channel` : r.why, 'small quiet'));
      return li;
    }));
    const shown = new Set(rows.map(r => r.id));
    return c.details.people.filter(r => !shown.has(r.id));
  }
  // A claim: its kind as a small label (ideas are the only grouping), coloured by the idea it feeds.
  function claimButton(cl, onClick) {
    const li = el('li', null, cl.superseded ? 'superseded' : '');
    const b = el('button', null, 'ce-claim'); b.type = 'button'; b.dataset.claim = cl.id;
    b.append(el('span', cl.mark && cl.mark.kind === 'lent' ? `lent · ${cl.kind || 'fact'}` : (cl.kind || 'fact'), 'ce-kind-label'), document.createTextNode(cl.statement));
    if (cl.idea != null) { b.style.setProperty('--idea', ideaVar(cl.idea)); b.classList.add('in-brief'); }
    b.addEventListener('click', onClick);
    li.append(b);
    return li;
  }
  function group(title, rows, open, colour) {
    const det = el('details', null, 'ce-group'); det.open = open;
    if (colour != null) det.style.setProperty('--idea', ideaVar(colour));
    det.append(el('summary', `${title} · ${rows.length}`));
    const ul = el('ul', null, 'ce-claims'); rows.forEach(r => ul.append(r)); det.append(ul);
    return det;
  }
  function briefSide() {
    const rest = ranked();
    $('people-label').textContent = $('ranked-box').hidden ? 'People' : 'Also named';
    badges($('people'), rest); badges($('orgs'), c.details.organizations);
    $('claims').replaceChildren();
  }
  function sourceSide(doc, claims) {
    $('ranked-box').hidden = true; $('people-label').textContent = 'People';
    badges($('people'), doc.people || [], doc.id); badges($('orgs'), doc.organizations || [], doc.id);
    // Grouped by the idea each claim feeds or lends a fact to; the rest, folded, as not in the brief.
    const groups = [];
    c.major.forEach((x, n) => {
      const mine = claims.filter(cl => cl.mark && cl.mark.idea === n);
      if (mine.length) groups.push(group(`Idea ${n + 1} · ${x.sources[doc.id] || 'lends facts'}`, mine.map(row), true, n));
    });
    const rest = claims.filter(cl => !cl.mark);
    if (rest.length) groups.push(group('Not in the brief', rest.map(row), !groups.length && rest.length <= 8));
    $('claims').replaceChildren(...groups);
    if (!groups.length) $('claims').append(el('p', 'No claims were extracted from this source.', 'small quiet'));
    function row(cl) {
      const li = claimButton({...cl, idea: cl.mark ? cl.mark.idea : null}, () => litClaim(cl.id, true));
      const b = li.querySelector('button');
      const on = () => { hover(cl.id, true); reveal(cl.id); }, off = () => { hover(cl.id, false); clearTimeout(revealing); };
      b.addEventListener('mouseenter', on); b.addEventListener('mouseleave', off);
      b.addEventListener('focus', on); b.addEventListener('blur', off);
      return li;
    }
  }
  // Hovering a claim brings its passage into view, after a pause, so passing over the list does not scroll the text.
  let revealing = null;
  function reveal(id) {
    clearTimeout(revealing);
    revealing = setTimeout(() => {
      const p = passesOf(id)[0]; if (!p) return;
      const r = p.getBoundingClientRect();
      if (r.top < 130 || r.bottom > innerHeight - 20) p.scrollIntoView({behavior: 'smooth', block: 'center'});
    }, 250);
  }
  function sourcesList() {
    const ids = c.written.ids, current = path[path.length - 1];
    const order = Object.keys(c.sources).sort((a, b) => (ids[a] ? +ids[a].slice(1) : 999) - (ids[b] ? +ids[b].slice(1) : 999) || a.localeCompare(b));
    for (const i of path) if (!order.includes(i)) order.push(i);  // sources outside the brief you opened
    $('sources').replaceChildren(...order.map(i => {
      const li = el('li', null, i === current ? 'current' : path.includes(i) ? 'on-path' : '');
      const a = el('a', source(i).title || (docs[i] ? '…' : i)); a.href = readHref(i);
      if (!source(i).title && docs[i]) docs[i].then(d => { a.textContent = d.title; }).catch(() => {});
      li.append(el('span', sid(i) || '·', 'sid'), a);
      const roles = el('span', null, 'roles');
      c.major.forEach((x, n) => { if (x.sources[i]) { const ch = el('span', `${x.sources[i]} · ${n + 1}`, 'chip role'); ch.style.setProperty('--idea', ideaVar(n)); roles.append(ch); } });
      li.append(roles); return li;
    }));
  }

  Object.assign(B, {briefSide, sourceSide, sourcesList});
})();

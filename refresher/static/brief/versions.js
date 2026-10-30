/* The brief: what arrived since the last checkpoint, and the saved versions. */
'use strict';
(() => {
  const B = window.RefresherBrief;
  const {$, day, el, plural, readHref} = B;
  function arrival(r, cls) {
    const box = el('div', null, 'ce-arrival ' + cls);
    const words = {'updates': 'Updates what you knew', 'contradicts': 'Contradicts what you knew', 'new idea': 'A new major idea',
      'new base': 'A new source to read', 'new authority': 'A new top voice', 'builds on': 'Builds on an idea you have',
      'lends facts': 'Adds facts', 'minor idea': 'A minor idea'};
    box.append(el('span', words[r.kind] || r.kind, 'what'));
    const h = el('h3'); const a = el('a', r.title); a.href = readHref(r.item); h.append(a); box.append(h);
    box.append(el('p', [r.source, day(r.when), r.idea_title ? `on “${r.idea_title}”` : null].filter(Boolean).join(' · '), 'small quiet'));
    if (r.why) box.append(el('p', r.why, 'small'));
    return box;
  }

  // One line under the top band, never in place of the ideas: what arrived since the last saved version, opened on demand.
  function since(thread) {
    const d = thread && thread.delta, box = $('since');
    if (!d) { box.hidden = true; return; }
    box.hidden = false;
    box.classList.toggle('critical', d.critical.length > 0);
    if (!d.arrived) { box.replaceChildren(el('p', `You're up to date with your saved sources since your checkpoint of ${day(d.since)}.`, 'ce-since-line')); return; }
    const noise = Object.values(d.noise).reduce((n, rows) => n + rows.length, 0);
    const det = el('details');
    det.append(el('summary', [`${plural(d.arrived, 'source')} since your checkpoint of ${day(d.since)}`, d.critical.length && `${d.critical.length} critical`,
      d.notes.length && plural(d.notes.length, 'note'), noise && `${noise} overlapping or less relevant`].filter(Boolean).join(' · ')));
    const reasons = {retells: ['retells what you know', 'retell what you know'],
      outdated: ['is an older view already replaced', 'are older views already replaced'],
      near: ['is only near the subject', 'are only near the subject']};
    const body = el('div', null, 'ce-since-body');
    body.append(...d.critical.map(r => arrival(r, 'critical')), ...d.notes.map(r => arrival(r, 'note')), ...Object.entries(d.noise).map(([k, rows]) => {
      const n = el('details', null, 'ce-noise');
      n.append(el('summary', `${plural(rows.length, 'source')} ${(reasons[k] || [k, k])[rows.length === 1 ? 0 : 1]}`));
      const ul = el('ul'); rows.forEach(r => { const li = el('li'); const a = el('a', r.title); a.href = readHref(r.item); li.append(a, document.createTextNode(` · ${r.source || ''}`)); ul.append(li); });
      n.append(ul); return n;
    }));
    det.append(body);
    box.replaceChildren(det);
  }

  // Saved versions of this brief, newest first, each opening in a dialog.
  function versions(thread) {
    const btn = $('versions'), list = $('versions-list');
    const n = thread ? thread.stamps.length : 0;
    $('versions-box').hidden = !n; list.hidden = true; btn.setAttribute('aria-expanded', 'false');
    if (!n) return;
    btn.textContent = `Versions (${n}) ▾`;
    fetch(`/api/threads/${encodeURIComponent(thread.thread.slug)}/snapshots`).then(r => r.json()).then(body => {
      const diffs = Object.fromEntries((body.diffs || []).map(d => [d.to, d]));
      list.replaceChildren(...[...body.snapshots].reverse().map(s => {
        const li = el('li');
        const b = el('button', `${day(s.at)} · ${plural(s.major.length, 'idea')} · ${plural(s.counts.sources, 'source')}`);
        b.type = 'button'; b.addEventListener('click', () => { toggleVersions(false); snapshot(s); }); li.append(b);
        const d = diffs[s.stamp];
        if (d) li.append(el('p', [d.added.length && `+${d.added.length} ideas`, d.dropped.length && `−${d.dropped.length}`,
          d.rebased.length && `${d.rebased.length} new base`, d.replaced.length && `${d.replaced.length} replaced`].filter(Boolean).join(' · ') || 'no change in the core', 'diff'));
        return li;
      }));
    }).catch(() => { $('versions-box').hidden = true; });
  }
  function toggleVersions(open) {
    $('versions-list').hidden = !open; $('versions').setAttribute('aria-expanded', String(open));
  }

  function snapshot(s) {
    $('snapshot-title').textContent = `Your version of ${day(s.at)}`;
    const body = $('snapshot-body'); body.replaceChildren();
    for (const x of s.major) {
      body.append(el('h3', x.title, 'serif'));
      body.append(el('p', (x.text && x.text.paragraph || '').replace(/\[\+?S\d+\]/g, '').trim(), 'quiet'));
    }
    $('snapshot').showModal();
  }


  Object.assign(B, {since, toggleVersions, versions});
})();

/* The topics index: every topic and cluster with what its dossier holds. Values only enter textContent. */
'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = String(text); if (cls) n.className = cls; return n; };
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const TIERS = ['core', 'supporting', 'fading', 'replaced', 'noise'];
  let lastVisit = '';
  try { lastVisit = localStorage.getItem('topics-visit') || ''; } catch (e) { /* no storage: nothing is marked new */ }

  function tierBar(counts) {
    const bar = el('div', null, 'tier-bar'); bar.setAttribute('aria-hidden', 'true');
    for (const t of TIERS) if (counts[t]) { const s = el('span', null, 't-' + t); s.style.flex = String(counts[t]); bar.append(s); }
    return bar;
  }
  function card(row) {
    const a = el('a', null, 'card'); a.href = '/topic/?id=' + encodeURIComponent(row.id);
    const fresh = lastVisit && row.latest > lastVisit;
    const head = el('div', null, 'counts');
    if (fresh) head.append(el('span', 'new since your last visit', 'chip new'));
    for (const t of row.in_topics) head.append(el('span', 'in ' + t, 'chip muted'));
    if (head.childNodes.length) a.append(head);
    a.append(el('h3', row.title), el('p', row.top, 'top'), tierBar(row.counts));
    const counts = el('div', null, 'counts');
    counts.append(el('span', `${plural(row.counts.ideas, 'Idea')} · ${plural(row.counts.items, 'source')}`, 'chip muted'),
      el('span', `${row.counts.core} core`, 'chip'));
    if (row.changed) counts.append(el('span', `${row.changed} changed`, 'chip'));
    if (row.contested) counts.append(el('span', `${row.contested} contested`, 'chip warn'));
    counts.append(el('span', 'latest ' + new Date(row.latest).toLocaleDateString('en', {day: 'numeric', month: 'short'}), 'chip muted'));
    a.append(counts);
    return a;
  }
  fetch('/api/topics').then(r => { if (!r.ok) throw new Error(); return r.json(); }).then(rows => {
    const order = (a, b) => Number(b.latest > lastVisit) - Number(a.latest > lastVisit) || b.latest.localeCompare(a.latest);
    const topics = rows.filter(r => r.kind === 'topic').sort(order), clusters = rows.filter(r => r.kind === 'cluster').sort(order);
    $('topics').replaceChildren(...topics.map(card)); $('clusters').replaceChildren(...clusters.map(card));
    $('topics-shelf').hidden = !topics.length; $('clusters-shelf').hidden = !clusters.length; $('empty').hidden = rows.length > 0;
    $('topics-count').textContent = plural(topics.length, 'topic');
    try { localStorage.setItem('topics-visit', new Date().toISOString()); } catch (e) { /* fine without */ }
  }).catch(() => { $('error').textContent = 'Cannot reach Refresher. Is `refresher serve` still running?'; $('error').hidden = false; });
})();

'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  const labels = {queued: 'Saved · queued', processing: 'Saved · analyzing', pending: 'Saved · needs attention', complete: 'Processing finished', saved: 'Saved · not analyzed', analyzed: 'Analyzed', not_selected: 'Saved without extracted ideas'};
  const get = async path => { const r = await fetch(path); const body = await r.json(); if (!r.ok) throw new Error(body.error || 'Could not load your Library.'); return body; };
  const link = (text, href) => { const a = el('a', text); a.href = href; return a; };
  const returnTo = new URLSearchParams(location.search).get('return');
  if (returnTo) {
    try { const u = new URL(returnTo, location.origin);
      if (u.origin === location.origin && u.pathname === '/timeline/') { $('source-back').href = u.pathname + u.search; $('source-back').textContent = '← Back to timeline'; }
    } catch { /* invalid return link: retain Library fallback */ }
  }
  let rows = [];
  function render() {
    const q = $('library-search').value.trim().toLowerCase(), filter = $('library-filter').value;
    const group = r => r.status === 'complete' ? r.claims ? 'analyzed' : 'saved' : ['queued', 'processing'].includes(r.status) ? 'saved' : r.status;
    const found = rows.filter(r => (!filter || group(r) === filter) && `${r.title} ${r.channel} ${r.url || ''}`.toLowerCase().includes(q));
    $('library-count').textContent = `${found.length} of ${rows.length} saved sources`;
    $('library-list').replaceChildren(...found.map(r => {
      const a = link('', r.id ? '/library/?source=' + encodeURIComponent(r.id) : '/capture/?receipt=' + encodeURIComponent(r.receipt)); a.className = 'collection-row';
      const main = el('div'); main.append(el('h2', r.title), el('p', [r.channel === 'app' ? 'Browser' : r.channel, r.created_at ? new Date(r.created_at).toLocaleDateString() : '', `${r.claims} extracted ideas`].filter(Boolean).join(' · ')));
      a.append(main, el('span', labels[r.status] || r.status, 'chip')); return a;
    }));
    $('library-empty').hidden = found.length > 0;
    $('library-empty').querySelector('h2').textContent = rows.length ? 'No matching sources.' : 'No saved sources yet.';
    $('library-empty').querySelector('p').textContent = rows.length ? 'Change your search or choose another status.' : 'Add a source to start your collection.';
  }
  async function source(id) {
    $('collection').hidden = true;
    const r = await get('/api/library/source?id=' + encodeURIComponent(id));
    $('source-reading').hidden = false; document.title = `Refresher — ${r.title}`;
    $('source-title').textContent = r.title;
    $('source-meta').textContent = [r.channel, r.author?.name || 'Author unknown', r.published_at ? new Date(r.published_at).toLocaleDateString() : 'Publication date unknown'].join(' · ');
    $('source-status').textContent = (labels[r.status] || r.status) + (r.reason ? '. ' + r.reason : '') + (r.claims.length ? `. Source review: ${r.quality.replaceAll('_', ' ')}.` : '.');
    $('source-text').textContent = r.text || 'No readable text is available yet. Download the original, retry in the capture channel, or paste its text as a new source.';
    const actions = $('source-actions');
    let read = r.read;
    const mark = el('button', read === 'read' ? '✓ Read · undo' : 'Mark read', 'journey-primary'); mark.type = 'button';
    mark.addEventListener('click', async () => {
      mark.disabled = true; const value = read === 'read' ? 'unread' : 'read';
      try { const result = await fetch('/api/reading', {method:'POST',headers:{'Content-Type':'application/json','X-Refresher':'1'},body:JSON.stringify({item:id,state:value})}); if (!result.ok) throw new Error('Reading status was not saved. Please try again.'); read=value; mark.textContent=read==='read'?'✓ Read · undo':'Mark read'; }
      catch(e) { $('source-status').textContent=e.message; } finally { mark.disabled=false; }
    });
    if (r.text) actions.append(mark);
    if (r.original) { const download = link('Download saved original', '/api/library/original?' + new URLSearchParams(r.original)); download.download=''; actions.append(download); }
    if (r.receipt) actions.append(link('View saved receipt', '/capture/?receipt=' + encodeURIComponent(r.receipt)));
    const explore = link('Explore connections →', '/atlas/?item=' + encodeURIComponent(id) + '&return=' + encodeURIComponent(location.pathname + location.search));
    if (r.claims.length) actions.append(explore);
    try { const u = new URL(r.url); if (['http:', 'https:'].includes(u.protocol)) { const a = link('Open original website ↗', u.href); a.target = '_blank'; a.rel = 'noopener noreferrer'; actions.append(a); } } catch { /* no external source URL */ }
    const chars = Array.from(r.text);
    $('source-claims').replaceChildren(...r.claims.map(c => {
      const box = el('section', null, 'evidence-card'); box.append(el('p', c.statement));
      for (const span of Array.isArray(c.evidence) ? c.evidence : [c.evidence]) if (Number.isInteger(span?.start_char) && Number.isInteger(span?.end_char)) box.append(el('blockquote', chars.slice(span.start_char, span.end_char).join('')));
      return box;
    }));
    if (!r.claims.length) $('source-claims').append(el('p', 'No ideas have been extracted. Your original remains available above.', 'quiet'));
    window.RefresherJourney?.reading(r.title);
  }
  $('library-search').addEventListener('input', render); $('library-filter').addEventListener('change', render);
  const id = new URLSearchParams(location.search).get('source');
  (id ? source(id) : get('/api/library').then(result => { rows = result; render(); })).catch(e => { $('library-error').hidden = false; $('library-error').replaceChildren(el('p', e.message), link('Return to Library', '/library/')); });
})();

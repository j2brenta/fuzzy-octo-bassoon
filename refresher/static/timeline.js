'use strict';

// Same source/claim projection and filter meanings as Explore, without loading a graph canvas.
function timelineSources(data, query) {
  const bands = new Map((data.claims || []).map(c => [c.id, c.band]));
  const cluster = query.get('cluster');
  const members = cluster ? new Set((data.clusters || []).find(c => c.id === cluster)?.items || []) : null;
  const search = (query.get('search') || '').trim().toLocaleLowerCase();
  const topic = query.get('topic'), idea = query.get('idea'), quality = query.get('quality');
  const freshness = query.get('freshness'), from = query.get('from');
  return data.items.filter(item => (!members || members.has(item.id)) &&
    (!topic || (topic === 'unassigned' ? !item.topics.length : item.topics.includes(topic))) &&
    (!idea || item.stories.includes(idea)) && (!quality || item.quality === quality) &&
    (!freshness || item.claims.some(id => bands.get(id) === freshness)) &&
    (!from || (item.time && item.time.slice(0, 10) >= from)) &&
    (query.get('hide_reposts') !== '1' || !item.repost_of) &&
    (!search || `${item.title} ${item.source} ${item.author || ''}`.toLocaleLowerCase().includes(search))
  ).sort((a, b) => (Date.parse(b.time) || 0) - (Date.parse(a.time) || 0) || a.id.localeCompare(b.id));
}

if (typeof module !== 'undefined') module.exports = {timelineSources};
if (typeof document !== 'undefined') (() => {
  const $ = id => document.getElementById(id);
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  const link = (text, href) => { const a = el('a', text); a.href = href; return a; };
  const fields = {search:'timeline-search', topic:'timeline-topic', idea:'timeline-idea', quality:'timeline-quality', freshness:'timeline-freshness', from:'timeline-from'};
  const query = new URLSearchParams(location.search);
  let data = null;
  function render() {
    if (!data) return;
    for (const [key, id] of Object.entries(fields)) { const value = $(id).value; if (value) query.set(key, value); else query.delete(key); }
    if ($('timeline-hide-reposts').checked) query.set('hide_reposts', '1'); else query.delete('hide_reposts');
    const rows = timelineSources(data, query);
    if (!rows.some(r => r.id === query.get('item'))) query.delete('item');
    const href = '/timeline/' + (query.size ? '?' + query : '');
    history.replaceState(null, '', href);
    if (query.get('via') === 'atlas') {  // opened from Explore: back to the same slice there
      const slice = new URLSearchParams(query); slice.delete('via');
      $('timeline-back').href = '/atlas/' + (slice.size ? '?' + slice : '');
      $('timeline-back').textContent = '← Back to Explore';
    }
    $('timeline-count').textContent = `${rows.length} of ${data.items.length} connected sources`;
    const cluster = (data.clusters || []).find(c => c.id === query.get('cluster'));
    $('timeline-scope').textContent = cluster ? `Within ${cluster.title}. Clear filters to see all connected sources.` : '';
    $('timeline-empty').hidden = rows.length > 0;
    const list = $('timeline-list'); list.replaceChildren();
    let month = null, group = null;
    for (const item of rows) {
      const parsed = new Date(item.time), valid = Number.isFinite(parsed.getTime());
      const heading = valid ? parsed.toLocaleDateString(undefined, {year:'numeric',month:'long'}) : 'Date unknown';
      if (heading !== month) { month = heading; const section = el('section', null, 'timeline-month'); section.append(el('h2', heading)); group = el('ol'); section.append(group); list.append(section); }
      const row = el('li', null, 'timeline-entry' + (item.id === query.get('item') ? ' is-selected' : ''));
      row.dataset.item = item.id;
      row.append(el('p', valid ? `${item.time_basis === 'publication' ? 'Published' : 'Captured'} ${parsed.toLocaleDateString(undefined,{day:'numeric',month:'short',year:'numeric'})}` : 'Date unknown', 'quiet small'));
      const selected = new URLSearchParams(query); selected.set('item', item.id);
      const here = '/timeline/?' + selected;
      const remember = event => {
        if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
        query.set('item', item.id); history.replaceState(null, '', here);
        document.querySelectorAll('.timeline-entry').forEach(n => n.classList.toggle('is-selected', n.dataset.item === item.id));
      };
      const title = el('h3'), read = link(item.title, '/library/?source=' + encodeURIComponent(item.id) + '&return=' + encodeURIComponent(here));
      read.addEventListener('click', remember); title.append(read); row.append(title);
      row.append(el('p', [item.source, item.author, item.quality === 'accepted' ? 'Checked against source' : 'Source review: ' + item.quality.replaceAll('_',' '), item.repost_of ? 'Repost' : ''].filter(Boolean).join(' · '), 'quiet'));
      const actions = el('div', null, 'timeline-entry-actions');
      const explore = link('Show in Explore →', '/atlas/?' + selected); explore.addEventListener('click', remember);
      actions.append(explore); row.append(actions); group.append(row);
    }
  }
  async function load() {
    const response = await fetch('/api/data');
    if (!response.ok) throw new Error('Could not load the timeline. Please reload to reconnect.');
    data = await response.json();
    for (const [id, rows] of [['timeline-topic',data.topics], ['timeline-idea',data.stories]]) for (const row of rows) {
      const opt = el('option', row.title); opt.value = row.id; $(id).append(opt);
    }
    for (const [key, id] of Object.entries(fields)) $(id).value = query.get(key) || '';
    $('timeline-hide-reposts').checked = query.get('hide_reposts') === '1';
    render();
    const selected = query.get('item');
    if (selected) requestAnimationFrame(() => [...document.querySelectorAll('.timeline-entry')].find(n => n.dataset.item === selected)?.scrollIntoView({block:'center'}));
  }
  $('timeline-filters').addEventListener('submit', e => { e.preventDefault(); render(); });
  $('timeline-search').addEventListener('input', render);
  $('timeline-filters').addEventListener('change', render);
  $('timeline-filters').addEventListener('reset', e => {
    e.preventDefault(); for (const id of Object.values(fields)) $(id).value = '';
    $('timeline-hide-reposts').checked = false; query.delete('cluster'); query.delete('item'); render();
  });
  load().catch(error => { $('timeline-count').textContent = ''; $('timeline-error').hidden = false; $('timeline-error').textContent = error.message; });
})();

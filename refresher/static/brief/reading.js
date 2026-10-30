/* The brief: reading, a path of sources (the breadcrumb); the brief slides into the rail. */
'use strict';
(() => {
  const B = window.RefresherBrief;
  const {$, briefSide, c, claimsOf, el, litClaim, loadDoc, markHere, path, query, short, sid, sourcePanel,
    sourceSide, sourcesList, st} = B;
  const {contextDrawer} = B.later;
  // --- reading: a path of sources (the breadcrumb); the brief slides into the rail, the source takes the centre ---
  let overviewScroll = 0;
  function go(next, claim = null, push = true) {
    if (push) window.RefresherJourney?.position();
    if (!path.length && next.length) overviewScroll = window.scrollY;
    path.splice(0, path.length, ...next.filter(Boolean)); st.lit = claim;
    $('tab-sources').href = '/topic/?' + query + (path.length ? '&read=' + encodeURIComponent(path[path.length - 1]) : '');
    if (push) {
      const u = new URL(location.href);
      if (path.length) u.searchParams.set('read', path.join(',')); else u.searchParams.delete('read');
      window.history.pushState({read: path.join(',')}, '', u);
    }
    show();
  }
  function open(item, claim = null) {
    if (!path.length) return go([item], claim);
    if (item === path[path.length - 1]) return claim ? litClaim(claim, true) : null;
    const at = path.indexOf(item);
    go(at >= 0 ? path.slice(0, at + 1) : [...path, item], claim);
  }
  function back() { go(path.slice(0, -1)); }
  function layout() {
    // Three tracks, the brief | the source | the narrow panel, in percentages so the change between states animates:
    // the brief narrows into the rail as the source opens beside it; the narrow panel keeps its width (the map stays put).
    const grid = $('grid');
    if (matchMedia('(max-width: 1000px)').matches) { grid.style.gridTemplateColumns = ''; return; }
    const W = grid.clientWidth || 1200, GAP = 14, side = Math.min(360, Math.max(290, 0.24 * W)), rail = 240;
    const pc = px => (100 * px / W).toFixed(2) + '%';
    grid.style.gridTemplateColumns = path.length
      ? `${pc(rail)} ${pc(W - rail - side - 2 * GAP)} ${pc(side)}`
      : `${pc(W - side - 2 * GAP)} 0% ${pc(side)}`;
  }
  async function show() {
    const reading = path.length > 0;
    $('grid').classList.toggle('reading', reading);
    document.body.classList.toggle('ce-reading', reading);
    $('esc-hint').hidden = !reading;
    $('context-toggle').hidden = !reading;
    $('subject-start').hidden = reading || !Object.keys(c.sources).length;
    contextDrawer(false);
    layout();
    crumbs([]);
    sourcesList();
    if (!reading) {
      document.querySelectorAll('.ce-idea').forEach(card => { card.classList.remove('here'); card.querySelector('.ce-here').textContent = ''; });
      $('source').replaceChildren(); st.current = null;
      briefSide(); markHere(null);
      setTimeout(() => window.scrollTo(0, overviewScroll), 380);
      return;
    }
    const opening = path.join(',');
    const opened = await Promise.all(path.map(i => loadDoc(i).catch(e => ({error: e.message, id: i, title: i}))));
    if (opening !== path.join(',')) return;
    const doc = opened[opened.length - 1];
    st.current = doc;
    crumbs(opened);
    sourcesList();
    c.major.forEach((x, n) => {  // the ideas this source serves, marked where they stand
      const card = $('idea-' + (n + 1)); if (!card) return;
      const role = x.sources[doc.id] || ((x.adjacent || []).some(a => a.item === doc.id) ? 'lends facts' : '');
      card.classList.toggle('here', !!role); card.querySelector('.ce-here').textContent = role ? `here · ${role}` : '';
    });
    if (doc.error) { $('source').replaceChildren(el('p', doc.error, 'quiet')); return; }
    const claims = claimsOf(doc);
    sourcePanel(doc, claims);
    sourceSide(doc, claims);
    markHere(doc.id);
    window.scrollTo({top: 0});
    if (st.lit) requestAnimationFrame(() => litClaim(st.lit, true));
    window.RefresherJourney?.reading(doc.title);
  }
  function crumbs(opened) {
    const items = [];
    const root = el('li'); const rootLink = el('a', 'Home'); rootLink.href = '/'; root.append(rootLink); items.push(root);
    const home = el('li');
    if (path.length) { const a = el('a', `← Back to ${short(c.scope.title, 40)}`); a.href = '#'; a.addEventListener('click', e => { e.preventDefault(); go([]); }); home.append(a); }
    else home.append(el('strong', short(c.scope.title, 60)));
    items.push(home);
    opened.forEach((d, k) => {
      const li = el('li'), label = `${sid(path[k]) || ''} ${short(d.title || '', 34)}`.trim();
      if (k === opened.length - 1) li.append(el('strong', label));
      else { const a = el('a', label); a.href = '#'; a.addEventListener('click', e => { e.preventDefault(); go(path.slice(0, k + 1)); }); li.append(a); }
      items.push(li);
    });
    $('crumbs').replaceChildren(...items);
  }
  Object.assign(B, {back, go, layout, open});
})();

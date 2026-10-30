/* Refresher Atlas: the proposals the hourly review makes; decided only through `refresher serve`. */
'use strict';
(() => {
  const B = window.RefresherAtlas;
  if (!B) return;  // an empty Atlas wires nothing
  const {$, button, data, el, kinds, reviewing, state} = B;
  const {renderComparison, selectNode, showDetail} = B.later;
  // --- proposals: what the hourly review suggests; decided only through `refresher serve` ---
  function renderProposals() {
    const all = $('show-decided').checked;
    const shown = data.proposals.filter(p => all || p.status === 'proposed');
    $('proposals').replaceChildren();
    $('proposal-note').textContent = data.served
      ? 'Accept applies the change at once; a decision on changed evidence is refused and you see the current version.'
      : 'This file is read-only. Run `uv run refresher serve` to decide these here.';
    if (!shown.length) $('proposals').append(el('p', all ? 'The review has proposed nothing yet.' : 'Nothing waits for you.', 'empty-proposals'));
    for (const p of shown) $('proposals').append(proposalCard(p));
  }
  function proposalCard(p) {
    const card = el('article', null, 'proposal' + (p.status === 'proposed' ? ' waiting' : ''));
    card.append(el('span', kinds[p.kind] || p.kind, 'kind'));
    const head = p.kind === 'topic' ? p.title : p.kind === 'relation' ? `${p.claims[0]?.statement.slice(0, 60)}… ${p.type.toLowerCase()} an earlier claim`
      : p.kind === 'merge_people' ? p.people.join(' = ') : p.kind === 'split_person' ? `${p.people[0]} → ${p.people.slice(1).join(' + ')}`
      : 'These say the same thing';
    card.append(el('h3', head));
    if (p.claims.length) {
      const list = el('ul');
      for (const c of p.claims) {
        const li = el('li');
        li.append(c.item ? button(c.statement, () => { selectNode(c.id); if (reviewing && matchMedia('(max-width: 1000px)').matches) showDetail(); })
          : el('span', c.statement));
        list.append(li);
      }
      card.append(list);
    }
    card.append(el('p', p.rationale, 'why'));
    const chips = el('div', null, 'chips');
    chips.append(el('span', p.status, 'chip' + (p.status === 'stale' ? ' warn' : '')));
    p.decisions.slice(-1).forEach(d => chips.append(el('span', `${d.action} · ${new Date(d.at).toLocaleString('en')}`, 'chip')));
    card.append(chips);
    if (p.reason) card.append(el('p', p.reason, 'why'));
    if (p.kind === 'merge_ideas' && p.claims.length === 2 && p.claims.every(c => c.item)) {
      card.append(button('Compare side by side ↓', () => {state.comparison = p.claims.map(c => c.id); renderComparison();
        $('comparison').scrollIntoView({behavior: 'smooth', block: 'start'});}));
    }
    if (data.served && ['proposed', 'accepted', 'rejected'].includes(p.status)) {
      const actions = el('div', null, 'actions');
      const why = el('input'); why.placeholder = 'Your reason (optional)'; why.setAttribute('aria-label', 'Your reason');
      why.id = 'why-' + p.id;
      const feedback = el('p', '', 'feedback');
      for (const action of ['accept', 'reject', 'defer']) {
        if (p.status !== 'proposed' && action === 'defer') continue;
        const actionLabel = action === 'reject' ? 'Dismiss' : action === 'accept' && p.kind === 'topic' ? 'Create topic' : action === 'accept' && p.kind === 'merge_ideas' ? 'Merge ideas' : action === 'accept' && p.kind === 'merge_people' ? 'Merge people' : action === 'defer' ? 'Decide later' : 'Accept';
        actions.append(button(actionLabel, () => decide(p, action, why.value, feedback, actions), action));
      }
      actions.prepend(why);
      card.append(actions, feedback);
    }
    return card;
  }
  async function decide(p, action, rationale, feedback, actions) {
    actions.querySelectorAll('button').forEach(b => {b.disabled = true;});
    feedback.className = 'feedback'; feedback.textContent = 'Saving…';
    try {
      const response = await fetch('/api/proposals/' + encodeURIComponent(p.id), {method: 'POST',
        headers: {'Content-Type': 'application/json', 'X-Refresher': '1'},
        body: JSON.stringify({action, token: p.token, rationale})});
      const body = await response.json().catch(() => ({}));
      if (response.ok) { feedback.textContent = 'Decision saved. Updating suggestions…'; document.dispatchEvent(new Event('refresher:proposals')); location.reload(); return; }
      feedback.className = 'feedback error';
      feedback.textContent = body.error || `Not saved (HTTP ${response.status}).`;
      feedback.append(' ', button('Look again', () => location.reload()));
    } catch (error) {
      feedback.className = 'feedback error'; feedback.textContent = 'The server did not answer. Is `refresher serve` still running?';
    }
    actions.querySelectorAll('button').forEach(b => {b.disabled = false;});
  }

  Object.assign(B, {renderProposals});
})();

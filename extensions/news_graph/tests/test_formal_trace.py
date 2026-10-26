"""The same fixed trace is read by Lean and by real Python/browser paths."""
import json
from pathlib import Path

import pytest
from newsmap_sidecar import review as rv
from newsmap_sidecar.common import NewsmapError, StaleError
from newsmap_sidecar.intake import import_batch, packages
from newsmap_sidecar.automation import assess
from newsmap_sidecar.metering import Ledger, MeteredModel
from newsmap_sidecar.runner import SyntheticWire
from conftest import fixture_record, publish, reseal
from test_review import prepare, context
from test_view_contracts import browser_view  # Reuse real offline Chrome fixture.

TRACE = json.loads((Path(__file__).parents[1] / 'proofs' / 'news-trace.json').read_text())


def test_shared_revision_review_trace(workspace, tmp_path):
    db, vault = workspace
    refs = prepare(db, vault)
    premise = rv.assign(db, 'subject_assign', refs[0], {'subject':'Aurora','version':'1'},
                        'Fixture premise', refs, 'fixture_operator')
    pid = rv.propose(db, *refs, 'SUPPORTS', context(), 'Fixture assessment', refs, [premise],
                     'fixture_model', provenance={'provider':'fixture','model':'fixture-model',
                     'prompt_version':'trace-v1','request_id':'trace-1','origin':'SIMULATED_OFFLINE'})
    original = refs[0]['bundle']; current_tokens = {0:rv.show(db,pid)['token']}
    newer, raw = fixture_record('a'); newer['claims'][0]['statement'] += ' New revision.'; reseal(newer)
    ledger = Ledger(tmp_path/'budget'/'ledger.sqlite','formal-fixture'); ledger.stage('trace',cap_micro=1_000_000)
    try:
        for step in TRACE['review']:
            action = step['action']
            before = rv.show(db,pid)
            if action in {'accept','reject','stale_reject','ineligible_accept'}:
                review_action = 'reject' if 'reject' in action else 'accept'
                token = current_tokens[step['expected_token']]
                if step['error']:
                    with pytest.raises(StaleError if step['error']=='stale' else NewsmapError):
                        rv.review(db,pid,review_action,token,'Trace command','fixture_operator')
                else:
                    rv.review(db,pid,review_action,token,'Trace command','fixture_operator')
            elif action == 'import_new':
                publish(vault,newer,raw); import_batch(db,'demo',vault)
            elif action in {'select_new','select_original'}:
                row = next(r for r in packages(db) if r['bundle']==original)
                db.select('demo',row['item'], newer['bundle_id'] if action=='select_new' else original,
                          row['head_token'],'fixture_operator')
            elif action == 'rerun_model':
                client=MeteredModel(ledger,'trace','map','fixture-model',SyntheticWire(),context={'stage':'automation'})
                result=assess(db,client,mode='NEBIUS')
                assert pid in result['preserved_decisions']
            elif action == 'revoke_premise':
                rv.premise_revoke(db,premise,'Trace withdrawal','fixture_operator')
            state=rv.show(db,pid)
            assert state['active_reviewed']==step['human'], action
            assert state['active_model_assessed']==step['model'], action
            assert len(state['reviews'])==step['history'], action
            if step['token'] in current_tokens:
                assert state['token']==current_tokens[step['token']], action
            else:
                assert state['token'] not in current_tokens.values(), action
                current_tokens[step['token']]=state['token']
            if step['error']: assert state['reviews']==before['reviews']
    finally: ledger.close()


def test_shared_unicode_span_uses_codepoints_not_utf16(workspace):
    db,vault=workspace;u=TRACE['unicode']
    r,raw=fixture_record(text=u['text']+'\nSecond qualifier')
    r['claims'][0]['evidence'][0].update(start_char=u['start'],end_char=u['stop'])
    reseal(r);publish(vault,r,raw);import_batch(db,'demo',vault)
    anchor=packages(db)[0]['evidence']['claims'][0]['anchors'][0]
    assert anchor['quote']==u['quote']
    assert anchor['coordinate_unit']=='unicode_code_points'
    assert len(u['quote'].encode('utf-16-le'))//2 != u['stop']-u['start']


def test_shared_graph_trace_real_browser(browser_view):
    page,_,_=browser_view
    graph=TRACE['graph']
    page.evaluate('''g => {
      const a=window.newsmap, ids=a.data.claims.map(c=>c.id), model=a.data.edges[0];
      a.data.edges=g.edges.map(e=>({...model,id:'trace-'+String(e.id).padStart(3,'0'),
        source:ids[e.source-1] || 'outside',target:ids[e.target-1] || 'outside',
        context:{...model.context,dataset:e.context},decision:undefined}));
      a.data.limits.primary_nodes=g.node_cap;
      a.refresh();
    }''',graph)
    page.locator('#mode-claims').click()
    actual=page.evaluate("window.newsmap.cy.edges().map(e=>Number(e.id().split('-')[1]))")
    assert actual==graph['expected_ids']
    page.locator('details.accessible summary').click()
    page.get_by_role('button',name='Следующая страница связей',exact=True).click()
    actual=page.evaluate("window.newsmap.cy.edges().map(e=>Number(e.id().split('-')[1]))")
    assert actual==graph['page_one_ids']
    # Reordering canonical input must not substitute another pair's context.
    page.evaluate("window.newsmap.data.edges.reverse(); window.newsmap.refresh(); window.newsmap.selectEdge({id:'trace-002'});")
    assert graph['selected_context'] in page.locator('#comparison').inner_text()
    assert page.evaluate('window.newsmap.state.comparison')==page.evaluate('window.newsmap.data.claims.map(c=>c.id).reverse()')
    page.locator('#mode-documents').click()
    assert graph['selected_context'] not in page.locator('#comparison').inner_text()


def test_right_revision_and_blank_rationale_are_fail_closed(workspace):
    db, vault = workspace
    refs = prepare(db, vault)
    pid = rv.propose(db, *refs, 'SUPPORTS', context(), 'Fixture relation', refs, [], 'fixture_operator')
    token = rv.show(db, pid)['token']
    with pytest.raises(NewsmapError, match='rationale'):
        rv.review(db, pid, 'accept', token, ' \t\n', 'fixture_operator')
    assert rv.show(db, pid)['token'] == token
    assert rv.show(db, pid)['reviews'] == []
    rv.review(db, pid, 'accept', token, 'Inspected both endpoints', 'fixture_operator')
    accepted = rv.show(db, pid)
    newer, raw = fixture_record('b')
    newer['claims'][0]['statement'] += ' New right endpoint.'
    reseal(newer); publish(vault, newer, raw); import_batch(db, 'demo', vault)
    row = next(r for r in packages(db) if r['bundle'] == refs[1]['bundle'])
    db.select('demo', row['item'], newer['bundle_id'], row['head_token'], 'fixture_operator')
    assert not rv.show(db, pid)['active_reviewed']
    row = next(r for r in packages(db) if r['bundle'] == refs[1]['bundle'])
    db.select('demo', row['item'], refs[1]['bundle'], row['head_token'], 'fixture_operator')
    restored = rv.show(db, pid)
    assert restored['active_reviewed']
    assert restored['token'] != accepted['token']
    assert restored['reviews'] == accepted['reviews']
    with pytest.raises(StaleError):
        rv.review(db, pid, 'accept', accepted['token'], 'Stale approval', 'fixture_operator')

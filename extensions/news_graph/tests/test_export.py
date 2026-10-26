from copy import deepcopy
import json

import pytest

from newsmap_sidecar import review as rv
from newsmap_sidecar.common import NewsmapError
from newsmap_sidecar.export import export_view
from newsmap_sidecar.intake import import_batch, packages
from newsmap_sidecar.projection import build, safe_url
from conftest import fixture_record, publish, reseal


def grant(row):
    return {**rv.reference(row), 'fields': ['source', 'quality', 'url', 'time', 'roles'],
            'claims': [{'claim': c['claim_id'], 'statement': True, 'surrounding': False,
                        'spans': list(range(len(c['anchors'])))} for c in row['evidence'].get('claims', [])],
            'links': list(range(len(row['evidence'].get('links', []))))}


def test_public_review_never_rebinds_changed_evidence(workspace, monkeypatch):
    from newsmap_sidecar import projection
    from test_review import prepare, proposal, approve
    db, vault = workspace
    refs = prepare(db, vault)
    pid = proposal(db, refs); rid = approve(db, pid)
    state = rv.snapshot(db)
    allow = {'actor': 'fixture_operator', 'items': [grant(r) for r in state['packages']], 'decisions': [pid, rid]}
    assert build(db, allow)['edges']
    # Fault injection beyond the immutable database contract: never relabel changed
    # observed anchors as the exact snapshot the reviewer approved.
    state['packages'][0]['evidence']['claims'][0]['anchors_digest'] = 'different'
    monkeypatch.setattr(projection, 'snapshot', lambda db, **kwargs: state)
    with pytest.raises(NewsmapError, match='disclosed dependency'):
        build(db, allow)


def setup_items(db, vault):
    for name in ('public', 'private'):
        r, raw = fixture_record(name)
        if name == 'private':
            r['source']['source_name'] = 'HIDDEN_SOURCE_CANARY'
            r['claims'][0]['statement'] = 'HIDDEN_CLAIM_CANARY'
            reseal(r)
        publish(vault, r, raw)
    import_batch(db, 'demo', vault)
    rows = packages(db)
    public = next(r for r in rows if r['record']['source']['source_id'] == 'synthetic:public')
    private = next(r for r in rows if r['record']['source']['source_id'] == 'synthetic:private')
    return public, private


def test_public_closure_excludes_private_rationale_premise_and_counts(workspace, tmp_path):
    db, vault = workspace
    public, private = setup_items(db, vault)
    public_ref, private_ref = rv.reference(public), rv.reference(private)
    hidden_story = rv.story_create(db, 'HIDDEN_TITLE_CANARY', 'models', 'HIDDEN_RATIONALE_CANARY', [private_ref], 'fixture_operator')
    assignment = rv.assign(db, 'story_assign', public_ref, hidden_story, 'HIDDEN_ASSIGNMENT_CANARY', [public_ref], 'fixture_operator')
    known = rv.identities(packages(db))
    people = [x for x in known if x['role'] == 'author_hint']
    identity = rv.identity_link(db, people[0]['id'], people[1]['id'], 'HIDDEN_IDENTITY_CANARY', [public_ref, private_ref], 'fixture_operator')
    allow = {'actor': 'fixture_operator', 'items': [grant(public)], 'decisions': [hidden_story, assignment, identity]}
    # Explicitly requested but unclosed decisions now fail, rather than disappear.
    with pytest.raises(NewsmapError, match='disclosed dependency'):
        build(db, allow)
    allow['decisions'] = []
    view = build(db, allow)
    payload = json.dumps(view)
    assert 'HIDDEN_' not in payload and 'demo' not in payload
    assert view['counts'] == {'items': 1, 'claims': 1, 'identities': 2, 'edges': 0, 'stories': 0}
    assert not view['decisions'] and not view['topics']
    assert public['bundle'] not in payload and public['item'] not in payload
    out = tmp_path / 'export'
    export_view(db, out, allow)
    assert all(b'HIDDEN_' not in p.read_bytes() for p in out.iterdir())


def test_private_metadata_addition_does_not_change_public_projection(workspace):
    db, vault = workspace
    public, private = setup_items(db, vault)
    allow = {'actor': 'fixture_operator', 'items': [grant(public)], 'decisions': []}
    before = build(db, allow)
    new, raw = fixture_record('unrelated-hidden')
    new['claims'][0]['statement'] = 'OTHER_HIDDEN'
    reseal(new)
    publish(vault, new, raw)
    import_batch(db, 'demo', vault)
    after = build(db, allow)
    before.pop('snapshot_at'); after.pop('snapshot_at')
    assert before == after


def test_public_quotes_exact_excerpts_without_surrounding(workspace):
    db, vault = workspace
    r, raw = fixture_record(text='PUBLIC QUOTE\nPRIVATE_SURROUNDING_CANARY')
    r['claims'][0]['statement'] = 'Public description'
    reseal(r)
    publish(vault, r, raw)
    import_batch(db, 'demo', vault)
    row = packages(db)[0]
    g = grant(row); g['claims'][0]['spans'] = [0]
    view = build(db, {'actor': 'fixture_operator', 'items': [g], 'decisions': []})
    assert 'PRIVATE_SURROUNDING_CANARY' not in json.dumps(view)
    assert not view['claims'][0]['complete_context']
    assert 'prefix' not in view['claims'][0]['anchors'][0]


def test_html_script_breakout_safe_and_fresh_output(workspace, tmp_path):
    db, vault = workspace
    r, raw = fixture_record(text='</script><script>window.PWNED=1</script>\nUntrusted inert text')
    r['claims'][0]['statement'] = '</script><img src=https://example.invalid/a onerror=alert(1)>'
    r['source']['url'] = 'javascript:alert(1)'
    reseal(r)
    publish(vault, r, raw)
    import_batch(db, 'demo', vault)
    out = tmp_path / 'export'
    export_view(db, out)
    html = (out / 'index.html').read_text()
    assert '</script><script>window.PWNED' not in html
    assert '\\u003c/script\\u003e' in html
    assert build(db)['items'][0]['url'] is None
    assert 'connect-src \'none\'' in html
    with pytest.raises(NewsmapError): export_view(db, out)
    with pytest.raises(NewsmapError): export_view(db, vault / 'export')
    with pytest.raises(NewsmapError): export_view(db, db.path.parent / 'export')
    backup = tmp_path / 'backup' / 'copy.sqlite'
    db.backup(backup)
    with pytest.raises(NewsmapError): export_view(db, backup.parent / 'export')


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'data:text/html,x', 'file:///tmp/a', 'tg://user?id=1',
    'https://user:password@example.org', 'https://example.org/\n', 'https://example.org:invalid', 'https:\\evil.example'])
def test_unsafe_links_inert(url):
    assert safe_url(url) is None


def test_safe_links_and_claim_limits(workspace):
    assert safe_url('https://example.org/news?q=test') == 'https://example.org/news?q=test'
    db, vault = workspace
    r, raw = fixture_record()
    r['claims'][0]['evidence'] *= 6
    reseal(r)
    publish(vault, r, raw)
    assert import_batch(db, 'demo', vault)['pending_evidence'] == 1


def test_public_relation_needs_all_premises_review_and_full_quotes(workspace):
    db, vault = workspace
    public, private = setup_items(db, vault)
    refs = [rv.reference(row, row['record']['claims'][0]['claim_id']) for row in (public, private)]
    premise = rv.assign(db, 'subject_assign', refs[0], {'subject': 'Aurora', 'version': '1'},
                        'PERMITTED_MANUAL_SUBJECT', refs, 'fixture_operator')
    context = {k: 'unknown' for k in rv.CONTEXT_FIELDS}
    pid = rv.propose(db, *refs, 'SUPPORTS', context, 'PERMITTED_MANUAL_RELATION', refs, [premise], 'fixture_operator')
    rid = rv.review(db, pid, 'accept', rv.show(db, pid)['token'], 'PERMITTED_REVIEW', 'fixture_operator')
    allow = {'actor': 'fixture_operator', 'items': [grant(public), grant(private)], 'decisions': [pid, rid]}
    with pytest.raises(NewsmapError, match='disclosed dependency'):
        build(db, allow)
    allow['decisions'].append(premise)
    view = build(db, allow)
    assert len(view['edges']) == 1
    assert view['edges'][0]['basis'] == 'review_record'
    assert not view['edges'][0]['active_reviewed']  # No claim about private current heads.
    allow['decisions'].remove(rid)
    hidden_review_view = build(db, allow)
    assert hidden_review_view['edges'][0]['review_record'] == 'proposed'
    assert all(d['kind'] != 'review' for d in hidden_review_view['decisions'])
    allow['decisions'].append(rid)
    allow['items'][0]['claims'][0]['spans'] = [0]
    with pytest.raises(NewsmapError, match='disclosed dependency'):
        build(db, allow)


def test_parent_capture_is_structural_and_not_public_by_default(workspace):
    db, vault = workspace
    parent, raw_parent = fixture_record('parent')
    child, raw_child = fixture_record('child')
    child['parent'] = {'item_id': parent['item_id'], 'raw_path': parent['raw_path']}
    reseal(child)
    publish(vault, parent, raw_parent); publish(vault, child, raw_child)
    import_batch(db, 'demo', vault)
    view = build(db)
    assert {e['kind'] for e in view['edges']} == {'CAPTURED_FROM', 'CAPTURE_RECORD_MATCH'}
    assert all(e['basis'] == 'structural' for e in view['edges'])
    assert not build(db, {'actor': 'fixture_operator', 'items': [grant(r) for r in packages(db)], 'decisions': []})['edges']


def test_public_source_role_projection_never_dumps_unknown_fields_or_local_ids(workspace):
    db, vault = workspace
    r, raw = fixture_record()
    r['source']['source_name'] = None
    r['source']['source_id'] = 'file:///HIDDEN_LOCAL_PATH_CANARY'
    r['source']['author_hint'] = {'name': 'Example', 'private_note': 'HIDDEN_ROLE_CANARY', 'email': 'HIDDEN_EMAIL_CANARY'}
    reseal(r); publish(vault, r, raw); import_batch(db, 'demo', vault)
    view = build(db, {'actor': 'fixture_operator', 'items': [grant(packages(db)[0])], 'decisions': []})
    assert 'HIDDEN_' not in json.dumps(view)


def test_broad_item_citation_cannot_clear_unreleased_editorial_context(workspace):
    db, vault = workspace
    r, raw = fixture_record(text='Public passage\nHIDDEN_CONTEXT_CANARY')
    r['claims'][0]['statement'] = 'Public description'
    reseal(r); publish(vault, r, raw); import_batch(db, 'demo', vault)
    row = packages(db)[0]
    story = rv.story_create(db, 'HIDDEN_CONTEXT_CANARY derived title', 'models', 'Broad item citation',
                           [rv.reference(row)], 'fixture_operator')
    g = grant(row); g['claims'][0]['spans'] = [0]
    with pytest.raises(NewsmapError, match='disclosed dependency'):
        build(db, {'actor': 'fixture_operator', 'items': [g], 'decisions': [story]})
    view = build(db, {'actor': 'fixture_operator', 'items': [g], 'decisions': []})
    assert 'HIDDEN_CONTEXT_CANARY' not in json.dumps(view)
    assert not view['stories']

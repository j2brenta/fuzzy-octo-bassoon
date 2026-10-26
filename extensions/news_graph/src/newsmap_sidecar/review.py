"""Explicit editorial decisions and revision-bound human interpretations."""
import json
from pathlib import Path

from .common import require, nonblank, fingerprint, StaleError
from .intake import packages

CONTEXT_FIELDS = ('subject', 'version', 'metric', 'dataset', 'unit', 'conditions', 'time_attribution', 'unknowns')
RELATIONS = {'SUPPORTS', 'CONTRADICTS', 'CORRECTS', 'SUPERSEDES', 'DUPLICATES', 'ELABORATES'}
_TOPIC_CONFIG = json.loads((Path(__file__).resolve().parent / 'assets' / 'topics.json').read_text())
TOPICS = {t['id']: t['title'] for t in _TOPIC_CONFIG['topics']}
TOPIC_DEFINITIONS = {t['id']: t['definition'] for t in _TOPIC_CONFIG['topics']}


def reference(row, claim=None):
    ref = {k: row[k] for k in ('namespace', 'item', 'bundle')}
    if claim is not None:
        ref['claim'] = claim
    return ref


def lookup(rows, ref):
    require(isinstance(ref, dict) and set(ref) in ({'namespace', 'item', 'bundle'},
            {'namespace', 'item', 'bundle', 'claim'}), 'reference must name an exact imported revision')
    candidates = [r for r in rows if all(r[k] == ref[k] for k in ('namespace', 'item', 'bundle'))]
    require(len(candidates) == 1, 'referenced revision not retained')
    row = candidates[0]
    if 'claim' in ref:
        require(any(c['claim_id'] == ref['claim'] for c in row['record']['claims']), 'claim not in referenced package')
    return row


def identities(rows):
    """No display-name deduplication; every hint/mention retains its own role."""
    result = []
    for row in rows:
        src = row['record']['source']
        for role, hint in [('source_record', {'name': src.get('source_name') or src['source_id']}),
                           ('author_hint', src.get('author_hint')), ('forwarder_hint', src.get('forwarded_by'))]:
            if hint:
                if isinstance(hint, str):
                    hint = {'name': hint}
                result.append({'id': fingerprint([reference(row), role]), 'role': role, 'hint': hint,
                               'reference': reference(row), 'basis': 'retained producer source metadata'})
        for entity in row['record']['entities']:
            result.append({'id': fingerprint([reference(row), 'mention', entity['entity_id']]),
                           'role': 'extracted_mention', 'hint': {'name': entity['name'], 'kind': entity['kind']},
                           'reference': reference(row), 'entity_id': entity['entity_id'],
                           'basis': 'upstream extracted entity; authorship unconfirmed'})
    return result


def events(db):
    return [{**dict(e), 'payload': json.loads(e['payload'])} for e in db.conn.execute('SELECT * FROM events ORDER BY id')]


def get_event(log, event_id, kinds):
    matches = [e for e in log if e['id'] == event_id and e['kind'] in kinds]
    require(len(matches) == 1, 'unknown decision or wrong decision type')
    return matches[0]


def revocations(log, event_id):
    return [e for e in log if e['kind'] == 'premise_revoke' and e['payload']['decision'] == event_id]


def decision_method(provenance, manual):
    if provenance is None: return {'method': manual}
    require(isinstance(provenance, dict) and set(provenance) == {'provider', 'model', 'prompt_version', 'request_id', 'origin'}, 'assessment provenance required')
    require(all(nonblank(v) and len(v) <= 256 for v in provenance.values()), 'invalid assessment provenance')
    return {'method': 'model_assessed', 'provenance': dict(provenance)}


def story_create(db, title, topic, rationale, evidence_refs, actor, *, provenance=None):
    require(nonblank(title) and nonblank(rationale), 'story title and rationale required')
    require(topic in TOPICS, 'unknown news topic')
    with db.transaction():
        rows = packages(db)
        require(isinstance(evidence_refs, list) and bool(evidence_refs), 'story requires retained provenance')
        for ref in evidence_refs:
            lookup(rows, ref)
        return db.event('story', {'title': title, 'topic': topic, 'rationale': rationale,
                                 'evidence': evidence_refs, **decision_method(provenance, 'manual_editorial')}, actor)


def assign(db, kind, target, value, rationale, evidence_refs, actor, *, provenance=None):
    require(kind in {'story_assign', 'topic_assign', 'subject_assign'}, 'unsupported assignment')
    require(nonblank(rationale), 'assignment rationale required')
    with db.transaction():
        rows, log = packages(db), events(db)
        lookup(rows, target)
        require(isinstance(evidence_refs, list) and bool(evidence_refs), 'assignment requires retained evidence references')
        for ref in evidence_refs:
            lookup(rows, ref)
        if kind == 'story_assign':
            get_event(log, value, {'story'})
        elif kind == 'topic_assign':
            require(value in TOPICS, 'unknown news topic')
        else:
            require(isinstance(value, dict) and set(value) == {'subject', 'version'} and
                    all(nonblank(v) for v in value.values()), 'subject assignment needs subject and version (or explicit unknown)')
        return db.event(kind, {'target': target, 'value': value, 'rationale': rationale,
                              'evidence': evidence_refs, **decision_method(provenance, 'manual_editorial')}, actor)


def identity_link(db, left, right, rationale, evidence_refs, actor):
    require(nonblank(rationale), 'identity rationale required')
    with db.transaction():
        rows = packages(db)
        known = {i['id']: i for i in identities(rows)}
        require(left != right and left in known and right in known, 'identity link requires two existing distinct identities')
        require(isinstance(evidence_refs, list) and bool(evidence_refs), 'identity link requires retained evidence references')
        for ref in evidence_refs:
            lookup(rows, ref)
        return db.event('identity_link', {'left': left, 'right': right, 'rationale': rationale,
                                        'evidence': evidence_refs, 'method': 'manual_identity_interpretation'}, actor)


def premise_revoke(db, event_id, rationale, actor):
    require(nonblank(rationale), 'revocation rationale required')
    with db.transaction():
        log = events(db)
        get_event(log, event_id, {'identity_link', 'subject_assign', 'story_assign', 'topic_assign'})
        require(not revocations(log, event_id), 'decision already revoked')
        return db.event('premise_revoke', {'decision': event_id, 'rationale': rationale}, actor)


def endpoint(rows, ref):
    row = lookup(rows, ref)
    require('claim' in ref, 'relation endpoint must name a claim')
    c = next(c for c in row['record']['claims'] if c['claim_id'] == ref['claim'])
    observed = next((c for c in row['evidence'].get('claims', []) if c['claim_id'] == ref['claim']), None)
    # A blocked proposal may still be retained for missing evidence, but cannot be approved.
    return {'ref': ref, 'payload_digest': fingerprint(c),
            'anchors_digest': observed['anchors_digest'] if observed else None,
            'anchors': observed['anchors'] if observed else [], 'statement': c['statement']}


def propose(db, left, right, relation, context, rationale, evidence_refs, premises, actor, *, provenance=None):
    require(relation in RELATIONS, 'unsupported relation type')
    require(left != right, 'self relation is not supported')
    require(nonblank(rationale), 'relation rationale required')
    require(isinstance(context, dict) and set(context) == set(CONTEXT_FIELDS) and
            all(isinstance(v, str) for v in context.values()), 'comparison must explicitly include all context fields')
    require(isinstance(premises, list) and all(type(p) is int for p in premises) and
            len(set(premises)) == len(premises), 'premises must be distinct decision IDs')
    with db.transaction():
        rows, log = packages(db), events(db)
        require(isinstance(evidence_refs, list) and bool(evidence_refs), 'operator context requires cited evidence')
        for ref in evidence_refs:
            lookup(rows, ref)
        for p in premises:
            get_event(log, p, {'identity_link', 'subject_assign'})
        payload = {'left': endpoint(rows, left), 'right': endpoint(rows, right), 'relation': relation,
                   'context': context, 'rationale': rationale, 'evidence': evidence_refs,
                   'premises': premises, **decision_method(provenance, 'manual_interpretation')}
        payload['digest'] = fingerprint(payload)
        return db.event('proposal', payload, actor)


def _show(rows, log, proposal_id):
    proposal = get_event(log, proposal_id, {'proposal'})
    p = proposal['payload']
    blocked, historical, recheck, endpoints = [], [], [], []
    for side in ('left', 'right'):
        bound = p[side]
        current = endpoint(rows, bound['ref'])
        row = lookup(rows, bound['ref'])
        if row['admission'] != 'valid' or row['quality'] != 'accepted' or not row['record']['quality_gate_passed']:
            blocked.append(side + ': endpoint quality/evidence ineligible')
        if not current['anchors_digest'] or not current['anchors'] or not bound['anchors_digest'] or not bound['anchors']:
            blocked.append(side + ': exact retained evidence unavailable')
        if current['payload_digest'] != bound['payload_digest'] or current['anchors_digest'] != bound['anchors_digest']:
            blocked.append(side + ': proposal evidence binding changed; create a new proposal')
        if not row['selected']:
            historical.append(side + ': endpoint revision not selected')
        endpoints.append({'binding': current, 'head_token': row['head_token'], 'admission': row['admission'],
                          'quality': row['quality'], 'quality_gate_passed': row['record']['quality_gate_passed'],
                          'selected': row['selected']})
    premise_states = []
    for premise_id in p['premises']:
        decision = get_event(log, premise_id, {'identity_link', 'subject_assign'})
        revoked = revocations(log, premise_id)
        premise_states.append({'decision': decision, 'revocations': revoked})
        if revoked:
            recheck.append('premise ' + str(premise_id) + ' revoked')
    reviews = [e for e in log if e['kind'] == 'review' and e['payload']['proposal'] == proposal_id]
    latest = reviews[-1] if reviews else None
    model_assessed = p.get('method') == 'model_assessed'
    status = latest['payload']['action'] if latest else ('model_assessed' if model_assessed else 'proposed')
    token = fingerprint({'proposal': proposal, 'endpoints': endpoints, 'premises': premise_states, 'latest_review': latest})
    return {'proposal': proposal, 'token': token, 'review_status': status, 'latest_review': latest,
            'reviews': reviews, 'blocked': blocked, 'historical': historical, 'needs_recheck': recheck,
            'active_reviewed': status == 'accept' and not blocked and not historical and not recheck,
            'active_model_assessed': model_assessed and latest is None and not blocked and not historical and not recheck,
            'premise_states': premise_states, 'endpoints': endpoints,
            'label': 'reviewer-approved interpretation; not independent fact verification' if status == 'accept'
                     else 'manual proposal/review; not a truth assertion'}


def show(db, proposal_id):
    with db.transaction(write=False):
        return _show(packages(db), events(db), proposal_id)


def review(db, proposal_id, action, expected_token, rationale, actor):
    require(action in {'accept', 'reject', 'defer', 'revoke'}, 'invalid review action')
    require(nonblank(rationale), 'review rationale required')
    with db.transaction():
        current = _show(packages(db), events(db), proposal_id)
        if current['token'] != expected_token:
            raise StaleError('review token changed; inspect current evidence and review again')
        if action == 'accept':
            require(not current['blocked'] and not current['historical'] and not current['needs_recheck'],
                    'approval blocked by quality/evidence, selection, or revoked premise')
        return db.event('review', {'proposal': proposal_id, 'action': action, 'expected_token': expected_token,
                                  'rationale': rationale, 'proposal_digest': current['proposal']['payload']['digest']}, actor)


def snapshot(db, *, public_grants=None, bounded=False):
    with db.transaction(write=False):
        rows = packages(db, keys=public_grants['items'] if public_grants is not None else None, bounded=bounded)
        if public_grants is not None:
            ids = public_grants['decisions']
            require(len(ids) <= 2000, 'view decision work limit exceeded')
            log = [dict(e) for e in db.conn.execute('SELECT * FROM events WHERE id IN (' + (','.join('?' for _ in ids) or 'NULL') + ') ORDER BY id', ids)]
            for e in log: e['payload'] = json.loads(e['payload'])
        else:
            if bounded:
                require(db.conn.execute('SELECT count(*) FROM events').fetchone()[0] <= 2000, 'view decision work limit exceeded')
            log = events(db)
        if bounded:
            require(sum(len(r['record']['claims']) for r in rows) <= 200, 'view claim work limit exceeded')
        return {'packages': rows, 'identities': identities(rows), 'events': log,
                'relations': [({'proposal': e} if public_grants is not None else _show(rows, log, e['id'])) for e in log if e['kind'] == 'proposal'],
                'topics': TOPICS, 'topic_definitions': TOPIC_DEFINITIONS}

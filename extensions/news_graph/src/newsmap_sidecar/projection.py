"""Minimal viewer DTOs. Public authorization precedes topology and aggregation."""
from urllib.parse import urlsplit

from .common import require, nonblank, fingerprint, now
from .review import snapshot, reference, revocations, endpoint


def safe_url(value):
    if not isinstance(value, str) or any(ord(c) <= 32 or ord(c) == 127 for c in value) or '\\' in value:
        return None
    try:
        p = urlsplit(value)
        if p.scheme not in {'http', 'https'} or not p.hostname or p.username is not None or p.password is not None:
            return None
        p.port
        return value
    except ValueError:
        return None


def key(ref):
    return tuple(ref[k] for k in ('namespace', 'item', 'bundle'))


def build(db, allowlist=None):
    public = allowlist is not None
    grants = {}
    if public:
        require(isinstance(allowlist, dict) and set(allowlist) == {'actor', 'items', 'decisions'}, 'invalid public allowlist shape')
        require(nonblank(allowlist['actor']), 'public export requires a declared operator')
        require(isinstance(allowlist['items'], list) and isinstance(allowlist['decisions'], list), 'allowlist collections required')
        require(all(type(x) is int for x in allowlist['decisions']), 'decision approvals must be IDs')
        for g in allowlist['items']:
            require(isinstance(g, dict) and set(g) == {'namespace', 'item', 'bundle', 'fields', 'claims', 'links'}, 'invalid item grant')
            require(key(g) not in grants, 'duplicate item grant')
            require(isinstance(g['fields'], list) and all(x in {'source', 'url', 'time', 'quality', 'roles'} for x in g['fields']),
                    'unsupported source field grant')
            require(isinstance(g['claims'], list) and isinstance(g['links'], list) and
                    all(type(x) is int and x >= 0 for x in g['links']), 'invalid claims/links grants')
            grants[key(g)] = g
        state = snapshot(db, public_grants={'items': list(grants), 'decisions': allowlist['decisions']}, bounded=True)
        known = {key(r) for r in state['packages']}
        require(set(grants) <= known, 'allowlist references an unknown revision')
        permitted_decisions = set(allowlist['decisions'])
        require(permitted_decisions <= {e['id'] for e in state['events']}, 'allowlist references unknown decisions')
    else:
        state = snapshot(db, bounded=True)
        permitted_decisions = {e['id'] for e in state['events']}
        for r in state['packages']:
            grants[key(r)] = {'fields': ['source', 'url', 'time', 'quality', 'roles'],
                             'claims': [{'claim': c['claim_id'], 'statement': True,
                                         'spans': list(range(len(c['anchors']))), 'surrounding': True}
                                        for c in r['evidence'].get('claims', [])],
                             'links': list(range(len(r['evidence'].get('links', []))))}
    rows = [r for r in state['packages'] if key(r) in grants]
    row_ids = {key(r): 'item-' + str(i + 1) for i, r in enumerate(rows)}
    claim_ids, full_claims, items, claims, ids, edges = {}, set(), [], [], [], []
    for row in rows:
        rk, grant = key(row), grants[key(row)]
        fields, item_id, source = grant['fields'], row_ids[rk], row['record']['source']
        item = {'id': item_id, 'title': 'Материал ' + str(len(items) + 1),
                'source': (source.get('source_name') or ('Источник' if public else source['source_id'])) if 'source' in fields else 'Источник не раскрыт',
                'url': safe_url(source.get('url')) if 'url' in fields else None,
                'time': (source.get('published_at') or source['captured_at']) if 'time' in fields else None,
                'time_basis': ('publication' if source.get('published_at') else 'capture') if 'time' in fields else 'not_disclosed',
                'quality': row['quality'] if 'quality' in fields else 'not_disclosed',
                'admission': row['admission'] if not public else 'authorized_excerpt',
                'selected': row['selected'] if not public else None,
                'current_applicability': 'not_disclosed' if public else 'evaluated_in_snapshot',
                'disclosure_authorized': public,
                'revision': row['bundle'] if not public else 'Разрешённая версия',
                'availability': 'upstream availability not confirmed', 'claims': [], 'roles': [],
                'stories': [], 'topics': [], 'subjects': []}
        if not public:
            item.update(first_observed_batch=row['first_observed_batch'], last_observed_batch=row['last_observed_batch'],
                        raw_basis=row['evidence'].get('raw_basis', 'evidence unavailable'),
                        evidence_observation_reason=row['evidence_observation_reason'])
        available = {c['claim_id']: c for c in row['evidence'].get('claims', [])}
        seen = set()
        for cg in grant['claims']:
            require(isinstance(cg, dict) and set(cg) == {'claim', 'statement', 'spans', 'surrounding'}, 'invalid claim grant')
            require(type(cg['statement']) is bool and type(cg['surrounding']) is bool, 'claim field grants must be Boolean')
            require(cg['claim'] in available and cg['claim'] not in seen, 'unknown/duplicate retained claim grant')
            seen.add(cg['claim'])
            c = available[cg['claim']]
            spans = cg['spans']
            require(isinstance(spans, list) and all(type(i) is int and 0 <= i < len(c['anchors']) for i in spans)
                    and len(set(spans)) == len(spans), 'invalid excerpt span grant')
            if not spans and not cg['statement']:
                continue
            cid = 'claim-' + str(len(claims) + 1)
            claim_ids[(rk, c['claim_id'])] = cid
            if cg['statement'] and set(spans) == set(range(len(c['anchors']))):
                full_claims.add((rk, c['claim_id']))
            anchors = []
            for index in spans:
                original = c['anchors'][index]
                a = {k: original[k] for k in ('quote', 'start_char', 'end_char', 'coordinate_unit')}
                a['span'] = index + 1
                if cg['surrounding']:
                    a.update(prefix=original['prefix'], suffix=original['suffix'])
                if not public:
                    a.update({k: original[k] for k in ('representation_id', 'text_sha256', 'field', 'section_id')})
                anchors.append(a)
            statement = c['statement'] if cg['statement'] else 'Разрешённый фрагмент'
            claims.append({'id': cid, 'item': item_id, 'statement': statement, 'anchors': anchors,
                           'complete_context': (rk, c['claim_id']) in full_claims,
                           'quality': item['quality'], 'selected': item['selected']})
            item['claims'].append(cid)
            if item['title'].startswith('Материал ') and cg['statement']:
                item['title'] = statement
        if 'roles' in fields:
            for ident in state['identities']:
                if key(ident['reference']) == rk:
                    iid = 'identity-' + str(len(ids) + 1)
                    hint = {k: v for k, v in ident['hint'].items() if k in {'name', 'handle', 'url', 'kind'}}
                    if public and ident['role'] == 'source_record':
                        hint = {'name': item['source']}
                    ids.append({'id': iid, 'local_id': ident['id'], 'item': item_id,
                                'role': ident['role'], 'hint': hint, 'basis': ident['basis']})
                    item['roles'].append(iid)
        items.append(item)

    def ref_allowed(ref, *, evidence=False):
        rk = key(ref)
        if rk not in row_ids:
            return False
        if 'claim' in ref:
            return (rk, ref['claim']) in full_claims
        # A whole-item citation does not identify which private/unreleased passages
        # informed a manual string. Public editorial evidence must name full claims.
        return not (public and evidence)

    def map_ref(ref):
        if 'claim' in ref:
            return claim_ids.get((key(ref), ref['claim']))
        return row_ids.get(key(ref))

    identity_map = {i['local_id']: i['id'] for i in ids}
    log = [e for e in state['events'] if not public or e['id'] in permitted_decisions]
    exportable = {}
    for e in log:
        if e['id'] not in permitted_decisions:
            continue
        p = e['payload']
        # A manual string needs its own explicit approval AND closed evidence provenance.
        if e['kind'] in {'story', 'story_assign', 'topic_assign', 'subject_assign', 'identity_link', 'proposal'}:
            if not all(ref_allowed(r, evidence=True) for r in p['evidence']):
                continue
            if e['kind'].endswith('_assign') and not ref_allowed(p['target']):
                continue
            if e['kind'] == 'story_assign' and p['value'] not in exportable:
                continue
            if e['kind'] == 'identity_link' and (p['left'] not in identity_map or p['right'] not in identity_map):
                continue
            if e['kind'] == 'proposal':
                if not all(ref_allowed(p[side]['ref']) for side in ('left', 'right')) or not all(x in exportable for x in p['premises']):
                    continue
                # Public review records must refer to the exact frozen evidence.
                if public and any(endpoint(rows, p[side]['ref']) != p[side] for side in ('left', 'right')):
                    continue
            exportable[e['id']] = e
        elif e['kind'] == 'review' and p['proposal'] in exportable:
            exportable[e['id']] = e
        elif e['kind'] == 'premise_revoke' and p['decision'] in exportable:
            exportable[e['id']] = e
    if public:
        require(permitted_decisions <= set(exportable), 'required disclosed dependency or exact evidence binding missing')
    decision_ids = {eid: 'decision-' + str(i + 1) for i, eid in enumerate(exportable)}
    stories, decisions = [], []
    item_by_id = {i['id']: i for i in items}
    for eid, e in exportable.items():
        p, kind = e['payload'], e['kind']
        d = {'id': decision_ids[eid], 'kind': kind, 'actor': e['actor'], 'at': e['at'],
             'rationale': p.get('rationale', ''), 'evidence': [map_ref(r) for r in p.get('evidence', [])]}
        if not public:
            d['authority_id'] = eid
        d['assessment'] = 'MODEL_ASSESSED' if p.get('method') == 'model_assessed' else 'HUMAN_REVIEWED' if kind == 'review' else 'OPERATOR_DECISION'
        if p.get('provenance'): d['provenance'] = p['provenance']
        if kind == 'review':
            d.update(action=p['action'], proposal=decision_ids[p['proposal']])
        decisions.append(d)
        if kind == 'story':
            stories.append({'id': d['id'], 'title': p['title'], 'topic': p['topic'], 'items': [], 'decision': d['id']})
        human_override = p.get('method') == 'model_assessed' and any(x['kind'] == kind and x['payload'].get('target') == p.get('target') and x['payload'].get('method') != 'model_assessed' for x in log)
        if kind.endswith('_assign') and not revocations(log, eid) and not human_override:
            target_item = item_by_id[row_ids[key(p['target'])]]
            if kind == 'story_assign':
                sid = decision_ids[p['value']]
                target_item['stories'].append(sid)
                story = next(s for s in stories if s['id'] == sid)
                if target_item['id'] not in story['items']:
                    story['items'].append(target_item['id'])
                target_item['topics'].append(story['topic'])
            elif kind == 'topic_assign':
                target_item['topics'].append(p['value'])
            else:
                target_item['subjects'].append({'value': p['value'], 'decision': d['id']})
    # Directed provenance is derived only after authorized items and fields exist.
    resources = {}
    for row in rows:
        rk, grant = key(row), grants[key(row)]
        observed = row['evidence'].get('links', [])
        require(all(i < len(observed) for i in grant['links']), 'link grant outside retained links')
        for index in grant['links']:
            link = observed[index]
            url = link['url']
            if url not in resources:
                rid = 'url-' + str(len(resources) + 1)
                resources[url] = {'id': rid, 'url': safe_url(url), 'label': url, 'kind': 'resource'}
            a = link['anchor']
            edges.append({'source': row_ids[rk], 'target': resources[url]['id'], 'kind': 'REFERS_TO',
                          'label': 'ссылается на', 'basis': 'structural',
                          'rationale': 'Literal URL in retained passage' if link['kind'] == 'url' else 'Retained embedded text link',
                          'anchors': [{k: a[k] for k in ('quote', 'start_char', 'end_char', 'coordinate_unit')}],
                          'item': row_ids[rk]})
    for url, resource in resources.items():
        for item in items:
            if item['url'] == url:
                edges.append({'source': resource['id'], 'target': item['id'], 'kind': 'URL_MATCH',
                              'label': 'URL совпадает', 'basis': 'structural', 'anchors': [], 'item': item['id'],
                              'rationale': 'Exact captured source URL match; not identity of document versions'})
    if not public:
        for row in rows:
            parent = row['record'].get('parent')
            if parent is None:
                continue
            rid = 'capture-' + str(len(resources) + 1)
            resources[rid] = {'id': rid, 'url': None, 'label': 'Capture reference · ' + parent['item_id'][:12], 'kind': 'resource'}
            edges.append({'source': row_ids[key(row)], 'target': rid, 'kind': 'CAPTURED_FROM',
                          'label': 'capture-родитель', 'basis': 'structural', 'anchors': [], 'item': row_ids[key(row)],
                          'rationale': 'Retained envelope parent pointer; capture relationship, not proof of original authorship'})
            for candidate in rows:
                if candidate['namespace'] == row['namespace'] and candidate['item'] == parent['item_id']:
                    edges.append({'source': rid, 'target': row_ids[key(candidate)], 'kind': 'CAPTURE_RECORD_MATCH',
                                  'label': 'capture совпадает', 'basis': 'structural', 'anchors': [],
                                  'item': row_ids[key(candidate)], 'rationale': 'Scoped capture item match; parent package revision was not specified'})
    for relation in state['relations']:
        e = relation['proposal']
        if e['id'] not in exportable:
            continue
        disclosed_reviews = [x for x in log if x['kind'] == 'review' and x['payload']['proposal'] == e['id']]
        latest = (disclosed_reviews[-1] if disclosed_reviews else None) if public else relation['latest_review']
        status = (latest['payload']['action'] if latest else ('model_assessed' if e['payload'].get('method') == 'model_assessed' else 'proposed')) if public else relation['review_status']
        p = e['payload']
        # Public exports disclose the approved exact-revision review, never private current heads.
        active = relation['active_reviewed'] if not public else False
        basis = ('review_record' if latest else 'proposal') if public else (
            'reviewed' if active else 'model_assessed' if relation.get('active_model_assessed') else 'review_history' if latest else 'assessment_history' if p.get('method') == 'model_assessed' else 'proposal')
        edges.append({'source': map_ref(p['left']['ref']), 'target': map_ref(p['right']['ref']),
                      'kind': p['relation'], 'label': p['relation'].lower(), 'basis': basis,
                      'rationale': p['rationale'], 'context': p['context'], 'decision': decision_ids[e['id']],
                      'review_status': status, 'review_record': status if public else None,
                      'current_applicability': 'not_disclosed' if public else 'evaluated_in_snapshot',
                      'disclosure_authorized': public, 'actor': latest['actor'] if latest else e['actor'],
                      'at': latest['at'] if latest else e['at'], 'active_reviewed': active,
                      'active_model_assessed': False if public else relation.get('active_model_assessed', False),
                      'assessment': 'HUMAN_REVIEWED' if latest else 'MODEL_ASSESSED' if p.get('method') == 'model_assessed' else 'OPERATOR_PROPOSAL',
                      'provenance': p.get('provenance'),
                      'blocked': relation['blocked'] if not public else [], 'historical': relation['historical'] if not public else [],
                      'applicability': 'private current selection not disclosed' if public else 'evaluated in authority snapshot',
                      'needs_recheck': [] if public else (['premise revoked'] if relation['needs_recheck'] else []), 'anchors': []})
    for eid, event in exportable.items():
        if event['kind'] != 'identity_link': continue
        p = event['payload']
        edges.append({'source': identity_map[p['left']], 'target': identity_map[p['right']],
                      'kind': 'IDENTITY_LINK', 'label': 'ручная гипотеза идентичности',
                      'basis': 'review_record' if public else 'operator_identity',
                      'rationale': p['rationale'], 'decision': decision_ids[eid], 'anchors': [],
                      'actor': event['actor'], 'at': event['at'], 'assessment': 'OPERATOR_DECISION',
                      'active_reviewed': False, 'review_status': 'historical_record' if public else
                          ('revoked' if revocations(log,eid) else 'operator_asserted'),
                      'current_applicability': 'not_disclosed' if public else 'evaluated_in_snapshot'})
    for i, edge in enumerate(edges):
        edge['id'] = 'edge-' + str(i + 1)
    for item in items:
        item['topics'] = sorted(set(item['topics']))
        item['stories'] = sorted(set(item['stories']))
    for ident in ids:
        ident.pop('local_id')
    active_topics = sorted({t for i in items for t in i['topics']})
    return {'schema': 1, 'snapshot_at': now(), 'scope': 'public_allowlist' if public else 'private_local',
            'items': items, 'claims': claims, 'identities': ids, 'resources': list(resources.values()),
            'edges': edges, 'stories': stories, 'decisions': decisions,
            'topics': [{'id': t, 'title': state['topics'][t], 'definition': state['topic_definitions'][t],
                        'count': sum(t in i['topics'] for i in items)} for t in active_topics],
            'counts': {'items': len(items), 'claims': len(claims), 'identities': len(ids), 'edges': len(edges), 'stories': len(stories)},
            'limits': {'primary_nodes': 20, 'edges': 60}, 'independence': 'unknown',
            'curation': 'MODEL_ASSESSED and HUMAN_REVIEWED are distinct; stored interpretations do not establish independent truth.'}

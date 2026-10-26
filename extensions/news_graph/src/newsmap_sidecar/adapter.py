"""Two explicit producer adapters. Hashes establish consistency, not truth."""
import re

from .common import fingerprint, digest, identifier, require, nonblank, parse_json

HALF_LIVES = {'foundation': 720, 'reference': 180, 'news': 30, 'ephemeral': 7}


def _list(record, name):
    value = record.get(name)
    require(isinstance(value, list), name + ' must be a list')
    return value


def _objects(record, name, key):
    rows = _list(record, name)
    require(all(isinstance(r, dict) and nonblank(r.get(key)) for r in rows), name + ' IDs missing')
    require(len({r[key] for r in rows}) == len(rows), name + ' IDs repeated')
    return rows


def quality(record, legacy):
    """Validate embedded findings rather than trusting a single accepted flag."""
    reviews = record.get('reviews')
    require(isinstance(reviews, dict), 'reviews missing')
    states = []
    for collection, key, omitted in [('claims', 'claim_id', 'omitted_concepts'),
                                     ('entities', 'entity_id', 'omitted_entities')]:
        expected = {r[key]: r for r in record[collection]}
        review = reviews.get(collection)
        require(isinstance(review, dict), 'review missing')
        state = review.get('status')
        require(state in {'pending', 'accepted', 'needs_revision'}, 'unsupported embedded review status')
        states.append(state)
        if state == 'pending':
            require(not review.get(collection), 'pending review cannot contain evaluated rows')
            continue
        rows = _objects(review, collection, key)
        require({r[key] for r in rows} == set(expected), 'review must cover exactly all IDs')
        omissions = _list(review, omitted)
        require(nonblank(review.get('summary')), 'review summary missing')
        for row in rows:
            allowed = {'supported', 'needs_revision'} if collection == 'entities' else {
                'supported', 'needs_revision', 'unsupported', 'duplicate'}
            require(isinstance(row.get('verdict'), str) and row['verdict'] in allowed, 'invalid verdict')
            require(nonblank(row.get('reason')), 'review reason missing')
            if collection == 'claims':
                issues = _list(row, 'issues')
                allowed_issues = {'unsupported', 'wrong_evidence', 'lost_qualification', 'wrong_attribution',
                                  'duplicate', 'not_atomic', 'wrong_kind', 'wrong_durability'}
                require(all(isinstance(i, str) and i in allowed_issues for i in issues), 'invalid review issue')
                require(len(issues) == len(set(issues)), 'duplicate review issue')
                require((row['verdict'] == 'supported') == (not issues), 'verdict/issue mismatch')
        blocking = [r for r in rows if legacy or collection == 'claims'
                    or expected[r[key]]['kind'] != 'concept']
        accepted = all(r['verdict'] == 'supported' for r in blocking) and (not legacy or not omissions)
        require(state == ('accepted' if accepted else 'needs_revision'), 'embedded review findings/status mismatch')
    triage = record.get('triage')
    require(triage is None or isinstance(triage, dict), 'invalid triage')
    if triage is not None:
        require(type(triage.get('keep')) is bool, 'triage keep must be Boolean')
        require(triage.get('kind') in {'post', 'article', 'newsletter_digest', 'announcement', 'thought', 'conversation', 'other'}
                and triage.get('durability_class') in HALF_LIVES and type(triage.get('split_digest')) is bool
                and nonblank(triage.get('reason')), 'invalid triage fields')
    if triage is not None and not triage['keep']:
        expected_status = 'skipped'
    elif 'needs_revision' in states:
        expected_status = 'needs_revision'
    elif triage is None or 'pending' in states:
        expected_status = 'pending'
    else:
        expected_status = 'accepted'
    require(record.get('quality_status') == expected_status, 'quality status contradicts embedded findings')
    require(type(record.get('quality_gate_passed')) is bool and
            record['quality_gate_passed'] == (expected_status == 'accepted'), 'quality gate mismatch')


def envelope(data, filename):
    record = parse_json(data)
    require(isinstance(record, dict), 'envelope must be an object')
    core = {k: v for k, v in record.items() if k not in {'bundle_id', 'created_at'}}
    require(identifier(record.get('bundle_id')) and record['bundle_id'] == fingerprint(core), 'bundle fingerprint mismatch')
    require(identifier(record.get('item_id')) and filename == record['item_id'] + '.json', 'item/filename mismatch')
    current = (record.get('builder_version') == 'handoff-v2' and
               record.get('quality_policy_version') == 'correctness-v1')
    legacy = record.get('builder_version') == 'handoff-v1' and 'quality_policy_version' not in record
    supported = (type(record.get('schema_version')) is int and record['schema_version'] == 1
                 and record.get('stage') == 'processed_extraction' and (current or legacy))
    if not supported:
        return record, 'unsupported_contract'
    require(identifier(record.get('source_content_hash')), 'source content hash missing')
    require(record.get('raw_path') == 'raw/' + filename, 'raw path does not match item')
    source = record.get('source')
    require(isinstance(source, dict), 'source missing')
    require(all(nonblank(source.get(k)) for k in ('channel', 'source_id', 'captured_at')), 'source identity/time missing')
    from datetime import datetime
    for key in ('captured_at', 'published_at'):
        if source.get(key) is not None:
            require(isinstance(source[key], str), 'source timestamp must be text')
            try:
                require(datetime.fromisoformat(source[key].replace('Z', '+00:00')).tzinfo is not None,
                        'source timestamp requires timezone')
            except ValueError:
                require(False, 'invalid source timestamp')
    for hint in ('author_hint', 'forwarded_by'):
        v = source.get(hint)
        require(v is None or isinstance(v, str) or
                (isinstance(v, dict) and all(isinstance(k, str) and isinstance(x, str) for k, x in v.items())),
                'invalid source role hint')
    _objects(record, 'sections', 'section_id')
    require(bool(record['sections']), 'section missing')
    claims = _objects(record, 'claims', 'claim_id')
    entities = _objects(record, 'entities', 'entity_id')
    require(len(claims) <= 50 and len(entities) <= 40, 'producer claim/entity limits exceeded')
    for c in claims:
        require(nonblank(c.get('statement')) and len(c['statement']) <= 1200, 'claim statement missing or too long')
        require(isinstance(c.get('kind'), str) and c['kind'] in {'fact', 'announcement', 'opinion', 'how-to', 'principle'}, 'invalid claim kind')
        require(isinstance(c.get('durability_class'), str) and c['durability_class'] in HALF_LIVES,
                'invalid claim durability')
        require(type(c.get('half_life_days')) is int and c['half_life_days'] == HALF_LIVES[c['durability_class']],
                'claim half life mismatch')
        require(type(c.get('confidence')) in {int, float} and 0 <= c['confidence'] <= 1, 'invalid claim confidence')
        require(isinstance(c.get('evidence'), (list, dict)), 'claim evidence missing')
    for e in entities:
        require(nonblank(e.get('name')) and e.get('kind') in {'person', 'organization', 'concept'}, 'invalid entity')
    _list(record, 'links')
    if 'parent' in record:
        parent = record['parent']
        require(isinstance(parent, dict) and identifier(parent.get('item_id')) and
                parent.get('raw_path') == 'raw/' + parent['item_id'] + '.json', 'invalid captured-parent reference')
    require(isinstance(record.get('inputs'), dict), 'inputs must retain producer fingerprints')
    quality(record, legacy)
    return record, 'handoff-v1' if legacy else 'handoff-v2/correctness-v1'


def evidence(record, raw_bytes=None, retained_text=None):
    raw = parse_json(raw_bytes) if raw_bytes is not None else None
    if raw is not None:
        require(isinstance(raw, dict) and raw.get('item_id') == record['item_id'], 'raw item mismatch')
        text = raw.get('text')
        require(isinstance(text, str), 'raw text missing')
        require(digest(text.encode('utf-8')) == raw.get('content_hash') == record['source_content_hash'],
                'source text fingerprint mismatch')
        raw_match = record['inputs'].get(record['raw_path']) == fingerprint(raw)
        basis = 'raw_object_match' if raw_match else 'text_match_metadata_unconfirmed'
    else:
        require(retained_text is not None, 'no matching retained evidence')
        text = retained_text
        require(digest(text.encode('utf-8')) == record['source_content_hash'], 'retained text fingerprint mismatch')
        basis = 'retained_text_only'
    require(len(text) <= 60000, 'text exceeds supported producer text-adapter limit')
    sections = {}
    for section in record['sections']:
        p = section.get('provenance')
        require(isinstance(p, dict) and p.get('item_id') == record['item_id'] and p.get('field') == 'text',
                'unsupported section provenance')
        a, b = p.get('start_char'), p.get('end_char')
        require(type(a) is int and type(b) is int and 0 <= a < b <= len(text), 'section range invalid')
        sections[section['section_id']] = (a, b)

    def anchor(span, item_required=True):
        require(isinstance(span, dict), 'span must be an object')
        require(not item_required or span.get('item_id') == record['item_id'], 'span item mismatch')
        section = span.get('section_id')
        require(isinstance(section, str) and section in sections, 'span section missing')
        a, b = span.get('start_char'), span.get('end_char')
        lo, hi = sections[section]
        require(type(a) is int and type(b) is int and lo <= a < b <= hi, 'span outside retained section')
        return {'representation_id': 'raw.text/utf8-v1', 'text_sha256': record['source_content_hash'],
                'field': 'text', 'section_id': section, 'coordinate_unit': 'unicode_code_points',
                'start_char': a, 'end_char': b, 'quote': text[a:b],
                'prefix': text[max(lo, a - 100):a], 'suffix': text[b:min(hi, b + 100)]}

    claims = []
    for claim in record['claims']:
        spans = claim['evidence']
        if isinstance(spans, dict):
            spans = [spans]
        require(0 < len(spans) <= 10, 'claim requires 1..10 evidence spans')
        anchors = [anchor(s) for s in spans]
        claims.append({'claim_id': claim['claim_id'], 'statement': claim['statement'],
                       'payload_digest': fingerprint(claim), 'anchors_digest': fingerprint(anchors),
                       'anchors': anchors})
    # Entity evidence is interpreted as a mention, never proof of authorship.
    passages = [m for m in re.finditer(r'[^\r\n]+', text) if m.group().strip()]
    entities = []
    for entity in record['entities']:
        spans = entity.get('mentions')
        require(isinstance(spans, list) and bool(spans), 'entity requires retained producer mention spans')
        aliases = entity.get('aliases', [])
        require(isinstance(aliases, list) and len(aliases) <= 10 and all(nonblank(x) for x in aliases), 'invalid entity aliases')
        anchors = []
        for span in spans:
            a = anchor(span)
            surface = span.get('surface_name', entity['name'])
            require(surface in [entity['name'], *aliases] and a['quote'] == surface, 'entity surface does not match retained mention')
            require(type(span.get('passage_id')) is int and span['passage_id'] > 0, 'invalid entity passage ID')
            pid = span['passage_id']
            require(pid <= len(passages) and passages[pid - 1].start() <= a['start_char'] < a['end_char'] <= passages[pid - 1].end(),
                    'entity passage ID does not contain retained mention')
            anchors.append(a)
        entities.append({'entity_id': entity['entity_id'], 'name': entity['name'], 'kind': entity['kind'],
                         'anchors': anchors})
    links = []
    for link in record['links']:
        require(isinstance(link, dict) and nonblank(link.get('url')), 'invalid link')
        require('item_id' not in link or link['item_id'] == record['item_id'], 'link item mismatch')
        basis_anchor = anchor(link, False)
        require(link.get('kind') in {'url', 'text_link'}, 'unsupported link kind')
        if link['kind'] == 'url':
            require(basis_anchor['quote'] == link['url'], 'literal link differs from quote')
        links.append({'url': link['url'], 'kind': link['kind'], 'anchor': basis_anchor})
    return {'text': text, 'raw_basis': basis, 'claims': claims, 'entities': entities, 'links': links}

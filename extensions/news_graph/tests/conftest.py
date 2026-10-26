from copy import deepcopy
import json

import pytest

from newsmap_sidecar.common import fingerprint, digest
from newsmap_sidecar.storage import Database


def fixture_record(label='announcement', *, version=2, accepted=True, text=None):
    """Synthetic producer-shaped data, never a claim of a live model run."""
    text = text or 'Синтетический тест 🧭 cafe\u0301. Model Aurora reports 42 points.\nNot measured on mobile devices.'
    item = digest(label.encode())
    section = item + ':section:1'
    raw = {'item_id': item, 'text': text, 'content_hash': digest(text.encode()), 'kind': 'text',
           'channel': 'file', 'source_id': 'synthetic:' + label, 'source_name': 'Synthetic laboratory',
           'captured_at': '2026-10-26T10:00:00+00:00', 'published_at': None,
           'author_hint': {'name': 'Alex Example'}, 'forwarded_by': None, 'url': None,
           'channel_metadata': None, 'attachments': []}
    spans = [{'item_id': item, 'section_id': section, 'start_char': a, 'end_char': b}
             for a, b in [(0, text.index('\n')), (text.index('\n') + 1, len(text))]]
    claim_id = item + ':claim:1'
    claims = [{'claim_id': claim_id, 'statement': 'Aurora reports 42 points; mobile not measured.',
               'kind': 'announcement', 'confidence': 0.9, 'durability_class': 'news', 'half_life_days': 30,
               'evidence': spans}]
    record = {'schema_version': 1, 'stage': 'processed_extraction', 'builder_version': 'handoff-v' + str(version),
              'item_id': item, 'source_content_hash': raw['content_hash'], 'raw_path': f'raw/{item}.json',
              'source': {k: raw[k] for k in ('channel', 'source_id', 'source_name', 'captured_at', 'published_at',
                                           'author_hint', 'forwarded_by', 'url', 'kind')},
              'sections': [{'section_id': section, 'kind': 'text', 'provenance': {
                  'item_id': item, 'field': 'text', 'start_char': 0, 'end_char': len(text)}}],
              'claims': claims, 'entities': [], 'links': [], 'attachments': [],
              'triage': {'keep': True, 'kind': 'post', 'durability_class': 'news', 'split_digest': False, 'reason': 'Synthetic'},
              'reviews': {'claims': {'status': 'accepted', 'claims': [
                  {'claim_id': claim_id, 'verdict': 'supported', 'issues': [], 'reason': 'Synthetic fixture judgment'}],
                  'omitted_concepts': [], 'summary': 'Synthetic fixture review'},
                  'entities': {'status': 'accepted', 'entities': [], 'omitted_entities': [], 'summary': 'Synthetic fixture review'}},
              'quality_status': 'accepted', 'quality_gate_passed': True,
              'inputs': {f'raw/{item}.json': fingerprint(raw)}, 'created_at': '2026-10-26T10:01:00+00:00'}
    if version == 2:
        record['quality_policy_version'] = 'correctness-v1'
    if not accepted:
        record['reviews']['claims'] = {'status': 'pending'}
        record['quality_status'], record['quality_gate_passed'] = 'pending', False
    reseal(record)
    return record, raw


def reseal(record):
    record['bundle_id'] = fingerprint({k: v for k, v in record.items() if k not in {'created_at', 'bundle_id'}})
    return record


def publish(vault, record, raw, *, ensure_ascii=False, indent=2):
    (vault / 'processed').mkdir(parents=True, exist_ok=True)
    (vault / 'raw').mkdir(exist_ok=True)
    p = vault / 'processed' / (record['item_id'] + '.json')
    p.write_text(json.dumps(record, ensure_ascii=ensure_ascii, indent=indent))
    if raw is not None:
        (vault / record['raw_path']).write_text(json.dumps(raw, ensure_ascii=ensure_ascii, indent=indent))
    return p


@pytest.fixture
def workspace(tmp_path):
    vault = tmp_path / 'vault'
    vault.mkdir()
    db = Database(tmp_path / 'authority' / 'news.sqlite', create=True)
    db.register('demo', vault, actor='fixture_operator')
    yield db, vault
    db.close()

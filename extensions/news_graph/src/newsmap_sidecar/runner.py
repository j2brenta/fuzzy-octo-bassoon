"""Bounded synthetic runner through existing producer functions, no paid inference.

python -m newsmap_sidecar.runner MANIFEST_JSON FRESH_OUTPUT
Runtime host imports are lazy. The normal viewer/import remains stdlib-only.
"""
import json
from pathlib import Path
import sys

from .common import require, encoded, fingerprint, outside_repositories, parse_json
from .metering import Ledger, MeteredModel


class SyntheticWire:
    """Labelled mock, data-derived replies; not a model quality benchmark."""
    offline_only = True
    def __init__(self, scenario='accepted'):
        require(scenario in {'accepted', 'invalid_then_repair', 'judge_reject'}, 'unknown synthetic scenario')
        self.scenario, self.calls = scenario, []

    def send(self, aid, spec_bytes):
        spec = json.loads(spec_bytes)
        stage = spec['context']['stage']
        previous = sum(s == stage for _, s, _ in self.calls)
        self.calls.append((aid, stage, spec['payload']))
        content = spec['payload']['messages'][1]['content']
        if stage == 'triage':
            answer = {'keep': True, 'kind': 'post', 'durability_class': 'news', 'split_digest': False,
                      'reason': 'SIMULATED_OFFLINE: synthetic producer validation'}
        else:
            data = json.loads(content)
            passages = data.get('passages', [])
            if stage == 'automation':
                from .review import CONTEXT_FIELDS
                claims = {c['id']:c for c in data['claims']}
                assignments = []
                for item in data['items']:
                    text = ' '.join(claims[c]['statement'] for c in item['claims'])
                    topic = 'evaluation' if any(w in text.casefold() for w in ['points','score','benchmark']) else 'models'
                    assignments.append({'item':item['id'], 'topic':topic,
                                        'story':(text.split()[0] + ' / synthetic development') if text else None,
                                        'rationale':'SIMULATED_OFFLINE data-derived grouping'})
                relations=[]
                for left,right in data['candidate_pairs']:
                    same = claims[left]['statement'] == claims[right]['statement']
                    relations.append({'left':left,'right':right,'relation':'DUPLICATES' if same else 'not_comparable',
                                      'context':{k:'unknown in synthetic fixture' for k in CONTEXT_FIELDS},
                                      'rationale':'SIMULATED_OFFLINE exact wording comparison; no independence claim',
                                      'evidence':[left,right]})
                answer={'assignments':assignments,'relations':relations}
            elif stage == 'extract' or 'previous_claims' in data:
                selected = passages[:2]
                answer = {'claims': [{'statement': ' '.join(p['text'] for p in selected),
                                     'kind': 'announcement', 'durability_class': 'news', 'confidence': .8,
                                     'evidence_passage_ids': [p['id'] for p in selected]}]}
            elif stage == 'judge':
                reject = self.scenario == 'judge_reject'
                answer = {'claims': [{'claim_id': c['claim_id'], 'verdict': 'unsupported' if reject else 'supported',
                                      'issues': ['unsupported'] if reject else [], 'reason': 'SIMULATED_OFFLINE judge'}
                                     for c in data['claims']], 'omitted_concepts': [], 'summary': 'SIMULATED_OFFLINE'}
            elif stage == 'entities':
                # Empty extraction is valid for a fixture with no named people/orgs.
                answer = {'entities': []}
            elif stage == 'entity_review':
                answer = {'entities': [{'entity_id': e['entity_id'], 'verdict': 'supported', 'reason': 'SIMULATED_OFFLINE'}
                                       for e in data['entities']], 'omitted_entities': [], 'summary': 'SIMULATED_OFFLINE'}
            else:
                raise ValueError('unsupported synthetic stage')
        text = json.dumps(answer, ensure_ascii=False)
        if self.scenario == 'invalid_then_repair' and stage == 'extract' and previous == 0:
            text = '{invalid synthetic response'
        return encoded({'id': 'synthetic-' + aid, 'model': spec['model'],
                        'usage': {'prompt_tokens': 50, 'completion_tokens': 40, 'total_tokens': 90},
                        'choices': [{'finish_reason': 'stop', 'message': {'content': text}}]})


def execute_producer(raw_path, client_for):
    """No substitute extraction, quality, citation repair, coverage or packaging."""
    from engine.parse import parse_capture
    from engine.triage import triage_capture
    from engine.extract import extract_capture
    from engine.quality import quality_capture
    from engine.entities import extract_entities, review_entities
    from engine.package import build_extraction
    completed = []
    try:
        parse_capture(raw_path); completed.append('parse')
        triage = triage_capture(raw_path, client_for('triage')); completed.append('triage')
        if not triage.labels['keep']: return {'status': 'skipped', 'completed': completed}
        extract_capture(raw_path, client_for('extract')); completed.append('extract')
        quality_capture(raw_path, client_for('judge')); completed.append('quality_with_original_repair_branches')
        entities = extract_entities(raw_path, client_for('entities')); completed.append('entities')
        review_entities(raw_path, client_for('entity_review'), entities); completed.append('entity_review')
        package = build_extraction(raw_path); completed.append('package')
        return {'status': package.record['quality_status'], 'package': str(package.path), 'completed': completed}
    except Exception as exc:
        # Partial producer artifacts survive cap exhaustion and parser/quality failure.
        return {'status': 'incomplete', 'completed': completed, 'error_type': type(exc).__name__}


def run(manifest, output):
    require(isinstance(manifest, dict) and manifest.get('mode') == 'OFFLINE_SYNTHETIC', 'only explicit OFFLINE_SYNTHETIC mode is enabled')
    require(manifest.get('paid_cap_micro', 0) == 0, 'paid cap remains zero')
    require(manifest.get('model') == 'fixture-model', 'explicit fixture model required; no fallback')
    texts = manifest.get('texts')
    require(isinstance(texts, list) and 1 <= len(texts) <= 2 and all(isinstance(t,str) and 0 < len(t.encode()) <= 16384 for t in texts), 'one or two bounded synthetic texts required')
    require(isinstance(manifest.get('project'), str) and 0 < len(manifest['project']) <= 128, 'explicit project ID required')
    output = outside_repositories(output)
    require(not output.exists(), 'runner requires a fresh isolated output; existing producer vaults are never written')
    output.mkdir(parents=True, mode=0o700)
    (output / 'manifest.json').write_bytes(encoded(manifest))
    from store.raw import capture_text
    from .storage import Database
    from .intake import import_batch
    from .export import export_view
    wire = SyntheticWire(manifest.get('scenario', 'accepted'))
    # Project authority is explicitly supplied outside this stage's fresh output.
    ledger = Ledger(Path(manifest['ledger']), manifest['project'], previous_micro=manifest.get('previous_micro', 0))
    ledger.stage(manifest['stage'], cap_micro=manifest.get('simulation_cap_micro', 0),
                 max_attempts=manifest.get('max_attempts', 10))
    results, clients = [], []
    try:
        vault = output / 'producer-vault'
        for n, text in enumerate(texts):
            capture = capture_text(text, vault, channel='file', source_id='synthetic:' + str(n),
                                   source_name='SIMULATED_OFFLINE', captured_at='2026-10-26T10:00:00+00:00')
            def client_for(stage):
                client = MeteredModel(ledger, manifest['stage'], capture.item_id, manifest['model'], wire,
                                    context={'stage': stage, 'manifest': fingerprint(manifest), 'source_hash': fingerprint(text)})
                clients.append(client)
                return client
            results.append(execute_producer(capture.path, client_for))
        db = Database(output / 'snapshots' / 'news.sqlite', create=True)
        try:
            db.register('offline', vault, actor='fixture_operator')
            if (vault / 'processed').exists():
                import_batch(db, 'offline', vault)
            from .automation import assess
            automation = assess(db, MeteredModel(ledger, manifest['stage'], 'map', manifest['model'], wire,
                                context={'stage':'automation','manifest':fingerprint(manifest)}), mode=manifest.get('workflow','MANUAL'))
            exported = export_view(db, output / 'viewer')
        finally: db.close()
        report = {'execution_status': 'PASS', 'requirement_status': 'PASS' if all(r['status']=='accepted' for r in results) else 'INCOMPLETE',
                  'origin': 'SIMULATED_OFFLINE; no live model evaluation', 'automation':automation, 'results': results, 'ledger': ledger.report(),
                  'cached_responses': sum(c.cache_hits for c in clients),
                  'wire_attempts': [aid for aid, _, _ in wire.calls], 'export': exported,
                  'paid_calls': 0, 'gpu_used': False}
        (output / 'run-report.json').write_bytes(encoded(report))
        return report
    finally: ledger.close()


def main():
    require(len(sys.argv)==3, 'usage: python -m newsmap_sidecar.runner MANIFEST FRESH_OUTPUT')
    path = Path(sys.argv[1]); require(path.stat().st_size <= 65536, 'manifest exceeds 64 KiB')
    report = run(parse_json(path.read_bytes()), sys.argv[2])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['requirement_status']=='PASS' else 2


if __name__ == '__main__': raise SystemExit(main())

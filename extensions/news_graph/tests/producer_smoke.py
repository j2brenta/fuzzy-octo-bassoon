"""Integration harness: real producer stages with explicitly simulated model replies.

This development-only harness imports the host; the sidecar runtime never does.
Usage: PYTHON_DOTENV_DISABLED=1 python producer_smoke.py REPO NEW_SCRATCH_DIRECTORY
"""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch


def main():
    repo = Path(sys.argv[1]).resolve()
    scratch = Path(sys.argv[2]).resolve()
    sys.path[:0] = [str(repo), str(repo / 'extensions/news_graph/src')]
    from newsmap_sidecar.common import outside_repositories, require
    from newsmap_sidecar.storage import Database
    from newsmap_sidecar.intake import import_batch, packages
    from newsmap_sidecar import review as rv
    from newsmap_sidecar.export import export_view
    from engine.parse import parse_capture
    from engine.triage import triage_capture
    from engine.extract import extract_capture
    from engine.judge import judge_capture
    from engine.quality import quality_capture
    from engine.entities import extract_entities, review_entities
    from engine.package import build_extraction
    from engine.placement import apply_storage_stub
    from engine.llm import ModelReply
    require(os.environ.get('PYTHON_DOTENV_DISABLED') == '1', 'run with PYTHON_DOTENV_DISABLED=1')
    outside_repositories(scratch)
    require(not scratch.exists(), 'smoke requires fresh scratch directory')
    scratch.mkdir(mode=0o700, parents=True)
    source = scratch / 'synthetic-news.txt'
    text = ('🧭 Alex Example announces Aurora, a synthetic local AI model with 42 Atlas points.\n'
            'Desktop only; mobile performance was not measured. This is a rights-cleared synthetic test.\n'
            'Reference: https://example.invalid/aurora')
    source.write_text(text)
    vault = scratch / 'producer-vault'
    # Existing, unmodified, supported file-import CLI; no substitute adapter.
    command = [sys.executable, '-m', 'channels.importer', str(source), '--vault', str(vault),
               '--source-name', 'Clearly synthetic producer smoke']
    imported = subprocess.run(command, cwd=repo, capture_output=True, text=True, check=True)
    (scratch / 'capture-cli.txt').write_text(imported.stdout)
    raw_path, = (vault / 'raw').glob('*.json')
    captured_bytes = raw_path.read_bytes()

    class FixtureReplies:
        provider = 'fixture'
        model_provider = 'fixture'
        model_name = 'explicit-synthetic-responses'
        def __init__(self, payload):
            self.payload = payload
            self.calls = 0
        def complete_json(self, system_prompt, content):
            self.calls += 1
            require(self.calls == 1, 'unexpected additional model request in smoke')
            return ModelReply(self.payload, actual_provider='fixture', actual_model=self.model_name)

    with patch('socket.socket.connect', side_effect=AssertionError('network disabled in smoke')):
        parsed = parse_capture(raw_path)
        triage = FixtureReplies({'keep': True, 'kind': 'post', 'durability_class': 'news',
                                'split_digest': False, 'reason': 'Clearly synthetic fixture'})
        triage_capture(raw_path, triage)
        extractor = FixtureReplies({'claims': [{'statement': 'Aurora reports 42 Atlas points on desktop; mobile is unmeasured.',
                          'kind': 'announcement', 'durability_class': 'news', 'confidence': .8, 'evidence_passage_ids': [1, 2]}]})
        extracted = extract_capture(raw_path, extractor)
        judge = FixtureReplies({'claims': [{'claim_id': c['claim_id'], 'verdict': 'supported', 'issues': [],
                                            'reason': 'Synthetic source-faithfulness fixture'} for c in extracted.claims],
                                'omitted_concepts': [], 'summary': 'Synthetic judge response, not model evaluation'})
        judged = judge_capture(raw_path, judge)
        checked = quality_capture(raw_path, judge)
        entity_model = FixtureReplies({'entities': [{'name': 'Alex Example', 'kind': 'person', 'evidence_passage_ids': [1]}]})
        entities = extract_entities(raw_path, entity_model)
        entity_judge = FixtureReplies({'entities': [{'entity_id': e['entity_id'], 'verdict': 'supported',
                                                    'reason': 'Exact synthetic mention'} for e in entities.entities],
                                      'omitted_entities': [], 'summary': 'Synthetic entity review'})
        review_entities(raw_path, entity_judge, entities)
        packaged = build_extraction(raw_path)
        placement = apply_storage_stub(packaged.path)
        assert raw_path.read_bytes() == captured_bytes
        assert packaged.record['quality_gate_passed'] is True
        assert placement['graph_applied'] is False
        vault_before = {str(p.relative_to(vault)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in vault.rglob('*') if p.is_file()}
        db = Database(scratch / 'authority' / 'news.sqlite', create=True)
        try:
            db.register('producer-smoke', vault, actor='fixture_operator')
            receipt = import_batch(db, 'producer-smoke', vault)
            assert receipt['valid'] == 1
            row, = packages(db)
            assert row['eligible'] and len(row['evidence']['claims'][0]['anchors']) == 2
            assert row['evidence']['entities'][0]['anchors'][0]['quote'] == 'Alex Example'
            ref = rv.reference(row)
            story = rv.story_create(db, 'Synthetic producer → evidence map', 'models',
                                   'Fixture walkthrough of the existing producer route', [ref], 'fixture_operator')
            rv.assign(db, 'story_assign', ref, story, 'Synthetic manual story assignment', [ref], 'fixture_operator')
            exported = export_view(db, scratch / 'viewer')
            backup = db.backup(scratch / 'backup' / 'news.sqlite')
            report = {
                'sample_origin': 'rights-cleared synthetic text created by this harness',
                'sample_items': 1, 'live_end_to_end': 'blocked_or_unverified',
                'live_model_reason': 'external model runtime/access and budget not authorized; no credentials loaded',
                'stages': {
                    'capture_import': 'executed: existing channels.importer CLI',
                    'parse': 'executed: existing parse_capture',
                    'triage': 'executed producer function; simulated model reply',
                    'extract': 'executed producer function and span validation; simulated model reply',
                    'judge': 'executed producer function and quality validation; simulated model reply',
                    'quality': 'executed: reused matching judge artifact; no repair necessary',
                    'entities': 'executed producer extraction and review; simulated model replies',
                    'package': 'executed: existing build_extraction and storage-only placement',
                    'sidecar_import': 'executed: read-only input, committed SQLite',
                    'story_and_map': 'executed: explicit fixture_operator assignments and static graph export'},
                'fixture_model_calls': sum(x.calls for x in (triage, extractor, judge, entity_model, entity_judge)),
                'external_model_calls': 0, 'sidecar': receipt, 'export': exported, 'backup': backup,
                'raw_sha256': hashlib.sha256(captured_bytes).hexdigest(),
                'bundle_id': packaged.record['bundle_id'], 'claim_count': len(extracted.claims),
                'entity_count': len(entities.entities), 'human_usefulness_walkthrough': 'pending',
            }
        finally:
            db.close()
        vault_after = {str(p.relative_to(vault)): hashlib.sha256(p.read_bytes()).hexdigest()
                       for p in vault.rglob('*') if p.is_file()}
        assert vault_before == vault_after
        report['sidecar_vault_unchanged'] = True
    (scratch / 'producer-smoke.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

import json
import pytest
from newsmap_sidecar.runner import run


@pytest.mark.parametrize('scenario,expected_calls,expected_status', [
    ('accepted',5,'accepted'), ('invalid_then_repair',6,'accepted'), ('judge_reject',6,'needs_revision')])
def test_original_stages_parser_repairs_and_quality_branch(tmp_path, monkeypatch, scenario, expected_calls, expected_status):
    def no_network(*args, **kwargs): raise AssertionError('product network forbidden')
    monkeypatch.setattr('socket.socket.connect', no_network)
    manifest = {'mode':'OFFLINE_SYNTHETIC', 'project':'fixture', 'stage':'first', 'model':'fixture-model',
                'paid_cap_micro':0, 'simulation_cap_micro':1_000_000, 'scenario':scenario,
                'ledger':str(tmp_path/'ledger'/'budget.sqlite'),
                'texts':['Synthetic model reports 42 points on desktop.\nMobile was not measured.']}
    result = run(manifest,tmp_path/'run')
    assert result['results'][0]['status'] == expected_status
    assert len(result['wire_attempts']) == expected_calls
    assert result['wire_attempts'] == [r['id'] for r in result['ledger']['attempts']]
    assert all(r['charge'] == 130 for r in result['ledger']['attempts'])
    assert result['paid_calls'] == 0
    assert result['results'][0]['completed'][-1] == 'package'
    record = json.loads(open(result['results'][0]['package']).read())
    assert record['quality_gate_passed'] == (expected_status == 'accepted')


def test_cap_exhaustion_preserves_prior_stage_artifacts(tmp_path):
    manifest={'mode':'OFFLINE_SYNTHETIC','project':'fixture','stage':'first','model':'fixture-model',
              'simulation_cap_micro':1_000_000, 'max_attempts':2,
              'ledger':str(tmp_path/'ledger'/'budget.sqlite'), 'texts':['Synthetic report.\nBounded fixture.']}
    result=run(manifest,tmp_path/'run')
    assert result['results'][0]['status']=='incomplete'
    assert result['results'][0]['completed']==['parse','triage','extract']
    assert list((tmp_path/'run'/'producer-vault'/'extractions').glob('*.json'))
    assert len(result['wire_attempts']) == 2

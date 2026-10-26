import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from newsmap_sidecar.common import BusyError, NewsmapError
from newsmap_sidecar.storage import Database
from newsmap_sidecar.intake import import_batch, packages
from conftest import fixture_record, publish, reseal


def test_writer_contention_is_bounded_and_distinct(workspace):
    db, vault = workspace
    r, raw = fixture_record()
    publish(vault, r, raw)
    import_batch(db, 'demo', vault)
    token = packages(db)[0]['head_token']
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')}
    code = '''import sys
from newsmap_sidecar.storage import Database
db=Database(sys.argv[1])
with db.transaction():
    db.event('fixture_lock',{},'fixture_operator')
    print('locked',flush=True)
    sys.stdin.readline()
db.close()
'''
    writer = subprocess.Popen([sys.executable, '-c', code, str(db.path)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
    second = None
    try:
        assert writer.stdout.readline().strip() == 'locked'
        second = Database(db.path, busy_ms=40)
        before = second.conn.execute('SELECT count(*) FROM events').fetchone()[0]
        with pytest.raises(BusyError):
            second.select('demo', r['item_id'], r['bundle_id'], token, 'fixture_operator')
        with pytest.raises(BusyError):
            import_batch(second, 'demo', vault)
        assert second.conn.execute('SELECT count(*) FROM events').fetchone()[0] == before
        assert not second.conn.in_transaction
    finally:
        writer.communicate('\n', timeout=5)
        if second:
            second.close()
    assert writer.returncode == 0
    assert db.select('demo', r['item_id'], r['bundle_id'], token, 'fixture_operator')['bundle'] == r['bundle_id']


def test_import_order_neutral_with_explicit_selection(workspace, tmp_path):
    a, vault = workspace
    old, raw = fixture_record(accepted=False)
    new, _ = fixture_record()
    b = Database(tmp_path / 'other-authority' / 'news.sqlite', create=True)
    try:
        b.register('demo', vault, actor='fixture_operator')
        for db, sequence in ((a, [old, new]), (b, [new, old])):
            for record in sequence:
                publish(vault, record, raw)
                import_batch(db, 'demo', vault)
            head = packages(db)[0]['head_token']
            db.select('demo', new['item_id'], new['bundle_id'], head, 'fixture_operator')
        def scientific_view(db):
            return [{k: r[k] for k in ('namespace', 'item', 'bundle', 'record', 'admission', 'evidence', 'selected', 'eligible')}
                    for r in packages(db)]
        assert scientific_view(a) == scientific_view(b)
    finally:
        b.close()


def test_ordered_nonmonotonic_spans_stay_ordered(workspace):
    db, vault = workspace
    r, raw = fixture_record(text='👨\u200d💻 cafe\u0301 announcement\nNot tested elsewhere.')
    r['claims'][0]['evidence'].reverse()
    reseal(r)
    publish(vault, r, raw)
    import_batch(db, 'demo', vault)
    anchors = packages(db)[0]['evidence']['claims'][0]['anchors']
    assert anchors[0]['quote'] == 'Not tested elsewhere.'
    assert anchors[1]['quote'].startswith('👨\u200d💻 cafe\u0301')


def test_overlapping_authority_backup_and_repo_paths_rejected(workspace, tmp_path):
    db, vault = workspace
    with pytest.raises(NewsmapError):
        db.backup(vault / 'backup.sqlite')
    with pytest.raises(NewsmapError):
        db.backup(db.path.parent / 'backup.sqlite')
    with pytest.raises(NewsmapError):
        db.register('overlap', db.path.parent, actor='fixture_operator')
    fake_repo = tmp_path / 'repo'
    (fake_repo / '.git').mkdir(parents=True)
    with pytest.raises(NewsmapError):
        Database(fake_repo / 'state.sqlite', create=True)


def test_standalone_cli_without_site_or_host_packages(tmp_path):
    launcher = Path(__file__).resolve().parents[1] / 'run.py'
    db = tmp_path / 'isolated-state' / 'news.sqlite'
    result = subprocess.run([sys.executable, '-S', str(launcher), '--db', str(db), 'init'],
                            capture_output=True, text=True, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['integrity'] == 'ok'


def test_two_cli_writers_cannot_reuse_selection_token(workspace):
    db, vault = workspace
    r, raw = fixture_record(); publish(vault, r, raw); import_batch(db, 'demo', vault)
    token = packages(db)[0]['head_token']
    launcher = Path(__file__).resolve().parents[1] / 'run.py'
    command = [sys.executable, str(launcher), '--db', str(db.path), 'select', '--namespace', 'demo',
               '--item', r['item_id'], '--bundle', r['bundle_id'], '--expect-current', token, '--actor', 'fixture_operator']
    processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    outputs = [p.communicate(timeout=10) for p in processes]
    assert sorted(p.returncode for p in processes) == [0, 2]
    failed = next(output for p, output in zip(processes, outputs) if p.returncode == 2)
    assert json.loads(failed[0])['error'] == 'stale'

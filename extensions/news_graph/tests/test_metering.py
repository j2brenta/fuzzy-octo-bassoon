"""Offline accounting and physical gate regressions, never live inference."""
from dataclasses import replace
import json
import pytest
from newsmap_sidecar.metering import Ledger, MeteredModel, BoundPolicy
from newsmap_sidecar.common import NewsmapError, encoded


class Wire:
    offline_only = True
    def __init__(self, alter=None): self.calls = []; self.alter = alter
    def send(self, aid, raw):
        spec = json.loads(raw); self.calls.append((aid, raw))
        response = {'id': 'fixture-' + aid, 'model': spec['model'],
                    'usage': {'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15},
                    'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]}
        if self.alter: self.alter(response)
        return encoded(response)


@pytest.fixture
def ledger(tmp_path):
    db = Ledger(tmp_path / 'meter' / 'ledger.sqlite', 'synthetic-project')
    db.stage('offline', cap_micro=1_000_000)
    yield db
    db.close()


def model(db, wire=None, **kw):
    return MeteredModel(db, 'offline', 'item-1', 'fixture-model', wire or Wire(), **kw)


def test_zero_default_and_exact_wire_spec_and_cache(ledger):
    ledger.stage('zero')
    client = MeteredModel(ledger, 'zero', 'i', 'fixture-model', Wire())
    with pytest.raises(NewsmapError, match='cap'): client.complete_json('s','c')
    wire = Wire(); client = model(ledger, wire)
    assert client.complete_json('s', 'c').data == {'ok': True}
    aid, spec_bytes = wire.calls[0]; spec = json.loads(spec_bytes)
    assert spec['payload']['max_tokens'] == 2048
    row = ledger.conn.execute('SELECT * FROM attempts WHERE id=?', (aid,)).fetchone()
    assert row['spec'] == spec_bytes and row['charge'] == 20
    assert client.complete_json('s','c').data == {'ok': True}
    assert len(wire.calls) == 1 and client.cache_hits == 1
    client.complete_json('changed prompt','c')
    assert len(wire.calls) == 2
    assert ledger.reconcile(aid) == 20 and ledger.exposure() == 40


@pytest.mark.parametrize('kind', ['invalid', 'empty', 'truncated', 'refusal'])
def test_billable_metadata_before_actual_host_parser(ledger, kind):
    from engine.llm import ModelOutputError, ModelError
    def alter(r):
        r['choices'][0]['message']['content'] = '{invalid' if kind=='invalid' else ''
        if kind == 'truncated': r['choices'][0]['finish_reason'] = 'length'
        if kind == 'refusal': r['choices'][0]['message']['refusal'] = 'fixture refusal'
    client = model(ledger, Wire(alter))
    with pytest.raises((ModelOutputError, ModelError)): client.complete_json('s','c')
    assert ledger.report()['attempts'][0]['charge'] == 20
    assert ledger.conn.execute('SELECT count(*) FROM cache').fetchone()[0] == 0


def test_timeout_restart_unknown_cannot_cancel_or_settle_zero(ledger):
    wire = Wire()
    def timeout(aid, raw): wire.calls.append((aid, raw)); raise TimeoutError('fixture')
    wire.send = timeout
    with pytest.raises(TimeoutError): model(ledger, wire).complete_json('s','c')
    aid = wire.calls[0][0]
    reopened = Ledger(ledger.path, 'synthetic-project')
    try:
        assert reopened.report()['attempts'][0]['accounting_status'] == 'UNKNOWN_BILLABLE'
        with pytest.raises(NewsmapError): reopened.cancel(aid)
        with pytest.raises(NewsmapError, match='receipt'): reopened.reconcile(aid)
        with pytest.raises(NewsmapError, match='unresolved'): model(reopened).complete_json('s','different')
    finally: reopened.close()


def test_forged_spec_and_second_dispatch_refused_before_wire(ledger):
    policy = BoundPolicy(); wire = Wire()
    payload = {'model':'fixture-model','messages':[{'role':'user','content':'hello'}],
               'temperature':0,'response_format':{'type':'json_object'},'max_tokens':2048}
    raw = policy.spec(payload,model='fixture-model',context={})
    forged = json.loads(raw); forged['input_bound'] = 0
    with pytest.raises(NewsmapError, match='bound mismatch'): ledger.reserve('offline','i',encoded(forged))
    aid = ledger.reserve('offline','i',raw)
    with pytest.raises(NewsmapError, match='specification mismatch'): ledger.dispatch(aid, raw+b' ', wire)
    assert not wire.calls
    ledger.dispatch(aid,raw,wire)
    with pytest.raises(NewsmapError, match='already dispatched'): ledger.dispatch(aid,raw,wire)
    assert len(wire.calls) == 1


def test_overrun_recorded_freezes_project_and_wrong_model_keeps_hold(ledger):
    def overrun(r): r['usage'] = {'prompt_tokens':10000,'completion_tokens':5000,'total_tokens':15000}
    model(ledger,Wire(overrun)).complete_json('s','c')
    report = ledger.report()
    assert report['exposure_micro'] == 20000 and report['project']['frozen'] == 1
    with pytest.raises(NewsmapError,match='frozen'): model(ledger).complete_json('s','different')


def test_wrong_receipt_does_not_release_reserve(ledger):
    wire = Wire(lambda r: r.update(model='wrong'))
    with pytest.raises(NewsmapError,match='identity'): model(ledger,wire).complete_json('s','c')
    a = ledger.report()['attempts'][0]
    assert a['charge'] is None and ledger.exposure() == a['reserved']


def test_attempt_caps_stage_cannot_reset_project_or_prior(ledger):
    client = model(ledger)
    for n in range(10): client.complete_json('s',str(n))
    with pytest.raises(NewsmapError,match='attempt cap'): client.complete_json('s','11')
    ledger.stage('offline',cap_micro=1_000_000)
    with pytest.raises(NewsmapError,match='attempt cap'): client.complete_json('s','12')
    ledger.stage('second',cap_micro=0)
    assert ledger.exposure() == 200
    with pytest.raises(NewsmapError,match='prior'): Ledger(ledger.path,'synthetic-project',previous_micro=1)


def test_competing_connection_preserves_single_reservation(ledger):
    policy=BoundPolicy(); payload={'model':'fixture-model','messages':[], 'temperature':0,'response_format':{'type':'json_object'},'max_tokens':2048}
    spec=policy.spec(payload,model='fixture-model',context={})
    other=Ledger(ledger.path,'synthetic-project')
    try:
        aid=ledger.reserve('offline','i',spec)
        with pytest.raises(NewsmapError,match='concurrent'): other.reserve('offline','i',spec)
        ledger.cancel(aid)
        other.reserve('offline','i',spec)
    finally: other.close()


def test_actual_loopback_request_is_identical_to_reserved_payload(ledger):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from newsmap_sidecar.metering import LoopbackWire
    observed=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            body=self.rfile.read(int(self.headers['Content-Length']))
            observed.append((self.headers['X-Fixture-Attempt'],body))
            payload=json.loads(body)
            reply=encoded({'id':'local-'+observed[-1][0], 'model':payload['model'],
                           'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15},
                           'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}]})
            self.send_response(200);self.send_header('Content-Length',str(len(reply)));self.end_headers();self.wfile.write(reply)
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    Thread(target=server.serve_forever,daemon=True).start()
    try:
        client=model(ledger,LoopbackWire(server.server_port))
        client.complete_json('bounded system','synthetic user')
        client.complete_json('bounded system','synthetic user')
        assert len(observed)==1
        aid,body=observed[0]
        row=ledger.conn.execute('SELECT spec FROM attempts WHERE id=?',(aid,)).fetchone()
        assert body==encoded(json.loads(row[0])['payload'])
    finally:server.shutdown();server.server_close()


def test_real_competing_workers_only_one_can_reserve(ledger):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    barrier=Barrier(2)
    payload={'model':'fixture-model','messages':[],'temperature':0,'response_format':{'type':'json_object'},'max_tokens':2048}
    spec=BoundPolicy().spec(payload,model='fixture-model',context={})
    def worker():
        db=Ledger(ledger.path,'synthetic-project')
        try:
            barrier.wait(timeout=5)
            try:db.reserve('offline','i',spec);return 'reserved'
            except NewsmapError:return 'refused'
        finally:db.close()
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:worker(),range(2)))
    assert sorted(results)==['refused','reserved']


def test_saved_response_recovery_is_idempotent(ledger):
    client=model(ledger)
    original=ledger.reconcile
    ledger.reconcile=lambda aid: (_ for _ in ()).throw(InterruptedError('after receipt persistence'))
    with pytest.raises(InterruptedError):client.complete_json('s','c')
    ledger.reconcile=original
    aid=ledger.report()['attempts'][0]['id']
    assert ledger.reconcile(aid)==20
    assert ledger.reconcile(aid)==20 and ledger.exposure()==20


def test_shared_lean_breach_trace_matches_concrete_accounting(ledger):
    from pathlib import Path
    trace=json.loads((Path(__file__).parents[1]/'proofs'/'trace.json').read_text())
    def overrun(r): r['usage']={'prompt_tokens':10000,'completion_tokens':5000,'total_tokens':15000}
    assert ledger.exposure()==trace['settled']
    model(ledger,Wire(overrun)).complete_json('s','c')
    report=ledger.report();attempt=report['attempts'][0]
    assert attempt['reserved']==trace['held'] and attempt['charge']==trace['bill']
    assert report['exposure_micro']==trace['bill'] and report['project']['frozen']==1


def test_unknown_billable_category_keeps_exposure(ledger):
    def unexpected(r): r['usage']['extra_billable_tokens']=123
    with pytest.raises(NewsmapError,match='unknown billing'): model(ledger,Wire(unexpected)).complete_json('s','c')
    assert ledger.report()['attempts'][0]['charge'] is None


@pytest.mark.parametrize('phase', ['reserve','dispatch'])
def test_hard_process_exit_preserves_durable_gate(ledger, phase):
    import subprocess,sys
    script='''
import os,sys
from newsmap_sidecar.metering import Ledger,BoundPolicy
p={'model':'fixture-model','messages':[],'temperature':0,'response_format':{'type':'json_object'},'max_tokens':2048}
db=Ledger(sys.argv[1],'synthetic-project')
spec=BoundPolicy().spec(p,model='fixture-model',context={})
aid=db.reserve('offline','i',spec)
if sys.argv[2]=='reserve': os._exit(23)
class Wire:
 offline_only=True
 def send(self,aid,spec): os._exit(23)
db.dispatch(aid,spec,Wire())
'''
    result=subprocess.run([sys.executable,'-c',script,str(ledger.path),phase],timeout=5)
    assert result.returncode==23
    row=ledger.report()['attempts'][0]
    if phase=='reserve':
        assert row['state']=='reserved';ledger.cancel(row['id']);assert ledger.exposure()==0
    else:
        assert row['accounting_status']=='UNKNOWN_BILLABLE'
        with pytest.raises(NewsmapError):ledger.cancel(row['id'])
        with pytest.raises(NewsmapError):ledger.reconcile(row['id'])
        assert ledger.exposure()==row['reserved']

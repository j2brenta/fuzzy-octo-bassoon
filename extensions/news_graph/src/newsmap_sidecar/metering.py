"""Opt-in, offline physical-attempt gate. No credentials or paid transport.

Amounts are integer micro-USD estimates, not invoices. Fixture charges are explicitly
simulated. A future live transport needs separate authorization and verified tariff,
tokenizer/envelope and reasoning bounds; this module does not enable one.
"""
from contextlib import contextmanager
from dataclasses import dataclass, asdict
import json
import os
import sys
import sqlite3
import uuid

from .common import require, encoded, digest, local_storage, now, parse_json


@dataclass(frozen=True)
class BoundPolicy:
    policy_id: str = 'offline-byte-envelope-v1'
    pricing_id: str = 'synthetic-tariff-v1'
    input_micro_per_token: int = 1
    output_micro_per_token: int = 2
    wrapper_tokens: int = 512
    output_tokens: int = 2048
    reasoning_in_output_cap: bool = True

    def spec(self, payload, *, model, context):
        require(payload.get('model') == model, 'request model mismatch')
        require(payload.get('max_tokens') == self.output_tokens, 'outbound output cap mismatch')
        require(set(payload) == {'model', 'messages', 'response_format', 'temperature', 'max_tokens'}, 'unbounded generation settings')
        require(payload['response_format'] == {'type': 'json_object'} and payload['temperature'] == 0, 'unsupported response/settings')
        require(self.reasoning_in_output_cap, 'reasoning billing bound unspecified')
        require(all(type(v) is int and v >= 0 for v in (self.input_micro_per_token, self.output_micro_per_token, self.wrapper_tokens)), 'invalid tariff/bound')
        require(1 <= self.output_tokens <= 2048, 'offline output bound exceeds supported cap')
        require(len(encoded(payload)) <= 32768, 'request byte bound exceeded')
        # A conservative byte-token envelope for fixtures only. Provider-specific
        # tokenization, hidden templates and billable categories are NOT verified live.
        input_bound = len(encoded(payload)) + self.wrapper_tokens
        spec = {'provider': 'nebius', 'model': model, 'payload': payload, 'context': context,
                'bound_policy': self.policy_id, 'pricing_policy': self.pricing_id,
                'input_bound': input_bound, 'output_bound': self.output_tokens,
                'input_rate': self.input_micro_per_token, 'output_rate': self.output_micro_per_token,
                'accounting': 'SIMULATED_OFFLINE', 'reasoning_in_output_cap': True, 'policy': asdict(self)}
        return encoded(spec)


class Ledger:
    """One persistent project authority, transactional reservation and receipt history."""
    def __init__(self, path, project, *, previous_micro=0, shared_remaining_micro=20_000_000):
        path = local_storage(path)
        require(path.suffix == '.sqlite', 'ledger must end in .sqlite')
        require(type(previous_micro) is int and 0 <= previous_micro <= 20_000_000, 'invalid prior exposure')
        require(type(shared_remaining_micro) is int and shared_remaining_micro >= 0, 'invalid shared credit observation')
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not path.exists():
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            except FileExistsError:
                pass  # Another cooperating opener won exclusive creation.
        self.conn = sqlite3.connect(path, isolation_level=None, timeout=1)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA synchronous=FULL')
        require(self.conn.execute('PRAGMA journal_mode').fetchone()[0] == 'delete', 'ledger requires DELETE journal mode')
        if sys.platform == 'darwin': self.conn.execute('PRAGMA fullfsync=ON')
        self.conn.execute('PRAGMA foreign_keys=ON')
        self.conn.execute('PRAGMA max_page_count=4096')  # <=16 MiB with default 4 KiB pages
        self.conn.executescript('''
        CREATE TABLE IF NOT EXISTS project(id TEXT PRIMARY KEY, prior INTEGER NOT NULL, ceiling INTEGER NOT NULL,
          frozen INTEGER NOT NULL DEFAULT 0, paid_cap INTEGER NOT NULL DEFAULT 0 CHECK(paid_cap=0));
        CREATE TABLE IF NOT EXISTS stages(id TEXT PRIMARY KEY, cap INTEGER NOT NULL, attempts INTEGER NOT NULL, logical INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY, stage TEXT NOT NULL REFERENCES stages(id),
          logical TEXT NOT NULL, spec BLOB NOT NULL, digest TEXT NOT NULL, reserved INTEGER NOT NULL,
          state TEXT NOT NULL, charge INTEGER, receipt BLOB, receipt_digest TEXT, provider_id TEXT UNIQUE, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS cache(key TEXT PRIMARY KEY, response BLOB NOT NULL, created TEXT NOT NULL);
        ''')
        with self.tx():
            existing = self.conn.execute('SELECT * FROM project').fetchone()
            if existing:
                require(existing['id'] == project and existing['prior'] == previous_micro, 'project identity/prior exposure cannot reset')
                ceiling = min(existing['ceiling'], self.exposure() + shared_remaining_micro)
                self.conn.execute('UPDATE project SET ceiling=?', (ceiling,))
            else:
                self.conn.execute('INSERT INTO project(id,prior,ceiling) VALUES (?,?,?)',
                                  (project, previous_micro, min(20_000_000, previous_micro + shared_remaining_micro)))
        self.path = path

    def close(self): self.conn.close()

    @contextmanager
    def tx(self):
        self.conn.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.conn.execute('COMMIT')
        except BaseException:
            if self.conn.in_transaction: self.conn.execute('ROLLBACK')
            raise

    def stage(self, stage, *, cap_micro=0, max_attempts=10, max_logical=2):
        require(type(cap_micro) is int and 0 <= cap_micro <= 1_000_000, 'stage cap must be <= $1')
        require(type(max_attempts) is int and 1 <= max_attempts <= 10, 'attempt limit must be <=10')
        require(type(max_logical) is int and 1 <= max_logical <= 2, 'logical item limit must be <=2')
        with self.tx():
            old = self.conn.execute('SELECT * FROM stages WHERE id=?', (stage,)).fetchone()
            if old:
                require(tuple(old)[1:] == (cap_micro, max_attempts, max_logical), 'stage cannot be reset/reconfigured')
            else:
                require(self.conn.execute('SELECT count(*) FROM stages').fetchone()[0] < 20, 'stage metadata cap')
                self.conn.execute('INSERT INTO stages VALUES (?,?,?,?)', (stage, cap_micro, max_attempts, max_logical))

    def exposure(self, stage=None):
        query = "SELECT coalesce(sum(CASE WHEN state='cancelled' THEN 0 WHEN charge IS NOT NULL THEN charge ELSE reserved END),0) FROM attempts"
        spent = self.conn.execute(query + (' WHERE stage=?' if stage else ''), (stage,) if stage else ()).fetchone()[0]
        return spent if stage else spent + self.conn.execute('SELECT prior FROM project').fetchone()[0]

    def reserve(self, stage, logical, spec_bytes):
        spec = parse_json(spec_bytes)
        require(encoded(spec) == spec_bytes, 'request specification must be canonical immutable bytes')
        require(spec['accounting'] == 'SIMULATED_OFFLINE', 'paid inference is not enabled')
        expected = BoundPolicy(**spec['policy']).spec(spec['payload'], model=spec['model'], context=spec['context'])
        require(spec_bytes == expected, 'reservation specification/bound mismatch')
        bound = spec['input_bound'] * spec['input_rate'] + spec['output_bound'] * spec['output_rate']
        require(type(bound) is int and bound >= 0, 'invalid reservation')
        with self.tx():
            project = self.conn.execute('SELECT * FROM project').fetchone()
            policy = self.conn.execute('SELECT * FROM stages WHERE id=?', (stage,)).fetchone()
            require(policy is not None, 'explicit stage manifest required')
            require(not project['frozen'], 'project frozen after accounting breach')
            require(not self.conn.execute("SELECT 1 FROM attempts WHERE state IN ('reserved','dispatched','unknown')").fetchone(), 'unresolved exposure or concurrent attempt; reconcile first')
            require(self.exposure() + bound <= project['ceiling'] and self.exposure(stage) + bound <= policy['cap'], 'reservation exceeds project/stage cap')
            count = self.conn.execute('SELECT count(*) FROM attempts WHERE stage=?', (stage,)).fetchone()[0]
            require(count < policy['attempts'], 'physical attempt cap exhausted')
            logicals = {r[0] for r in self.conn.execute('SELECT DISTINCT logical FROM attempts WHERE stage=?', (stage,))}
            require(logical in logicals or len(logicals) < policy['logical'], 'logical item cap exhausted')
            aid = uuid.uuid4().hex
            self.conn.execute('INSERT INTO attempts(id,stage,logical,spec,digest,reserved,state,created) VALUES (?,?,?,?,?,?,?,?)',
                              (aid, stage, logical, spec_bytes, digest(spec_bytes), bound, 'reserved', now()))
            return aid

    def cancel(self, aid):
        with self.tx():
            row = self.conn.execute('SELECT state FROM attempts WHERE id=?', (aid,)).fetchone()
            require(row is not None and row[0] == 'reserved', 'only pre-dispatch reservations can be cancelled')
            self.conn.execute("UPDATE attempts SET state='cancelled' WHERE id=?", (aid,))

    def dispatch(self, aid, spec_bytes, wire):
        # One gate invocation consumes the attempt before the one wire invocation.
        with self.tx():
            row = self.conn.execute('SELECT * FROM attempts WHERE id=?', (aid,)).fetchone()
            require(row is not None and row['state'] == 'reserved', 'attempt already dispatched or not reserved')
            require(row['spec'] == spec_bytes and row['digest'] == digest(spec_bytes), 'dispatch specification mismatch')
            require(getattr(wire, 'offline_only', False) is True, 'live transport is not authorized')
            self.conn.execute("UPDATE attempts SET state='dispatched' WHERE id=?", (aid,))
        try:
            response = wire.send(aid, spec_bytes)
            require(type(response) is bytes and len(response) <= 1_048_576, 'response bound exceeded')
            with self.tx():
                self.conn.execute('UPDATE attempts SET receipt=?,receipt_digest=? WHERE id=?', (response, digest(response), aid))
            self.reconcile(aid)
            return parse_json(response)
        except BaseException:
            with self.tx():
                self.conn.execute("UPDATE attempts SET state='unknown' WHERE id=? AND charge IS NULL", (aid,))
            raise

    def reconcile(self, aid):
        # No caller-supplied usage or amount. Only the response recorded at this gate.
        with self.tx():
            row = self.conn.execute('SELECT * FROM attempts WHERE id=?', (aid,)).fetchone()
            require(row is not None and row['state'] in {'dispatched', 'unknown', 'settled'}, 'no dispatched accounting record')
            if row['charge'] is not None: return row['charge']
            require(row['receipt'] is not None and digest(row['receipt']) == row['receipt_digest'], 'missing/bad receipt; reservation retained')
            reply, spec = parse_json(row['receipt']), parse_json(row['spec'])
            require(reply.get('model') == spec['model'] and isinstance(reply.get('id'), str) and bool(reply['id']), 'receipt provider/model identity mismatch')
            usage = reply.get('usage', {})
            require(isinstance(usage, dict), 'missing usage; reservation retained')
            require(set(usage) <= {'prompt_tokens','completion_tokens','total_tokens','prompt_tokens_details','completion_tokens_details'}, 'unknown billing categories; reservation retained')
            inp, out, total = (usage.get(k) for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
            require(all(type(n) is int and n >= 0 for n in (inp, out, total)) and inp + out == total, 'invalid usage receipt; reservation retained')
            # This policy treats reasoning as included in completion_tokens.
            detail = usage.get('completion_tokens_details', {})
            require(isinstance(detail, dict) and set(detail) <= {'reasoning_tokens'}, 'unbounded usage categories')
            prompt_detail = usage.get('prompt_tokens_details', {})
            require(isinstance(prompt_detail,dict) and set(prompt_detail) <= {'cached_tokens'}, 'unbounded input billing categories')
            cached = prompt_detail.get('cached_tokens',0)
            require(type(cached) is int and 0 <= cached <= inp, 'invalid cached input usage')
            reasoning = detail.get('reasoning_tokens', 0)
            require(type(reasoning) is int and 0 <= reasoning <= out, 'reasoning usage not bounded by completion')
            require(not self.conn.execute('SELECT 1 FROM attempts WHERE provider_id=? AND id<>?', (reply['id'], aid)).fetchone(), 'replayed receipt ID')
            charge = inp * spec['input_rate'] + out * spec['output_rate']
            self.conn.execute("UPDATE attempts SET state='settled',charge=?,provider_id=? WHERE id=?", (charge, reply['id'], aid))
            if charge > row['reserved'] or inp > spec['input_bound'] or out > spec['output_bound']:
                self.conn.execute('UPDATE project SET frozen=1')
            return charge

    def cached(self, spec_bytes):
        row = self.conn.execute('SELECT response FROM cache WHERE key=?', (digest(spec_bytes),)).fetchone()
        return parse_json(row[0]) if row else None

    def cache(self, spec_bytes, response):
        body = encoded(response)
        require(len(body) <= 1_048_576, 'cache entry size exceeded')
        with self.tx():
            count, size = self.conn.execute('SELECT count(*),coalesce(sum(length(response)),0) FROM cache').fetchone()
            if count < 128 and size + len(body) <= 4 * 1024 * 1024:
                self.conn.execute('INSERT OR IGNORE INTO cache VALUES (?,?,?)', (digest(spec_bytes), body, now()))

    def report(self):
        p = dict(self.conn.execute('SELECT * FROM project').fetchone())
        rows = [dict(r) for r in self.conn.execute('SELECT id,stage,logical,digest,reserved,state,charge,receipt_digest,provider_id FROM attempts ORDER BY rowid')]
        for r in rows:
            r['accounting_status'] = 'UNKNOWN_BILLABLE' if r['state'] in {'dispatched','unknown'} else r['state'].upper()
        return {'project': p, 'exposure_micro': self.exposure(), 'attempts': rows,
                'accounting': 'SIMULATED_OFFLINE; estimates are not provider invoices', 'paid_calls': 0}


class MeteredModel:
    """Real host parser plus injected reserve-before-physical-dispatch transport."""
    provider = 'nebius'
    model_provider = 'nebius'

    def __init__(self, ledger, stage, logical, model, wire, *, policy=None, context=None):
        from engine.llm import NebiusModel  # deliberately lazy host integration
        self.ledger, self.stage, self.logical, self.model_name, self.wire = ledger, stage, logical, model, wire
        self.policy = policy or BoundPolicy()
        self.context = context or {'evidence_contract': 'exact-unicode-v1', 'runner': 'offline-v1'}
        self._host = NebiusModel('', model, max_tokens=self.policy.output_tokens, request_transport=self._request)
        self.last = None
        self.cache_hits = 0

    def _request(self, key, base_url, path, payload, timeout):
        require(key == '' and path == '/chat/completions', 'unexpected credential/request path')
        spec = self.policy.spec(payload, model=self.model_name, context=self.context)
        cached = self.ledger.cached(spec)
        self.last = None
        if cached is not None:
            self.cache_hits += 1
            return cached
        aid = self.ledger.reserve(self.stage, self.logical, spec)
        response = self.ledger.dispatch(aid, spec, self.wire)
        self.last = (spec, response)
        return response

    def complete_json(self, system_prompt, content):
        result = self._host.complete_json(system_prompt, content)
        if self.last is not None: self.ledger.cache(*self.last)
        return result


class LoopbackWire:
    """One HTTP request to a local fixture server, no redirects, SDK or retry loop.

    This test transport cannot address a product endpoint. It deliberately supports
    only literal 127.0.0.1, and never accepts authorization headers or credentials.
    """
    offline_only = True
    def __init__(self, port, *, timeout=2):
        require(type(port) is int and 1 <= port <= 65535, 'invalid fixture port')
        require(type(timeout) is int and 1 <= timeout <= 5, 'fixture timeout must be 1..5 seconds')
        self.port, self.timeout = port, timeout

    def send(self, aid, spec_bytes):
        import http.client
        spec = parse_json(spec_bytes)
        payload = encoded(spec['payload'])
        conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=self.timeout)
        try:
            conn.request('POST', '/chat/completions', body=payload,
                         headers={'Content-Type':'application/json', 'X-Fixture-Attempt':aid})
            response = conn.getresponse()
            require(response.status == 200, 'fixture HTTP response is not successful; no redirect/retry')
            body = response.read(1_048_577)
            require(len(body) <= 1_048_576, 'fixture response too large')
            return body
        finally: conn.close()

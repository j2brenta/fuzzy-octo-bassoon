"""Local, source-bound attribution corrections; model verdicts and originals stay unchanged."""
import hashlib
import ipaddress
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from engine.artifacts import fingerprint
from engine.extract import source_passages
from engine.llm import ModelOutputError
from graph.attribution import from_model, author_name, is_named_person


def public_url(value):
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError("Use a public http(s) URL.")
    try:
        u = urlsplit(value.strip())
        host = (u.hostname or '').lower().rstrip('.')
        port = u.port
        if (u.scheme not in {'http', 'https'} or not host or u.username or u.password
                or host == 'localhost' or '.' not in host or any(c.isspace() for c in value)):
            raise ValueError
        try:
            if not ipaddress.ip_address(host).is_global:
                raise ValueError
        except ValueError:
            # A domain name is allowed, but private literal addresses are not.
            if re_ip(host):
                raise ValueError
        query = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
                 if not k.lower().startswith('utm_') and k.lower() not in {'fbclid', 'gclid'}]
        authority = host + (f':{port}' if port and (u.scheme, port) not in {('https',443),('http',80)} else '')
        return urlunsplit((u.scheme.lower(), authority, u.path.rstrip('/') or '/', urlencode(sorted(query)), ''))
    except (ValueError, UnicodeError):
        raise ValueError("Use a public http(s) URL without credentials.") from None


def re_ip(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _folder(root, claim_id):
    return root / 'people-reviews' / hashlib.sha256(claim_id.encode()).hexdigest()


def _latest(root, claim_id):
    files = sorted(_folder(root, claim_id).glob('*.json'))
    if not files:
        return None
    return json.loads(files[-1].read_text(encoding='utf-8'))


def context(ex, text, claim, relation):
    original = next((v.attribution for v in relation.verdicts if v.claim_id == claim.claim_id), None) if relation else None
    return fingerprint({'claim': claim.model_dump(mode='json'), 'text':text,
                        'source':ex.source.model_dump(mode='json'),
                        # New optional organization/base-name fields must not invalidate an
                        # older review when the source supplied none of them.
                        'entities':[{k:v for k,v in e.model_dump(mode='json').items()
                                     if k not in {'base_name','org_type'} or v is not None} for e in ex.entities],
                        'model_attribution':original.model_dump(mode='json') if original else None})


def load(root, ex, text, relation):
    """Only apply corrections to precisely the evidence the reviewer saw."""
    out = {}
    for claim in ex.claims:
        row = _latest(root, claim.claim_id)
        if row and row.get('context') == context(ex, text, claim, relation) and row.get('action') == 'review':
            # Revalidate even locally edited sidecars, including their exact source spans.
            try:
                out[claim.claim_id] = validate(row['review'], ex, text, claim)
            except (ValueError, TypeError, KeyError):
                continue
    return out


def validate(body, ex, text, claim):
    if not isinstance(body, dict) or set(body) != {'kind','person','passages','identity_url','origin_url','origin_checked'}:
        raise ValueError('Review needs kind, person, passages, identity_url, origin_url and origin_checked.')
    item = SimpleNamespace(source=ex.source, entities=ex.entities, text=text)
    try:
        attr = from_model({k:body[k] for k in ('kind','person','passages')}, item, claim)
    except ModelOutputError as error:
        raise ValueError(str(error)) from None
    if any(body[k] is not None and not isinstance(body[k],str) for k in ('identity_url','origin_url')):
        raise ValueError('Profile and origin must be URLs or empty.')
    identity = public_url(body['identity_url']) if body['identity_url'] else None
    origin = public_url(body['origin_url']) if body['origin_url'] else None
    if type(body['origin_checked']) is not bool or bool(origin) != body['origin_checked']:
        raise ValueError('Check the primary evidence origin explicitly, or leave it empty.')
    if attr.kind == 'unknown' and (identity or origin):
        raise ValueError('Unknown speakers cannot have a profile or evidence origin.')
    return {'attribution':attr, 'identity_url':identity, 'origin_url':origin}


def find(root, claim_id):
    from refresher import vault
    for ex, text in vault.load_extractions(root):
        claim = next((c for c in ex.claims if c.claim_id == claim_id), None)
        if claim:
            return ex, text, claim, vault.load_relation(root, ex)
    raise KeyError(claim_id)


def detail(root, claim_id):
    ex, text, claim, relation = find(root, claim_id)
    latest = _latest(root, claim_id)
    ctx = context(ex, text, claim, relation)
    current = latest.get('review') if latest and latest.get('context') == ctx and latest.get('action') == 'review' else None
    invalid_review = False
    if current:
        try:
            validate(current, ex, text, claim)
        except (ValueError, TypeError, KeyError):
            current, invalid_review = None, True
    people = [{'id':f'p{n}', 'name':e.name, 'kind':'quoted'} for n,e in
              enumerate((e for e in ex.entities if e.kind == 'person'), 1) if is_named_person(e.name)]
    if author_name(ex.source):
        people.insert(0, {'id':'author','name':ex.source.author_hint.name,'kind':'author'})
    attr = next((v.attribution for v in relation.verdicts if v.claim_id == claim_id),None) if relation else None
    suggestion = {'kind':'unknown','person':None,'passages':[], 'identity_url':None,'origin_url':None,'origin_checked':False}
    if attr and attr.kind != 'unknown':
        person = next((p for p in people if p['name'] == attr.person_name and p['kind'] == attr.kind),None)
        if person:
            suggestion.update(kind=attr.kind,person=person['id'],passages=[p['id'] for p in source_passages(text)
                if any(p['start'] == s.start_char and p['end'] == s.end_char for s in attr.evidence)])
    return {'claim_id':claim_id,'statement':claim.statement,'item_id':ex.item_id,
            'token':fingerprint({'context':ctx,'latest':latest}), 'people':people,
            'passages':[{'id':p['id'],'text':p['text'],
                         'supports_claim':any(p['start'] < s.end_char and s.start_char < p['end'] for s in claim.spans)}
                        for p in source_passages(text)],
            'review':current, 'suggestion':suggestion, 'stale_review':invalid_review or bool(latest and latest.get('context') != ctx)}


def save(root, claim_id, body):
    from refresher import vault
    if not isinstance(body, dict) or body.get('action') not in {'review','reset'}:
        raise ValueError('Choose review or reset.')
    with vault.lock(root):
        current = detail(root, claim_id)
        if body.get('token') != current['token']:
            raise vault.StaleToken('The source or review changed. Open the review again.')
        ex, text, claim, relation = find(root, claim_id)
        if body['action'] == 'review':
            validate(body.get('review'), ex, text, claim)
        row = {'claim_id':claim_id,'context':context(ex,text,claim,relation),'action':body['action'],
               'review':body.get('review') if body['action'] == 'review' else None,
               'at':datetime.now(timezone.utc).isoformat()}
        path = _folder(root, claim_id) / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'-'+uuid4().hex+'.json')
        vault.atomic_write(path, json.dumps(row,ensure_ascii=False,indent=2)+'\n')
    return {'ok':True}

"""Preview first; explicitly requested, bounded upgrades of old attribution records."""
import json
from datetime import datetime, timezone
from uuid import uuid4

from engine.artifacts import fingerprint
from engine.llm import BudgetedModel, ModelError, judge_client_from_environment
from graph import relate
from refresher import vault

MAX_DOCUMENTS = 3
MAX_TOKENS = 120_000
MAX_SECONDS = 600


def _settings(client):
    # No keys or credentials in the preview/job; bind approval to provider and request limits.
    return fingerprint({k:getattr(client,k,None) for k in
                        ('provider','model_provider','model_name','base_url','max_tokens','timeout_seconds')})


def _write(root, job):
    vault.atomic_write(root / 'people-jobs' / (job['id']+'.json'), json.dumps(job,ensure_ascii=False,indent=2)+'\n')


def job(root, job_id):
    if len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
        raise KeyError(job_id)
    path = root / 'people-jobs' / (job_id+'.json')
    if not path.exists():
        raise KeyError(job_id)
    return json.loads(path.read_text(encoding='utf-8'))


def active(root):
    rows = [json.loads(p.read_text(encoding='utf-8')) for p in sorted((root / 'people-jobs').glob('*.json'))]
    return next((r for r in rows if r['status'] in {'queued','processing'}), None)


def recover(root):
    """Never retry a possibly paid request after a restart."""
    for path in (root / 'people-jobs').glob('*.json'):
        row = json.loads(path.read_text(encoding='utf-8'))
        if row['status'] in {'queued','processing'}:
            row.update(status='interrupted', message='Server restarted. Completed results retained; preview again to continue.')
            _write(root, row)


def preview(root):
    rows, hashes = [], []
    for ex, text in vault.load_extractions(root):
        relation = vault.load_relation(root, ex)
        if not ex.quality_gate_passed or not ex.claims or not text or (ex.triage and not ex.triage.keep):
            continue
        if (relation and relation.relator == 'model' and relation.prompt_version == relate.PROMPT_VERSION
                and all(v.attribution is not None for v in relation.verdicts)):
            continue
        content = fingerprint({'extraction':ex.model_dump(mode='json'), 'text':text,
                               'relation':relation.model_dump(mode='json') if relation else None})
        rows.append({'item_id':ex.item_id,'title':ex.source.source_name or ex.item_id,
                     'characters':len(text),'claims':len(ex.claims),'fingerprint':content})
        hashes.append((ex.item_id,content))
    settings = None
    ready, message, model = False, 'Configure JUDGE_PROVIDER and its API key to run a paid update.', None
    try:
        client = judge_client_from_environment()  # constructs a client; never makes a request
        if client:
            ready, model, message = True, client.model_name, 'Paid model usage starts only when you run the selected update.'
            settings = _settings(client)
    except ModelError:
        pass
    return {'documents':rows,'token':fingerprint({'version':relate.PROMPT_VERSION,'items':hashes,'settings':settings}),
            'max_documents':MAX_DOCUMENTS,'max_tokens':MAX_TOKENS,'max_seconds':MAX_SECONDS,
            'ready':ready,'model':model,'settings_token':settings,'message':message,'active_job':active(root)}


def start(root, body, worker):
    if not isinstance(body, dict) or body.get('confirm_paid') is not True:
        raise ValueError('Explicitly confirm paid model usage.')
    ids = body.get('item_ids')
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_DOCUMENTS or
            any(not isinstance(i,str) for i in ids) or len(set(ids)) != len(ids)):
        raise ValueError(f'Select one to {MAX_DOCUMENTS} distinct documents.')
    with vault.lock(root):
        current = preview(root)
        if current['active_job']:
            raise vault.StaleToken('An update is already running; follow its progress.')
        if current['token'] != body.get('token'):
            raise vault.StaleToken('Documents or model settings changed. Preview again before spending.')
        if not current['ready']:
            raise ValueError(current['message'])
        candidates = {r['item_id']:r for r in current['documents']}
        if any(i not in candidates for i in ids):
            raise ValueError('Select only documents listed in the current preview.')
        row = {'id':uuid4().hex,'status':'queued','at':datetime.now(timezone.utc).isoformat(),
               'documents':[candidates[i] for i in ids], 'completed':[], 'calls':0,'spent_tokens':0,
               'model':current['model'],'settings_token':current['settings_token'],'message':'Waiting for the document worker.'}
        _write(root,row)
    try:
        worker.submit(run, root, row['id'])
    except RuntimeError:
        row.update(status='interrupted',message='Worker stopped. Preview again to continue.')
        _write(root,row)
    return row


def run(root, job_id):
    row = job(root,job_id)
    model = None
    try:
        client = judge_client_from_environment()
        if client is None or _settings(client) != row['settings_token']:
            raise ValueError('Model settings changed. Preview again.')
        model = BudgetedModel(client, max_tokens=MAX_TOKENS, max_seconds=MAX_SECONDS)
        if model.model_name != row['model']:
            raise ValueError('Model settings changed. Preview again.')
        relator = relate.ModelRelator(model)
        row.update(status='processing', message='Updating selected documents only.')
        _write(root,row)
        # ponytail: the existing vault write lock serializes this bounded batch with ingestion/reviews.
        with vault.lock(root):
            for selected in row['documents']:
                current = {r['item_id']:r for r in preview(root)['documents']}
                if selected['item_id'] not in current or current[selected['item_id']]['fingerprint'] != selected['fingerprint']:
                    raise ValueError('A selected source changed or was already upgraded. Preview again.')
                ex,text = next((e,t) for e,t in vault.load_extractions(root) if e.item_id == selected['item_id'])
                store = vault.replay(root,include_unreviewed=True)
                result = relate.judge(ex,text,store.candidate_pool(ex.item_id),relator)
                vault.save_relation(root,result.model_copy(update={'pool':store.placed_fingerprint()}))
                row['completed'].append(ex.item_id)
                row.update(calls=model.calls,spent_tokens=model.spent)
                _write(root,row)
        row.update(status='complete',message='Attribution update finished. Review inferred speakers in the graph.')
    except Exception as error:
        # Keep completed writes, stop this batch, and require another explicit paid action.
        import logging
        logging.getLogger(__name__).exception('People update %s stopped',job_id)
        row.update(status='failed',message='Update stopped. Completed results retained; check the server log and preview again.')
    finally:
        if row['completed']:
            try:
                from refresher.app import rebuild
                rebuild(root, True)
            except Exception:
                import logging
                logging.getLogger(__name__).exception('People update export failed')
                row.update(status='failed',message='Model results saved, but view export failed. Check the server log.')
        if model:
            row.update(calls=model.calls,spent_tokens=model.spent)
        _write(root,row)

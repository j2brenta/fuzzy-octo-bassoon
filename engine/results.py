"""One readable current result per item, alongside the machine-readable handoff.

    python -m engine.results .local-vault/telegram

This exporter makes no model calls and never changes raw source records.
"""

import argparse
import html
import json
import os
from pathlib import Path
import re
import tempfile

from engine.package import PackageError, build_extraction
from engine.entities import EntityError
from engine.extract import ExtractionError, evidence_spans, source_passages
from engine.judge import JudgeError
from engine.triage import TriageError


USER_NOTES_MARKER = '<!-- Your notes below this line are preserved -->'

STATES = {'accepted': 'Accepted', 'needs_revision': 'Needs revision', 'pending': 'Processing incomplete', 'skipped': 'Skipped by triage', 'linked': 'Article saved separately', 'download_failed': 'Download blocked or failed', 'link_saved': 'Link saved'}


def _plain(value, limit=None):
    text = ' '.join(str(value or '').split())
    if limit is not None and len(text) > limit:
        text = text[:limit - 1] + '…'
    return html.escape(text).replace('|', r'\|').replace('[', r'\[').replace(']', r'\]')


def entity_summary(entities: list[dict], review: dict, limit=6) -> str:
    """Show actual names and aliases in Telegram, rather than internal entity IDs."""
    lines = []
    findings = {row['entity_id']: row for row in review.get('entities', [])}
    for kind, label in [('person', 'People'), ('organization', 'Organizations')]:
        rows = [entity for entity in entities if entity['kind'] == kind]
        lines.append(f'{label}: {len(rows)}')
        for entity in rows[:limit]:
            name = ' '.join(entity['name'].split())[:100]
            aliases = ', '.join(entity.get('aliases', []))[:100]
            suffix = f' (also: {aliases})' if aliases else ''
            flagged = findings.get(entity['entity_id'], {}).get('verdict') == 'needs_revision'
            status = ' — needs review' if flagged else ''
            lines.append(f'- {name}{suffix}{status}')
        if len(rows) > limit:
            lines.append(f'- {len(rows) - limit} more in the saved result note.')
    lines.append('Review: ' + ('accepted' if review['status'] == 'accepted' else 'needs revision'))
    lines.append(review['summary'])
    return '\n'.join(lines)


def _atomic_text(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _title(raw):
    web = (raw.get('channel_metadata') or {}).get('web_article') or {}
    if web.get('title'):
        return ' '.join(web['title'].split())[:120]
    for line in raw['text'].splitlines():
        line = line.strip().lstrip('#').strip()
        if line and not re.match(r'^.+, \[\d{1,2} \w+ \d{4} at [\d:]+\]:$', line):
            return line[:120]
    return raw.get('source_name') or 'Saved item'


def _render_entities(entities, review, exists):
    if not exists:
        return ['## People and organizations', '', 'Entity extraction has not finished yet.', '']
    rows_by_id = {row['entity_id']: row for row in (review or {}).get('entities', [])}
    lines = []
    for kind, heading in [('person', 'People'), ('organization', 'Organizations'), ('concept', 'Legacy concepts')]:
        entries = [entity for entity in entities if entity['kind'] == kind]
        if kind == 'concept' and not entries:
            continue
        lines += ['## ' + heading, '']
        if not entries:
            lines += ['No named ' + heading.lower() + ' extracted.', '']
            continue
        lines += ['| Name | Also written as | Mentions | Review |', '|---|---|---:|---|']
        findings = []
        for entity in entries:
            result = rows_by_id.get(entity['entity_id'])
            state = 'Not reviewed' if result is None else ('Supported' if result['verdict'] == 'supported' else 'Needs review')
            aliases = ', '.join(entity.get('aliases', [])) or '—'
            lines.append(f"| {_plain(entity['name'])} | {_plain(aliases)} | {len(entity.get('mentions', []))} | {state} |")
            if result is not None and result['verdict'] != 'supported':
                findings.append(f"- **{_plain(entity['name'])}:** {_plain(result['reason'])}")
        lines.append('')
        if findings:
            lines.extend(findings + [''])
    return lines


def write_item_note(raw_path: Path, *, refresh_index=True) -> Path:
    """Rebuild the current view from validated artifacts, including unfinished items."""
    raw_path = Path(raw_path)
    raw = json.loads(raw_path.read_text(encoding='utf-8'))
    item_id = raw['item_id']
    if raw_path.parent.name != 'raw' or not re.fullmatch(r'[0-9a-f]{64}', item_id) or raw_path.stem != item_id:
        raise ValueError('Expected a captured item in raw/.')
    from engine.parse import parse_capture
    prepared = parse_capture(raw_path)
    document = json.loads(prepared.path.read_text(encoding='utf-8'))
    record = None
    error = None
    try:
        record = build_extraction(raw_path).record
    except (PackageError, EntityError, ExtractionError, JudgeError, TriageError) as failure:
        error = str(failure)
    status = record['quality_status'] if record else 'pending'
    link_results = []
    if record is None and document['links']:
        from engine.parse.text import text_without_links
        remainder = text_without_links(raw['text'], document['links'])
        if not any(character.isalnum() for character in remainder):
            status = 'link_saved'
            path = raw_path.parent.parent / 'web-links' / raw_path.name
            if path.exists():
                saved = json.loads(path.read_text(encoding='utf-8'))
                if saved.get('source_content_hash') == raw['content_hash']:
                    link_results = saved.get('links', [])
                    if any(row.get('status') == 'captured' for row in link_results):
                        status = 'linked'
                    elif any(row.get('status') == 'failed' for row in link_results):
                        status = 'download_failed'
            error = None  # A URL pointer intentionally has no claim extraction.

    entities = record.get('entities', []) if record else []
    claims = record.get('claims', []) if record else []
    review = (record or {}).get('reviews', {})
    title = _title(raw)
    metadata = {
        'type': 'extraction-result', 'item_id': item_id, 'title': title,
        'status': status, 'captured_at': raw.get('captured_at'),
        'claim_count': len(claims),
        'people_count': sum(entity['kind'] == 'person' for entity in entities) if (record or {}).get('entity_status') == 'saved' else None,
        'organization_count': sum(entity['kind'] == 'organization' for entity in entities) if (record or {}).get('entity_status') == 'saved' else None,
    }
    lines = ['---', *[f'{key}: {json.dumps(value, ensure_ascii=False)}' for key, value in metadata.items()], '---', '',
             '# ' + _plain(title), '', '**Status:** ' + STATES[status], '',
             '[All results](index.md) · ' + f'[Original record](../raw/{item_id}.json)', '']
    if record is not None:
        lines += [f'[Combined data]({item_id}.json)', '']
    if raw.get('url'):
        lines += ['**Source URL:** ' + _plain(raw['url']), '']
    author = raw.get('author_hint') or {}
    if author.get('name'):
        lines += ['**Source author hint:** ' + _plain(author['name']), '']
    forwarder = raw.get('forwarded_by')
    if isinstance(forwarder, dict):
        forwarder = forwarder.get('name') or forwarder.get('handle')
    if forwarder:
        lines += ['**Shared by:** ' + _plain(forwarder), '']
    if error:
        lines += ['> Processing is incomplete: ' + _plain(error), '']
    if status in {'linked', 'link_saved', 'download_failed'}:
        lines += ['This item is a link pointer. Article claims belong to the downloaded child, not to the URL text.', '']
        for row in link_results:
            child_id = row.get('item_id')
            if row.get('status') == 'captured' and isinstance(child_id, str) and re.fullmatch(r'[0-9a-f]{64}', child_id):
                lines.append(f"- [Read the linked article result]({child_id}.md)")
            elif row.get('reason'):
                lines.append('- ' + _plain(row['reason']))
        lines.append('')

    lines += ['## Claims', '']
    claim_reviews = {row['claim_id']: row for row in review.get('claims', {}).get('claims', [])}
    by_span = {(passage['start'], passage['end']): passage['id'] for passage in source_passages(raw['text'])}
    for number, claim in enumerate(claims, 1):
        finding = claim_reviews.get(claim['claim_id'])
        label = 'Not reviewed' if finding is None else ('Supported' if finding['verdict'] == 'supported' else 'Needs review')
        ids = [by_span[(span['start_char'], span['end_char'])] for span in evidence_spans(claim)]
        lines += [f"{number}. **{label}** — {_plain(claim['statement'])}",
                  f"   - Source passages: {', '.join(map(str, ids))}. Kind: {_plain(claim['kind'])}; durability: {_plain(claim['durability_class'])}."]
        if finding is not None and finding['verdict'] != 'supported':
            lines.append('   - Review: ' + _plain(finding['reason']))
    if not claims:
        lines.append('No validated claim output is available yet.')
    lines.append('')
    lines += _render_entities(entities, review.get('entities'), (record or {}).get('entity_status') == 'saved')
    notes = (record or {}).get('coverage_notes', {})
    if notes.get('claims') or notes.get('entities'):
        lines += ['## Coverage notes', '', 'These are missing-idea suggestions, not correctness failures.', '']
        for omission in notes.get('claims', []):
            lines.append(f"- Passage {omission['passage_id']}: {_plain(omission['description'])}")
        for omission in notes.get('entities', []):
            lines.append('- Possible missing entity: ' + _plain(omission['name']))
        lines.append('')
    lines += ['## Processing state', '',
              'This note is the current readable result. Intermediate folders hold retry/cache data; they are not extra independent articles.', '',
              'Source support is reviewed; the source itself is not independently fact-checked. Graph placement remains separate.', '']
    path = raw_path.parent.parent / 'processed' / f'{item_id}.md'
    user_notes = ''
    if path.exists():
        old = path.read_text(encoding='utf-8')
        if USER_NOTES_MARKER in old:
            user_notes = old.split(USER_NOTES_MARKER, 1)[1].lstrip('\n')
    lines += [USER_NOTES_MARKER, user_notes]
    _atomic_text(path, '\n'.join(lines))
    if refresh_index:
        write_index(raw_path.parent.parent)
    return path


def write_index(vault: Path) -> Path:
    entries = []
    for path in (vault / 'processed').glob('*.md'):
        if path.name == 'index.md':
            continue
        text = path.read_text(encoding='utf-8')
        if not text.startswith('---\n'):
            continue
        header = text.split('---\n', 2)[1]
        try:
            fields = {key: json.loads(value.strip()) for key, value in (line.split(':', 1) for line in header.splitlines())}
            if fields.get('type') == 'extraction-result':
                entries.append((fields, path.name))
        except (ValueError, TypeError):
            continue
    entries.sort(key=lambda row: (row[0].get('captured_at') or '', row[0]['item_id']), reverse=True)
    lines = ['# Your saved results', '',
             'Open an item to read its claims, people, organizations and review status. One current note is kept per item.', '',
             '| Item | Status | Claims | People | Organizations |', '|---|---|---:|---:|---:|']
    for fields, name in entries:
        people = fields['people_count'] if fields['people_count'] is not None else '—'
        organizations = fields['organization_count'] if fields['organization_count'] is not None else '—'
        lines.append(f"| [{_plain(fields['title'])}]({name}) | {STATES[fields['status']]} | {fields['claim_count']} | {people} | {organizations} |")
    lines += ['', 'Original sources are in `../raw/`. JSON files here are the combined data for the graph handoff. Other folders are internal pipeline/cache data.', '']
    path = vault / 'processed' / 'index.md'
    _atomic_text(path, '\n'.join(lines))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('vault', type=Path)
    parser.add_argument('--item-id')
    args = parser.parse_args()
    paths = sorted((args.vault / 'raw').glob('*.json'))
    if args.item_id:
        paths = [path for path in paths if path.stem == args.item_id]
    failures = 0
    for path in paths:
        try:
            write_item_note(path, refresh_index=False)
        except (OSError, ValueError, KeyError) as failure:
            failures += 1
            print(f'{path.stem}: result unavailable ({type(failure).__name__})')
    index = write_index(args.vault)
    print(f'Readable results: {len(paths) - failures}; unavailable: {failures}. Open {index}')
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())

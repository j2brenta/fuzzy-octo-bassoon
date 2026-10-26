"""Clearly synthetic corpus and fixture-operator decisions through generic APIs."""
import json
import time
from pathlib import Path

from .common import digest, fingerprint, outside_repositories, require
from .storage import Database
from .intake import import_batch, packages
from . import review as rv
from .export import export_view


def record(label, statement, qualifier, *, hour=10, link=None, pending=False, author='Alex Example', revision=1):
    item = digest(('synthetic-news:' + label).encode())
    text = statement + '\n' + qualifier
    if link:
        text += '\nСсылка: ' + link
    section = item + ':section:1'
    raw = {'schema_version': 1, 'item_id': item, 'channel': 'file', 'source_id': 'synthetic:' + label,
           'source_name': 'Синтетический источник · ' + label, 'captured_at': f'2026-10-26T{hour:02d}:10:00+00:00',
           'published_at': f'2026-10-26T{hour:02d}:00:00+00:00', 'author_hint': {'name': author},
           'forwarded_by': {'name': 'Fixture forwarding account'} if label.startswith('repost') else None,
           'url': 'https://example.invalid/news/' + label, 'kind': 'text', 'text': text,
           'content_hash': digest(text.encode()), 'channel_metadata': {}, 'attachments': []}
    spans = [{'item_id': item, 'section_id': section, 'start_char': 0, 'end_char': len(statement)},
             {'item_id': item, 'section_id': section, 'start_char': len(statement) + 1,
              'end_char': len(statement) + 1 + len(qualifier)}]
    cid = fingerprint([item, statement, qualifier, revision])
    links = []
    if link:
        links = [{'url': link, 'kind': 'url', 'section_id': section, 'start_char': text.index(link), 'end_char': len(text)}]
    r = {'schema_version': 1, 'stage': 'processed_extraction', 'builder_version': 'handoff-v2',
         'quality_policy_version': 'correctness-v1', 'item_id': item, 'raw_path': 'raw/' + item + '.json',
         'source_content_hash': raw['content_hash'], 'source': {k: raw[k] for k in ('channel', 'source_id', 'source_name',
         'captured_at', 'published_at', 'author_hint', 'forwarded_by', 'url', 'kind')},
         'sections': [{'section_id': section, 'kind': 'text', 'provenance': {'item_id': item, 'field': 'text',
                        'start_char': 0, 'end_char': len(text)}}], 'links': links, 'entities': [],
         'claims': [{'claim_id': cid, 'statement': statement, 'kind': 'announcement', 'durability_class': 'news',
                     'confidence': .8, 'half_life_days': 30, 'evidence': spans}],
         'triage': {'keep': True, 'kind': 'post', 'durability_class': 'news', 'split_digest': False, 'reason': 'Synthetic fixture'},
         'reviews': {'claims': {'status': 'accepted', 'claims': [{'claim_id': cid, 'verdict': 'supported', 'issues': [],
                      'reason': 'Synthetic fixture judgment, not a model result'}], 'omitted_concepts': [], 'summary': 'Synthetic'},
                     'entities': {'status': 'accepted', 'entities': [], 'omitted_entities': [], 'summary': 'Synthetic'}},
         'quality_status': 'accepted', 'quality_gate_passed': True, 'inputs': {f'raw/{item}.json': fingerprint(raw)},
         'fixture_origin': 'clearly synthetic; fixture operator; no live model', 'revision_note': revision,
         'created_at': '2026-10-26T20:00:00+00:00'}
    if pending:
        r['reviews']['claims'] = {'status': 'pending'}
        r['quality_status'], r['quality_gate_passed'] = 'pending', False
    r['bundle_id'] = fingerprint({k: v for k, v in r.items() if k not in {'created_at', 'bundle_id'}})
    return r, raw


def write(vault, record, raw):
    for folder in ('processed', 'raw'):
        (vault / folder).mkdir(parents=True, exist_ok=True)
    for relative, value in [(record['raw_path'], raw), ('processed/' + record['item_id'] + '.json', record)]:
        (vault / relative).write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def make_demo(directory):
    started = time.perf_counter()
    root = outside_repositories(directory)
    require(not root.exists(), 'demo requires a fresh out-of-tree directory')
    root.mkdir(parents=True, mode=0o700)
    vault = root / 'fixture-vault'
    vault.mkdir()
    db = Database(root / 'authority' / 'news.sqlite', create=True)
    try:
        db.register('synthetic', vault, actor='fixture_operator')
        definitions = [
          ('announcement', 'Aurora: новая открытая модель для локальных агентов', 'Синтетический анонс: 42 балла на наборе Atlas; один GPU, mobile не измерялся.', 10, None, False, 'Alex Example'),
          ('repost-lab', 'Лаборатория пересказывает анонс Aurora', 'Перепечатка, а не независимый эксперимент. Все числа взяты из исходного анонса.', 11, 'announcement', False, 'Dana Example'),
          ('repost-digest', 'Дайджест: что обещает Aurora', 'Это явная ссылка на объявление. Независимость подтверждения не установлена.', 12, 'announcement', False, 'Robin Example'),
          ('benchmark', 'Тест Aurora: 37 баллов при другом бюджете памяти', 'Atlas-mini, 8 GB VRAM, один запуск. Независимость источника неизвестна; сравнение с Atlas не установлено.', 13, None, False, 'Alex Example'),
          ('correction', 'Исправление: результат Aurora относится только к desktop', 'Авторы уточняют условия анонса: результаты на мобильных устройствах не измерялись.', 14, 'announcement', False, 'Sam Example'),
          ('conditions', 'Другая конфигурация: Aurora на двух GPU', 'Показатель throughput, batch 16; это не та же метрика, что качество на Atlas.', 15, 'benchmark', False, 'Jules Example'),
          ('pending-origin', 'Orion: заявлен новый метод проверки агентов', 'Синтетический оригинал ожидает extraction review; он не допускается к активным semantic-связям.', 16, None, True, 'Alex Example'),
          ('safety-note', 'Orion: заметка об ограничениях проверки', 'Оригинал ещё pending. Ссылка устанавливает происхождение обсуждения, но не подтверждает вывод.', 17, 'pending-origin', False, 'Morgan Example'),
        ]
        for label, statement, qualifier, hour, link, pending, author in definitions:
            r, raw = record(label, statement, qualifier, hour=hour,
                            link='https://example.invalid/news/' + link if link else None, pending=pending, author=author)
            write(vault, r, raw)
        first_receipt = import_batch(db, 'synthetic', vault)
        rows = packages(db)
        by_label = {r['record']['source']['source_id'].split(':', 1)[1]: r for r in rows}
        stories = {}
        for title, topic, labels in [
            ('Aurora / от анонса к уточнению', 'models', ['announcement', 'repost-lab', 'repost-digest', 'correction']),
            ('Измерения / условия имеют значение', 'evaluation', ['benchmark', 'conditions']),
            ('Orion / границы подтверждения', 'safety', ['pending-origin', 'safety-note'])]:
            refs = [rv.reference(by_label[x]) for x in labels]
            story = rv.story_create(db, title, topic, 'Синтетическая ручная организация истории', refs, 'fixture_operator')
            stories[topic] = story
            for ref in refs:
                rv.assign(db, 'story_assign', ref, story, 'Синтетическое назначение, не автоматическое обнаружение', [ref], 'fixture_operator')
        left = rv.reference(by_label['correction'], by_label['correction']['record']['claims'][0]['claim_id'])
        right = rv.reference(by_label['announcement'], by_label['announcement']['record']['claims'][0]['claim_id'])
        context = dict(zip(rv.CONTEXT_FIELDS, ['Aurora', '1', 'quality score', 'Atlas', 'points',
                          'desktop, single GPU; mobile not measured', '2026-10-26 / synthetic authors', 'source independence unknown']))
        proposal = rv.propose(db, left, right, 'CORRECTS', context, 'Синтетическая интерпретация: уточнение условий анонса',
                             [left, right], [], 'fixture_operator')
        rv.review(db, proposal, 'accept', rv.show(db, proposal)['token'], 'Синтетический review после просмотра обеих цитат', 'fixture_operator')
        # Held-out item uses the same import and assignment operations, with no ID-specific consumer logic.
        held, raw = record('heldout', 'Новый материал: воспроизведение теста Aurora',
                           'Условия совпадают только частично. Автоматической semantic-связи не создаётся.',
                           hour=18, link='https://example.invalid/news/benchmark', author='Taylor Example')
        write(vault, held, raw)
        held_receipt = import_batch(db, 'synthetic', vault, 'processed/' + held['item_id'] + '.json')
        held_row = next(r for r in packages(db) if r['item'] == held['item_id'])
        rv.assign(db, 'story_assign', rv.reference(held_row), stories['evaluation'], 'Ручное назначение held-out материала',
                  [rv.reference(held_row)], 'fixture_operator')
        # A replacement package is retained first; neither selection nor review migrates automatically.
        replacement, raw = record('announcement', definitions[0][1], definitions[0][2] + ' Уточнение: речь о версии 1.', revision=2)
        write(vault, replacement, raw)
        revision_receipt = import_batch(db, 'synthetic', vault, 'processed/' + replacement['item_id'] + '.json')
        assert rv.show(db, proposal)['active_reviewed']
        current = next(r for r in packages(db) if r['item'] == replacement['item_id'] and r['selected'])
        db.select('synthetic', replacement['item_id'], replacement['bundle_id'], current['head_token'], 'fixture_operator')
        assert rv.show(db, proposal)['historical']
        replaced = next(r for r in packages(db) if r['bundle'] == replacement['bundle_id'])
        rv.assign(db, 'story_assign', rv.reference(replaced), stories['models'], 'Явное назначение новой ревизии',
                  [rv.reference(replaced)], 'fixture_operator')
        new_right = rv.reference(replaced, replaced['record']['claims'][0]['claim_id'])
        new_proposal = rv.propose(db, left, new_right, 'CORRECTS', context,
                                 'Новое синтетическое предложение к новой версии, без наследования review',
                                 [left, new_right], [], 'fixture_operator')
        rv.review(db, new_proposal, 'accept', rv.show(db, new_proposal)['token'],
                  'Отдельный синтетический review новой пары', 'fixture_operator')
        exported = export_view(db, root / 'viewer')
        receipt = {'origin': 'synthetic producer-shaped artifacts; model stages not executed',
                   'initial_import': first_receipt, 'heldout_import': held_receipt, 'revision_import': revision_receipt, 'export': exported,
                   'manual_decisions': len([e for e in rv.events(db) if e['actor'] == 'fixture_operator']),
                   'setup_wall_seconds': round(time.perf_counter() - started, 3),
                   'historical_review': proposal, 'new_review_proposal': new_proposal,
                   'manual_effort_note': 'Fixture decisions executed by script; no human timing or usability pilot claimed',
                   'human_usefulness_walkthrough': 'pending'}
        (root / 'demo-receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
        return receipt
    finally:
        db.close()

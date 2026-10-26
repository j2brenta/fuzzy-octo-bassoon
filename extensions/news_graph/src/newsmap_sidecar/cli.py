"""Explicit local operations with machine-readable outcomes."""
import argparse
import json
import sqlite3
from pathlib import Path

from .common import NewsmapError, parse_json
from .storage import Database
from .intake import import_batch, packages
from . import review as editorial


def json_arg(value):
    try:
        if len(value) > 100000:
            raise NewsmapError('command JSON too large')
        return parse_json(value.encode('utf-8'))
    except NewsmapError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def decision_args(p):
    p.add_argument('--actor', required=True)
    p.add_argument('--rationale', required=True)


def evidence_args(p):
    p.add_argument('--evidence', required=True, type=json_arg, help='JSON-массив точных ссылок на ревизии/утверждения')


def parser():
    p = argparse.ArgumentParser(description='Локальная карта новостей и сохранённых доказательств')
    p.add_argument('--db', type=Path, help='SQLite вне репозитория и исходного vault; обязательна кроме demo')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('init', help='Создать новое хранилище')
    demo = sub.add_parser('demo', help='Создать синтетический walkthrough в новом внешнем каталоге')
    demo.add_argument('directory', type=Path)
    sub.add_parser('doctor', help='Проверить целостность и параметры SQLite')
    sub.add_parser('rebuild', help='Пересобрать индекс выбранных ревизий из неизменяемого журнала')
    reg = sub.add_parser('register', help='Зарегистрировать namespace и read-only vault')
    reg.add_argument('--namespace', required=True)
    reg.add_argument('--vault', type=Path, required=True)
    reg.add_argument('--relocate', action='store_true')
    reg.add_argument('--actor', required=True)
    imp = sub.add_parser('import', help='Импортировать опубликованные processed-артефакты')
    imp.add_argument('--namespace', required=True)
    imp.add_argument('--vault', type=Path, required=True)
    imp.add_argument('--scope', default='processed')
    imp.add_argument('--max-items', type=int, default=200)
    sub.add_parser('list', help='Состояния сохранённых ревизий')
    inspect = sub.add_parser('inspect', help='Точные сохранённые цитаты и source metadata')
    inspect.add_argument('--namespace', required=True)
    inspect.add_argument('--item', required=True)
    inspect.add_argument('--bundle')
    sub.add_parser('history', help='Неизменяемые решения и квитанции импортов')
    sel = sub.add_parser('select', help='Явно выбрать импортированную ревизию')
    for key in ('namespace', 'item', 'bundle', 'expect-current', 'actor'):
        sel.add_argument('--' + key, required=True)
    back = sub.add_parser('backup', help='SQLite backup API в новый каталог')
    back.add_argument('destination', type=Path)
    restore = sub.add_parser('restore-check', help='Проверить восстановленную копию и сравнить каноническое состояние')
    restore.add_argument('restored', type=Path)
    for group in ('story', 'topic', 'subject'):
        parent = sub.add_parser(group)
        actions = parent.add_subparsers(dest='action', required=True)
        if group == 'story':
            create = actions.add_parser('create')
            create.add_argument('--title', required=True)
            create.add_argument('--topic', required=True, choices=editorial.TOPICS)
            decision_args(create)
            evidence_args(create)
        assign = actions.add_parser('assign')
        assign.add_argument('--target', required=True, type=json_arg)
        assign.add_argument('--value', required=True, type=json_arg if group == 'subject' else int if group == 'story' else str)
        decision_args(assign)
        evidence_args(assign)
    identity = sub.add_parser('identity').add_subparsers(dest='action', required=True)
    identity.add_parser('list')
    link = identity.add_parser('link')
    link.add_argument('--left', required=True)
    link.add_argument('--right', required=True)
    decision_args(link)
    evidence_args(link)
    revoke = identity.add_parser('revoke')
    revoke.add_argument('decision', type=int)
    decision_args(revoke)
    assignment = sub.add_parser('assignment').add_subparsers(dest='action', required=True)
    revoke = assignment.add_parser('revoke')
    revoke.add_argument('decision', type=int)
    decision_args(revoke)
    relation = sub.add_parser('relation').add_subparsers(dest='action', required=True)
    prop = relation.add_parser('propose')
    for side in ('left', 'right', 'context'):
        prop.add_argument('--' + side, required=True, type=json_arg)
    prop.add_argument('--type', choices=sorted(editorial.RELATIONS), required=True)
    prop.add_argument('--premises', type=json_arg, default=[])
    decision_args(prop)
    evidence_args(prop)
    rev = sub.add_parser('review').add_subparsers(dest='action', required=True)
    for action in ('show', 'accept', 'reject', 'defer', 'revoke'):
        action_parser = rev.add_parser(action)
        action_parser.add_argument('proposal', type=int)
        if action != 'show':
            action_parser.add_argument('--expect-token', required=True)
            decision_args(action_parser)
    sub.add_parser('view-data', help='Согласованный частный snapshot; не публичный экспорт')
    for command in ('export-local', 'export-public'):
        exp = sub.add_parser(command, help='Новый статический каталог; без публикации')
        exp.add_argument('output', type=Path)
        if command == 'export-public':
            exp.add_argument('--allowlist', type=Path, required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    db = None
    try:
        if args.command == 'demo':
            from .demo import make_demo
            print(json.dumps(make_demo(args.directory), ensure_ascii=False, indent=2))
            return 0
        if args.db is None:
            raise NewsmapError('--db is required for this command')
        db = Database(args.db, create=args.command == 'init')
        if args.command in {'init', 'doctor'}:
            result = db.doctor()
        elif args.command == 'rebuild':
            result = db.rebuild_heads()
        elif args.command == 'register':
            result = db.register(args.namespace, args.vault, relocate=args.relocate, actor=args.actor)
        elif args.command == 'import':
            result = import_batch(db, args.namespace, args.vault, args.scope, max_items=args.max_items)
        elif args.command in {'list', 'inspect'}:
            result = packages(db)
            if args.command == 'inspect':
                result = [r for r in result if r['namespace'] == args.namespace and r['item'] == args.item
                          and (not args.bundle or r['bundle'] == args.bundle)]
            else:
                result = [{k: v for k, v in r.items() if k not in {'record', 'evidence', 'envelope_digest'}} for r in result]
        elif args.command == 'select':
            result = db.select(args.namespace, args.item, args.bundle, args.expect_current, args.actor)
        elif args.command == 'history':
            with db.transaction(write=False):
                result = {t: [dict(r) for r in db.conn.execute('SELECT * FROM ' + t)]
                          for t in ('events', 'batches', 'quarantine')}
        elif args.command == 'backup':
            result = db.backup(args.destination)
        elif args.command == 'restore-check':
            restored = Database(args.restored)
            try:
                result = {'restored': restored.doctor(), 'canonical_equal': db.canonical() == restored.canonical()}
            finally:
                restored.close()
        elif args.command in {'story', 'topic', 'subject'}:
            if args.action == 'create':
                result = {'decision': editorial.story_create(db, args.title, args.topic, args.rationale, args.evidence, args.actor)}
            else:
                result = {'decision': editorial.assign(db, args.command + '_assign', args.target, args.value,
                                                       args.rationale, args.evidence, args.actor)}
        elif args.command == 'identity' and args.action == 'list':
            result = editorial.identities(packages(db))
        elif args.command == 'identity' and args.action == 'link':
            result = {'decision': editorial.identity_link(db, args.left, args.right, args.rationale, args.evidence, args.actor)}
        elif args.command in {'identity', 'assignment'} and args.action == 'revoke':
            result = {'decision': editorial.premise_revoke(db, args.decision, args.rationale, args.actor)}
        elif args.command == 'relation':
            result = {'proposal': editorial.propose(db, args.left, args.right, args.type, args.context,
                                                   args.rationale, args.evidence, args.premises, args.actor)}
        elif args.command == 'review':
            if args.action == 'show':
                result = editorial.show(db, args.proposal)
            else:
                result = {'decision': editorial.review(db, args.proposal, args.action, args.expect_token, args.rationale, args.actor)}
        elif args.command == 'view-data':
            result = editorial.snapshot(db)
        elif args.command in {'export-local', 'export-public'}:
            from .export import export_view
            allowlist = None
            if args.command == 'export-public':
                if args.allowlist.stat().st_size > 1024 * 1024:
                    raise NewsmapError('allowlist exceeds 1 MiB')
                allowlist = parse_json(args.allowlist.read_bytes())
            result = export_view(db, args.output, allowlist)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (NewsmapError, sqlite3.Error, OSError) as exc:
        print(json.dumps({'error': getattr(exc, 'code', type(exc).__name__),
                          'message': str(exc) if isinstance(exc, NewsmapError) else type(exc).__name__}, ensure_ascii=False))
        return 2
    finally:
        if db is not None:
            db.close()


if __name__ == '__main__':
    raise SystemExit(main())

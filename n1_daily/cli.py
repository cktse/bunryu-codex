import argparse
from contextlib import closing
import hashlib
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys

from .config import Config, SecretSource, private_path, read_object
from .content import assignment, canonical, check_lesson, valid_date
from .errors import InputError
from .gmail import Sender
from .http import sanitize
from .jsonio import dumps
from .llm import Model
from .render import email_message, render_html, render_markdown, render_text
from .store import Store, run_lock
from .workflow import clock, delivery_complete, generate, publish, resolve_email, validate_resolution

PROJECT = Path(__file__).resolve().parent.parent
STATUS_FIELDS = ('date', 'status', 'phase', 'attempt_count', 'first_published_at', 'failure_reasons',
                 'context_reconstructed', 'email_status', 'email_attempt_at', 'email_completed_at', 'email_error',
                 'markdown_status', 'markdown_attempt_at', 'markdown_completed_at', 'markdown_error',
                 'index_status', 'index_attempt_at', 'index_completed_at', 'index_error')


def date_argument(value):
    if not valid_date(value):
        raise argparse.ArgumentTypeError('date must use YYYY-MM-DD')
    return value


def parser():
    root = argparse.ArgumentParser(prog='python -m n1_daily')
    commands = root.add_subparsers(dest='command', required=True)
    for name in ('preview', 'validate', 'generate', 'publish', 'daily', 'status', 'resolve-email', 'schedule'):
        command = commands.add_parser(name)
        for field in ('config', 'secrets', 'db'):
            command.add_argument('--' + field, type=Path)
        if name in ('preview', 'validate'):
            selector = command.add_mutually_exclusive_group()
            selector.add_argument('--date', type=date_argument)
            selector.add_argument('--lesson', type=Path)
        elif name != 'schedule':
            command.add_argument('--date', type=date_argument)
        if name in ('preview', 'schedule'):
            command.add_argument('--output', type=Path)
        if name in ('preview', 'publish', 'daily'):
            command.add_argument('--ruby-mode', choices=('html', 'parentheses'))
        if name in ('publish', 'daily'):
            command.add_argument('--activate-email', action='store_true')
            command.add_argument('--activate-vault', action='store_true')
        if name == 'resolve-email':
            choice = command.add_mutually_exclusive_group(required=True)
            choice.add_argument('--retry', action='store_true')
            choice.add_argument('--accepted-id')
            command.add_argument('--accepted-at')
            command.add_argument('--mailbox-checked', action='store_true')
    return root


def notification():
    if sys.platform == 'darwin':
        try:
            subprocess.run(['osascript', '-e', 'display notification "Daily N1 needs attention; inspect status for the saved reasons." with title "Daily N1"'],
                           capture_output=True, timeout=5, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass


def report(record):
    print(f"{record['date']}: {record['status']} (phase {record['phase']}, draft {record['attempt_count']}); "
          f"email={record['email_status']}, Markdown={record['markdown_status']}, index={record['index_status']}")
    reasons = record['failure_reasons'] + [record[field] for field in ('email_error', 'markdown_error', 'index_error') if record[field]]
    for reason in dict.fromkeys(reasons):
        print(sanitize(reason), file=sys.stderr)


def preview_input(args, config):
    if not args.date and not args.lesson:
        lesson = canonical(read_object(PROJECT / 'examples' / 'sample-lesson.json'))
        return lesson, check_lesson(lesson, assignment(lesson['date'])), 'Sample assignment checked; production history and LLM review were not checked.'
    if args.lesson:
        lesson = canonical(read_object(args.lesson.expanduser().resolve()))
        errors = check_lesson(lesson)
        if errors or not (args.config or args.db):
            return lesson, errors, 'Standalone structure checked; saved assignment, production history, and LLM review were not checked.'
        day = lesson['date']
    else:
        day = args.date
    with closing(Store(config.check_database(), readonly=True)) as store:
        record = store.get(day)
        if not record or (not args.lesson and record['phase'] != 'ready'):
            raise InputError('Saved preview requires an existing ready lesson; contextual validation requires its saved assignment')
        if not args.lesson:
            lesson = record['lesson']
        errors = check_lesson(lesson, record['assignment'], store.history(day))
    return lesson, errors, 'Mechanical content, saved assignment, and published history checked; linguistic correctness was not checked.'


def preview(args, config):
    lesson, errors, scope = preview_input(args, config)
    print(scope)
    if errors:
        for error in errors:
            print(sanitize(error), file=sys.stderr)
        return 1
    if args.command == 'validate':
        print('Valid.')
        return 0
    output = config.check_output(args.output or config.preview_path)
    output.mkdir(parents=True, exist_ok=True)
    artifacts = {'lesson.html': render_html(lesson).encode(), 'lesson.md': render_markdown(lesson, config.ruby_mode).encode(),
                 'lesson.txt': render_text(lesson).encode(), 'lesson.json': (dumps(canonical(lesson)) + '\n').encode(),
                 'lesson.eml': email_message(lesson, config.sender or 'preview@example.test', config.recipient or 'reader@example.test')}
    for name, data in artifacts.items():
        destination = config.check_output(output / name)
        destination.write_bytes(data)
    print(f'Preview: {output}')
    return 0


def schedule(args, config):
    if not args.config or not args.secrets or not args.output:
        raise InputError('schedule requires --config, --secrets, and --output')
    output = config.check_output(args.output)
    secret_path = private_path(args.secrets, config.vault_path)
    arguments = [str(Path(sys.executable).resolve()), '-m', 'n1_daily', 'daily',
                 '--config', str(args.config.expanduser().resolve()), '--secrets', str(secret_path),
                 '--activate-email', '--activate-vault']
    if args.db:
        arguments += ['--db', str(config.db_path)]
    template = {'Label': 'local.n1-daily', 'ProgramArguments': arguments, 'WorkingDirectory': str(PROJECT),
                'RunAtLoad': True, 'StartInterval': 60, 'StandardOutPath': '/tmp/n1-daily-launchd.log',
                'StandardErrorPath': '/tmp/n1-daily-launchd-error.log'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(plistlib.dumps(template))
    print(f'Inactive template: {output}. Edit timing/channels before installing: it runs immediately on load and every 60 seconds, with both delivery channels activated.')
    return 0


def writable(args, config, day):
    if args.command in ('publish', 'resolve-email') and not config.db_path.is_file():
        raise InputError('This command requires an existing database')
    with run_lock(config.db_path):
        if args.command == 'resolve-email':
            # Validate even the saved timestamps read-only before migrations or send recovery.
            with closing(Store(config.db_path, readonly=True)) as reader:
                validate_resolution(reader.get(day), mailbox_checked=args.mailbox_checked, retry=args.retry,
                                    accepted_id=args.accepted_id, accepted_at=args.accepted_at, now=clock())
        with closing(Store(config.db_path, create=args.command in ('generate', 'daily'))) as store:
            if args.command != 'resolve-email':
                store.recover_inflight()
            secrets = SecretSource(args.secrets, config)
            if args.command == 'resolve-email':
                record = resolve_email(store, day, mailbox_checked=args.mailbox_checked, retry=args.retry,
                                       accepted_id=args.accepted_id, accepted_at=args.accepted_at, now=clock())
                report(record)
                return 0
            if args.command in ('generate', 'daily'):
                record = generate(store, day, Model(config, secrets))
                if record['phase'] != 'ready':
                    report(record)
                    notify_failure(store, record, args.command)
                    return 1
            if args.command in ('publish', 'daily'):
                record = publish(store, day, config, Sender(config, secrets), email=args.activate_email,
                                 vault=args.activate_vault, now=clock)
                complete = delivery_complete(record, args.activate_email, args.activate_vault)
            else:
                complete = True
            report(record)
            if not complete:
                notify_failure(store, record, args.command)
            return 0 if complete else 1


def notify_failure(store, record, command):
    fields = ('status', 'phase', 'failure_reasons', 'email_status', 'email_error', 'markdown_status', 'markdown_error', 'index_status', 'index_error')
    value = hashlib.sha256(dumps({key: record[key] for key in fields}).encode()).hexdigest()
    if command != 'daily' or value != record['notification_fingerprint']:
        notification()
        store.update(record['date'], notification_fingerprint=value)


def main(argv=None):
    try:
        try:
            args = parser().parse_args(argv)
        except SystemExit as exc:
            return exc.code
        config = Config.load(args.config) if args.config else Config()
        if args.db:
            config.db_path = args.db.expanduser().resolve()
        if getattr(args, 'ruby_mode', None):
            config.ruby_mode = args.ruby_mode
        config.check_database()
        if args.command in ('generate', 'publish', 'daily') and not args.config:
            raise InputError(f'{args.command} requires --config')
        if args.command in ('publish', 'daily') and not (args.activate_email or args.activate_vault):
            raise InputError('Publication requires --activate-email and/or --activate-vault')
        if args.command in ('preview', 'validate'):
            return preview(args, config)
        if args.command == 'schedule':
            return schedule(args, config)
        today = clock().date().isoformat()
        day = args.date or today
        if args.command in ('publish', 'daily') and day != today:
            raise InputError('Publication is limited to today; automatic backlog delivery is not supported')
        config.check_database()
        if args.command == 'status':
            with closing(Store(config.db_path, readonly=True)) as store:
                record = store.get(day)
                if record is None:
                    raise InputError(f'No saved record for {day}')
                print(dumps({key: record[key] for key in STATUS_FIELDS}))
            return 0
        return writable(args, config, day)
    except (InputError, OSError, sqlite3.Error) as exc:
        print(sanitize(f'{type(exc).__name__}: {exc}'), file=sys.stderr)
        return 2

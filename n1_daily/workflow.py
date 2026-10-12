"""Generation checkpoints and independent publication channels."""
from datetime import datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path
from zoneinfo import ZoneInfo

from .content import canonical, check_lesson, check_review, fingerprint
from .errors import DeliveryError, InputError, ModelOutputError, ServiceError, StorageError
from .files import confined, index_text, lesson_path, write_file
from .http import sanitize
from .render import email_message, render_markdown

TZ = ZoneInfo('Asia/Hong_Kong')


def clock():
    return datetime.now(TZ)


def safe_error(error, service=None):
    secrets = getattr(service, 'secrets', None)
    return sanitize(str(error), secrets.redactions() if secrets else ())


def review_binding(record, report):
    context = {'generation': record['context'], 'review_stages': record['review_stages']}
    return fingerprint(record['lesson'], record['assignment'], context, report)


def content_decision(store, record, reasons, **results):
    """Commit a completed content decision together with its next phase."""
    if not reasons:
        results.update(status='ready', phase='ready', failure_reasons=[], correction_feedback=[])
        report = results.get('review_result', record['review_result'])
        results['binding'] = review_binding(record, report)
    elif record['attempt_count'] < 2:
        results.update(status='draft', phase='generating', attempt_count=2,
                       failure_reasons=reasons, correction_feedback=reasons, binding=None)
    else:
        results.update(status='failed', phase='failed', failure_reasons=reasons, correction_feedback=reasons)
    store.update(record['date'], **results)


def generate(store, day, model):
    record = store.ensure(day)
    while record['phase'] not in ('ready', 'failed') and record['status'] not in ('missed', 'partial'):
        phase = record['phase']
        if phase not in ('generating', 'checking', 'reviewing'):
            raise InputError(f'Stored {day}.phase is unsupported')
        if phase == 'checking':
            reasons = record['check_result'] or check_lesson(record['lesson'], record['assignment'], record['context']['history'])
            if reasons:
                content_decision(store, record, reasons)
            else:
                store.update(day, phase='reviewing', status='draft', check_result=[])
        else:
            try:
                if hasattr(model, 'prepare'):
                    model.prepare(phase)
                if phase == 'generating':
                    if record['attempt_count'] == 0:
                        store.update(day, attempt_count=1)
                    try:
                        draft = model.draft(record['context'], record['correction_feedback'], record['lesson'])
                        draft = canonical(draft) if isinstance(draft, dict) else draft
                        if isinstance(draft, dict):
                            draft.update(generation_model=model.generation_model, review_model=model.review_model, prompt_version='v2')
                        problems = check_lesson(draft, record['assignment'], record['context']['history'])
                    except ModelOutputError as exc:
                        draft, problems = None, [safe_error(exc, model)]
                    store.update(day, lesson=draft, check_result=problems, phase='checking', status='draft',
                                 review_result=None, binding=None)
                else:
                    try:
                        report = model.review(record['lesson'], record['context'])
                        reasons = check_review(record['lesson'], report)
                    except ModelOutputError as exc:
                        report, reasons = None, [safe_error(exc, model)]
                    # Review and its decision are one transaction: no completed review is replayed.
                    content_decision(store, record, reasons, review_result=report)
            except StorageError:
                raise
            except (ServiceError, InputError) as exc:
                store.update(day, status='failed', failure_reasons=[safe_error(exc, model)])
                break
        record = store.get(day)
    return store.get(day)


def publication_checks(store, record):
    record = store.ensure_context(record)
    if record['phase'] != 'ready':
        return ['Lesson is not ready for publication']
    reasons = check_lesson(record['lesson'], record['assignment'], record['context']['history'])
    if not reasons:
        reasons += check_review(record['lesson'], record['review_result'])
    if reasons:
        return reasons
    binding = review_binding(record, record['review_result'])
    if record['binding'] and record['binding'] != binding:
        return ['Saved content/review binding mismatch; inspect the lesson and database edits']
    if not record['binding']:
        # Legacy baseline establishes integrity from this point, not before migration.
        store.update(record['date'], binding=binding)
    if not record['first_published_at']:
        reasons += check_lesson(record['lesson'], record['assignment'], store.history(record['date']))
        reasons += store.stale_reviews(record)
    return reasons


def send_email(store, record, config, sender, now):
    day = record['date']
    if record['email_status'] in ('unknown', 'sending'):
        return
    try:
        config.require('sender', 'recipient')
        message = record['message_bytes']
        if message:
            parsed = BytesParser(policy=policy.default).parsebytes(message)
            saved_from, saved_to = record['email_from'] or str(parsed['From']), record['email_to'] or str(parsed['To'])
            if (saved_from != config.sender or saved_to != config.recipient
                    or str(parsed['From']) != saved_from or str(parsed['To']) != saved_to):
                raise InputError('Saved email sender/recipient differs from configuration; restore the frozen destination')
        else:
            message = email_message(record['lesson'], config.sender, config.recipient, now=now())
            store.update(day, message_bytes=message, email_from=config.sender, email_to=config.recipient)
        sender.prepare()
    except StorageError:
        raise
    except (InputError, ServiceError, OSError) as exc:
        store.update(day, email_status='failed', email_error=safe_error(exc, sender))
        return
    store.update(day, email_status='sending', email_attempt_at=now().isoformat(), email_error=None)
    try:
        identity = sender.send(message)
        if not isinstance(identity, str) or not identity.strip():
            raise DeliveryError('Gmail acceptance has no message ID', uncertain=True)
    except Exception as exc:
        uncertain = not isinstance(exc, DeliveryError) or exc.uncertain
        store.update(day, email_status='unknown' if uncertain else 'failed', email_error=safe_error(exc, sender))
        return
    completed = now()
    store.channel_success(day, completed, email_status='sent', gmail_id=identity,
                          email_completed_at=completed.isoformat(), email_error=None)


def write_vault(store, record, config, now):
    day = record['date']
    field = 'markdown_error' if record['markdown_status'] != 'written' else 'index_error'
    try:
        config.require('vault_path')
        root = Path(config.vault_path).expanduser().resolve()
        if not root.is_dir():
            raise InputError(f'Vault root must already exist as a directory: {root}')
        path = lesson_path(root, day)
        if (record['vault_root'] and record['vault_root'] != str(root)) or (record['markdown_path'] and record['markdown_path'] != str(path)):
            raise InputError('Vault destination changed for unfinished lesson/index work')
        confined(root, path)
        confined(root, root / 'Index.md')
        store.update(day, vault_root=str(root), markdown_path=str(path))
        if record['markdown_status'] != 'written':
            store.update(day, markdown_attempt_at=now().isoformat())
            checksum = write_file(store, root, path, render_markdown(record['lesson'], config.ruby_mode))
            completed = now()
            store.channel_success(day, completed, markdown_status='written', markdown_checksum=checksum,
                                  markdown_completed_at=completed.isoformat(), markdown_error=None)
        field = 'index_error'
        if record['index_status'] != 'written':
            store.update(day, index_attempt_at=now().isoformat())
            write_file(store, root, root / 'Index.md', index_text(store, root))
            store.update(day, index_status='written', index_completed_at=now().isoformat(), index_error=None)
    except StorageError:
        raise
    except (InputError, OSError) as exc:
        status_field = 'markdown_status' if field == 'markdown_error' else 'index_status'
        store.update(day, **{field: safe_error(exc), status_field: 'failed'})


def delivery_complete(record, email, vault):
    return ((not email or record['email_status'] == 'sent')
            and (not vault or record['markdown_status'] == record['index_status'] == 'written'))


def publish(store, day, config, sender, *, email=False, vault=False, now=clock):
    record = store.get(day)
    if record is None:
        raise InputError(f'No saved lesson for {day}')
    if delivery_complete(record, email, vault):
        return record
    reasons = publication_checks(store, record)
    if reasons:
        store.update(day, failure_reasons=reasons)
        return store.get(day)
    store.update(day, failure_reasons=[])
    for active, channel in ((email, 'email'), (vault, 'vault')):
        record = store.get(day)
        complete = record['email_status'] == 'sent' if channel == 'email' else record['markdown_status'] == record['index_status'] == 'written'
        if not active or complete:
            continue
        if now().astimezone(TZ).date().isoformat() != day:
            store.update(day, failure_reasons=['Selected date has ended; no additional old-date channel will start'])
            break
        if channel == 'email':
            send_email(store, record, config, sender, now)
        else:
            write_vault(store, record, config, now)
    return store.get(day)


def validate_resolution(record, *, mailbox_checked=False, retry=False, accepted_id=None, accepted_at=None, now=None):
    now = now or clock()
    if not mailbox_checked:
        raise InputError('Resolution requires --mailbox-checked')
    if bool(retry) == (accepted_id is not None) or (retry and accepted_at is not None):
        raise InputError('Choose exactly --retry or --accepted-id with --accepted-at')
    if record is None or record['email_status'] not in ('unknown', 'sending') or not record['message_bytes']:
        raise InputError('Resolution requires an existing unknown/interrupted send record')
    if retry:
        return None
    if not isinstance(accepted_id, str) or not accepted_id.strip():
        raise InputError('--accepted-id must be nonempty')
    try:
        accepted = datetime.fromisoformat(accepted_at) if isinstance(accepted_at, str) else accepted_at
        attempted = datetime.fromisoformat(record['email_attempt_at'])
        if (not isinstance(accepted, datetime) or accepted.tzinfo is None or accepted.utcoffset() is None
                or attempted.tzinfo is None or accepted > now or accepted < attempted):
            raise ValueError()
    except (ValueError, TypeError):
        raise InputError('--accepted-at must have an offset and lie between the recorded send attempt and now') from None
    return accepted.astimezone(TZ)


def resolve_email(store, day, *, mailbox_checked=False, retry=False, accepted_id=None, accepted_at=None, now=None):
    record = store.get(day)
    accepted = validate_resolution(record, mailbox_checked=mailbox_checked, retry=retry,
                                   accepted_id=accepted_id, accepted_at=accepted_at, now=now)
    if retry:
        store.update(day, email_status='failed', email_error='Mailbox checked; explicit retry authorized')
    else:
        store.ensure_context(record)
        store.channel_success(day, accepted, email_status='sent', gmail_id=accepted_id,
                              email_completed_at=accepted.isoformat(), email_error=None)
    return store.get(day)

"""Persist generation progress; deliver only saved ready content."""

from datetime import time
from pathlib import Path

from .content import check_lesson, check_review
from .files import write_generated
from .render import message_bytes, render_markdown
from .services import DefiniteFailure, InvalidResponse, ServiceUnavailable


def generate(store, day, model):
    record = store.get(day) or store.begin(day, store.assignment(day))
    if record["status"] == "ready" or record["phase"] == "failed":
        return record
    if model is None:
        raise ValueError("LLM configuration is required to generate an unsaved lesson")
    if record["attempt_count"] == 0:
        store.update(day, attempt_count=1)
    context = store.context(day, record["assignment"])
    history = store.history(before=day)
    while True:
        record = store.get(day)
        errors = None
        try:
            if record["phase"] == "generating":
                try:
                    draft = model.draft(context, record["correction_feedback"], record["lesson"])
                except InvalidResponse as error:
                    draft = None
                    errors = [f"lesson: {error}"]
                if isinstance(draft, dict):
                    draft.update(generation_model=model.generation_model, review_model=model.review_model, prompt_version="v1")
                store.update(day, lesson=draft, review_result=None, status="draft", phase="checking")
                record = store.get(day)
            if record["phase"] == "checking":
                errors = errors or check_lesson(record["lesson"], record["assignment"], history)
                if not errors:
                    store.update(day, phase="reviewing")
                    record = store.get(day)
            if record["phase"] == "reviewing":
                try:
                    review = model.review(record["lesson"], context)
                    errors = check_review(record["lesson"], review)
                except InvalidResponse as error:
                    review, errors = None, [f"review: {error}"]
                store.update(day, review_result=review)
                if not errors:
                    lesson = record["lesson"]
                    lesson.update(review_model=model.review_model, prompt_version="v1")
                    store.update(day, lesson=lesson, status="ready", phase="ready", failure_reasons=[])
                    return store.get(day)
        except (ServiceUnavailable, OSError, TimeoutError) as error:
            store.update(day, status="failed", failure_reasons=[f"Model unavailable: {error}"])
            return store.get(day)
        if errors:
            if record["attempt_count"] >= 2:
                store.update(day, status="failed", phase="failed", failure_reasons=errors)
                return store.get(day)
            store.update(day, attempt_count=2, status="draft", phase="generating", failure_reasons=errors, correction_feedback=errors)


def deliver(store, day, sender, vault, from_address, recipient, now, ruby_mode="html"):
    record = store.get(day)
    if not record or record["status"] != "ready":
        raise ValueError("delivery requires a saved ready lesson")
    lesson = record["lesson"]
    if vault:
        vault = Path(vault).expanduser().resolve()
    if sender and record["email_status"] in ("pending", "failed"):
        try:
            # Local credential failures are definite failures. Validate before
            # recording a send in progress; other channels remain available.
            if hasattr(sender, "prepare"):
                sender.prepare()
            payload = record["message_bytes"] or message_bytes(lesson, from_address, recipient)
            # Commit outgoing bytes and intent before making a remote request.
            store.update(day, message_bytes=payload, email_status="sending", email_attempt_at=now.isoformat(), email_error=None)
            message_id = sender.send(payload)
            if not message_id:
                raise ValueError("Gmail returned no message ID")
        except DefiniteFailure as error:
            store.update(day, email_status="failed", email_attempt_at=now.isoformat(), email_error=str(error))
        except Exception as error:
            # Unexpected failures during send are also uncertain. An operator
            # must inspect the mailbox before authorizing a resend.
            store.update(day, email_status="unknown", email_error=str(error))
        else:
            store.channel_success(day, now, email_status="sent", gmail_id=message_id, email_error=None)
    record = store.get(day)
    if vault and record["markdown_status"] != "written":
        path = vault / "Lessons" / day[:4] / day[5:7] / f"{day}.md"
        store.update(day, markdown_attempt_at=now.isoformat(), markdown_path=str(path.absolute()))
        try:
            digest = write_generated(store, path, render_markdown(lesson, ruby_mode))
        except (OSError, ValueError) as error:
            store.update(day, markdown_status="failed", markdown_error=str(error))
        else:
            store.channel_success(day, now, markdown_status="written", markdown_checksum=digest, markdown_error=None)
    record = store.get(day)
    if vault and record["markdown_status"] == "written" and record["index_status"] != "written":
        # The index includes only lessons actually written, even when an email
        # alone has already established publication for another date.
        rows = store.conn.execute("SELECT date FROM lessons WHERE markdown_status='written' ORDER BY date DESC")
        lines = ["# JLPT N1 Lessons", ""]
        for row in rows:
            old = store.get(row["date"])["lesson"]
            d = old["date"]
            lines.append(f"- [[Lessons/{d[:4]}/{d[5:7]}/{d}|{d} · {old['theme']}]]")
        try:
            write_generated(store, Path(vault) / "Index.md", "\n".join(lines) + "\n")
        except (OSError, ValueError) as error:
            store.update(day, index_status="failed", index_error=str(error))
        else:
            store.update(day, index_status="written", index_error=None)
    return store.get(day)


def daily(store, model, sender, vault, from_address, recipient, now,
          generation_time=time(7, 45), publication_time=time(8), cutoff=time(12), ruby_mode="html", current_time=None):
    day = now.date().isoformat()
    clock = now.time().replace(tzinfo=None)
    if clock < generation_time:
        return "waiting"
    if clock > cutoff:
        record = store.get(day) or store.begin(day, store.assignment(day))
        complete = (record["first_published_at"] and (not sender or record["email_status"] == "sent")
                    and (not vault or (record["markdown_status"] == "written" and record["index_status"] == "written")))
        if complete:
            return "published"
        missed = "Missed publication window; no backlog will be sent"
        if missed in record["failure_reasons"]:
            return "closed"
        # A saved ready lesson remains usable for explicit rendering later.
        values = {"failure_reasons": record["failure_reasons"] + [missed]}
        if record["status"] != "ready":
            values.update(status="failed", phase="failed")
        store.update(day, **values)
        return "missed"
    record = generate(store, day, model)
    if record["status"] != "ready":
        return "failed"
    publication_now = current_time() if current_time else now
    publication_clock = publication_now.time().replace(tzinfo=None)
    if publication_now.date() != now.date() or publication_clock > cutoff:
        store.update(day, failure_reasons=["Generation completed after the publication window; saved content remains ready"])
        return "missed"
    if publication_clock < publication_time:
        return "ready"
    record = deliver(store, day, sender, vault, from_address, recipient, publication_now, ruby_mode)
    if sender and record["email_status"] != "sent":
        return "failed"
    if vault and (record["markdown_status"] != "written" or record["index_status"] != "written"):
        return "failed"
    return "published" if record["first_published_at"] else "ready"

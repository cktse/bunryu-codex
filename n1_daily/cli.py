"""Local previews are safe by default. Live channels require explicit flags."""

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import plistlib
import sqlite3
import subprocess
import sys
from zoneinfo import ZoneInfo

from .config import Config, ConfigError, SecretSource, check_private_path
from .content import check_lesson, sample_assignment
from .files import atomic_write
from .llm import JsonModel
from .render import message_bytes, render_html, render_markdown, render_text
from .services import DefiniteFailure, GmailSender
from .store import Store, run_lock
from .workflow import close_window, daily, deliver, generate


class ContentCheckFailure(ValueError):
    """Checks ran and rejected content; distinct from an input error."""


def parser():
    root = argparse.ArgumentParser(description="Daily original JLPT N1 practice")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("preview", "validate", "generate", "publish", "daily", "status", "resolve-email", "schedule"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, help="explicit path to config.json")
        command.add_argument("--secrets", type=Path, help="explicit path to private secrets.json; loaded only for service requests")
        command.add_argument("--db", type=Path)
        if name in ("preview", "validate"):
            input_choice = command.add_mutually_exclusive_group()
            input_choice.add_argument("--date", type=date.fromisoformat)
            input_choice.add_argument("--lesson", type=Path)
        elif name not in ("schedule", "daily"):
            command.add_argument("--date", type=date.fromisoformat)
        if name in ("preview", "schedule"):
            command.add_argument("--output", type=Path)
        if name in ("preview", "publish", "daily"):
            command.add_argument("--ruby-mode", choices=("html", "parentheses"))
        if name in ("publish", "daily"):
            command.add_argument("--activate-email", action="store_true")
            command.add_argument("--activate-vault", action="store_true")
        if name == "resolve-email":
            choice = command.add_mutually_exclusive_group(required=True)
            choice.add_argument("--accepted-id")
            choice.add_argument("--retry", action="store_true")
            command.add_argument("--accepted-at", type=datetime.fromisoformat)
            command.add_argument("--mailbox-checked", action="store_true")
    return root


def notification(message):
    print(message, file=sys.stderr)
    if sys.platform == "darwin":
        try:
            subprocess.run(["osascript", "-e", f'display notification {json.dumps(message)} with title "Daily N1"'],
                           timeout=5, capture_output=True, check=False)
        except (OSError, subprocess.TimeoutExpired):
            pass


class ConfiguredModel:
    def __init__(self, config, secrets):
        self.config, self.secrets = config, secrets
        self.generation_model = config.generation_model
        self.review_model = config.review_model
        self._client = None

    def client(self):
        if self._client is None:
            self.config.require("llm_endpoint", "generation_model", "review_model")
            credentials = self.secrets.require("llm_api_key")
            self._client = JsonModel(self.config.llm_endpoint, credentials.llm_api_key,
                                     self.generation_model, self.review_model, self.config.timeout_seconds)
        return self._client

    def draft(self, context, failures, previous):
        return self.client().draft(context, failures, previous)

    def review(self, lesson, context):
        return self.client().review(lesson, context)

    def prepare(self):
        self.client()


class ConfiguredSender:
    def __init__(self, config, secrets):
        self.config, self.secrets = config, secrets
        self._client = None

    def prepare(self):
        if self._client is None:
            try:
                self.config.require("sender", "recipient", "google_client_id")
                credentials = self.secrets.require("google_client_secret", "google_refresh_token")
            except ConfigError as error:
                raise DefiniteFailure(str(error)) from None
            self._client = GmailSender(self.config.google_client_id, credentials.google_client_secret,
                                       credentials.google_refresh_token, self.config.timeout_seconds)

    def send(self, payload):
        self.prepare()
        return self._client.send(payload)


def channels(args, config, secrets):
    if not args.activate_email and not args.activate_vault:
        raise ValueError("Live publication requires --activate-email and/or --activate-vault after explicit user activation")
    sender, vault = None, None
    if args.activate_email:
        sender = ConfiguredSender(config, secrets)
    if args.activate_vault:
        vault = config.vault_path
    return sender, vault


def preview_lesson(args, config):
    def checked(lesson, assignment=None, history=None):
        errors = check_lesson(lesson, assignment, history)
        if errors:
            raise ContentCheckFailure("\n".join(errors))

    def read_record(day, require_ready):
        config.check_database_path()
        store = Store(config.db_path, readonly=True)
        try:
            record = store.get(day)
            if not record or (require_ready and record["status"] != "ready"):
                raise ValueError("The requested date has no saved ready lesson" if require_ready else "No saved assignment for the input date")
            return record, store.history(before=day, published_only=True)
        finally:
            store.close()

    full_scope = "All mechanical checks passed against the saved assignment and prior published history; linguistic review is still required"
    if args.date:
        record, history = read_record(args.date.isoformat(), True)
        checked(record["lesson"], record["assignment"], history)
        return record["lesson"], full_scope

    path = args.lesson or Path(__file__).parents[1] / "examples/sample-lesson.json"

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Lesson JSON contains duplicate keys")
            result[key] = value
        return result

    lesson = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    if not args.lesson:
        assignment = sample_assignment(lesson.get("date", "")) if isinstance(lesson, dict) else sample_assignment("")
        checked(lesson, assignment)
        return lesson, "Sample structure and assignment checks passed; production-history checks and LLM review were not performed"
    checked(lesson)
    if args.config or args.db:
        record, history = read_record(lesson["date"], False)
        checked(lesson, record["assignment"], history)
        return lesson, full_scope
    return lesson, "Local structure checks passed; assignment/history checks and LLM review were not performed"


def export_schedule(args, config):
    if not args.output or not args.secrets:
        raise ValueError("Schedule export requires --output and --secrets file paths")
    secret_path = check_private_path(args.secrets, config.vault_path)
    config.check_preview_path(args.output)
    # This only creates a reviewable artifact. Installing it requires a later
    # explicit activation request; the application never invokes launchctl.
    data = {"Label": "local.n1-daily", "ProgramArguments": [sys.executable, "-m", "n1_daily", "daily",
            "--config", str(args.config.expanduser().resolve()), "--secrets", str(secret_path),
            "--activate-email", "--activate-vault"],
            "WorkingDirectory": str(Path(__file__).parents[1]), "RunAtLoad": True,
            "StartInterval": 60, "StandardOutPath": "/tmp/n1-daily-launchd.log",
            "StandardErrorPath": "/tmp/n1-daily-launchd-error.log"}
    if args.db:
        data["ProgramArguments"] += ["--db", str(args.db.expanduser().resolve())]
    atomic_write(args.output, plistlib.dumps(data))
    print(f"Exported inactive schedule to {args.output}; no job was installed or loaded")


def main(arguments=None):
    args = parser().parse_args(arguments)
    try:
        if args.command in ("generate", "publish", "daily", "schedule") and not args.config:
            raise ValueError(f"{args.command} requires an explicit --config file path")
        config = Config.from_file(args.config)
        if args.db:
            config.db_path = args.db.expanduser().resolve()
        secrets = SecretSource(args.secrets, config)
        if args.command == "schedule":
            config.check_database_path()
            export_schedule(args, config)
            return 0
        ruby_mode = getattr(args, "ruby_mode", None) or config.ruby_mode
        if args.command in ("preview", "validate"):
            lesson, scope = preview_lesson(args, config)
            if args.command == "validate":
                print(scope)
                return 0
            output = (args.output or config.preview_path).expanduser().resolve()
            config.check_preview_path(output)
            outputs = {"lesson.html": render_html(lesson), "lesson.txt": render_text(lesson),
                       "lesson.md": render_markdown(lesson, ruby_mode), "lesson.json": json.dumps(lesson, ensure_ascii=False, indent=2)}
            for name, text in outputs.items():
                atomic_write(output / name, text.encode("utf-8"))
            atomic_write(output / "lesson.eml", message_bytes(lesson, config.sender or "preview@example.test", config.recipient or "reader@example.test"))
            print(f"{scope}\nPreview: {output}\nEmail: lesson.html / lesson.eml\nObsidian: lesson.md\nNo email was sent; the configured vault was untouched")
            return 0
        sender, vault = channels(args, config, secrets) if args.command in ("publish", "daily") else (None, None)
        config.check_database_path()
        now = datetime.now(ZoneInfo("Asia/Hong_Kong"))
        day = (getattr(args, "date", None) or now.date()).isoformat()
        if args.command == "generate" and (day != now.date().isoformat() or not config.generate_time <= now.time() <= config.cutoff_time):
            raise ValueError("Generation is limited to today between generate_time and cutoff_time")
        if args.command == "publish" and (day != now.date().isoformat() or not config.publish_time <= now.time() <= config.cutoff_time):
            raise ValueError("Publication is limited to today's configured window; use preview to view another date")
        if args.command == "status":
            store = Store(config.db_path, readonly=True)
            try:
                record = store.get(day)
                if not record:
                    raise ValueError("No lesson record for this date")
                print(json.dumps({k: record[k] for k in ("date", "status", "attempt_count", "first_published_at", "failure_reasons", "email_status", "email_error", "markdown_status", "markdown_error", "index_status", "index_error")}, ensure_ascii=False, indent=2))
            finally:
                store.close()
            return 0
        with run_lock(config.db_path):
            store = Store(config.db_path)
            try:
                store.recover_inflight()
                if args.command == "generate":
                    existing = store.get(day)
                    model = None if existing and (existing["status"] == "ready" or existing["phase"] == "failed") else ConfiguredModel(config, secrets)
                    record = generate(store, day, model)
                    print(f"{day}: {record['status']} (draft {record['attempt_count']})")
                    if record["status"] != "ready":
                        notification("Lesson generation failed; inspect the recorded reasons with status")
                        return 1
                elif args.command == "publish":
                    current_time = lambda: datetime.now(ZoneInfo("Asia/Hong_Kong"))
                    record = deliver(store, day, sender, vault, config.sender, config.recipient, now, ruby_mode,
                                     current_time=current_time, cutoff=config.cutoff_time, vault_requested=args.activate_vault)
                    print(f"{day}: email={record['email_status']}, Markdown={record['markdown_status']}, index={record['index_status']}")
                    if (sender and record["email_status"] != "sent") or (args.activate_vault and (record["markdown_status"] != "written" or record["index_status"] != "written")):
                        after = current_time()
                        if after.date() != now.date() or after.time() > config.cutoff_time:
                            close_window(store, day, sender, args.activate_vault)
                        notification("Lesson delivery remains incomplete; inspect status before retrying")
                        return 1
                elif args.command == "daily":
                    # A ready lesson needs no model configuration or network.
                    existing = store.get(day)
                    needs_generation = (config.generate_time <= now.time() <= config.cutoff_time
                                        and not (existing and (existing["status"] == "ready" or existing["phase"] == "failed")))
                    model = ConfiguredModel(config, secrets) if needs_generation else None
                    outcome = daily(store, model, sender, vault, config.sender, config.recipient, now,
                                    config.generate_time, config.publish_time, config.cutoff_time, ruby_mode,
                                    current_time=lambda: datetime.now(ZoneInfo("Asia/Hong_Kong")), vault_requested=args.activate_vault)
                    print(f"{day}: {outcome}")
                    if outcome in ("failed", "missed", "partial"):
                        current = store.get(day)
                        issue_fields = ("status", "failure_reasons", "email_status", "email_error", "markdown_status", "markdown_error", "index_status", "index_error")
                        if not existing or any(existing[field] != current[field] for field in issue_fields):
                            notification(f"Daily N1: {outcome}; inspect status")
                        return 1
                elif args.command == "resolve-email":
                    record = store.get(day)
                    if not record or record["email_status"] != "unknown":
                        raise ValueError("Only an unknown email outcome can be resolved")
                    if not args.mailbox_checked:
                        raise ValueError("Inspect the sending mailbox and specify --mailbox-checked")
                    if args.retry:
                        store.update(day, email_status="failed", email_error="Operator authorized retry after mailbox check")
                    else:
                        if not args.accepted_id.strip():
                            raise ValueError("Provide a nonempty Gmail message ID")
                        if not args.accepted_at or args.accepted_at.tzinfo is None:
                            raise ValueError("Provide the actual timezone-aware Gmail acceptance time with --accepted-at")
                        store.channel_success(day, args.accepted_at.astimezone(ZoneInfo("Asia/Hong_Kong")), email_status="sent", gmail_id=args.accepted_id, email_error=None)
                    print("Recorded the operator's mailbox decision; no email was sent")
            finally:
                store.close()
        return 0
    except ContentCheckFailure as error:
        print(f"Content checks failed: {error}", file=sys.stderr)
        return 1
    except sqlite3.Error:
        print("Error: SQLite storage is unavailable or corrupt", file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

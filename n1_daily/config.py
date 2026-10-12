"""Explicit JSON configuration and deferred, private credential loading."""

from dataclasses import dataclass, field
from datetime import time
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlparse

CONFIG_FIELDS = {"db_path", "preview_path", "vault_path", "sender", "recipient", "timezone",
                 "generate_time", "publish_time", "cutoff_time", "timeout_seconds", "ruby_mode",
                 "llm_endpoint", "generation_model", "review_model", "google_client_id",
                 "generation_reasoning_effort", "review_reasoning_effort"}
REASONING_EFFORTS = {"low", "medium", "high", "xhigh", "max"}
SECRET_FIELDS = {"llm_api_key", "google_client_secret", "google_refresh_token"}
UNSET_FIELDS = {"vault_path", "sender", "recipient", "llm_endpoint", "generation_model",
                "review_model", "google_client_id"}
ADDRESS = re.compile(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+")


def valid_endpoint(endpoint):
    try:
        parsed = urlparse(endpoint)
        parsed.port
        return (parsed.scheme == "https" and bool(parsed.hostname) and bool(parsed.path)
                and parsed.username is None and parsed.password is None and "?" not in endpoint
                and "#" not in endpoint and not any(c.isspace() for c in endpoint))
    except ValueError:
        return False


class ConfigError(ValueError):
    """A diagnostic containing a file and field, with no supplied values."""

    def __init__(self, source, name, reason):
        name = name if re.fullmatch(r"[a-z_]{1,64}", str(name)) else "<field>"
        super().__init__(f"{source}: {name}: {reason}")


def in_icloud(path):
    return any(part in ("Mobile Documents", "iCloud Drive", "com~apple~CloudDocs")
               for part in Path(path).expanduser().resolve().parts)


def beneath(path, root):
    return Path(path).expanduser().resolve().is_relative_to(Path(root).expanduser().resolve())


def in_vault(path):
    path = Path(path).expanduser().resolve()
    return any((p / ".obsidian").is_dir() for p in (path, *path.parents))


def check_private_path(path, vault=None):
    path = Path(path).expanduser().resolve()
    if in_icloud(path) or in_vault(path) or (vault and beneath(path, vault)):
        raise ConfigError(path, "file", "private files must stay outside iCloud and the vault")
    return path


def read_object(path, allowed_fields, private=False):
    def unique_keys(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ConfigError(path, name, "duplicate field")
            result[name] = value
        return result

    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise ConfigError(path, "file", "expected a regular JSON file")
            if private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) not in (0o600, 0o400)):
                raise ConfigError(path, "permissions", "secrets must be owned by the running user with mode 0600 or 0400")
            values = json.load(handle, object_pairs_hook=unique_keys)
    except ConfigError:
        raise
    except (ValueError, UnicodeError):
        raise ConfigError(path, "file", "malformed JSON") from None
    except OSError:
        raise ConfigError(path, "file", "cannot read the JSON file") from None
    if not isinstance(values, dict):
        raise ConfigError(path, "file", "expected a flat JSON object")
    for name in values:
        if name not in allowed_fields:
            raise ConfigError(path, name, "field is not allowed in this file")
    return values


@dataclass
class Config:
    db_path: Path
    preview_path: Path
    vault_path: Path | None
    sender: str
    recipient: str
    timezone: str
    llm_endpoint: str
    generation_model: str
    review_model: str
    generation_reasoning_effort: str
    review_reasoning_effort: str
    google_client_id: str
    timeout_seconds: float
    generate_time: time
    publish_time: time
    cutoff_time: time
    ruby_mode: str
    source: Path | str = field(repr=False)

    @classmethod
    def from_file(cls, path=None):
        source = check_private_path(path) if path is not None else "config.json (defaults)"
        values = read_object(source, CONFIG_FIELDS) if path is not None else {}
        for name, value in values.items():
            if name in ("generation_reasoning_effort", "review_reasoning_effort"):
                if not isinstance(value, str) or value not in REASONING_EFFORTS:
                    raise ConfigError(source, name, "expected low, medium, high, xhigh, or max")
            elif name == "timeout_seconds":
                if type(value) not in (int, float) or not 0 < value <= 300 or not math.isfinite(value):
                    raise ConfigError(source, name, "expected a finite number greater than 0 and at most 300")
            elif not isinstance(value, str) or (not value.strip() and not (name in UNSET_FIELDS and value == "")):
                raise ConfigError(source, name, "expected a nonempty string, or an empty string for an unused setting")

        def clock(name, default):
            value = values.get(name, default)
            if not re.fullmatch(r"\d{2}:\d{2}", value):
                raise ConfigError(source, name, "expected local time in HH:MM format")
            try:
                return time.fromisoformat(value)
            except ValueError:
                raise ConfigError(source, name, "invalid local time") from None

        def data_path(name, default=""):
            value = values.get(name, default)
            if not value:
                return None
            try:
                result = Path(value).expanduser()
                if not result.is_absolute():
                    result = (source.parent if path is not None else Path.cwd()) / result
                return result.resolve()
            except (OSError, ValueError, RuntimeError):
                raise ConfigError(source, name, "invalid local path") from None

        generation = clock("generate_time", "07:45")
        publication = clock("publish_time", "08:00")
        cutoff = clock("cutoff_time", "12:00")
        if generation > publication:
            raise ConfigError(source, "publish_time", "must be at or after generate_time")
        if publication >= cutoff:
            raise ConfigError(source, "cutoff_time", "must be after publish_time")
        if values.get("timezone", "Asia/Hong_Kong") != "Asia/Hong_Kong":
            raise ConfigError(source, "timezone", "only Asia/Hong_Kong is supported")
        if values.get("ruby_mode", "html") not in ("html", "parentheses"):
            raise ConfigError(source, "ruby_mode", "expected html or parentheses")
        endpoint = values.get("llm_endpoint", "")
        if endpoint and not valid_endpoint(endpoint):
            raise ConfigError(source, "llm_endpoint", "expected HTTPS host and path with no userinfo, query, or fragment")
        for name in ("sender", "recipient"):
            if values.get(name) and not ADDRESS.fullmatch(values[name]):
                raise ConfigError(source, name, "expected one email address without a display name or list")
        config = cls(
            db_path=data_path("db_path", "~/Library/Application Support/N1Daily/lessons.sqlite3"),
            preview_path=data_path("preview_path", str(Path(tempfile.gettempdir()) / "n1-daily-preview")),
            vault_path=data_path("vault_path"), sender=values.get("sender", ""), recipient=values.get("recipient", ""),
            timezone="Asia/Hong_Kong", llm_endpoint=endpoint,
            generation_model=values.get("generation_model", ""), review_model=values.get("review_model", ""),
            generation_reasoning_effort=values.get("generation_reasoning_effort", "medium"),
            review_reasoning_effort=values.get("review_reasoning_effort", "medium"),
            google_client_id=values.get("google_client_id", ""), timeout_seconds=values.get("timeout_seconds", 30),
            generate_time=generation, publish_time=publication, cutoff_time=cutoff,
            ruby_mode=values.get("ruby_mode", "html"), source=source)
        if path is not None:
            check_private_path(source, config.vault_path)
        return config

    def require(self, *names):
        for name in names:
            if not getattr(self, name):
                raise ConfigError(self.source, name, "required for this service request")

    def check_database_path(self):
        if in_icloud(self.db_path) or in_vault(self.db_path) or (self.vault_path and beneath(self.db_path, self.vault_path)):
            raise ConfigError(self.source, "db_path", "SQLite must stay outside iCloud and the vault")

    def check_preview_path(self, path):
        if in_icloud(path) or in_vault(path) or (self.vault_path and beneath(path, self.vault_path)):
            raise ConfigError(self.source, "preview_path", "preview output must stay outside iCloud and the vault")
        if any(part in ("LaunchAgents", "LaunchDaemons") for part in Path(path).expanduser().resolve().parts):
            raise ConfigError(self.source, "preview_path", "export output must stay outside launchd job directories")


@dataclass(repr=False, frozen=True)
class Secrets:
    llm_api_key: str = ""
    google_client_secret: str = ""
    google_refresh_token: str = ""


class SecretSource:
    """Keep a path until a service actually requires credentials."""

    def __init__(self, path, config):
        self.path = Path(path).expanduser().resolve() if path is not None else None
        self.config = config
        self._credentials = None

    def require(self, *names):
        if self.path is None:
            raise ConfigError("secrets.json", "file", "--secrets is required for this service request")
        if self._credentials is None:
            path = check_private_path(self.path, self.config.vault_path)
            values = read_object(path, SECRET_FIELDS, private=True)
            for name, value in values.items():
                if not isinstance(value, str) or (value and not value.strip()):
                    raise ConfigError(path, name, "expected a string; unused credentials may be empty")
            self._credentials = Secrets(**values)
        for name in names:
            if not getattr(self._credentials, name):
                raise ConfigError(self.path, name, "required for this service request")
        return self._credentials

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit

from .errors import InputError
from .jsonio import loads


SECRET_FIELDS = {"llm_api_key", "google_client_secret", "google_refresh_token"}
SERVICE_FIELDS = {"sender", "recipient", "llm_endpoint", "generation_model", "review_model", "google_client_id"}
PATH_FIELDS = {"db_path", "preview_path", "vault_path"}
LEGACY_TIMES = {"generate_time", "publish_time", "cutoff_time"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}


def private_path(path, vault=None):
    path = Path(path).expanduser().resolve()
    if {"Mobile Documents", "iCloud Drive", "com~apple~CloudDocs"}.intersection(path.parts):
        raise InputError(f"Private/export path is inside iCloud: {path}")
    if vault and path.is_relative_to(Path(vault).resolve()):
        raise InputError(f"Private/export path is inside the vault: {path}")
    if any((parent / ".obsidian").is_dir() for parent in (path, *path.parents)):
        raise InputError(f"Private/export path is inside an Obsidian vault: {path}")
    return path


def read_object(path, *, secret=False):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                raise InputError("JSON input must be a regular file")
            if secret and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)):
                raise InputError("Secrets must be owned by the current user with mode 0400 or 0600")
            result = loads(stream.read())
    except (OSError, UnicodeError, ValueError) as exc:
        if isinstance(exc, InputError):
            raise
        raise InputError(f"Cannot read valid JSON from {path}: {type(exc).__name__}") from exc
    if not isinstance(result, dict):
        raise InputError("JSON input must be an object")
    return result


def endpoint_valid(value):
    try:
        parts = urlsplit(value)
        port = parts.port
        return (parts.scheme == "https" and bool(parts.hostname) and bool(parts.path)
                and not parts.username and not parts.password and not parts.query
                and not parts.fragment and "?" not in value and "#" not in value
                and not re.search(r"[\s\x00-\x1f\x7f]", value)
                and (port is None or 0 < port <= 65535))
    except ValueError:
        return False


@dataclass
class Config:
    db_path: Path = Path("~/Library/Application Support/N1Daily/lessons.sqlite3").expanduser()
    preview_path: Path = Path(tempfile.gettempdir()) / "n1-daily-preview"
    vault_path: Path | None = None
    sender: str = ""
    recipient: str = ""
    timezone: str = "Asia/Hong_Kong"
    timeout_seconds: float = 30
    ruby_mode: str = "html"
    llm_endpoint: str = ""
    generation_model: str = ""
    review_model: str = ""
    generation_reasoning_effort: str = "medium"
    review_reasoning_effort: str = "medium"
    google_client_id: str = ""

    @classmethod
    def load(cls, path):
        path = private_path(path)
        values = read_object(path)
        if values.keys() - (cls.__dataclass_fields__.keys() | LEGACY_TIMES):
            raise InputError("Unknown configuration field; credentials belong in the secrets file")
        for key, value in values.items():
            if key == "timeout_seconds":
                if type(value) not in (int, float) or not 0 < value <= 300 or not math.isfinite(value):
                    raise InputError("timeout_seconds must be a finite number greater than 0 and at most 300")
            elif not isinstance(value, str) or (not value.strip() and key not in SERVICE_FIELDS | {"vault_path"}):
                raise InputError(f"{key} must be a nonempty string")
            elif key in LEGACY_TIMES and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
                raise InputError(f"{key} must use HH:MM")
            elif key.endswith("reasoning_effort") and value not in EFFORTS:
                raise InputError(f"{key} must be low, medium, high, xhigh, or max")
            elif key in ("sender", "recipient") and value and not re.fullmatch(r"[^\s<>@,;]+@[^\s<>@,;]+\.[^\s<>@,;]+", value):
                raise InputError(f"{key} must be a single email address")
            elif key == "llm_endpoint" and value and not endpoint_valid(value):
                raise InputError("llm_endpoint requires HTTPS host/path without credentials, query, or fragment")
        values = {key: value for key, value in values.items() if key not in LEGACY_TIMES}
        for key in PATH_FIELDS & values.keys():
            value = values[key]
            expanded = Path(value).expanduser() if value else None
            values[key] = (path.parent / expanded).resolve() if expanded else None
        result = cls(**values)
        if result.timezone != "Asia/Hong_Kong" or result.ruby_mode not in ("html", "parentheses"):
            raise InputError("timezone must be Asia/Hong_Kong; ruby_mode must be html or parentheses")
        private_path(path, result.vault_path)
        return result

    def require(self, *fields):
        missing = [field for field in fields if not getattr(self, field)]
        if missing:
            raise InputError("Missing service configuration: " + ", ".join(missing))

    def check_database(self):
        self.db_path = private_path(self.db_path, self.vault_path)
        return self.db_path

    def check_output(self, path):
        path = private_path(path, self.vault_path)
        if {"LaunchAgents", "LaunchDaemons"}.intersection(path.parts):
            raise InputError("Exports must stay outside launchd job directories")
        return path


class SecretSource:
    def __init__(self, path, config):
        self.path = path
        self.config = config
        self._values = {}

    def require(self, *fields):
        if not self.path:
            raise InputError("This service requires an explicit --secrets file")
        path = private_path(self.path, self.config.vault_path)
        values = read_object(path, secret=True)
        if values.keys() - SECRET_FIELDS or any(not isinstance(value, str) for value in values.values()):
            raise InputError("Secrets contain unknown fields or invalid values")
        self._values.update(values)
        missing = [field for field in fields if not values.get(field, "").strip()]
        if missing:
            raise InputError("Missing service secrets: " + ", ".join(missing))
        return {field: values[field] for field in fields}

    def redactions(self):
        return tuple(value for value in self._values.values() if value)

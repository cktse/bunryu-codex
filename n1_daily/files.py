"""Complete file replacement with persisted checksums and edit protection."""

import hashlib
import os
from pathlib import Path
import tempfile


def checksum(data):
    return hashlib.sha256(data).hexdigest()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".n1-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_generated(store, path, text):
    path = Path(path).absolute()
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError(f"Refusing generated file through a symlink: {path}")
    data = text.encode("utf-8")
    new_checksum = checksum(data)
    tracked = store.file_record(path)
    if path.exists():
        current = checksum(path.read_bytes())
        allowed = {tracked["checksum"], tracked["pending_checksum"]} if tracked else set()
        if current not in allowed:
            raise ValueError(f"Preserved unexpected edits at {path}")
        if current == new_checksum:
            store.finish_file(path, new_checksum)
            return new_checksum
    store.prepare_file(path, new_checksum)
    atomic_write(path, data)
    store.finish_file(path, new_checksum)
    return new_checksum

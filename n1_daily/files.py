"""Atomic vault writes with recoverable checksums and edit preservation."""
import hashlib
import os
from pathlib import Path
import tempfile

from .errors import InputError


def digest(data):
    return hashlib.sha256(data).hexdigest()


def confined(root, path):
    root, path = Path(root), Path(path)
    if not path.is_relative_to(root) or not path.resolve().is_relative_to(root):
        raise InputError(f'Vault path escapes its root: {path}')
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise InputError(f'Vault destination contains a symlink: {path}')
    return path


def write_file(store, root, path, text):
    path = confined(root, path)
    data = text.encode('utf-8')
    checksum = digest(data)
    tracked = store.file_record(path)
    if path.exists():
        if not path.is_file():
            raise InputError(f'Vault destination is not a regular file: {path}')
        current = digest(path.read_bytes())
        if not tracked or current not in (tracked['checksum'], tracked['pending_checksum']):
            raise InputError(f'Preserving untracked or edited file: {path}')
        if current == tracked['pending_checksum']:
            store.finish_file(path, current)
        if current == checksum:
            return checksum
    store.prepare_file(path, checksum)
    path.parent.mkdir(parents=True, exist_ok=True)
    confined(root, path)
    descriptor, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    store.finish_file(path, checksum)
    return checksum


def lesson_path(root, day):
    return Path(root) / 'Lessons' / day[:4] / day[5:7] / (day + '.md')


def index_text(store, root):
    lines = ['# JLPT N1', '']
    rows = store.conn.execute("SELECT date FROM lessons WHERE markdown_status='written' ORDER BY date DESC").fetchall()
    for row in rows:
        record = store.get(row['date'])
        expected = lesson_path(root, row['date'])
        if ((not record['vault_root'] or Path(record['vault_root']) == root)
                and record['markdown_path'] and Path(record['markdown_path']) == expected):
            lines.append(f"- [[{expected.relative_to(root).with_suffix('').as_posix()}|{row['date']} · {record['lesson']['theme']}]]")
    return '\n'.join(lines) + '\n'

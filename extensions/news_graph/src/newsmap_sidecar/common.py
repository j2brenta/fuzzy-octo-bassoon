"""Small explicit value and filesystem boundaries."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat


class NewsmapError(ValueError):
    code = 'invalid'


class StaleError(NewsmapError):
    code = 'stale'


class BusyError(NewsmapError):
    code = 'busy_retry'


class UnsafePath(NewsmapError):
    code = 'unsafe_path'


class Oversize(NewsmapError):
    code = 'oversize'


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    # The producer's default separators are part of its fingerprint contract.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')


def fingerprint(value):
    return digest(encoded(value))


def parse_json(data):
    def pairs(rows):
        obj = {}
        for k, v in rows:
            if k in obj:
                raise NewsmapError('duplicate JSON key')
            obj[k] = v
        return obj

    def constant(_):
        raise NewsmapError('non-finite JSON number')

    try:
        value = json.loads(data.decode('utf-8'), object_pairs_hook=pairs, parse_constant=constant)
        # Also catches overflowing floating point literals and lone surrogates.
        encoded(value)
        return value
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise NewsmapError('invalid bounded UTF-8 JSON: ' + type(exc).__name__) from None


def require(condition, message):
    if not condition:
        raise NewsmapError(message)


def identifier(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[0-9a-f]{64}', value))


def nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def relative_parts(path):
    if not isinstance(path, str) or '\\' in path or '\x00' in path:
        raise UnsafePath('unsupported path form')
    parts = path.split('/')
    if not parts or any(p in {'', '.', '..'} for p in parts) or PurePosixPath(path).is_absolute():
        raise UnsafePath('path must be a normalized vault-relative path')
    return parts


def checked_path(path):
    """Reject links in every existing component; callers use trusted local directories."""
    path = Path(os.path.abspath(path))
    for component in [*reversed(path.parents), path]:
        if component.is_symlink():
            raise UnsafePath('symlink path component refused')
    return path


def overlaps(a, b):
    return a.is_relative_to(b) or b.is_relative_to(a)


def outside_repositories(path):
    path = checked_path(path)
    for parent in [path, *path.parents]:
        if (parent / '.git').exists():
            raise UnsafePath('state/output must be outside Git working trees')
    return path


def mount_type(path):
    """Reject ambiguous stacked mounts and bind/subtree aliases."""
    mounts = Path('/proc/self/mountinfo')
    require(mounts.exists(), 'storage verification requires Linux mountinfo')
    choices = []
    for line in mounts.read_text().splitlines():
        left, right = line.split(' - ', 1)
        mount = Path(left.split()[4].replace('\\040', ' '))
        if path.is_relative_to(mount):
            choices.append((len(str(mount)), right.split()[0], left.split()[3]))
    require(bool(choices), 'storage mount unknown')
    deepest = [row for row in choices if row[0] == max(x[0] for x in choices)]
    require(len(deepest) == 1, 'ambiguous stacked mounts are not supported')
    require(deepest[0][2] == '/', 'bind/subtree mounts are not supported for this boundary')
    return deepest[0][1]


def local_storage(path):
    """Fail closed on unknown/network/ephemeral mount types on Linux."""
    path = outside_repositories(path)
    kind = mount_type(path)
    require(kind in {'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs'},
            'persistent local storage not verified: ' + kind)
    return path


def open_directory(path):
    """Walk absolute components relative to opened descriptors, without symlinks."""
    path = checked_path(path)
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = nxt
        result, fd = fd, None
        return result
    finally:
        if fd is not None:
            os.close(fd)


class Vault:
    """Descriptor-relative no-follow reads of a trusted, quiescent input snapshot.

    File identity and metadata are checked across the read and again at batch end.
    This is not a globally atomic snapshot against malicious concurrent writers.
    """
    def __init__(self, root, max_bytes=10 * 1024 * 1024):
        self.root = checked_path(root)
        mount_type(self.root)
        self.fd = open_directory(self.root)
        self.max_bytes = max_bytes
        self.seen = {}

    def close(self):
        os.close(self.fd)

    def _open(self, path):
        parts = relative_parts(path)
        fd = os.dup(self.fd)
        try:
            for part in parts[:-1]:
                nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
            return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        finally:
            os.close(fd)

    @staticmethod
    def stamp(info):
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)

    def read(self, path):
        fd = self._open(path)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode):
                raise UnsafePath('only regular input files are supported')
            if before.st_nlink != 1:
                raise UnsafePath('hardlinked input files are not supported')
            if before.st_size > self.max_bytes:
                raise Oversize('artifact exceeds configured byte cap; full_bytes_retained=false')
            chunks, total = [], 0
            while True:
                chunk = os.read(fd, min(65536, self.max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > self.max_bytes:
                    raise Oversize('artifact grew beyond byte cap; full_bytes_retained=false')
            after = os.fstat(fd)
            require(self.stamp(before) == self.stamp(after), 'input changed during read')
            self.seen[path] = self.stamp(after)
            return b''.join(chunks)
        finally:
            os.close(fd)

    def unchanged(self):
        for path, stamp in self.seen.items():
            try:
                fd = self._open(path)
                try:
                    if self.stamp(os.fstat(fd)) != stamp:
                        return False
                finally:
                    os.close(fd)
            except OSError:
                return False
        return True

#!/usr/bin/env python3
"""Create or validate one persistent TSIG secret on the Ansible controller.

No secret is ever printed. Generation is serialized using flock and O_EXCL.
"""
import argparse
import base64
import binascii
import fcntl
import os
from pathlib import Path
import secrets
import stat
import time


class TsigSecretStore:
    """Publish a complete secret under a checked directory descriptor.

    Existing secrets are never rewritten. All path components reject symlinks;
    nonblocking opens reject FIFOs/devices before reading, and reads are bounded.
    The lock timeout prevents a stale cooperating process from hanging Ansible.
    """

    def __init__(self, path, lock_timeout=30, create_parent=False):
        self.path = Path(path)
        self.lock_timeout = lock_timeout
        self.create_parent = create_parent

    def parent_descriptor(self):
        """Walk without following links, then require a private owned directory."""
        if not self.path.is_absolute() or '..' in self.path.parts:
            raise ValueError('TSIG path must be absolute without parent traversal')
        descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            for part in self.path.parent.parts[1:]:
                if self.create_parent:
                    # The previous descriptor was checked before creating beneath it.
                    # Never chmod an existing directory or follow a redirected path.
                    try:
                        os.mkdir(part, 0o700, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
                ancestor = os.fstat(descriptor)
                sticky_root = ancestor.st_uid == 0 and ancestor.st_mode & stat.S_ISVTX
                if (ancestor.st_uid not in (0, os.geteuid()) or
                        (ancestor.st_mode & 0o022 and not sticky_root)):
                    raise ValueError('Unsafe TSIG ancestor ownership or writable permissions')
            info = os.fstat(descriptor)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('TSIG parent must be owned by the current user with mode 0700')
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def checked_open(parent, name, flags):
        """Return only an owned, private, singly linked regular file descriptor."""
        descriptor = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                             0o600, dir_fd=parent)
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or
                info.st_mode & 0o077 or info.st_nlink != 1):
            os.close(descriptor)
            raise ValueError('Unsafe TSIG file type, owner, permissions or link count')
        return descriptor

    def acquire(self, descriptor):
        """Wait at most the configured deadline for another generator to finish."""
        deadline = time.monotonic() + self.lock_timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError('TSIG lock timed out; inspect the active controller process')
                time.sleep(0.05)

    def existing(self, parent):
        """Validate the exact 32-byte canonical Base64 format, with optional LF."""
        try:
            descriptor = self.checked_open(parent, self.path.name, os.O_RDONLY)
        except FileNotFoundError:
            return False
        with os.fdopen(descriptor, 'rb') as stream:
            raw = stream.read(46)  # 44 Base64 bytes plus at most one newline.
        value = raw[:-1] if raw.endswith(b'\n') else raw
        try:
            decoded = base64.b64decode(value, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError('Invalid existing TSIG base64') from exc
        if len(decoded) != 32 or base64.b64encode(decoded) != value:
            raise ValueError('Existing TSIG must be canonical Base64 encoding exactly 32 bytes')
        return True

    def publish(self, parent):
        """Write/fsync privately, then link atomically without replacing any key."""
        temporary = '.bind-tsig-' + secrets.token_hex(16) + '.tmp'
        descriptor = self.checked_open(parent, temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
        try:
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(base64.b64encode(secrets.token_bytes(32)) + b'\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, self.path.name, src_dir_fd=parent, dst_dir_fd=parent,
                    follow_symlinks=False)
        finally:
            os.unlink(temporary, dir_fd=parent)
        os.fsync(parent)

    def provision(self):
        """Return True only when a new key was published; raise on unsafe input."""
        parent = self.parent_descriptor()
        try:
            lock = self.checked_open(parent, '.' + self.path.name + '.lock', os.O_CREAT | os.O_RDWR)
            try:
                self.acquire(lock)
                if self.existing(parent):
                    return False
                self.publish(parent)
                return True
            finally:
                os.close(lock)
        finally:
            os.close(parent)


def provision(path: Path) -> bool:
    """Preserve the helper API used by existing automation and tests."""
    return TsigSecretStore(path).provision()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=Path)
    parser.add_argument('--create-parent', action='store_true',
                        help='Create missing private ancestors without changing existing permissions')
    args = parser.parse_args()
    try:
        print('CREATED' if TsigSecretStore(args.path, create_parent=args.create_parent).provision() else 'UNCHANGED')
    except (OSError, ValueError) as exc:
        parser.exit(1, f'TSIG provisioning failed: {exc}\n')

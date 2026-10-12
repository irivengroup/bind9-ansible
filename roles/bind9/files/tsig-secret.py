#!/usr/bin/env python3
"""Create or validate one persistent TSIG secret on the Ansible controller.

No secret is ever printed. Generation is serialized using flock and O_EXCL.
"""
import argparse
import base64
import fcntl
import os
from pathlib import Path
import secrets
import stat


def provision(path: Path) -> bool:
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError('TSIG parent directory must exist and not be a symlink')
    if parent.stat().st_mode & 0o077:
        raise ValueError('TSIG parent directory must have mode 0700')
    if path.is_symlink():
        raise ValueError('Refusing symlink secret')
    lock = parent / ('.' + path.name + '.lock')
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        if path.is_symlink():
            raise ValueError('Refusing symlink secret')
        try:
            secret_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            secret_fd = None
        if secret_fd is not None:
            with os.fdopen(secret_fd, 'rb') as source:
                st = os.fstat(source.fileno())
                if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                    raise ValueError('Unsafe existing TSIG secret owner or permissions')
                raw = source.read()
            try:
                decoded = base64.b64decode(raw.strip(), validate=True)
            except (ValueError, base64.binascii.Error) as exc:
                raise ValueError('Invalid existing TSIG base64') from exc
            if len(decoded) != 32:
                raise ValueError('Existing TSIG secret must decode to 32 bytes')
            return False
        raw = base64.b64encode(secrets.token_bytes(32)) + b'\n'
        created_fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(created_fd, 'wb') as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        dir_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        return True
    finally:
        os.close(fd)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    try:
        print('CREATED' if provision(args.path) else 'UNCHANGED')
    except (OSError, ValueError) as exc:
        parser.exit(1, f'TSIG provisioning failed: {exc}\n')

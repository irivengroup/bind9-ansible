#!/usr/bin/env python3
"""Renew only declared private-zone NTAs; retain global DNSSEC auto validation.

RHEL 8 BIND 9.11 has no validate-except directive. Native rndc NTAs last at most
one week, so a systemd timer renews them daily and named hooks apply them before
health checks. Canonical configuration markers are the desired policy; a private
journal records ownership before mutation so rollback/removal is recoverable.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import stat
# Fixed RNDC argv, validated suffixes, no shell and a bounded timeout are enforced below.
import subprocess  # nosec B404
import sys
import tempfile


class PrivateDnssec:
    """Reconcile bounded, root-owned policy/state through authenticated IPv4 RNDC."""

    DOMAIN = re.compile(r'(?=.{1,235}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$')
    MARKER = re.compile(r'^// Bind9-Ansible DNSSEC-PRIVATE ([^\n]+)\n\s*zone "([^"\n]+)" IN \{\s+type forward;', re.M)
    LIMIT = 1048576

    def __init__(self, config='/etc/named.conf', state='/var/lib/Bind9-Ansible/dnssec-owned.json',
                 rndc='/usr/sbin/rndc', key='/etc/rndc.key', port=953, runner=subprocess.run):
        self.config, self.state = Path(config), Path(state)
        self.rndc, self.key, self.port, self.runner = str(rndc), str(key), port, runner

    @classmethod
    def domains(cls, values):
        """Reject root, metacharacters, duplicate names and excessive policy size."""
        if (not isinstance(values, list) or len(values) > 1024 or
                any(not isinstance(value, str) or not cls.DOMAIN.fullmatch(value) for value in values) or
                len(set(values)) != len(values)):
            raise ValueError('Invalid private DNS suffix list')
        return sorted(values)

    @classmethod
    def read(cls, path):
        """Read a bounded regular root-owned file without following a symlink."""
        with os.fdopen(os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW), 'r') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('Unsafe DNSSEC policy/state permissions: ' + str(path))
            content = stream.read(cls.LIMIT + 1)
        if len(content) > cls.LIMIT:
            raise ValueError('DNSSEC policy/state exceeds size limit')
        return content

    def desired(self):
        """Accept markers only immediately preceding their matching forward zone."""
        content = self.read(self.config)
        pairs = self.MARKER.findall(content)
        if len(pairs) != content.count('// Bind9-Ansible DNSSEC-PRIVATE '):
            raise ValueError('Malformed private DNSSEC marker')
        if any(marker != zone.lower() for marker, zone in pairs):
            raise ValueError('Private DNSSEC marker does not match its forward zone')
        return self.domains([marker for marker, _ in pairs])

    def owned(self):
        """Return previously recorded ownership; malformed state fails closed."""
        try:
            value = json.loads(self.read(self.state))
        except FileNotFoundError:
            return []
        if (not isinstance(value, dict) or set(value) != {'version', 'domains'} or
                type(value['version']) is not int or value['version'] != 1):
            raise ValueError('Unsupported DNSSEC ownership journal')
        return self.domains(value['domains'])

    def save(self, domains):
        """Atomically persist root-only ownership before executing RNDC mutations."""
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', dir=str(self.state.parent), delete=False) as stream:
                name = stream.name
                json.dump({'version': 1, 'domains': domains}, stream, sort_keys=True)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, str(self.state))
            # Persist the renamed directory entry before RNDC can change trust.
            directory = os.open(str(self.state.parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if name is not None and os.path.exists(name):
                os.unlink(name)

    def command(self, arguments):
        """Surface an RNDC failure without hiding it or printing credential data."""
        result = self.runner([self.rndc, '-s', '127.0.0.1', '-p', str(self.port), '-k', self.key,
                              'nta'] + arguments, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             universal_newlines=True, timeout=15)
        if result.returncode:
            detail = (getattr(result, 'stderr', '') or getattr(result, 'stdout', '') or 'no RNDC diagnostic').strip()[:512]
            raise RuntimeError('RNDC NTA operation failed: ' + ' '.join(arguments) + ': ' + detail)

    def preview(self):
        """Describe the proposed policy without RNDC, locking or filesystem writes."""
        desired = self.desired()
        return {'check': True, 'renew': desired, 'remove': sorted(set(self.owned()) - set(desired))}

    def reconcile(self):
        """Renew configured exceptions and remove only obsolete owned exceptions."""
        self.desired()  # Reject invalid configuration before creating state paths.
        self.state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        for parent in [self.state.parent] + list(self.state.parent.parents):
            info = parent.lstat()
            writable = info.st_mode & 0o022
            sticky_ancestor = parent != self.state.parent and info.st_mode & stat.S_ISVTX
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or (writable and not sticky_ancestor):
                raise ValueError('Unsafe DNSSEC state directory: ' + str(parent))
        lock = self.state.parent / 'dnssec.lock'
        with os.fdopen(os.open(str(lock), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), 'a+') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
                raise ValueError('Unsafe DNSSEC lock permissions')
            return self.locked(stream.fileno())

    def locked(self, descriptor):
        """Hold one advisory lock across the ownership journal and all mutations."""
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        # A timer may have waited while a deploy replaced the canonical file.
        desired = self.desired()
        previous = self.owned()
        self.save(sorted(set(previous) | set(desired)))
        obsolete = sorted(set(previous) - set(desired))
        for domain in obsolete:
            self.command(['-remove', domain])
        for domain in desired:
            self.command(['-force', '-lifetime', '1w', domain])
        self.save(desired)
        return {'renewed': desired, 'removed': obsolete}


def main():
    """CLI for systemd; failures are explicit and no DNSSEC error is ignored."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/named.conf', help='Canonical root-owned named.conf')
    parser.add_argument('--state', default='/var/lib/Bind9-Ansible/dnssec-owned.json', help='Private ownership journal')
    parser.add_argument('--rndc', default='/usr/sbin/rndc', help='Native RNDC executable')
    parser.add_argument('--key', default='/etc/rndc.key', help='Native RNDC key file; never a secret in argv')
    parser.add_argument('--port', type=int, default=953, help='RNDC port on 127.0.0.1')
    parser.add_argument('--check', action='store_true', help='Show proposed changes without RNDC or writes')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Root required')
    if not 1 <= args.port <= 65535:
        parser.error('RNDC port must be between 1 and 65535')
    try:
        options = vars(args).copy()
        check = options.pop('check')
        helper = PrivateDnssec(**options)
        result = helper.preview() if check else helper.reconcile()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print('Private DNSSEC exception update failed: ' + str(error), file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())

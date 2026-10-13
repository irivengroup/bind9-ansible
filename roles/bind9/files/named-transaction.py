#!/usr/bin/env python3
"""Conservative deployment snapshot/rollback for named-managed files only.

Never extracts arbitrary tar members; only paths from the fixed allowlist are used.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

MANAGED = [
    '/etc/named.conf',
    '/etc/named/10-tsig.conf',
    '/etc/named/20-acl.conf',
    '/etc/named/30-options.conf',
    '/etc/named/40-views.conf',
    '/etc/named/zones/10-authoritative-zones.conf',
    '/etc/named/zones/15-external-authoritative-zones.conf',
    '/etc/named/zones/16-internal-authoritative-zones.conf',
    '/etc/named/zones/20-forward-zones.conf',
    '/var/named/empty-root.zone',
]
STATE = Path('/var/lib/Bind9-Ansible/deployment')


def sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def check_path(path):
    for part in [path, *path.parents]:
        if part.is_symlink():
            raise RuntimeError(f'Symlink refused: {part}')


def atomic_json(path, data):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as out:
        json.dump(data, out, sort_keys=True)
        out.flush()
        os.fsync(out.fileno())
        tmp = out.name
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def begin(zones):
    if (STATE / 'pending.json').exists():
        raise RuntimeError('Pending deployment: run restore before retrying')
    managed = MANAGED + [f'/var/named/zones/{domain}.zone' for domain in zones]
    for name in managed:
        check_path(Path(name))
    existing = [name for name in managed if Path(name).is_file()]
    archive = STATE / 'pending.tar.gz'
    partial = STATE / 'pending.tar.gz.tmp'
    with tarfile.open(partial, 'w:gz', format=tarfile.PAX_FORMAT) as tar:
        for name in existing:
            tar.add(name, arcname=name.lstrip('/'), recursive=False)
    os.chmod(partial, 0o600)
    os.replace(partial, archive)
    manifest = {'managed': managed, 'existing': existing, 'checksum': sha256(archive)}
    atomic_json(STATE / 'pending.json', manifest)
    print('SNAPSHOT_CREATED')


def restore():
    metadata = STATE / 'pending.json'
    if not metadata.exists():
        print('NO_PENDING_DEPLOYMENT')
        return
    manifest = json.loads(metadata.read_text())
    archive = STATE / 'pending.tar.gz'
    if sha256(archive) != manifest['checksum']:
        raise RuntimeError('Snapshot checksum mismatch; refusing restore')
    managed = set(manifest['managed'])
    existing = set(manifest['existing'])
    if not managed.issubset(set(MANAGED) | {f'/var/named/zones/{d}.zone' for d in args.zones}):
        raise RuntimeError('Manifest paths outside authorized scope')
    for name in managed:
        check_path(Path(name))
    # Extract only regular files to temporary paths, then replace atomically.
    with tarfile.open(archive, 'r:gz') as tar:
        members = tar.getmembers()
        if {('/' + m.name) for m in members} != existing:
            raise RuntimeError('Archive/manifest mismatch')
        for member in members:
            if not member.isfile() or member.issym() or member.islnk():
                raise RuntimeError('Unsafe archive member')
            destination = Path('/') / member.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as tmp:
                shutil.copyfileobj(tar.extractfile(member), tmp)
                tmp.flush()
                os.fsync(tmp.fileno())
                tempname = tmp.name
            os.chmod(tempname, stat.S_IMODE(member.mode))
            os.chown(tempname, member.uid, member.gid)
            os.replace(tempname, destination)
    for name in managed - existing:
        path = Path(name)
        if path.is_file():
            path.unlink()
    subprocess.run(['restorecon', '-RF', '/etc/named.conf', '/etc/named', '/var/named'], check=True)
    metadata.unlink()
    archive.unlink()
    print('RESTORED')


def commit():
    metadata = STATE / 'pending.json'
    if not metadata.exists():
        raise RuntimeError('No deployment to commit')
    metadata.unlink()
    (STATE / 'pending.tar.gz').unlink()
    print('COMMITTED')


parser = argparse.ArgumentParser()
parser.add_argument('action', choices=['begin', 'restore', 'commit', 'status'])
parser.add_argument('--zone', action='append', dest='zones', default=[])
args = parser.parse_args()
STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
check_path(STATE)
os.chmod(STATE, 0o700)
with open(STATE / '.lock', 'a+') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        if args.action == 'begin':
            begin(args.zones)
        elif args.action == 'restore':
            restore()
        elif args.action == 'commit':
            commit()
        else:
            print('PENDING' if (STATE / 'pending.json').exists() else 'CLEAN')
    except (OSError, RuntimeError, tarfile.TarError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)

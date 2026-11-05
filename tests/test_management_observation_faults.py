"""Inject native races at I/O boundaries without changing production guards."""
import os
from pathlib import Path
import sys

import pytest

from test_management import CONF, Lab, M, pytestmark as base_pytestmark
from test_operation_resilience import InterruptedTransaction

pytestmark = base_pytestmark


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


class ObservedRead:
    """Keep real descriptors and delegate every operation except the selected read."""

    def __init__(self, stream, before=None, after=None):
        self.stream, self.before, self.after = stream, before, after

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def read(self, count=-1):
        if self.before:
            self.before()
        value = self.stream.read(count)
        return self.after(value) if self.after else value


def replace_path(path):
    """An operator atomically publishes identical bytes under a different inode."""
    replacement = path.with_name('operator-replacement')
    replacement.write_bytes(path.read_bytes())
    replacement.chmod(path.stat().st_mode & 0o777)
    replacement.replace(path)


@pytest.mark.parametrize('reader', ['configuration', 'private-json'])
def test_replacement_between_descriptor_and_path_observations_is_detected(lab, monkeypatch, reader):
    if reader == 'configuration':
        path = lab.root / 'etc/named.conf'
        invoke = lambda: M.SafeFiles(lab.root).read('/etc/named.conf')
    else:
        path = lab.root / 'evidence.json'
        path.write_bytes(b'{}')
        path.chmod(0o600)
        invoke = lambda: M.VerifiedIO.read_json(path, 4096)
    native = M.os.fstat
    observations = []

    def swapped_after_fstat(descriptor):
        info = native(descriptor)
        if os.readlink('/proc/self/fd/' + str(descriptor)) == str(path):
            observations.append(info.st_ino)
            if len(observations) == 2:
                replace_path(path)
        return info

    monkeypatch.setattr(M.os, 'fstat', swapped_after_fstat)
    with pytest.raises(M.ManagementError, match='changed during'):
        invoke()
    assert len(observations) == 2
    assert path.stat().st_ino != observations[0]


def test_growth_after_the_initial_file_stat_cannot_escape_the_read_bound(lab, monkeypatch):
    path = lab.root / 'etc/named.conf'
    monkeypatch.setattr(M.SafeFiles, 'LIMIT', len(path.read_bytes()))
    fdopen = M.os.fdopen
    def opened(descriptor, *args, **kwargs):
        stream = fdopen(descriptor, *args, **kwargs)
        if os.readlink('/proc/self/fd/' + str(descriptor)) != str(path):
            return stream
        def grow():
            with path.open('ab') as writer:
                writer.write(b'xx')
        return ObservedRead(stream, before=grow)
    monkeypatch.setattr(M.os, 'fdopen', opened)
    with pytest.raises(M.ManagementError, match='size bound'):
        M.SafeFiles(lab.root).read('/etc/named.conf')
    assert path.read_bytes() == CONF.encode() + b'xx'


@pytest.mark.parametrize('phase', ['descriptor', 'readback', 'last-path'])
def test_pending_cleanup_preserves_a_marker_changed_during_observation(lab, monkeypatch, phase):
    evidence = InterruptedTransaction(lab, 'committed')
    path = evidence.pointer
    native_open, native_lstat = M.os.open, Path.lstat
    changed = []
    if phase == 'descriptor':
        def opened(name, *args, **kwargs):
            descriptor = native_open(name, *args, **kwargs)
            if str(name) == str(path):
                replace_path(path)
                changed.append(True)
            return descriptor
        monkeypatch.setattr(M.os, 'open', opened)
    elif phase == 'readback':
        native_fdopen = M.os.fdopen
        def opened(descriptor, *args, **kwargs):
            stream = native_fdopen(descriptor, *args, **kwargs)
            if os.readlink('/proc/self/fd/' + str(descriptor)) == str(path):
                def damaged(value):
                    changed.append(True)
                    return b'!' + value[1:]
                return ObservedRead(stream, after=damaged)
            return stream
        monkeypatch.setattr(M.os, 'fdopen', opened)
    else:
        calls = []
        def lstat(target, *args, **kwargs):
            if target == path:
                calls.append(True)
                if len(calls) == 2:
                    replace_path(path)
                    changed.append(True)
            return native_lstat(target, *args, **kwargs)
        monkeypatch.setattr(Path, 'lstat', lstat)
    with pytest.raises(M.ManagementError, match='ownership changed'):
        lab.manager().clear_pending(evidence.folder)
    assert changed and path.exists()
    assert evidence.manifest.exists()


def test_closed_output_does_not_bypass_the_remaining_command_deadline(monkeypatch):
    native_read, clock = M.os.read, M.time.monotonic
    native_process = M.subprocess.Popen
    expired, output = [], []
    start = clock()
    def process(*args, **kwargs):
        child = native_process(*args, **kwargs)
        output.append(child.stdout.fileno())
        return child
    def read(descriptor, count):
        value = native_read(descriptor, count)
        if descriptor in output and value == b'':
            expired.append(True)
        return value
    monkeypatch.setattr(M.subprocess, 'Popen', process)
    monkeypatch.setattr(M.os, 'read', read)
    monkeypatch.setattr(M.time, 'monotonic', lambda: start + M.NativeCommand.TIMEOUT + 1 if expired else start)
    with pytest.raises(M.ManagementError, match='timed out'):
        M.NativeCommand.run([sys.executable, '-c', 'pass'])
    assert expired


def test_operation_lock_replacement_during_the_initial_stat_is_refused(lab, monkeypatch):
    manager = lab.manager()
    path = manager.private_directory('/var/lib/Bind9-Ansible') / 'operations.flock'
    path.touch(mode=0o600)
    original = M.os.fstat
    swaps = []
    def fstat(descriptor):
        info = original(descriptor)
        if os.readlink('/proc/self/fd/' + str(descriptor)) == str(path):
            replace_path(path)
            swaps.append(True)
        return info
    monkeypatch.setattr(M.os, 'fstat', fstat)
    with pytest.raises(M.ManagementError, match='Unsafe transient'):
        with manager.operation():
            pytest.fail('Replaced lock was accepted')
    assert swaps and path.exists()

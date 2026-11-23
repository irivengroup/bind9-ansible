"""A failed private write must never become the persistent authentication key."""
import errno
import importlib.util
import os
from pathlib import Path
import tempfile

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'roles/bind9/files/tsig-secret.py'
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Requires POSIX private publication')


@pytest.fixture
def store_module():
    spec = importlib.util.spec_from_file_location('tsig_publication_integrity', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FaultyWrite:
    """Inject actual damaged bytes while leaving the real descriptor and sync intact."""

    def __init__(self, stream, fault):
        self.stream = stream
        self.fault = fault

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def write(self, data):
        if self.fault == 'short':
            return self.stream.write(data[:-1])
        # Keep the expected length but change a byte: length alone is insufficient.
        changed = (chr(ord(data[0]) ^ 1) if isinstance(data, str) else bytes([data[0] ^ 1])) + data[1:]
        self.stream.write(changed)
        return len(data)


@pytest.mark.parametrize('fault', ['short', 'altered'])
def test_damaged_private_write_is_rejected_before_publication(tmp_path, monkeypatch, store_module, fault):
    tmp_path.chmod(0o700)
    key = tmp_path / 'site.key'
    original = store_module.os.fdopen

    def faulty_open(descriptor, mode, *args, **kwargs):
        stream = original(descriptor, mode, *args, **kwargs)
        return FaultyWrite(stream, fault) if mode.startswith('w') else stream

    monkeypatch.setattr(store_module.os, 'fdopen', faulty_open)
    with pytest.raises(OSError, match='TSIG.*(write|verification)'):
        store_module.provision(key)
    assert not key.exists()
    assert not list(tmp_path.glob('.bind-tsig-*.tmp'))
    # The lock is a coordination object, never secret material or a partial key.
    assert (tmp_path / '.site.key.lock').read_bytes() == b''


@pytest.mark.parametrize('timeout', [float('nan'), float('inf'), -float('inf'), -1,
                                    True, None, '30', 10 ** 400])
def test_lock_deadline_requires_a_finite_nonnegative_number(tmp_path, store_module, timeout):
    with pytest.raises(ValueError, match='finite nonnegative'):
        store_module.TsigSecretStore(tmp_path / 'site.key', lock_timeout=timeout)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('timeout', [0, 0.0, 0.001, 30])
def test_finite_timeout_keeps_the_immediate_uncontended_attempt(tmp_path, store_module, timeout):
    tmp_path.chmod(0o700)
    key = tmp_path / 'site.key'
    store = store_module.TsigSecretStore(key, lock_timeout=timeout)
    assert store.provision() is True
    before = key.read_bytes()
    assert store.provision() is False
    assert key.read_bytes() == before


@pytest.mark.skipif(not hasattr(os, 'geteuid') or os.geteuid() != 0, reason='Requires root to create foreign-owned fixtures')
@pytest.mark.parametrize('kind', ['ancestor-owner', 'key-owner', 'key-mode', 'key-hardlink'])
def test_foreign_or_shared_objects_are_refused_in_direct_calls(tmp_path, store_module, kind):
    tmp_path.chmod(0o700)
    parent = tmp_path / 'private'
    parent.mkdir(mode=0o700)
    key = parent / 'site.key'
    if kind == 'ancestor-owner':
        os.chown(parent, 1, -1)
    else:
        key.write_bytes(b'A' * 43 + b'=\n')
        key.chmod(0o600)
        if kind == 'key-owner':
            os.chown(key, 1, -1)
        elif kind == 'key-mode':
            key.chmod(0o644)
        else:
            os.link(key, parent / 'other.key')
    with pytest.raises(ValueError, match='Unsafe TSIG'):
        store_module.provision(key)
    assert not list(parent.glob('.bind-tsig-*.tmp'))


def test_metadata_read_failure_closes_the_private_descriptor(tmp_path, store_module, monkeypatch):
    """An I/O error after a real open must not leak the owned file descriptor."""
    tmp_path.chmod(0o700)
    key = tmp_path / 'site.key'
    original = b'unchanged synthetic fixture\n'
    key.write_bytes(original)
    key.chmod(0o600)
    identity = key.stat()
    parent = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    real_fstat = os.fstat
    opened = []

    def fail_metadata(descriptor):
        opened.append(descriptor)
        raise OSError(errno.EIO, 'metadata read failed')

    try:
        with monkeypatch.context() as isolated:
            isolated.setattr(store_module.os, 'fstat', fail_metadata)
            with pytest.raises(OSError, match='metadata read failed'):
                store_module.TsigSecretStore.checked_open(parent, key.name, os.O_RDONLY)
        assert len(opened) == 1
        try:
            real_fstat(opened[0])
        except OSError as error:
            assert error.errno == errno.EBADF
        else:
            pytest.fail('TSIG metadata failure leaked the private file descriptor')
        assert key.read_bytes() == original
    finally:
        # Reproducing the old leak must not leak into later tests. Close only
        # the descriptor still identifying this test-owned file, never a reuse.
        for descriptor in opened:
            try:
                current = real_fstat(descriptor)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise
            else:
                assert (current.st_dev, current.st_ino) == (identity.st_dev, identity.st_ino)
                os.close(descriptor)
        os.close(parent)


@pytest.mark.skipif(not hasattr(os, 'seteuid') or os.geteuid() != 0,
                    reason='Requires an isolated root process to exercise a real foreign identity')
def test_root_owned_terminal_parent_is_refused_for_an_unprivileged_generator(store_module):
    """Trusted root ancestors do not authorize a root-owned terminal store."""
    with tempfile.TemporaryDirectory(prefix='bind9-tsig-foreign-parent-') as directory:
        parent = Path(directory)
        parent.chmod(0o755)
        store = store_module.TsigSecretStore(parent / 'site.key')
        original_euid = os.geteuid()
        try:
            os.seteuid(65534)
            with pytest.raises(ValueError, match='TSIG parent must be owned by the current user'):
                store.provision()
        finally:
            os.seteuid(original_euid)
        assert list(parent.iterdir()) == []
        assert parent.stat().st_uid == original_euid

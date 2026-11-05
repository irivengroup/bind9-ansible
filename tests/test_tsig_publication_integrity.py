"""A failed private write must never become the persistent authentication key."""
import importlib.util
import os
from pathlib import Path

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

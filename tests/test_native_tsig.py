"""Canonical controller TSIG creation, strict refusals and protected facts."""
import base64
import os
from pathlib import Path

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeTsigRun

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')


@pytest.fixture
def tsig():
    with NativeFilesystemScope(NativeTsigRun):
        yield


def run(path, **values):
    settings = dict(bind_tsig_secret_file=str(path), bind_tsig_key_name='site-transfer',
                    bind_tsig_algorithm='hmac-sha256')
    settings.update(values)
    return NativeTsigRun('tasks.d/deploy/tsig.yml', settings,
        checks=['bind_tsig_secret | length >= 44', 'bind_tsig_provision.rc == 0']).run()


@pytest.mark.parametrize('existing', [False, True])
def test_native_controller_secret_creation_and_repeat_preserve_exact_bytes_and_private_mode(tsig, tmp_path, existing):
    path = tmp_path / 'site.key'
    if existing:
        path.write_bytes(base64.b64encode(bytes(range(32))) + b'\n')
        path.chmod(0o600)
    before = path.read_bytes() if existing else None
    fingerprints = []
    for iteration in range(2):
        status, result = run(path)
        assert status == 0 and not result.failed
        info = path.stat()
        assert info.st_uid == os.geteuid() and info.st_nlink == 1 and info.st_mode & 0o777 == 0o600
        raw = path.read_bytes()
        assert len(base64.b64decode(raw.strip(), validate=True)) == 32
        assert base64.b64encode(base64.b64decode(raw.strip())) == raw.strip()
        if before is not None: assert raw == before
        recorded = result.registers['bind_tsig_provision']
        assert recorded.get('censored') == 'the output has been hidden due to the fact that \'no_log: true\' was specified for this result'
        assert recorded.get('changed') is (not existing and iteration == 0)
        assert not {'stdout', 'stderr', 'invocation'}.intersection(recorded)
        assert recorded['_ansible_delegated_vars']['ansible_connection'] == 'local'
        assert 'bind_tsig_secret' not in result.facts
        fingerprints.append((raw, info.st_mtime_ns, info.st_ino))
    assert fingerprints[0] == fingerprints[1]


@pytest.mark.parametrize('values', [dict(bind_tsig_key_name='bad;key'), dict(bind_tsig_algorithm='hmac-md5'),
    dict(bind_tsig_secret_file='native-tsig-relative'), dict(bind_tsig_secret_file='/etc/named.conf')])
def test_native_controller_secret_envelope_refuses_before_creating_any_file(tsig, tmp_path, values):
    path = tmp_path / 'site.key'
    status, result = run(path, **values)
    assert status == 2 and result.failed == ['Validate TSIG site settings']
    assert not path.exists() and not Path('/etc/named.conf').exists()


@pytest.mark.parametrize('kind', ['malformed', 'mode', 'symlink', 'hardlink'])
def test_native_controller_secret_refusals_do_not_publish_facts_or_replace_operator_objects(tsig, tmp_path, kind):
    path = tmp_path / 'site.key'
    foreign = tmp_path / 'operator'
    raw = base64.b64encode(bytes(range(32))) + b'\n'
    foreign.write_bytes(raw)
    foreign.chmod(0o600)
    if kind == 'symlink': path.symlink_to(foreign)
    elif kind == 'hardlink': os.link(str(foreign), str(path))
    else:
        path.write_bytes(b'invalid secret\n' if kind == 'malformed' else raw)
        path.chmod(0o640 if kind == 'mode' else 0o600)
    original = path.lstat()
    status, result = run(path)
    assert status == 2 and result.failed == ['Generate or verify persistent site TSIG secret atomically on controller']
    assert 'bind_tsig_secret' not in result.facts
    assert path.lstat().st_ino == original.st_ino and path.lstat().st_mode == original.st_mode
    assert foreign.read_bytes() == raw

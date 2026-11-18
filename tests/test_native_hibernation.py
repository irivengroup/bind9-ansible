"""Hibernation never provisions DNS and seals only safe syntax-checked content."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeHibernationRun, NativeRecordedBoundary

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')
CONFIG = Path('/etc/named.conf')
SEAL = Path('/var/lib/Bind9-Ansible/.deployment.lock')


@contextmanager
def checker(tmp_path, failure=False):
    # EL8's genuine installed validator remains untouched. The modern image's
    # exact CLI boundary qualifies orchestration only, never BIND semantics.
    if Path('/usr/sbin/named-checkconf').exists():
        yield None
    else:
        replies = {json.dumps(['named-checkconf', '/etc/named.conf']): {'rc': 17 if failure else 0}}
        with NativeRecordedBoundary(tmp_path, NativeHibernationRun, replies) as state:
            yield state


@pytest.fixture
def assets():
    with NativeFilesystemScope(NativeHibernationRun):
        yield


def run(existing=False, check=False):
    return NativeHibernationRun('tasks.d/deploy/hibernate.yml', {
        'hibernate_deployment': True, 'bind_deployment_lease_acquired': True,
        'bind_deployment_seal_stat': {'results': [{}, {'stat': {'exists': existing}}]},
        'bind_deployment_seal_after_acquire': {'stat': {'exists': existing}}}, check_mode=check).run()


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('check', [False, True])
def test_native_hibernation_preserves_existing_seals_and_check_mode_never_publishes(assets, tmp_path, existing, check):
    raw = b'options { recursion no; };\n'
    CONFIG.write_bytes(raw)
    CONFIG.chmod(0o600)
    os.utime(str(CONFIG), ns=(1, 2))
    if existing:
        SEAL.parent.mkdir(parents=True)
        SEAL.write_bytes(b'previously inspected immutable seal\n')
        SEAL.chmod(0o600)
        before = (SEAL.read_bytes(), SEAL.stat().st_ino, SEAL.stat().st_mtime_ns)
    with checker(tmp_path) as state:
        status, result = run(existing, check)
        assert status == 0 and not result.failed
        assert CONFIG.read_bytes() == raw
        if state is not None:
            assert json.loads(state.read_text())['calls'] == ([] if existing else [['named-checkconf', '/etc/named.conf']])
    if existing:
        assert (SEAL.read_bytes(), SEAL.stat().st_ino, SEAL.stat().st_mtime_ns) == before
        assert 'bind_deployment_config_checked' not in result.facts
    elif check:
        assert not SEAL.exists() and result.facts['bind_deployment_config_checked'] is True
    else:
        payload = json.loads(SEAL.read_text())['payload']
        assert payload['kind'] == 'hibernate'
        assert payload['files'] == {'/etc/named.conf': hashlib.sha256(raw).hexdigest()}
    assert not Path('/var/named').exists()


@pytest.mark.parametrize('kind', ['missing', 'directory', 'symlink', 'hardlink', 'owner', 'mode', 'empty', 'oversize'])
def test_native_hibernation_refuses_unsafe_assets_without_opening_or_running_parser(assets, tmp_path, kind):
    foreign = tmp_path / 'operator-record'
    foreign.write_text('private operator data\n', encoding='utf-8')
    foreign.chmod(0o600)
    os.utime(str(foreign), ns=(1, 2))
    if kind == 'directory': CONFIG.mkdir()
    elif kind == 'symlink': CONFIG.symlink_to(foreign)
    elif kind == 'hardlink': os.link(str(foreign), str(CONFIG))
    elif kind != 'missing':
        CONFIG.write_bytes(b'' if kind == 'empty' else b'options { recursion no; };\n')
        CONFIG.chmod(0o600)
        if kind == 'owner': os.chown(str(CONFIG), 65534, 0)
        elif kind == 'mode': CONFIG.chmod(0o660)
        elif kind == 'oversize':
            with CONFIG.open('r+b') as stream: stream.truncate(16777217)
        os.utime(str(CONFIG), ns=(1, 2))
    with checker(tmp_path) as state:
        status, result = run()
        assert status == 2 and result.failed[-1] == 'Refuse hibernation after a controlled syntax check failure'
        assert 'bind_deployment_config_checked' not in result.facts
        if state is not None: assert json.loads(state.read_text())['calls'] == []
    assert not SEAL.exists() and foreign.stat().st_atime_ns == 1
    if CONFIG.exists() and CONFIG.is_file() and not CONFIG.is_symlink(): assert CONFIG.stat().st_atime_ns == 1


def test_native_hibernation_checker_failure_never_records_completion_or_seal(assets, tmp_path):
    CONFIG.write_text('deliberately invalid BIND syntax\n', encoding='utf-8')
    CONFIG.chmod(0o600)
    with checker(tmp_path, failure=True):
        status, result = run()
    assert status == 2 and result.failed[-1] == 'Refuse hibernation after a controlled syntax check failure'
    assert 'bind_deployment_config_checked' not in result.facts and not SEAL.exists()

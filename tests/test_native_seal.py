"""Completion seals must refuse unsafe assets before reading their contents."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeResults, NativeSealRun

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')
SEAL = Path('/var/lib/Bind9-Ansible/.deployment.lock')


@pytest.fixture
def assets():
    with NativeFilesystemScope(NativeSealRun):
        yield


def seal(**values):
    settings = dict(hibernate_deployment=True, bind_deployment_lease_acquired=True,
                    bind_deployment_seal_kind='hibernate', bind_deployment_config_checked=True)
    settings.update(values)
    return NativeSealRun('tasks.d/deploy/seal.yml', settings).run()


@pytest.mark.parametrize('kind', ['hardlink', 'symlink', 'dangling-link', 'directory', 'fifo',
                                 'owner', 'mode', 'empty', 'oversize', 'missing'])
def test_native_seal_checks_metadata_before_any_content_read_or_publication(assets, tmp_path, kind):
    target = Path('/etc/named.conf')
    foreign = tmp_path / 'operator-record'
    foreign.write_text('must not open an unsafe object\n', encoding='utf-8')
    foreign.chmod(0o600)
    if kind == 'directory': target.mkdir()
    elif kind == 'fifo': os.mkfifo(str(target), 0o600)
    elif kind == 'hardlink': os.link(str(foreign), str(target))
    elif kind in ('symlink', 'dangling-link'): target.symlink_to(foreign if kind == 'symlink' else tmp_path / 'absent')
    elif kind != 'missing':
        target.write_bytes(b'' if kind == 'empty' else b'synthetic configuration\n')
        target.chmod(0o600)
        if kind == 'owner': os.chown(str(target), 65534, 0)
        elif kind == 'mode': target.chmod(0o660)
        elif kind == 'oversize':
            with target.open('r+b') as stream: stream.truncate(16777217)
    for path in (foreign, target):
        if path.exists() and not path.is_symlink(): os.utime(str(path), ns=(1, 2))
    status, result = seal()
    assert status == 2 and result.failed
    assert not SEAL.exists()
    assert foreign.stat().st_atime_ns == 1
    if target.exists() and target.is_file() and not target.is_symlink():
        assert target.stat().st_atime_ns == 1


@pytest.mark.parametrize('kind,primary', [('hibernate', False), ('deploy', False), ('deploy', True)])
def test_native_seal_contains_only_independently_verified_digests_and_private_metadata(assets, kind, primary):
    paths = ['/etc/named.conf']
    if kind == 'deploy':
        paths += ['/var/named/empty-root.zone']
        if primary: paths += ['/var/named/zones/example.test.zone']
    expected = {}
    for name in paths:
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = ('synthetic fixture: ' + name + '\n').encode('ascii')
        path.write_bytes(raw)
        path.chmod(0o640)
        expected[name] = hashlib.sha256(raw).hexdigest()
    status, result = seal(bind_deployment_seal_kind=kind, bind_deployment_health_completed=kind == 'deploy',
                          bind_managed_zones=[{'domain': 'example.test'}],
                          group_names=['dns_primary' if primary else 'dns_secondary'])
    assert status == 0 and not result.failed
    data = json.loads(SEAL.read_text(encoding='utf-8'))
    assert data['payload'] == {'schema': 1, 'host': 'localhost', 'kind': kind, 'files': expected}
    assert data['sha256'] == hashlib.sha256(json.dumps(data['payload'], sort_keys=True).encode('utf-8')).hexdigest()
    assert SEAL.stat().st_uid == SEAL.stat().st_gid == 0 and SEAL.stat().st_mode & 0o777 == 0o600
    assert SEAL.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize('change', ['replace', 'mode', 'remove'])
def test_native_seal_refuses_an_independent_privileged_change_after_metadata_guard(assets, monkeypatch, change):
    target = Path('/etc/named.conf')
    target.write_text('guarded bytes\n', encoding='utf-8')
    target.chmod(0o640)
    original = NativeResults.v2_runner_on_ok
    mutations = []

    def record_and_change(callback, result):
        original(callback, result)
        if result._task.name == 'Require bounded private files before reading deployment contents':
            mutations.append(change)
            if change == 'replace':
                replacement = target.with_name('native-seal-replacement')
                replacement.write_text('independent privileged replacement\n', encoding='utf-8')
                replacement.chmod(0o640)
                replacement.replace(target)
            elif change == 'mode': target.chmod(0o660)
            else: target.unlink()

    monkeypatch.setattr(NativeResults, 'v2_runner_on_ok', record_and_change)
    status, result = seal()
    assert mutations == [change]
    assert status == 2 and result.failed == ['Require unchanged guarded files before recording deployment success']
    assert not SEAL.exists()


@pytest.mark.parametrize('settings', [dict(hibernate_deployment=False), dict(bind_deployment_lease_acquired=False),
    dict(bind_deployment_config_checked=False), dict(bind_deployment_seal_kind='deploy', bind_deployment_health_completed=False)])
def test_native_seal_requires_explicit_ownership_and_completion_before_reading_assets(assets, settings):
    target = Path('/etc/named.conf')
    target.write_text('must not read before completion\n', encoding='utf-8')
    target.chmod(0o640)
    os.utime(str(target), ns=(1, 2))
    status, result = seal(**settings)
    assert status == 2 and result.failed == ['Require completed health checks before sealing deployment success']
    assert target.stat().st_atime_ns == 1
    assert not SEAL.exists()

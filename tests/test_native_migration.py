"""Native predeployment backup archives and refusal before DNS changes."""
import hashlib
import os
from pathlib import Path
import shlex
import tarfile

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeMigrationRun, NativeResults

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')


@pytest.fixture
def assets():
    with NativeFilesystemScope(NativeMigrationRun):
        yield


def run(root=NativeMigrationRun.ROOT):
    return NativeMigrationRun('tasks.d/deploy/migration.yml', {'bind_backup_root': root}).run()


@pytest.mark.parametrize('legacy', [False, True])
def test_native_migration_preserves_exact_sources_and_records_a_real_verified_archive(assets, legacy):
    contents = {'/etc/named.conf': b'synthetic named configuration\n',
                '/var/named/zones/example.test.zone': b'synthetic static zone\n'}
    if legacy: contents['/etc/named/operator.conf'] = b'preserved manual fragment\n'
    for name, raw in contents.items():
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        path.chmod(0o640)
    for name in ('/usr/local/sbin/bind-backup.sh', '/usr/local/sbin/bind-configtool.sh'):
        Path(name).write_text('synthetic legacy executable\n', encoding='utf-8')
    status, result = run()
    assert status == 0 and not result.failed
    archive = Path(result.facts['bind_recovery_archive'])
    assert archive.parent == Path(NativeMigrationRun.ROOT)
    assert archive.is_file() and archive.stat().st_uid == 0 and archive.stat().st_mode & 0o777 == 0o600
    checksum = archive.with_name(archive.name + '.sha256')
    assert checksum.read_text().split() == [hashlib.sha256(archive.read_bytes()).hexdigest(), archive.name]
    assert checksum.stat().st_mode & 0o777 == 0o600
    with tarfile.open(str(archive), 'r:gz') as saved:
        for name, raw in contents.items():
            assert saved.extractfile(name.lstrip('/')).read() == raw
            assert Path(name).read_bytes() == raw
    helper = Path('/usr/local/sbin/bind-configtool')
    assert helper.read_bytes() == (Path(__file__).resolve().parents[1] / 'roles/bind9/files/bind-configtool.sh').read_bytes()
    assert helper.stat().st_mode & 0o777 == 0o700
    assert not Path('/usr/local/sbin/bind-backup.sh').exists()
    assert not Path('/usr/local/sbin/bind-configtool.sh').exists()


@pytest.mark.parametrize('root', [None, False, {}, 'relative-fixture'])
def test_native_migration_refuses_invalid_roots_before_installing_any_utility(assets, root):
    status, result = run(root)
    assert status == 2 and result.failed == ['Require an absolute backup root before installing the backup utility']
    assert not Path('/usr/local/sbin/bind-configtool').exists()
    assert not Path('/var/backups/Bind9-Ansible').exists()


@pytest.mark.parametrize('kind', ['hardlink', 'symlink', 'fifo'])
def test_native_migration_propagates_actual_backup_safety_refusals_without_recovery_receipt(assets, tmp_path, kind):
    target = Path('/etc/named.conf')
    foreign = tmp_path / 'operator-record'
    foreign.write_text('private source must not be opened\n', encoding='utf-8')
    foreign.chmod(0o600)
    if kind == 'hardlink': os.link(str(foreign), str(target))
    elif kind == 'symlink': target.symlink_to(foreign)
    else: os.mkfifo(str(target), 0o600)
    os.utime(str(foreign), ns=(1, 2))
    status, result = run()
    assert status == 2 and result.failed == ['Back up full BIND configuration before any changes']
    assert 'bind_recovery_archive' not in result.facts
    assert foreign.stat().st_atime_ns == 1
    assert not list(Path(NativeMigrationRun.ROOT).glob('*.tar.gz'))


@pytest.mark.parametrize('stdout', ['unrecognized successful reply', 'BACKUP_CREATED /different-root/test.tar.gz'])
def test_native_migration_rejects_a_corrupted_external_backup_receipt(assets, monkeypatch, stdout):
    original = NativeResults.v2_runner_on_ok
    injected = []

    def record_and_corrupt(callback, result):
        original(callback, result)
        if result._task.name == 'Remove previous installed configtool with .sh extension':
            # Model a privileged replacement at the external command boundary.
            # This actor is outside product coverage; no Bash hit is inferred.
            path = Path('/usr/local/sbin/bind-configtool')
            path.write_text('#!/bin/sh\nprintf "%s\\n" ' + shlex.quote(stdout) + '\n', encoding='utf-8')
            path.chmod(0o700)
            injected.append(True)

    monkeypatch.setattr(NativeResults, 'v2_runner_on_ok', record_and_corrupt)
    status, result = run()
    assert injected == [True]
    assert status == 2 and result.failed == ['Verify backup archive path was returned']
    assert not Path('/etc/named.conf').exists()

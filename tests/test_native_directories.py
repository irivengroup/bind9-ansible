"""Actual directory/log ownership and timestamp contracts in disposable roots."""
import grp
import os
from pathlib import Path
import pwd
import shutil
import subprocess

import pytest

from native_ansible_runner import NativeDirectoryRun, NativeFilesystemScope, NativePathRun

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')


def named_identity():
    """Create a disposable account only when the clean QA image lacks BIND."""
    targets = ('/etc/passwd', '/etc/group', '/etc/shadow', '/etc/gshadow')
    for line in Path('/proc/self/mountinfo').read_text(encoding='utf-8').splitlines():
        mount = line.split()[4]
        if mount != '/' and any(target == mount or target.startswith(mount + '/') for target in targets):
            raise ValueError('Disposable identity setup refuses mounted account databases')
    try:
        grp.getgrnam('named')
    except KeyError:
        subprocess.run([shutil.which('groupadd'), '--system', 'named'], check=True, timeout=10)
    try:
        pwd.getpwnam('named')
    except KeyError:
        subprocess.run([shutil.which('useradd'), '--system', '--no-create-home', '--gid', 'named',
                        '--home-dir', '/var/named', '--shell', shutil.which('nologin'), 'named'], check=True, timeout=10)
    return pwd.getpwnam('named').pw_uid, grp.getgrnam('named').gr_gid


@pytest.fixture
def tree():
    with NativeFilesystemScope(NativeDirectoryRun):
        yield named_identity()


@pytest.mark.parametrize('mode', ['auto', True, False])
@pytest.mark.parametrize('existing', [False, True])
def test_native_directory_and_log_metadata_preserve_records_and_timestamps(tree, mode, existing):
    uid, gid = tree
    logs = [Path('/var/log/named') / name for name in ('default.log', 'security.log', 'xfer.log')]
    dynamic = Path('/var/named/dynamic')
    prior = None
    if existing:
        logs[0].parent.mkdir(parents=True)
        for path in logs:
            path.write_text('existing audit record\n', encoding='utf-8')
            path.chmod(0o600)
            os.utime(str(path), ns=(1, 2))
        dynamic.mkdir(parents=True)
        dynamic.chmod(0o711)
        prior = (dynamic.stat().st_uid, dynamic.stat().st_gid, dynamic.stat().st_mode)
    status, result = NativeDirectoryRun('tasks.d/deploy/directories.yml', {'bind_dnssec_active': mode}).run()
    assert status == 0 and result.failed == []
    for name, owner in (('zones', 0), ('slaves', uid), ('data', uid)):
        info = (Path('/var/named') / name).stat()
        assert info.st_uid == owner and info.st_gid == gid and info.st_mode & 0o777 == 0o750
    if mode is False:
        assert dynamic.exists() is existing
        if existing:
            info = dynamic.stat()
            assert (info.st_uid, info.st_gid, info.st_mode) == prior
    else:
        info = dynamic.stat()
        assert info.st_uid == uid and info.st_gid == gid and info.st_mode & 0o777 == 0o750
    info = logs[0].parent.stat()
    assert info.st_uid == uid and info.st_gid == gid and info.st_mode & 0o777 == 0o750
    for path in logs:
        info = path.stat()
        assert info.st_uid == uid and info.st_gid == gid and info.st_mode & 0o777 == 0o640
        if existing:
            assert info.st_atime_ns == 1 and info.st_mtime_ns == 2
        assert path.read_text(encoding='utf-8') == ('existing audit record\n' if existing else '')


@pytest.mark.parametrize('name', ['/var/named/zones', '/var/named/slaves', '/var/named/data',
                                 '/var/named/dynamic', '/var/log/named'])
def test_native_directory_creation_refuses_a_link_inserted_after_path_preflight(tree, tmp_path, name):
    """The writing task must not rely solely on an earlier, separate stat check."""
    status, result = NativePathRun('tasks.d/path_safety.yml', {}).run()
    assert status == 0 and not result.failed
    foreign = tmp_path / 'operator-directory'
    foreign.mkdir(mode=0o711)
    evidence = foreign / 'operator-evidence'
    evidence.write_bytes(b'preserve unrelated bytes')
    before = foreign.stat()
    target = Path(name)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(foreign, target_is_directory=True)
    status, result = NativeDirectoryRun('tasks.d/deploy/directories.yml', {'bind_dnssec_active': 'auto'}).run()
    after = foreign.stat()
    assert (status, after.st_uid, after.st_gid, after.st_mode) == (2, before.st_uid, before.st_gid, before.st_mode)
    assert result.failed == ['Create BIND configuration and zone directories']
    assert target.is_symlink() and evidence.read_bytes() == b'preserve unrelated bytes'
    assert sorted(path.name for path in foreign.iterdir()) == ['operator-evidence']

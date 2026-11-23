"""Native path policy with actual links, hardlinks, file types and missing paths."""
import os
from pathlib import Path

import pytest

from native_ansible_runner import NativeFilesystemScope, NativePathRun

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')


@pytest.fixture(autouse=True)
def clean_targets():
    with NativeFilesystemScope(NativePathRun):
        yield


@pytest.mark.parametrize('scenario,accepted', [
    ('absent', True), ('regular_file', True), ('directory', True), ('symlink_file', False),
    ('symlink_directory', False), ('hardlink_file', False), ('directory_at_file', False),
    ('file_at_directory', False), ('fifo', False)])
def test_actual_stat_results_enforce_the_managed_path_contract(tmp_path, scenario, accepted):
    file = Path('/etc/named.conf')
    directory = Path('/var/named/slaves')
    outside = tmp_path / 'unrelated'
    outside.write_text('foreign evidence', encoding='utf-8')
    if scenario in ('regular_file', 'hardlink_file'):
        file.write_text('synthetic configuration', encoding='utf-8')
        file.chmod(0o600)
        if scenario == 'hardlink_file': os.link(str(file), str(tmp_path / 'alias'))
    elif scenario == 'directory': directory.mkdir(parents=True)
    elif scenario == 'symlink_file': file.symlink_to(outside)
    elif scenario == 'symlink_directory':
        directory.parent.mkdir()
        directory.symlink_to(tmp_path, target_is_directory=True)
    elif scenario == 'directory_at_file': file.mkdir()
    elif scenario == 'file_at_directory':
        directory.parent.mkdir()
        directory.write_text('foreign data', encoding='utf-8')
    elif scenario == 'fifo': os.mkfifo(str(file), 0o600)
    status, result = NativePathRun('tasks.d/path_safety.yml', {}).run()
    assert status == (0 if accepted else 2)
    assert result.failed == ([] if accepted else ['Refuse symlinks and non-regular managed files'])
    assert result.ok[0] == 'Inspect managed BIND paths without following symlinks'
    assert len(result.registers['bind_managed_paths']['results']) == 17
    assert outside.read_text(encoding='utf-8') == 'foreign evidence'
    if scenario == 'symlink_file': assert file.is_symlink()
    elif scenario == 'symlink_directory': assert directory.is_symlink()
    elif scenario == 'hardlink_file': assert file.stat().st_nlink == 2
    elif scenario == 'fifo': assert not file.is_file()


@pytest.mark.parametrize('hardlink', [False, True])
def test_path_inspection_never_reads_content_before_trust_is_established(tmp_path, hardlink):
    """Even a rejected inode must not be hashed or passed to content scanners."""
    target = Path('/etc/named.conf')
    target.write_bytes(b'synthetic content that must remain unread')
    target.chmod(0o600)
    if hardlink:
        os.link(str(target), str(tmp_path / 'unrelated-alias'))
    # An old atime makes a real content read observable on relatime filesystems.
    os.utime(str(target), ns=(1, 2))
    assert target.stat().st_atime_ns == 1
    status, result = NativePathRun('tasks.d/path_safety.yml', {}).run()
    assert status == (2 if hardlink else 0)
    assert target.stat().st_atime_ns == 1
    inspected = result.registers['bind_managed_paths']['results'][0]['stat']
    assert not {'checksum', 'mimetype', 'charset', 'attributes', 'attr_flags'}.intersection(inspected)


@pytest.mark.parametrize('name', ['/usr/local/sbin/bind-backup.sh', '/usr/local/sbin/bind-configtool.sh',
                               '/usr/local/sbin/named-transaction'])
@pytest.mark.parametrize('kind', ['regular', 'directory', 'symlink', 'hardlink'])
def test_native_preflight_authenticates_legacy_paths_before_their_retirement(tmp_path, name, kind):
    target = Path(name)
    foreign = tmp_path / 'operator-evidence'
    foreign.write_text('retain unrelated operator data', encoding='utf-8')
    if kind == 'directory':
        target.mkdir()
        (target / 'operator-evidence').write_bytes(foreign.read_bytes())
    elif kind == 'symlink': target.symlink_to(foreign)
    elif kind == 'hardlink': os.link(str(foreign), str(target))
    else: target.write_text('regular legacy executable', encoding='utf-8')
    status, result = NativePathRun('tasks.d/path_safety.yml', {}).run()
    assert status == (0 if kind == 'regular' else 2)
    assert result.failed == ([] if kind == 'regular' else ['Refuse symlinks and non-regular managed files'])
    assert foreign.read_text() == 'retain unrelated operator data'
    if kind == 'directory': assert (target / 'operator-evidence').read_bytes() == foreign.read_bytes()
    elif kind == 'symlink': assert target.is_symlink()
    else: assert target.is_file()

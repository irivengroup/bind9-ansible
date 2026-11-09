"""Exercise unchanged archive CLI bytes against owned disposable files.

TAR, checksums, locks, staging, extraction and rollback are native. Only the
systemd/BIND/SELinux command boundaries are explicit deterministic doubles.
"""
import hashlib
import os
from pathlib import Path

import pytest

from configtool_lab import configtool_lab as _configtool_fixture

configtool_lab = _configtool_fixture

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
    reason='Requires an explicitly enabled disconnected clean Linux root')


@pytest.fixture
def archive_cli(configtool_lab):
    lab = configtool_lab
    return lab.invoke, lab.configuration, lab.zones, lab.root


def archive_for_restore(invoke, backup_root):
    """Check the real generated backup before assigning a stable fixture name.

    Backup names intentionally contain time/random bytes. Restore inputs must
    be identical in the independent interpreter phases; renaming an already
    verified archive is a fixture operation, never a production-source change.
    """
    created = invoke('backup')
    assert created.returncode == 0 and created.stdout.startswith('BACKUP_CREATED '), created.stderr
    generated = Path(created.stdout[len('BACKUP_CREATED '):].strip())
    assert generated.parent == backup_root and generated.stat().st_mode & 0o777 == 0o600
    listing = invoke('list')
    assert listing.returncode == 0 and listing.stdout.strip() == generated.name
    digest = hashlib.sha256(generated.read_bytes()).hexdigest()
    sidecar = Path(str(generated) + '.sha256')
    assert sidecar.read_text(encoding='ascii') == digest + '  ' + generated.name + '\n'
    archive = backup_root / 'named-config-20261105T120000Z-12345678.tar.gz'
    generated.rename(archive)
    sidecar.rename(Path(str(archive) + '.sha256'))
    Path(str(archive) + '.sha256').write_text(digest + '  ' + archive.name + '\n', encoding='ascii')
    return archive


@pytest.mark.parametrize('fail_validation', [False, True])
def test_native_archive_cli_restores_or_rolls_back_without_changing_unrelated_files(archive_cli, fail_validation):
    invoke, configuration, zones, backup_root = archive_cli
    archive = archive_for_restore(invoke, backup_root)
    if fail_validation:
        # A prior successful validation must not consume a later armed failure.
        first = invoke('restore', '--archive', archive.name)
        assert first.returncode == 0 and first.stdout.startswith('RESTORED '), first.stderr
        assert not list(backup_root.glob('.restore-*'))
    configuration.write_text('operator configuration\n', encoding='ascii')
    (zones / 'original.zone').write_text('operator zone\n', encoding='ascii')
    (zones / 'unrelated.file').write_text('operator unrelated bytes\n', encoding='ascii')
    # This exact managed marker authorizes pruning; rollback must restore it.
    introduced = zones / 'new-managed.zone'
    introduced.write_text('; Bind9-Ansible-MANAGED-ZONE\nnew zone\n', encoding='ascii')
    result = invoke('restore', '--archive', archive.name,
        **({'BIND_ARCHIVE_FAIL_ONCE': '1'} if fail_validation else {}))
    assert result.returncode == (1 if fail_validation else 0), result.stderr
    assert configuration.read_text() == ('operator configuration\n' if fail_validation else 'original configuration\n')
    assert (zones / 'original.zone').read_text() == ('operator zone\n' if fail_validation else 'original zone\n')
    assert (zones / 'unrelated.file').read_text() == 'operator unrelated bytes\n'
    assert introduced.exists() == fail_validation
    assert not list(backup_root.glob('.restore-*'))
    assert (backup_root / (archive.name + '.sha256')).stat().st_mode & 0o777 == 0o600


def test_native_archive_cli_failed_restore_removes_only_newly_introduced_backup_paths(archive_cli):
    invoke, configuration, zones, backup_root = archive_cli
    directory = zones / 'archive-only'
    directory.mkdir()
    archived = directory / 'old.zone'
    archived.write_text('archive-only data\n', encoding='ascii')
    archive = archive_for_restore(invoke, backup_root)
    archived.unlink()
    directory.rmdir()
    configuration.write_text('pre-restore bytes\n', encoding='ascii')
    result = invoke('restore', '--archive', archive.name, BIND_ARCHIVE_FAIL_ONCE='1')
    assert result.returncode == 1 and 'recovering pre-restore' in result.stderr
    assert configuration.read_text() == 'pre-restore bytes\n'
    assert not directory.exists()
    assert not list(backup_root.glob('.restore-*'))


def test_native_archive_cli_rejects_unknown_options_before_creating_backup_state(archive_cli):
    invoke, configuration, zones, backup_root = archive_cli
    result = invoke('list', '--unexpected')
    assert result.returncode == 2 and result.stdout == '' and result.stderr.startswith('usage: bind-configtool ')
    assert not backup_root.exists()
    assert configuration.read_text() == 'original configuration\n'
    assert (zones / 'original.zone').read_text() == 'original zone\n'

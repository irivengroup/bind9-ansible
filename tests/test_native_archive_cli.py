"""Exercise unchanged archive CLI bytes against owned disposable files.

TAR, checksums, locks, staging, extraction and rollback are native. Only the
systemd/BIND/SELinux command boundaries are explicit deterministic doubles.
"""
import hashlib
import os
from pathlib import Path
import tarfile

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


def test_native_empty_archive_catalog_has_no_entries_or_mutations(configtool_lab):
    lab = configtool_lab
    original = lab.configuration.read_bytes()
    result = lab.invoke('list')
    assert result.returncode == 0 and result.stdout == '' and result.stderr == ''
    assert lab.configuration.read_bytes() == original
    assert not list(lab.root.glob('named-config-*'))


def test_native_empty_backup_pruning_rolls_back_when_validation_refuses(configtool_lab):
    """Restore from a first-deployment backup must recover later managed zones."""
    lab = configtool_lab
    lab.configuration.unlink()
    (lab.zones / 'original.zone').unlink()
    lab.zones.rmdir()
    lab.zones.parent.rmdir()
    archive = archive_for_restore(lab.invoke, lab.root)
    with tarfile.open(archive, mode='r:gz') as content:
        assert content.getnames() == []
    lab.configuration.write_text('later operator configuration\n', encoding='ascii')
    lab.zones.mkdir(parents=True)
    zone = lab.zones / 'later.zone'
    zone.write_text('; Bind9-Ansible-MANAGED-ZONE\nowned later zone\n', encoding='ascii')
    lab.command('named-checkconf',
        'if [[ ! -e $BIND_ARCHIVE_CHECKED ]]; then\n'
        '  [[ ! -e /var/named/zones/later.zone ]] || exit 92\n'
        '  : > "$BIND_ARCHIVE_CHECKED"; exit 1\nfi\n'
        '[[ -f /var/named/zones/later.zone ]] || exit 93\nexit 0\n')
    result = lab.invoke('restore', '--archive', archive.name)
    assert result.returncode == 1 and 'RESTORED ' not in result.stdout, result.stderr
    assert (lab.folder / 'configuration-checked').is_file()
    assert lab.configuration.read_text() == 'later operator configuration\n'
    assert zone.read_text() == '; Bind9-Ansible-MANAGED-ZONE\nowned later zone\n'
    assert not list(lab.root.glob('.restore-state.*')) and not list(lab.root.glob('.restore-safety.*'))


def install_packaged_bind_links():
    """Install only the distro layout in the paths owned by the isolated fixture."""
    shared = Path('/usr/share/named')
    shared.mkdir(mode=0o750)
    links = [Path('/var/named') / name for name in ('named.localhost', 'named.loopback', 'named.empty')]
    links.append(Path('/var/named/named.ca'))
    for link, directory in zip(links, [shared, shared, shared, Path('/etc')]):
        target = directory / link.name
        target.write_text('packaged ' + link.name + '\n', encoding='ascii')
        target.chmod(0o640)
        link.symlink_to('../..' + str(target))
    return links


@pytest.mark.parametrize('fail_validation', [False, True])
def test_native_archive_keeps_packaged_bind_links_through_backup_and_restore(archive_cli, fail_validation):
    invoke, configuration, zones, root = archive_cli
    old_files = [Path('/var/named') / name for name in ('named.localhost', 'named.ca')]
    for path in old_files:
        path.write_text('regular file from the older layout\n', encoding='ascii')
    legacy = archive_for_restore(invoke, root)
    for path in old_files:
        path.unlink()
    links = install_packaged_bind_links()
    before = {link: (os.readlink(link), link.lstat().st_ino, link.read_bytes()) for link in links}

    result = invoke('backup')
    assert result.returncode == 0 and result.stdout.startswith('BACKUP_CREATED '), result.stderr
    new_backup = Path(result.stdout[len('BACKUP_CREATED '):].strip())
    with tarfile.open(new_backup, mode='r:gz') as archive:
        names = archive.getnames()
    assert 'etc/named.conf' in names and 'var/named/zones/original.zone' in names
    assert all(str(link).lstrip('/') not in names for link in links)

    configuration.write_text('operator configuration\n', encoding='ascii')
    result = invoke('restore', '--archive', legacy.name,
                    **({'BIND_ARCHIVE_FAIL_ONCE': '1'} if fail_validation else {}))
    assert result.returncode == int(fail_validation), result.stderr
    assert configuration.read_text() == ('operator configuration\n' if fail_validation else 'original configuration\n')
    assert (zones / 'original.zone').read_text() == 'original zone\n'
    for link, (relative, inode, content) in before.items():
        assert os.readlink(link) == relative and link.lstat().st_ino == inode
        assert link.read_bytes() == content
    assert not list(root.glob('.restore-*'))


@pytest.mark.parametrize('kind', [
    'trailing_newline', 'link_owner', 'target_owner', 'target_mode', 'dangling', 'directory', 'indirect',
])
def test_native_archive_refuses_untrusted_variants_of_packaged_loopback_links(archive_cli, kind):
    invoke, _, _, root = archive_cli
    link = install_packaged_bind_links()[0]
    target = link.resolve()
    if kind == 'trailing_newline':
        target.with_name(target.name + '\n').write_bytes(target.read_bytes())
        link.unlink()
        link.symlink_to('../../usr/share/named/' + target.name + '\n')
    elif kind == 'link_owner':
        os.chown(link, 65534, 65534, follow_symlinks=False)
    elif kind == 'target_owner':
        os.chown(target, 65534, 65534)
    elif kind == 'target_mode':
        target.chmod(0o642)
    elif kind == 'dangling':
        target.unlink()
    else:
        target.unlink()
        if kind == 'directory':
            target.mkdir()
        else:
            target.symlink_to('named.loopback')
    relative = os.readlink(link)
    result = invoke('backup')
    assert result.returncode == 1 and 'Unsafe native BIND package link' in result.stderr
    assert not list(root.glob('*.tar.gz'))
    assert os.readlink(link) == relative


@pytest.mark.parametrize('program,argument,output', [
    ('stat', '%u', ''),
    ('readlink', '--', ''),
    ('stat', '%u:%a', ''),
    ('stat', '%u:%a', '0:2+2'),
])
def test_native_archive_refuses_untrusted_loopback_metadata(configtool_lab, program, argument, output):
    lab = configtool_lab
    links = install_packaged_bind_links()
    path = links[0] if argument != '%u:%a' else links[0].resolve()
    field = '$2' if program == 'stat' else '$1'
    lab.command(program, f'if [[ {field} == "$BIND_NATIVE_ARG" && ${{@: -1}} == "$BIND_NATIVE_OBJECT" ]]; then\n'
        '  printf "%s\\n" "$BIND_NATIVE_OUTPUT"; exit "$BIND_NATIVE_RC"\nfi\n'
        f'exec /usr/bin/{program} "$@"\n')
    result = lab.invoke('backup', BIND_NATIVE_OBJECT=str(path), BIND_NATIVE_ARG=argument,
        BIND_NATIVE_OUTPUT=output, BIND_NATIVE_RC='0' if output else '1')
    assert result.returncode == 1 and 'Unsafe native BIND package link' in result.stderr
    assert not list(lab.root.glob('*.tar.gz'))
    assert all(link.is_symlink() for link in links)


@pytest.mark.parametrize('phase,diagnostic', [
    ('staging', 'Live restore destinations changed after preparation'),
    ('snapshot', 'Live restore destinations changed before application'),
    ('recovery', 'Safety recovery incomplete'),
    ('after-recovery', 'Safety recovery incomplete'),
])
def test_native_archive_stops_when_packaged_loopback_layout_changes(configtool_lab, phase, diagnostic):
    lab = configtool_lab
    legacy = archive_for_restore(lab.invoke, lab.root)
    shared = Path('/usr/share/named')
    shared.mkdir(mode=0o750)
    target = shared / 'named.localhost'
    target.write_bytes(b'vendor data\n')
    target.chmod(0o640)
    link = Path('/var/named/named.localhost')
    make_link = '/usr/bin/ln -s ../../usr/share/named/named.localhost /var/named/named.localhost'
    if phase == 'recovery':
        lab.command('named-checkconf', make_link + '\nexit 1\n')
    else:
        pattern = {
            'staging': '[[ $1 == --extract && ${@: -1} == */.restore-state.*/tree ]]',
            'snapshot': '[[ $1 == --create && $* == */.restore-safety.* ]]',
            'after-recovery': '[[ $1 == --extract && ${@: -1} == / && $* == */.restore-safety.* ]]',
        }[phase]
        lab.command('tar', '/usr/bin/tar "$@" || exit $?\n'
            f'if {pattern}; then {make_link}; fi\n')
    lab.configuration.write_text('operator configuration\n', encoding='ascii')
    result = lab.invoke('restore', '--archive', legacy.name,
                        **({'BIND_ARCHIVE_FAIL_ONCE': '1'} if phase == 'after-recovery' else {}))
    assert result.returncode == 1 and diagnostic in result.stderr
    assert os.readlink(link) == '../../usr/share/named/named.localhost'
    assert target.read_bytes() == b'vendor data\n'
    if phase != 'recovery':
        assert lab.configuration.read_text(encoding='ascii') == 'operator configuration\n'
    if phase == 'after-recovery':
        assert list(lab.root.glob('.restore-safety.*')) and list(lab.root.glob('.restore-state.*'))


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

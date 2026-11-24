"""Read exact RPM loopback links while keeping their targets immutable."""
import os

import pytest

from test_management import CONF, Lab, M, ROOT_ZONE, ZONE, pytestmark as management_marks

pytestmark = management_marks


def vendor(lab, name='named.localhost'):
    """Create the same relative link and protected target as recent bind RPMs."""
    target = lab.root / 'usr/share/named' / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(ROOT_ZONE)
    target.chmod(0o644)
    link = lab.root / 'var/named' / name
    link.symlink_to('../../usr/share/named/' + name)
    return link, target


@pytest.mark.parametrize('name', ['named.localhost', 'named.loopback', 'named.empty'])
def test_approved_vendor_zone_links_can_be_read_without_broadening_public_paths(tmp_path, name):
    lab = Lab(tmp_path)
    link, target = vendor(lab, name)
    before = (link.lstat().st_ino, target.stat().st_ino, target.read_bytes())
    files = M.SafeFiles(lab.root)
    virtual = '/var/named/' + name
    assert files.read(virtual) == target.read_bytes()
    stamp = files.observations[virtual]
    assert stamp[:2] == (target.stat().st_dev, target.stat().st_ino)
    assert stamp[-1] == files.stamp(link.lstat())
    assert files.read(virtual) == target.read_bytes() and files.observations[virtual] == stamp
    with pytest.raises(M.ManagementError, match='under /etc or /var/named'):
        files.read('/usr/share/named/' + name)
    assert before == (link.lstat().st_ino, target.stat().st_ino, target.read_bytes())


@pytest.mark.parametrize('fault', ['link-owner', 'link-hardlink', 'absolute-link', 'different-relative-link',
    'ancestor-file', 'ancestor-link', 'ancestor-owner', 'ancestor-writable',
    'target-link', 'target-owner', 'target-writable', 'target-hardlink', 'target-fifo'])
def test_native_link_exception_preserves_every_ownership_inode_and_path_refusal(tmp_path, fault):
    lab = Lab(tmp_path)
    link, target = vendor(lab)
    if fault == 'link-owner':
        os.chown(link, 1, -1, follow_symlinks=False)
    elif fault == 'link-hardlink':
        os.link(link, lab.root / 'duplicate-native-link', follow_symlinks=False)
    elif fault in ('absolute-link', 'different-relative-link'):
        link.unlink()
        link.symlink_to(str(target) if fault == 'absolute-link' else '../../usr/share/named/named.empty')
    elif fault in ('ancestor-file', 'ancestor-link'):
        parent = target.parent
        held = parent.with_name('vendor-held')
        parent.rename(held)
        if fault == 'ancestor-file':
            parent.write_bytes(b'foreign ancestor')
        else:
            parent.symlink_to(held, target_is_directory=True)
    elif fault == 'ancestor-owner':
        os.chown(target.parent, 1, -1)
    elif fault == 'ancestor-writable':
        target.parent.chmod(0o775)
    elif fault == 'target-link':
        target.unlink()
        target.symlink_to(lab.root / 'etc/named.conf')
    elif fault == 'target-owner':
        os.chown(target, 1, -1)
    elif fault == 'target-writable':
        target.chmod(0o664)
    elif fault == 'target-hardlink':
        os.link(target, lab.root / 'duplicate-native-target')
    else:
        assert fault == 'target-fifo'
        target.unlink()
        os.mkfifo(target)
    with pytest.raises((M.ManagementError, OSError)):
        M.SafeFiles(lab.root).read('/var/named/named.localhost')
    assert (lab.root / 'etc/named.conf').read_text() == CONF and not lab.calls


def test_unlisted_native_looking_name_does_not_acquire_a_link_exception(tmp_path):
    lab = Lab(tmp_path)
    vendor(lab, 'operator.zone')
    with pytest.raises(OSError):
        M.SafeFiles(lab.root).read('/var/named/operator.zone')


def test_identical_native_link_replacement_changes_the_snapshot_identity(tmp_path):
    lab = Lab(tmp_path)
    link, target = vendor(lab)
    files = M.SafeFiles(lab.root)
    virtual = '/var/named/named.localhost'
    assert files.read(virtual) == target.read_bytes()
    observed = files.observations[virtual]
    temporary = link.with_name('replacement')
    temporary.symlink_to(os.readlink(link))
    temporary.replace(link)
    assert files.read(virtual) == target.read_bytes()
    assert files.observations[virtual][:2] == observed[:2]
    assert files.observations[virtual] != observed


def test_replacing_a_native_link_during_the_static_read_is_refused(tmp_path, monkeypatch):
    lab = Lab(tmp_path)
    link, target = vendor(lab)
    native = M.os.fstat
    samples = []
    def intervene(descriptor):
        info = native(descriptor)
        if os.readlink('/proc/self/fd/' + str(descriptor)) == str(target):
            samples.append(info.st_ino)
            if len(samples) == 2:
                temporary = link.with_name('replacement')
                temporary.symlink_to(os.readlink(link))
                temporary.replace(link)
        return info
    monkeypatch.setattr(M.os, 'fstat', intervene)
    with pytest.raises(M.ManagementError, match='link changed during'):
        M.SafeFiles(lab.root).read('/var/named/named.localhost')
    assert len(samples) == 2 and target.read_text() == ROOT_ZONE


def test_configuration_rechecks_the_link_identity_even_at_identical_vendor_bytes(tmp_path, monkeypatch):
    lab = Lab(tmp_path)
    link, target = vendor(lab)
    lab.put('/etc/named.conf', CONF + 'zone "localhost" { type master; file "named.localhost"; };\n')
    native = M.SafeFiles.read
    swaps = []
    def intervene(files, virtual):
        data = native(files, virtual)
        if virtual == '/var/named/named.localhost' and not swaps:
            temporary = link.with_name('replacement')
            temporary.symlink_to(os.readlink(link))
            temporary.replace(link)
            swaps.append(True)
        return data
    monkeypatch.setattr(M.SafeFiles, 'read', intervene)
    with pytest.raises(M.ManagementError, match='changed while observing'):
        M.Configuration(M.SafeFiles(lab.root))
    assert swaps and target.read_text() == ROOT_ZONE and not lab.calls


@pytest.mark.parametrize('linked', [False, True])
@pytest.mark.parametrize('filename', ['named.localhost', '/var/named//named.localhost'])
def test_operator_zone_alias_cannot_turn_a_native_asset_into_writable_record_data(tmp_path, linked, filename):
    lab = Lab(tmp_path)
    if linked:
        link, target = vendor(lab)
    else:
        target = lab.root / 'var/named/named.localhost'
        link = None
    target.write_text(ZONE)
    target.chmod(0o644)
    lab.put('/etc/named.conf', CONF.replace('file "zones/example.test.zone"', 'file "' + filename + '"'))
    manager = lab.manager('record', 'update', 'www', zone='example.test', type='A', values=['192.0.2.81'])
    # A doubled slash is not an approved vendor-link spelling. It must retain
    # O_NOFOLLOW refusal; the regular-file spelling still needs write denial.
    if linked and filename.startswith('/'):
        with pytest.raises(OSError):
            M.Configuration(manager.files)
    else:
        manager.config = M.Configuration(manager.files)
        with pytest.raises(M.ManagementError, match='non-native primary'):
            manager.plan()
    assert target.read_text() == ZONE and not lab.calls and not manager.changes
    assert link is None or link.is_symlink()

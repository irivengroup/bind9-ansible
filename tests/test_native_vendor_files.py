"""Distribution file prerequisites through unmodified native Ansible actions."""
import os
from pathlib import Path

import pytest

from native_ansible_runner import NativeFilesystemScope, NativePolicyParents, NativeVendorRun

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')


@pytest.mark.parametrize('installed', [True, False])
def test_policy_scope_preserves_vendor_siblings_and_releases_only_its_own_directories(tmp_path, monkeypatch, installed):
    leaf = tmp_path / 'crypto-policies/back-ends/bind.config'
    monkeypatch.setattr(NativePolicyParents, 'FILE', leaf)
    class ScopedVendorRun(NativeVendorRun):
        TARGETS = (str(leaf),)
    other = leaf.parent / 'openssl.config'
    if installed:
        other.parent.mkdir(parents=True)
        other.write_bytes(b'untouched vendor setting')
        identity = (other.stat().st_dev, other.stat().st_ino, other.stat().st_mtime_ns)
    with NativeFilesystemScope(ScopedVendorRun):
        leaf.write_bytes(b'synthetic BIND input')
    assert not os.path.lexists(leaf)
    if installed:
        assert other.read_bytes() == b'untouched vendor setting'
        assert (other.stat().st_dev, other.stat().st_ino, other.stat().st_mtime_ns) == identity
    else:
        assert not leaf.parent.parent.exists()


def test_policy_scope_refuses_an_existing_backend_and_an_indirect_parent(tmp_path, monkeypatch):
    leaf = tmp_path / 'crypto-policies/back-ends/bind.config'
    monkeypatch.setattr(NativePolicyParents, 'FILE', leaf)
    class ScopedVendorRun(NativeVendorRun):
        TARGETS = (str(leaf),)
    target = tmp_path / 'distribution-bind.txt'
    target.write_bytes(b'untouched vendor setting')
    leaf.parent.mkdir(parents=True)
    leaf.symlink_to(target)
    with pytest.raises(ValueError, match='empty test-owned roots'):
        with NativeFilesystemScope(ScopedVendorRun):
            pytest.fail('A pre-existing distribution symlink was accepted')
    assert leaf.readlink() == target and target.read_bytes() == b'untouched vendor setting'
    leaf.unlink()
    leaf.parent.rmdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    leaf.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match='Unsafe parent'):
        with NativeFilesystemScope(ScopedVendorRun):
            pytest.fail('An indirect policy parent was accepted')
    assert leaf.parent.is_symlink() and not list(outside.iterdir())


@pytest.fixture
def vendor_tree(tmp_path):
    files = [Path('/etc/named.rfc1912.zones')] + [Path('/var/named') / name for name in
             ('named.localhost', 'named.loopback', 'named.empty')] + [
             NativePolicyParents.FILE, Path('/etc/named.root.key')]
    with NativeFilesystemScope(NativeVendorRun):
        for path in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic vendor asset\n', encoding='utf-8')
            os.utime(str(path), ns=(1, 2))
        yield files


@pytest.fixture
def linked_loopback(vendor_tree):
    """Recreate the BIND 9.18 RPM layout and keep independent test-owned inodes."""
    shared = Path('/usr/share/named')
    shared.mkdir(mode=0o750)
    links = vendor_tree[1:4]
    for path in links:
        target = shared / path.name
        path.rename(target)
        target.chmod(0o640)
        os.utime(str(target), ns=(1, 2))
        path.symlink_to('../../usr/share/named/' + path.name)
    return links, shared


@pytest.mark.parametrize('mode', ['auto', True, False, 'true', 'false'])
def test_vendor_prerequisites_respect_dnssec_mode_without_opening_contents(vendor_tree, mode):
    status, result = NativeVendorRun('tasks.d/native_loopback.yml', {'bind_dnssec_active': mode}).run()
    assert status == 0 and result.failed == []
    assert len(result.ok) == (6 if mode not in (False, 'false') else 4)
    assert all(path.stat().st_atime_ns == 1 for path in vendor_tree)
    assert 'Inspect the native trust anchor only when validation is enabled' in (
        result.skipped if mode in (False, 'false') else result.ok)


@pytest.mark.parametrize('major', ['7', '8', '9', '10'])
def test_crypto_policy_is_required_only_on_platforms_that_ship_it(vendor_tree, major):
    vendor_tree[4].unlink()
    status, result = NativeVendorRun('tasks.d/native_loopback.yml', {
        'bind_dnssec_active': 'auto', 'ansible_distribution_major_version': major}).run()
    assert status == (0 if major == '7' else 2)
    assert result.failed == ([] if major == '7' else ['Require native crypto-policy include'])
    assert not vendor_tree[4].exists()
    if major == '7':
        assert 'Require native crypto-policy include' in result.skipped
        assert 'Require the native trust anchor for enabled DNSSEC modes' in result.ok


@pytest.mark.parametrize('index', [0, 5])
def test_el7_still_requires_native_loopback_and_dnssec_trust_anchor(vendor_tree, index):
    vendor_tree[index].unlink()
    status, result = NativeVendorRun('tasks.d/native_loopback.yml', {
        'bind_dnssec_active': 'auto', 'ansible_distribution_major_version': '7'}).run()
    assert status == 2
    assert result.failed == [('Require native BIND files without replacing missing or unsafe files'
                              if index == 0 else 'Require the native trust anchor for enabled DNSSEC modes')]
    assert not vendor_tree[index].exists()


@pytest.mark.parametrize('index,task', [(0, 'Require native BIND files without replacing missing or unsafe files'),
    (1, 'Require native BIND files without replacing missing or unsafe files'),
    (4, 'Require native crypto-policy include'), (5, 'Require the native trust anchor for enabled DNSSEC modes')])
@pytest.mark.parametrize('kind', ['missing', 'directory', 'symlink'])
def test_missing_or_unsafe_vendor_assets_are_never_replaced(vendor_tree, tmp_path, index, task, kind):
    path = vendor_tree[index]
    path.unlink()
    outside = tmp_path / 'foreign'
    outside.write_text('keep unchanged', encoding='utf-8')
    if kind == 'directory': path.mkdir()
    elif kind == 'symlink': path.symlink_to(outside)
    # Following the selected vendor crypto-policy link is intentional.
    accepted = kind == 'symlink' and index == 4
    status, result = NativeVendorRun('tasks.d/native_loopback.yml', {'bind_dnssec_active': 'auto'}).run()
    assert status == (0 if accepted else 2)
    assert result.failed == ([] if accepted else [task])
    if kind == 'missing': assert not path.exists()
    elif kind == 'symlink': assert path.is_symlink()
    else: assert path.is_dir()
    assert outside.read_text(encoding='utf-8') == 'keep unchanged'


def test_packaged_native_loopback_links_are_accepted_without_reading_or_replacing_data(linked_loopback):
    links, shared = linked_loopback
    status, result = NativeVendorRun('tasks.d/native_loopback.yml', {'bind_dnssec_active': 'auto'}).run()
    assert status == 0 and not result.failed
    assert all(path.is_symlink() and path.resolve() == shared / path.name for path in links)
    assert all((shared / path.name).stat().st_atime_ns == 1 for path in links)


@pytest.mark.parametrize('kind,failed_check', [
    ('absolute_link', 'Require native BIND files without replacing missing or unsafe files'),
    ('foreign_link_owner', 'Require native BIND files without replacing missing or unsafe files'),
    ('indirect_target', 'Require native BIND files without replacing missing or unsafe files'),
    ('missing_target', 'Require protected native BIND link targets'),
    ('directory_target', 'Require protected native BIND link targets'),
    ('foreign_target_owner', 'Require protected native BIND link targets'),
    ('group_writable_directory', 'Require protected native BIND link targets'),
    ('world_writable_target', 'Require protected native BIND link targets'),
])
def test_rejects_unsafe_variants_of_packaged_native_loopback_links(linked_loopback, kind, failed_check):
    links, shared = linked_loopback
    link = links[0]
    target = shared / link.name
    if kind == 'absolute_link':
        link.unlink()
        link.symlink_to(target)
    elif kind == 'foreign_link_owner':
        os.chown(link, 65534, 65534, follow_symlinks=False)
    elif kind == 'indirect_target':
        target.unlink()
        target.symlink_to(links[1].name)
    elif kind == 'missing_target':
        target.unlink()
    elif kind == 'directory_target':
        target.unlink()
        target.mkdir()
    elif kind == 'foreign_target_owner':
        os.chown(target, 65534, 65534)
    elif kind == 'group_writable_directory':
        shared.chmod(0o770)
    else:
        target.chmod(0o646)
    before_link = os.readlink(link)
    status, result = NativeVendorRun('tasks.d/native_loopback.yml', {'bind_dnssec_active': 'auto'}).run()
    assert status == 2 and result.failed == [failed_check]
    assert os.readlink(link) == before_link
    assert all((shared / path.name).stat().st_atime_ns == 1 for path in links[1:])

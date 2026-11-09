"""Distribution file prerequisites through unmodified native Ansible actions."""
import os
from pathlib import Path

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeVendorRun

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')


@pytest.fixture
def vendor_tree(tmp_path):
    files = [Path('/etc/named.rfc1912.zones')] + [Path('/var/named') / name for name in
             ('named.localhost', 'named.loopback', 'named.empty')] + [
             Path('/etc/crypto-policies/back-ends/bind.config'), Path('/etc/named.root.key')]
    with NativeFilesystemScope(NativeVendorRun):
        for path in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('synthetic vendor asset\n', encoding='utf-8')
            os.utime(str(path), ns=(1, 2))
        yield files


@pytest.mark.parametrize('mode', ['auto', True, False, 'true', 'false'])
def test_vendor_prerequisites_respect_dnssec_mode_without_opening_contents(vendor_tree, mode):
    status, result = NativeVendorRun('tasks.d/native_loopback.yml', {'bind_dnssec_active': mode}).run()
    assert status == 0 and result.failed == []
    assert len(result.ok) == (6 if mode not in (False, 'false') else 4)
    assert all(path.stat().st_atime_ns == 1 for path in vendor_tree)
    assert 'Inspect the native trust anchor only when validation is enabled' in (
        result.skipped if mode in (False, 'false') else result.ok)


@pytest.mark.parametrize('index,task', [(0, 'Require native BIND files without replacing missing or unsafe files'),
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

"""Preserve foreign inodes and restore genuine runtime bytes and permissions."""
import base64
import json
import os
from pathlib import Path

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeRestoreRun, NativeUnitBoundary

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')
ASSETS = ['/etc/systemd/system/named.service.d/' + name for name in (
    'bind9-runtime.conf', 'bind9-dnssec.conf', 'bind9-z-dnssec.conf')] + [
    '/usr/local/sbin/bind-private-dnssec', '/etc/systemd/system/named-private-dnssec.service',
    '/etc/systemd/system/named-private-dnssec.timer']


@pytest.fixture
def runtime(tmp_path):
    with NativeFilesystemScope(NativeRestoreRun):
        with NativeUnitBoundary(tmp_path) as state:
            yield state


def snapshot(path, existed=False):
    return {'item': str(path), 'stat': {'exists': existed, 'uid': 0, 'gid': 0, 'mode': '0640'}}


def restore(items, contents=(), **variables):
    return NativeRestoreRun('tasks.d/runtime_restore.yml', dict(
        bind_runtime_files_before={'results': items},
        bind_runtime_content_before={'results': list(contents)}, **variables)).run()


@pytest.mark.parametrize('asset', ASSETS)
@pytest.mark.parametrize('existed', [False, True])
def test_native_runtime_restore_removes_only_introduced_files_and_recovers_original_bytes(runtime, asset, existed):
    path = Path(asset)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('failed deployment bytes', encoding='utf-8')
    path.chmod(0o600)
    before = snapshot(path, existed)
    contents = [{'item': before, 'content': base64.b64encode(b'pre-deployment bytes\n').decode('ascii')}] if existed else []
    status, result = restore([before], contents, bind_dnssec_timer_enabled_before={'stdout': 'disabled'})
    assert status == 0 and not result.failed
    if existed:
        assert path.read_bytes() == b'pre-deployment bytes\n'
        assert path.stat().st_mode & 0o777 == 0o640
        assert path.stat().st_uid == path.stat().st_gid == 0
    else:
        assert not path.exists()
    assert {'daemon_reload': True} in json.loads(runtime.read_text())['operations']


@pytest.mark.parametrize('kind', ['directory', 'symlink', 'dangling-link', 'hardlink', 'owner', 'mode', 'size'])
def test_native_runtime_restore_checks_all_targets_before_removing_any_file(runtime, tmp_path, kind):
    safe, unsafe = Path(ASSETS[3]), Path(ASSETS[4])
    safe.write_text('safe introduced file must remain when any target is unsafe', encoding='utf-8')
    foreign = tmp_path / 'operator-evidence'
    foreign.write_text('unrelated state', encoding='utf-8')
    if kind == 'directory':
        unsafe.mkdir()
        (unsafe / 'operator-evidence').write_bytes(foreign.read_bytes())
    elif kind == 'symlink': unsafe.symlink_to(foreign)
    elif kind == 'dangling-link': unsafe.symlink_to(tmp_path / 'absent')
    elif kind == 'hardlink': os.link(str(foreign), str(unsafe))
    else:
        unsafe.write_text('replacement', encoding='utf-8')
        unsafe.chmod(0o644)
        if kind == 'owner': os.chown(str(unsafe), 65534, 65534)
        elif kind == 'mode': unsafe.chmod(0o666)
        else:
            with unsafe.open('r+b') as stream: stream.truncate(1048577)
    status, result = restore([snapshot(safe), snapshot(unsafe)])
    assert status == 2 and result.failed == ['Refuse unsafe replacement runtime files during rollback']
    assert safe.read_text().startswith('safe introduced file')
    assert foreign.read_text() == 'unrelated state'
    if kind == 'directory': assert (unsafe / 'operator-evidence').read_text() == 'unrelated state'
    elif kind in ('symlink', 'dangling-link'): assert unsafe.is_symlink()
    else: assert unsafe.is_file()
    assert json.loads(runtime.read_text())['operations'] == []


@pytest.mark.parametrize('kind', ['symlink', 'owner', 'mode'])
def test_native_runtime_restore_refuses_unsafe_parent_before_touching_contents(runtime, tmp_path, kind):
    parent = Path(ASSETS[0]).parent
    outside = tmp_path / 'foreign-directory'
    outside.mkdir()
    if kind == 'symlink': parent.symlink_to(outside)
    else:
        parent.mkdir()
        if kind == 'owner': os.chown(str(parent), 65534, 65534)
        else: parent.chmod(0o777)
    path = parent / 'bind9-runtime.conf'
    path.write_text('operator bytes', encoding='utf-8')
    status, result = restore([snapshot(path)])
    assert status == 2 and result.failed == ['Refuse unsafe runtime parent directories during rollback']
    assert path.read_text() == 'operator bytes'
    assert json.loads(runtime.read_text())['operations'] == []


@pytest.mark.parametrize('enabled', [False, True])
def test_native_runtime_restore_recovers_timer_enablement_without_starting_it(runtime, enabled):
    path = Path(ASSETS[-1])
    path.write_text('previous timer', encoding='utf-8')
    runtime.write_text(json.dumps({'units': {path.name: {'active': False, 'enabled': not enabled}}, 'operations': []}))
    status, result = restore([snapshot(path, True)], bind_dnssec_timer_enabled_before={
        'stdout': 'enabled\n' if enabled else 'disabled\n'})
    assert status == 0 and not result.failed
    assert json.loads(runtime.read_text())['units'][path.name] == {'active': False, 'enabled': enabled}


def test_native_runtime_restore_handles_empty_and_missing_snapshots_without_removing_assets(runtime):
    path = Path(ASSETS[3])
    path.write_text('outside empty snapshot', encoding='utf-8')
    for values in ({}, {'bind_runtime_files_before': {'results': []},
                        'bind_runtime_content_before': {'results': [{'skipped': True}]}}):
        status, result = NativeRestoreRun('tasks.d/runtime_restore.yml', values).run()
        assert status == 0 and not result.failed
        assert path.read_text() == 'outside empty snapshot'


def test_native_runtime_restore_propagates_daemon_reload_failure(runtime):
    runtime.write_text(json.dumps({'units': {}, 'operations': [], 'faults': {
        json.dumps(['daemon-reload']): {'rc': 1, 'stderr': 'synthetic reload refusal'}}}))
    status, result = restore([])
    assert status == 2 and result.failed == ['Reload the restored named runtime definitions']
    assert json.loads(runtime.read_text())['operations'] == []


@pytest.mark.parametrize('parent_kind', ['missing', 'regular'])
def test_native_runtime_restore_handles_missing_targets_and_refuses_non_directory_parents(runtime, parent_kind):
    path = Path(ASSETS[0])
    if parent_kind == 'regular': path.parent.write_text('foreign parent file', encoding='utf-8')
    status, result = restore([snapshot(path)])
    assert status == (0 if parent_kind == 'missing' else 2)
    assert not path.exists()
    if parent_kind == 'regular':
        # Native stat refuses ENOTDIR before parent inspection can run.
        assert result.failed == ['Inspect current runtime targets before rollback writes']
        assert path.parent.read_text() == 'foreign parent file'
    else: assert not result.failed


def test_native_runtime_restore_refuses_a_parent_replaced_after_target_observation(runtime, monkeypatch):
    from ansible.plugins.action import ActionBase
    path = Path(ASSETS[0])
    parent = path.parent
    parent.mkdir(mode=0o755)
    original = ActionBase.run
    def replace_parent(self, *args, **kwargs):
        # Model an operator race between the two real stat phases. Neither
        # module result is mocked; the native parent guard sees the new inode.
        if self._task.name == 'Inspect runtime parent directories before rollback writes' and parent.is_dir():
            parent.rmdir()
            parent.write_text('operator replacement after target observation', encoding='utf-8')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(ActionBase, 'run', replace_parent)
    status, result = restore([snapshot(path)])
    assert status == 2 and result.failed == ['Refuse unsafe runtime parent directories during rollback']
    assert result.registers['bind_runtime_restore_targets']['results'][0]['stat']['exists'] is False
    assert result.registers['bind_runtime_restore_parents']['results'][0]['stat']['isdir'] is False
    assert parent.read_text() == 'operator replacement after target observation'
    assert json.loads(runtime.read_text())['operations'] == []

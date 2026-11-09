"""Real metadata guards and bounded snapshot contents; service queries are a boundary double."""
import json
import os

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeSnapshotRun, NativeUnitBoundary

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')


@pytest.fixture
def runtime_tree(tmp_path):
    with NativeFilesystemScope(NativeSnapshotRun) as roots:
        with NativeUnitBoundary(tmp_path, {'named-private-dnssec.timer': {'active': True, 'enabled': True}}) as units:
            paths = [roots[0] / name for name in ('bind9-runtime.conf', 'bind9-dnssec.conf', 'bind9-z-dnssec.conf')]
            yield paths + list(roots[1:4]), units


@pytest.mark.parametrize('population', ['absent', 'mixed', 'all'])
@pytest.mark.parametrize('active,enabled', [(False, False), (True, False), (True, True)])
@pytest.mark.parametrize('check_mode', [False, True])
def test_native_snapshot_preserves_exact_existing_contents_and_timer_state(runtime_tree, population, active, enabled, check_mode):
    paths, state = runtime_tree
    unit = {'active': active, 'enabled': enabled}
    state.write_text(json.dumps({'units': {'named-private-dnssec.timer': unit}, 'operations': []}), encoding='utf-8')
    existing = paths if population == 'all' else paths[::2] if population == 'mixed' else []
    expected = {}
    for index, path in enumerate(existing):
        path.parent.mkdir(parents=True, exist_ok=True)
        data = ('synthetic runtime configuration %s\n' % index).encode('ascii')
        path.write_bytes(data)
        path.chmod(0o640)
        expected[str(path)] = data
    checks = ["bind_runtime_content_before.results | length == 6",
              "bind_runtime_content_before.results | selectattr('content', 'defined') | map(attribute='content') | map('b64decode') | list == lab_expected_contents",
              "bind_runtime_content_before.results | selectattr('content', 'defined') | map(attribute='item.item') | list == lab_expected_paths"]
    status, result = NativeSnapshotRun('tasks.d/runtime_snapshot.yml',
        {'lab_expected_contents': [expected[str(path)].decode('ascii') for path in existing],
         'lab_expected_paths': [str(path) for path in existing]}, check_mode=check_mode, checks=checks).run()
    assert status == 0 and result.failed == []
    assert result.facts['bind_runtime_snapshot_ready'] is True
    assert 'Verify native integration postconditions' in result.ok
    assert all(path.read_bytes() == expected[str(path)] for path in existing)
    if paths[-1] in existing:
        assert 'Record the private DNSSEC timer activity before deployment' not in result.skipped
        assert 'Record the private DNSSEC timer enablement before deployment' not in result.skipped
        assert result.registers['bind_dnssec_timer_active']['rc'] == (0 if active else 3)
        assert result.registers['bind_dnssec_timer_enabled_before']['stdout'] == ('enabled' if enabled else 'disabled')
    else:
        assert 'Record the private DNSSEC timer activity before deployment' in result.skipped
    assert json.loads(state.read_text(encoding='utf-8'))['operations'] == []


@pytest.mark.parametrize('kind', ['symlink', 'directory', 'hardlink', 'owner', 'writable', 'oversize'])
def test_unsafe_runtime_inode_is_refused_before_any_content_read(runtime_tree, tmp_path, kind):
    paths, state = runtime_tree
    path = paths[0]
    path.parent.mkdir(parents=True)
    outside = tmp_path / 'foreign'
    outside.write_text('must remain private', encoding='utf-8')
    if kind == 'symlink': path.symlink_to(outside)
    elif kind == 'directory': path.mkdir()
    else:
        path.write_bytes(b'x' * (1048577 if kind == 'oversize' else 16))
        path.chmod(0o600)
        if kind == 'hardlink': os.link(str(path), str(tmp_path / 'alias'))
        elif kind == 'owner': os.chown(str(path), 12345, 12345)
        elif kind == 'writable': path.chmod(0o620)
        os.utime(str(path), ns=(1, 2))
    initial = state.read_bytes()
    status, result = NativeSnapshotRun('tasks.d/runtime_snapshot.yml', {}).run()
    assert status == 2 and result.failed == ['Refuse unsafe managed runtime paths before making changes']
    assert result.facts['bind_runtime_snapshot_ready'] is False
    assert 'bind_runtime_content_before' not in result.registers
    assert state.read_bytes() == initial
    if kind not in ('symlink', 'directory'): assert path.stat().st_atime_ns == 1
    assert outside.read_text(encoding='utf-8') == 'must remain private'


@pytest.mark.parametrize('operation,rc,stdout,failure', [
    ('is-active', 2, 'active\n', 'Record the private DNSSEC timer activity before deployment'),
    ('is-enabled', 2, 'enabled\n', 'Record the private DNSSEC timer enablement before deployment'),
    ('is-enabled', 1, 'masked\n', 'Require a recognizable managed timer state')])
@pytest.mark.parametrize('check_mode', [False, True])
def test_failed_or_unrecognized_timer_queries_never_complete_a_snapshot(runtime_tree, operation, rc, stdout, failure, check_mode):
    paths, state = runtime_tree
    timer = paths[-1]
    timer.parent.mkdir(parents=True, exist_ok=True)
    timer.write_text('synthetic timer configuration', encoding='utf-8')
    timer.chmod(0o600)
    data = json.loads(state.read_text(encoding='utf-8'))
    data['faults'] = {json.dumps([operation, 'named-private-dnssec.timer']): {'rc': rc, 'stdout': stdout}}
    state.write_text(json.dumps(data), encoding='utf-8')
    initial = state.read_bytes()
    status, result = NativeSnapshotRun('tasks.d/runtime_snapshot.yml', {}, check_mode=check_mode).run()
    assert status == 2 and result.failed == [failure]
    assert result.facts['bind_runtime_snapshot_ready'] is False
    assert state.read_bytes() == initial
    assert timer.read_text(encoding='utf-8') == 'synthetic timer configuration'

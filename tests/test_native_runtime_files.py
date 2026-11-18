"""Canonical runtime provisioning with real files, templates and handlers."""
import json
import os
from pathlib import Path
import shlex

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeProvisionRun, NativeUnitBoundary

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')
ROLE = Path(__file__).resolve().parents[1] / 'roles/bind9'
DROPINS = Path('/etc/systemd/system/named.service.d')
DNSSEC = [DROPINS / 'bind9-z-dnssec.conf', Path('/usr/local/sbin/bind-private-dnssec'),
          Path('/etc/systemd/system/named-private-dnssec.service'),
          Path('/etc/systemd/system/named-private-dnssec.timer')]


@pytest.fixture
def runtime(tmp_path):
    with NativeFilesystemScope(NativeProvisionRun):
        with NativeUnitBoundary(tmp_path, {'named': {'active': True, 'enabled': True}}) as state:
            yield state


@pytest.mark.parametrize('legacy', [False, True])
def test_native_rndc_setup_preserves_key_contents_and_retires_only_the_legacy_dropin(runtime, legacy):
    key = Path('/etc/rndc.key')
    key.write_text('synthetic private key fixture', encoding='utf-8')
    key.chmod(0o600)
    os.utime(str(key), ns=(1, 2))
    if legacy:
        DROPINS.mkdir(parents=True)
        (DROPINS / 'bind9-dnssec.conf').write_text('legacy combined override', encoding='utf-8')
    status, result = NativeProvisionRun('tasks.d/named_runtime.yml', {}).run()
    assert status == 0 and result.failed == []
    assert key.stat().st_atime_ns == 1
    assert key.read_text(encoding='utf-8') == 'synthetic private key fixture'
    output = DROPINS / 'bind9-runtime.conf'
    assert output.read_text(encoding='utf-8') == (ROLE / 'templates/named-runtime-override.j2').read_text(encoding='utf-8')
    assert output.stat().st_mode & 0o777 == 0o644
    assert not (DROPINS / 'bind9-dnssec.conf').exists()
    operations = json.loads(runtime.read_text(encoding='utf-8'))['operations']
    assert {'name': 'named', 'state': 'restarted'} in operations
    assert 'Reload named configuration' in result.ok
    previous = output.stat().st_mtime_ns
    status, result = NativeProvisionRun('tasks.d/named_runtime.yml', {}).run()
    assert status == 0 and not result.failed and output.stat().st_mtime_ns == previous
    assert 'Reload named configuration' not in result.ok


@pytest.mark.parametrize('kind', ['missing', 'directory', 'symlink'])
def test_native_rndc_guard_stops_before_installing_runtime_files(runtime, tmp_path, kind):
    key = Path('/etc/rndc.key')
    foreign = tmp_path / 'foreign'
    foreign.write_text('unrelated evidence', encoding='utf-8')
    if kind == 'directory': key.mkdir()
    elif kind == 'symlink': key.symlink_to(foreign)
    status, result = NativeProvisionRun('tasks.d/named_runtime.yml', {}).run()
    assert status == 2 and result.failed == ['Require the native regular administrative RNDC key']
    assert not DROPINS.exists()
    assert foreign.read_text(encoding='utf-8') == 'unrelated evidence'
    if kind == 'missing': assert not key.exists()
    elif kind == 'symlink': assert key.is_symlink()
    else: assert key.is_dir()


@pytest.mark.parametrize('mode', ['auto', True, False])
@pytest.mark.parametrize('population,active', [('absent', False), ('all', False), ('all', True),
                                             ('timer', False), ('timer', True)])
def test_dnssec_file_transitions_keep_actual_handler_and_retirement_order(runtime, mode, population, active):
    existing = DNSSEC if population == 'all' else DNSSEC[-1:] if population == 'timer' else []
    for path in existing:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('synthetic predecessor asset', encoding='utf-8')
        path.chmod(0o644)
    data = json.loads(runtime.read_text(encoding='utf-8'))
    for path in existing:
        if path.suffix in ('.timer', '.service'):
            data['units'][path.name] = {'active': active, 'enabled': active and path.suffix == '.timer'}
    runtime.write_text(json.dumps(data), encoding='utf-8')
    if mode in ('auto', True):
        DROPINS.mkdir(parents=True, exist_ok=True)
    status, result = NativeProvisionRun('tasks.d/dnssec_prepare.yml', {'bind_dnssec_active': mode}).run()
    assert status == 0 and result.failed == []
    assert all(path.exists() for path in DNSSEC) if mode in ('auto', True) else not any(path.exists() for path in DNSSEC)
    if mode in ('auto', True):
        assert DNSSEC[1].read_bytes() == (ROLE / 'files/bind-private-dnssec.py').read_bytes()
        assert DNSSEC[1].stat().st_mode & 0o777 == 0o750
        assert 'ExecStartPost=/usr/bin/python3 /usr/local/sbin/bind-private-dnssec' in DNSSEC[0].read_text(encoding='utf-8')
        assert 'OnUnitActiveSec=1d' in DNSSEC[3].read_text(encoding='utf-8')
    final = json.loads(runtime.read_text(encoding='utf-8'))
    operations = final['operations']
    if mode is False and population != 'absent':
        assert final['units']['named-private-dnssec.timer'] == {'active': False, 'enabled': False}
        # Existing files do not imply running units. The genuine module sends
        # stop/disable only when needed; retain both initially inactive/active.
        assert ({'name': 'named-private-dnssec.timer', 'state': 'stopped'} in operations) is active
        assert ({'name': 'named-private-dnssec.timer', 'enabled': False} in operations) is active
    assert ('Reload named configuration' in result.ok) is (mode in ('auto', True) or population != 'absent')


@pytest.mark.parametrize('zones', [[], ['example.test'], ['example.test', '113.0.203.in-addr.arpa']])
def test_native_monitor_installation_preserves_source_and_renders_only_configured_zones(runtime, zones):
    variables = {'bind_back_ip': '192.0.2.11', 'bind_managed_zones': [{'domain': name} for name in zones]}
    status, result = NativeProvisionRun('tasks.d/deploy/monitoring.yml', variables).run()
    assert status == 0 and result.failed == []
    executable = Path('/usr/local/sbin/named-healthcheck')
    assert executable.read_bytes() == (ROLE / 'files/named-healthcheck.sh').read_bytes()
    assert executable.stat().st_mode & 0o777 == 0o750
    unit = Path('/etc/systemd/system/named-healthcheck.service').read_text(encoding='utf-8')
    command, = [line.split('=', 1)[1] for line in unit.splitlines() if line.startswith('ExecStart=')]
    assert shlex.split(command) == ['/usr/local/sbin/named-healthcheck', '192.0.2.11'] + zones
    assert 'User=root' in unit.splitlines()
    assert 'ProtectSystem=strict' in unit and 'ProtectHome=yes' in unit
    state = json.loads(runtime.read_text(encoding='utf-8'))
    assert state['units']['named-healthcheck.timer'] == {'active': True, 'enabled': True}


@pytest.mark.parametrize('field', [None, 'bind_named_runtime_override', 'bind_named_override',
                                 'bind_legacy_named_override', 'bind_dnssec_removed'])
def test_native_handler_restarts_only_for_changed_unit_definitions(runtime, field):
    # Ordinary zone/configuration notifications require reload. Only the four
    # documented unit-definition results require restart; none is fabricated
    # as a result of an unexecuted production action in the coverage receipt.
    variables = {'bind_back_ip': '192.0.2.11', 'bind_managed_zones': []}
    if field is not None:
        variables[field] = {'changed': True}
    status, result = NativeProvisionRun('tasks.d/deploy/monitoring.yml', variables, notify_handler=True).run()
    assert status == 0 and result.failed == []
    assert 'Reload named configuration' in result.ok
    operations = json.loads(runtime.read_text(encoding='utf-8'))['operations']
    operation = 'reloaded' if field is None else 'restarted'
    assert {'name': 'named', 'state': operation} in operations
    assert sum(item.get('name') == 'named' for item in operations) == 1

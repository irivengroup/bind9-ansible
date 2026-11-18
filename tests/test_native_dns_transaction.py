"""Integrated native YAML transaction with real file/archive restoration.

Private replies bound BIND/DNS/SELinux commands; the systemd CLI uses the shared
unit model. Real daemon, wire DNS and network qualification remain separate.
"""
import json
import os
from pathlib import Path

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeMigrationRun, NativeRecordedBoundary, NativeUnitBoundary
from native_transaction_runner import NativeTransactionRun
from test_native_directories import named_identity
from test_native_health import replies as health_replies
from test_native_front_routes import BASE, NEW, NativeRouteLab
from test_native_preflight import scope
from test_native_security import commands as security_replies

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
    reason='Requires an explicitly enabled disconnected clean Linux root')
ORIGINAL = b'// original native transaction fixture\noptions { directory "/var/named"; };\n'


def variables(dnssec):
    hosts, groups = scope()
    for name in ('bind_authoritative_zones', 'bind_managed_zones'):
        hosts['localhost'][name][0]['serial'] = 7
    # Keep host identities below global extra-vars so the peer remains distinct.
    values = dict(bind_dnssec_active=dnssec, bind_generated_reverse_zones=[],
        bind_front_routes_enabled=False, bind_front_routes_defer_commit=False,
        bind_backup_root=NativeMigrationRun.ROOT, bind_primary_host='localhost')
    return values, hosts, groups


def replies(failure=None, back='192.0.2.2'):
    data = security_replies()
    for key, value in health_replies()[0].items():
        # Rebase only exact address arguments when composing the existing
        # network fixture; DNS answers and validation predicates stay intact.
        argv = [back if item == '192.0.2.2' else '@' + back if item == '@192.0.2.2' else item
                for item in json.loads(key)]
        data[json.dumps(argv)] = value
    # The archive CLI restores only existing DNS paths, excluding log paths.
    data[json.dumps(['restorecon', '-RF', '/etc/named.conf', '/var/named'])] = {'rc': 0}
    data[json.dumps(['named-checkconf', '-z', '/etc/named.conf'])] = (
        {'sequence': [{'rc': 17, 'stderr': 'Injected native validator refusal\n'}, {'rc': 0}]}
        if failure == 'validator' else {'rc': 0})
    data[json.dumps(['named-checkconf', '-p', '/etc/named.conf'])] = {
        'rc': 0, 'stdout': 'allow-transfer { key "site-transfer"; };\n'}
    if failure == 'health':
        key = next(key for key in data if '"dig"' in key and '"localhost"' in key and '"+notcp"' in key)
        data[key] = {'rc': 17, 'stderr': 'Injected health refusal\n'}
    return data


@pytest.fixture
def transaction(tmp_path):
    with NativeFilesystemScope(NativeTransactionRun):
        named_identity()
        config = Path('/etc/named.conf')
        config.write_bytes(ORIGINAL)
        config.chmod(0o640)
        key = Path('/etc/rndc.key')
        key.write_text('key "rndc-key" { algorithm hmac-sha256; secret "cHVibGljLXN5bnRoZXRpYy1maXh0dXJlLTAwMDAwMDA="; };\n', encoding='ascii')
        key.chmod(0o600)
        commands = tmp_path / 'commands'
        commands.mkdir(mode=0o700)
        with NativeUnitBoundary(tmp_path, {'named': {'active': True, 'enabled': True}}) as units:
            yield config, units, commands


def check_dns_result(config, units, status, result, failure, retained=False):
    """Share the actual DNS restoration guarantees across composed scenarios."""
    assert status == (2 if failure else 0), (status, result.failed)
    archive = Path(result.facts['bind_recovery_archive'])
    assert archive.is_file() and Path(str(archive) + '.sha256').is_file()
    assert result.facts['bind_runtime_snapshot_ready'] is True
    unit_state = json.loads(units.read_text())
    operations = unit_state['operations']
    if failure:
        rollback = result.registers.get('bind_rollback', {})
        assert result.failed[-1] == 'Stop deployment after rollback', (
            '\n'.join(result.failed) + '\n' + rollback.get('stderr', ''))
        assert config.read_bytes() == ORIGINAL
        assert result.facts['bind_deployment_retain_lease'] is retained
        assert result.facts['bind_runtime_restore_incomplete'] is False
        assert any(item.get('name') == 'named' and item.get('state') == 'stopped' for item in operations)
        assert 'Restore pre-deployment BIND service state' in result.ok
        # Native systemd starts a stopped unit when asked for restarted;
        # the task contract and final state are the relevant guarantees.
        stopped = max(index for index, item in enumerate(operations)
            if item.get('name') == 'named' and item.get('state') == 'stopped')
        assert any(item.get('name') == 'named' and item.get('state') in ('started', 'restarted')
            for item in operations[stopped + 1:])
        assert unit_state['units']['named']['active'] is True
        assert 'Restore the pre-deployment BIND backup' in result.ok
    else:
        assert not result.failed and config.read_bytes() != ORIGINAL
        assert result.facts['bind_deployment_health_completed'] is True
        assert 'Restore the pre-deployment BIND backup' not in result.ok


@pytest.mark.parametrize('dnssec', [False, 'auto', True])
@pytest.mark.parametrize('failure', [None, 'validator', 'health'])
def test_native_protected_dns_transaction_commits_or_restores_exact_prior_configuration(transaction, dnssec, failure):
    config, units, commands = transaction
    values, hosts, groups = variables(dnssec)
    with NativeRecordedBoundary(commands, NativeTransactionRun, replies(failure)) as calls:
        status, result = NativeTransactionRun('tasks.d/deploy/main.yml', values,
            inventory_hosts=hosts, inventory_groups=groups).run()
        check_dns_result(config, units, status, result, failure)
        assert json.loads(calls.read_text())['calls']


@pytest.mark.skipif(os.environ.get('BIND9_CANONICAL_BASH_LAB') != '1',
                   reason='Requires the explicit canonical FRONT command boundaries')
@pytest.mark.parametrize('failure,rollback_failure', [(None, False), ('validator', False),
    ('health', False), ('validator', True)])
def test_native_dns_transaction_commits_front_only_after_health_and_keeps_failed_recovery(
        transaction, tmp_path, monkeypatch, failure, rollback_failure):
    config, units, commands = transaction
    folder = tmp_path / 'network'
    folder.mkdir(mode=0o700)
    route = NativeRouteLab(folder)
    route.data['addresses'][2]['addr_info'][0]['local'] = '127.0.0.1'
    if rollback_failure:
        route.data['fail_persistent_remove_once'] = True
    route.data_file.write_text(json.dumps(route.data), encoding='utf-8')
    monkeypatch.setenv('BIND_NETWORK_DOUBLE_STATE', str(route.data_file))
    values, hosts, groups = variables(False)
    values.update(bind_front_routes_enabled=True, bind_front_routes_defer_commit=True,
                  bind_cloud_clients=['203.0.113.0/24'])
    hosts['localhost']['bind_back_ip'] = '10.0.0.2'
    hosts['peer']['bind_back_ip'] = '10.0.0.3'
    with route.canonical.rebased(route.state, folder / 'bin', route.env):
        with NativeRecordedBoundary(commands, NativeTransactionRun, replies(failure, back='10.0.0.2')):
            status, result = NativeTransactionRun('tasks.d/deploy/main.yml', values,
                inventory_hosts=hosts, inventory_groups=groups).run()
    route.data = json.loads(route.data_file.read_text(encoding='utf-8'))
    check_dns_result(config, units, status, result, failure, retained=rollback_failure)
    assert result.facts['bind_front_route_transaction_pending'] is rollback_failure
    assert (route.state / 'front-routes.pending').exists() is rollback_failure
    if failure:
        assert route.data['runtime'] == BASE
        assert len(route.data['persistent']) == int(rollback_failure)
        assert result.facts['bind_front_action'] == 'rollback'
        assert result.ok.count('Run the single native FRONT transaction with bounded observations') == (1 if rollback_failure else 2)
        assert ('Record an incomplete FRONT rollback without masking it' in result.ok) is rollback_failure
    else:
        assert route.data['runtime'] == BASE + [NEW]
        assert len(route.data['persistent']) == 1
        # Static imports have no action callback of their own. Assert the
        # actual apply/verify/commit executions and their source facts instead.
        assert result.facts['bind_front_action'] == 'commit'
        assert result.ok.count('Run the single native FRONT transaction with bounded observations') == 3
        assert result.ok.index('Record that all selected deployment health checks completed') < result.ok.index(
            'Clear pending FRONT ownership only after successful completion')

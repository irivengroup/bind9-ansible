"""Canonical FRONT YAML over genuine SSH; only ip/nmcli use exact CLI fixtures."""
import hashlib
import json
import os
from pathlib import Path
import re

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeRecordedBoundary
from native_ssh_runner import LoopbackSshEnvironment, NativeFrontRun, NativePeerCommandRun, NativeSshFrontRun
from test_native_front_routes import NativeRouteLab

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_SSH_LAB') != '1',
                               reason='Requires the explicitly enabled disconnected SSH QA image')


class FrontWorkflow:
    """Share the native transaction fixture and assert every SSH-owned result."""

    def __init__(self, route, ssh):
        self.route, self.ssh = route, ssh
        self.hosts = {'localhost': {'bind_back_ip': '10.0.0.2'}, 'peer': {'bind_back_ip': '10.0.0.3'}}
        self.values = dict(bind_front_ip='198.51.100.2', bind_back_ip='10.0.0.2',
            bind_primary_host='localhost', bind_secondary_host='peer', bind_front_route_metric=100,
            bind_cloud_clients=['203.0.113.0/24'])

    def run(self, action=None, check=False, local=False, **variables):
        self.route.data_file.write_text(json.dumps(self.route.data))
        values = dict(self.values, **variables)
        if action is not None:
            values['bind_front_requested_action'] = action
        with self.route.canonical.rebased(self.route.state, self.route.folder / 'bin', self.route.env):
            options = dict(inventory_hosts=self.hosts, check_mode=check)
            runner = NativeFrontRun if local else NativeSshFrontRun
            if not local:
                options['ssh'] = self.ssh
            result = runner('tasks.d/deploy/front_return_routes.yml', values, **options).run()
        self.route.data = json.loads(self.route.data_file.read_text())
        return result

    @property
    def pending(self):
        return self.route.state / 'front-routes.pending'


@pytest.fixture
def front(tmp_path, monkeypatch):
    with NativeFilesystemScope(NativeSshFrontRun):
        route = NativeRouteLab(tmp_path)
        route.data['addresses'][2]['addr_info'][0]['local'] = '127.0.0.1'
        route.data_file.write_text(json.dumps(route.data))
        monkeypatch.setenv('BIND_NETWORK_DOUBLE_STATE', str(route.data_file))
        with LoopbackSshEnvironment(tmp_path / 'ssh', route.data_file) as ssh:
            yield FrontWorkflow(route, ssh)


def test_native_front_plan_reads_the_actual_ssh_peer_and_preserves_both_route_stores(front):
    before = json.loads(json.dumps(front.route.data))
    status, result = front.run('plan')
    assert status == 0 and not result.failed
    assert 'Read the real SSH peer before any route mutation' in result.ok
    peer = result.registers['bind_front_ssh_session']
    tokens = peer['stdout'].split()
    assert peer['rc'] == 0 and len(tokens) == 4 and tokens[0] == tokens[2] == '127.0.0.1'
    assert tokens[1].isdigit() and tokens[3] == str(front.ssh.port)
    operation = result.registers['bind_front_operation']
    assert operation['rc'] == 2 and operation['changed'] is True
    assert 'FRONT device=front0' in operation['stdout'] and 'gateway=198.51.100.1' in operation['stdout']
    assert front.route.data['runtime'] == before['runtime'] and front.route.data['persistent'] == []
    assert front.route.data['operations'] == []
    assert hashlib.sha256(front.route.script.read_bytes()).digest() == hashlib.sha256(
        (Path(__file__).resolve().parents[1] / 'roles/bind9/files/bind-front-routes.sh').read_bytes()).digest()


@pytest.mark.parametrize('clients', [['203.0.113.7'], ['203.0.113.0/24'],
    ['203.0.113.7', '203.0.113.7/32', '203.0.113.0/24', '203.0.113.0/24']])
def test_native_front_adds_ip_or_subnet_once_without_duplicate_routes(front, clients):
    for iteration in range(2):
        status, result = front.run(bind_cloud_clients=clients)
        assert status == 0 and not result.failed
        operation = result.registers['bind_front_operation']
        assert operation['rc'] == (2 if iteration == 0 else 0)
        assert operation['changed'] is (iteration == 0)
        expected = '203.0.113.7/32' if clients == ['203.0.113.7'] else '203.0.113.0/24'
        assert front.route.data['persistent'] == [{'network': expected, 'gateway': '198.51.100.1', 'metric': 100}]
        assert len([row for row in front.route.data['runtime'] if row['dst'] == expected]) == 1
        assert not front.pending.exists()
    assert len(front.route.data['operations']) == 2


@pytest.mark.parametrize('completion', ['commit', 'rollback', 'recover'])
def test_native_front_deferred_transactions_verify_then_commit_or_restore_exact_routes(front, completion):
    before = json.loads(json.dumps(front.route.data))
    status, result = front.run('apply', bind_front_routes_defer_commit=True)
    assert status == 0 and not result.failed
    assert result.facts['bind_front_route_transaction_pending'] is True
    assert result.facts['bind_front_route_rollback_attempted'] is False
    token = result.facts['bind_front_transaction_token']
    assert re.fullmatch('[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}', token)
    assert front.pending.is_dir()
    status, result = front.run('verify', bind_front_transaction_token=token)
    assert status == 0 and not result.failed and result.registers['bind_front_operation']['rc'] == 0
    assert 'Read the real SSH peer before any route mutation' in result.skipped
    status, result = front.run(completion, bind_front_transaction_token=token)
    assert status == 0 and not result.failed and not front.pending.exists()
    assert result.facts['bind_front_route_transaction_pending'] is False
    assert result.facts['bind_front_route_rollback_attempted'] is (completion != 'commit')
    if completion == 'commit':
        assert len(front.route.data['persistent']) == 1 and len(front.route.data['runtime']) == len(before['runtime']) + 1
    else:
        assert front.route.data['persistent'] == before['persistent'] and front.route.data['runtime'] == before['runtime']


def test_native_front_check_mode_selects_plan_and_keeps_the_real_route_stores_unchanged(front):
    before = json.loads(json.dumps(front.route.data))
    status, result = front.run(check=True)
    assert status == 0 and not result.failed and result.facts['bind_front_action'] == 'plan'
    assert front.route.data['persistent'] == before['persistent'] and front.route.data['runtime'] == before['runtime']
    assert front.route.data['operations'] == [] and not front.pending.exists()


@pytest.mark.parametrize('action,check,metric', [('unrecognized', False, 100), ('apply', True, 100), ('plan', False, True)])
def test_native_front_invalid_action_envelopes_stop_before_ssh_or_route_commands(front, action, check, metric):
    status, result = front.run(action, check=check, bind_front_route_metric=metric)
    assert status == 2 and result.failed == ['Validate the native FRONT action envelope']
    assert 'bind_front_ssh_session' not in result.registers and 'bind_front_operation' not in result.registers
    assert front.route.data['operations'] == []


def test_native_front_missing_interface_gateway_refuses_mutation_without_default_gateway_fallback(front):
    front.route.data['runtime'] = [row for row in front.route.data['runtime']
        if not (row.get('dev') == 'front0' and 'gateway' in row)]
    before = json.loads(json.dumps(front.route.data))
    status, result = front.run('apply')
    assert status == 2 and result.failed == ['Run the single native FRONT transaction with bounded observations']
    assert front.route.data['runtime'] == before['runtime'] and front.route.data['persistent'] == []
    assert front.route.data['operations'] == [] and not front.pending.exists()


def test_native_front_refuses_recovery_without_an_owned_journal_before_route_mutation(front):
    status, result = front.run('recover')
    # Recovery requires an owned journal by the canonical helper's explicit
    # contract. A missing journal is a refusal, never an invented successful job.
    assert status == 2 and result.failed == ['Run the single native FRONT transaction with bounded observations']
    assert result.registers['bind_front_operation']['rc'] == 1
    assert 'No pending FRONT transaction to recover' in result.registers['bind_front_operation']['stdout']
    assert 'bind_front_route_transaction_pending' not in result.facts
    assert front.route.data['operations'] == []


def test_native_front_empty_client_list_is_an_unchanged_plan(front):
    status, result = front.run('plan', bind_cloud_clients=[])
    assert status == 0 and not result.failed
    assert result.registers['bind_front_operation']['rc'] == 0
    assert result.registers['bind_front_operation']['changed'] is False
    assert front.route.data['operations'] == []


@pytest.mark.parametrize('check', [False, True])
def test_native_front_local_plan_does_not_invent_an_ssh_peer(front, check):
    status, result = front.run('plan', check=check, local=True)
    assert status == 0 and not result.failed
    assert 'Read the real SSH peer before any route mutation' in result.skipped
    assert 'bind_front_ssh_session' not in result.registers
    assert '--ssh-peer' not in result.facts['bind_front_arguments']
    assert result.registers['bind_front_operation']['rc'] == 2
    assert front.route.data['operations'] == []


@pytest.mark.parametrize('rc,stdout', [(1, ''), (0, 'malformed session')])
def test_native_front_refuses_a_failed_or_malformed_ssh_session_read_before_routes(front, tmp_path, rc, stdout):
    folder = tmp_path / 'peer-command'
    folder.mkdir(mode=0o700)
    argv = ['printenv', 'SSH_CONNECTION']
    # SSH remains real; only this explicitly owned command models a failed
    # environment read. Successful scenarios above always use native printenv.
    with NativeRecordedBoundary(folder, NativePeerCommandRun,
            {json.dumps(argv): {'rc': rc, 'stdout': stdout}}) as state:
        status, result = front.run('apply')
        calls = json.loads(state.read_text())['calls']
    assert status == 2 and result.failed == ['Read the real SSH peer before any route mutation']
    assert result.registers['bind_front_ssh_session']['rc'] == rc
    assert result.registers['bind_front_ssh_session']['stdout'] == stdout
    assert calls == [argv] and 'bind_front_operation' not in result.registers
    assert front.route.data['operations'] == [] and not front.pending.exists()


def test_native_front_shell_quoting_keeps_an_invalid_client_as_one_argument(front, tmp_path):
    marker = tmp_path / 'must-not-execute'
    status, result = front.run('apply', bind_cloud_clients=['203.0.113.7; touch ' + str(marker)])
    assert status == 2 and result.failed == ['Run the single native FRONT transaction with bounded observations']
    assert not marker.exists() and front.route.data['operations'] == []


def test_native_front_verification_requires_the_exact_transaction_token(front):
    status, result = front.run('verify')
    assert status == 2 and result.failed == ['Run the single native FRONT transaction with bounded observations']
    assert result.registers['bind_front_operation']['rc'] == 1
    assert 'Read the real SSH peer before any route mutation' in result.skipped
    assert front.route.data['operations'] == []


@pytest.mark.parametrize('fault', ['missing', 'duplicate', 'malformed'])
def test_native_front_untrusted_receipt_keeps_pending_ownership_and_refuses_followup(front, tmp_path, monkeypatch, fault):
    from ansible.plugins.loader import action_loader, init_plugin_loader
    init_plugin_loader()
    action = action_loader.get('ansible.builtin.script', class_only=True)
    original = action.run
    marker = tmp_path / 'receipt-corruption-observed'
    def corrupt(self, *args, **kwargs):
        # Fault injection follows a real, completed SSH script action. Only its
        # returned receipt is corrupted; native task predicates execute normally.
        result = original(self, *args, **kwargs)
        if self._task.name == 'Run the single native FRONT transaction with bounded observations':
            assert result['rc'] == 2
            receipt, = re.findall(r'(?m)^transaction: [0-9a-f-]{36}\r?$', result['stdout'])
            marker.write_text(fault, encoding='ascii')
            if fault == 'missing':
                result['stdout'] = result['stdout'].replace(receipt, '')
            elif fault == 'duplicate':
                result['stdout'] += '\n' + receipt + '\n'
            else:
                result['stdout'] = result['stdout'].replace(receipt, 'transaction: malformed')
            result['stdout_lines'] = result['stdout'].splitlines()
        return result
    monkeypatch.setattr(action, 'run', corrupt)
    status, result = front.run('apply', bind_front_routes_defer_commit=True)
    assert marker.read_text(encoding='ascii') == fault
    assert status == 2 and result.failed == ['Require an unambiguous receipt before any deferred route operation']
    assert result.facts['bind_front_route_transaction_pending'] is True
    assert result.facts['bind_front_transaction_token'] == ''
    assert result.facts['bind_front_route_rollback_attempted'] is False
    assert front.pending.is_dir() and len(front.route.data['persistent']) == 1
    assert len(front.route.data['operations']) == 2


@pytest.mark.parametrize('flag', ['BIND9_NATIVE_STATE_LAB', 'BIND9_NATIVE_SSH_LAB'])
def test_native_ssh_fixture_refuses_without_each_explicit_scope_flag(tmp_path, monkeypatch, flag):
    monkeypatch.delenv(flag, raising=False)
    with pytest.raises(ValueError, match='disconnected disposable root'):
        LoopbackSshEnvironment(tmp_path / 'must-not-create', tmp_path / 'unread-state')
    assert not (tmp_path / 'must-not-create').exists()


def test_native_front_ssh_adapter_refuses_execution_without_its_exact_live_listener():
    runner = NativeSshFrontRun('tasks.d/deploy/front_return_routes.yml', {})
    with pytest.raises(ValueError, match='exact live private listener'):
        runner.run()
    with pytest.raises(ValueError, match='exact live private listener'):
        NativeSshFrontRun('tasks.d/deploy/front_return_routes.yml', {}, ssh=object())

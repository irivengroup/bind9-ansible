"""Canonical service tasks through native modules; only systemctl is doubled."""
import json
import os

import pytest

from native_ansible_runner import NativeServiceRun, NativeUnitBoundary

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')


@pytest.fixture
def unit_cli(tmp_path):
    with NativeUnitBoundary(tmp_path) as state:
        yield state


@pytest.mark.parametrize('active,enabled', [(False, False), (True, False), (True, True)])
def test_native_systemd_module_applies_service_and_timer_state_without_task_rewriting(unit_cli, active, enabled):
    units = {'named-private-dnssec.service': {'active': active, 'enabled': False},
             'named-private-dnssec.timer': {'active': active, 'enabled': enabled}}
    unit_cli.write_text(json.dumps({'units': units, 'operations': []}), encoding='utf-8')
    status, result = NativeServiceRun('tasks.d/dnssec_activate.yml', {}).run()
    assert status == 0 and result.failed == [] and len(result.ok) == 2
    final = json.loads(unit_cli.read_text(encoding='utf-8'))
    assert final['units']['named-private-dnssec.service'] == {'active': True, 'enabled': False}
    assert final['units']['named-private-dnssec.timer'] == {'active': True, 'enabled': True}
    assert len(final['operations']) == 1 + 2 * (not active) + (not enabled)


def test_native_module_surfaces_cli_refusal_before_timer_activation(unit_cli):
    state = {'units': {}, 'operations': [], 'faults': {json.dumps(['start', 'named-private-dnssec.service']):
             {'rc': 1, 'stderr': 'synthetic native CLI refusal'}}}
    unit_cli.write_text(json.dumps(state), encoding='utf-8')
    status, result = NativeServiceRun('tasks.d/dnssec_activate.yml', {}).run()
    assert status == 2 and result.failed == ['Reconcile private DNSSEC suffix exceptions before DNS health checks']
    final = json.loads(unit_cli.read_text(encoding='utf-8'))
    assert final['operations'] == [{'daemon_reload': True}]
    assert not final['units']


@pytest.mark.parametrize('active,enabled', [(False, False), (True, False), (True, True)])
def test_named_start_and_native_readiness_observe_final_service_state(unit_cli, active, enabled):
    unit_cli.write_text(json.dumps({'units': {'named': {'active': active, 'enabled': enabled}}, 'operations': []}),
                        encoding='utf-8')
    status, result = NativeServiceRun('tasks.d/deploy/service.yml', {}).run()
    assert status == 0 and result.failed == []
    assert 'Check named is active' in result.ok
    assert result.registers['bind_service_active']['rc'] == 0
    assert result.registers['bind_service_active']['stdout'] == 'active'
    final = json.loads(unit_cli.read_text(encoding='utf-8'))
    assert final['units']['named'] == {'active': True, 'enabled': True}
    assert len(final['operations']) == 1 + (not active) + (not enabled)


@pytest.mark.parametrize('rc,stdout', [(1, 'active\n'), (0, 'inactive\n'), (3, 'inactive\n')])
def test_failed_or_negative_native_readiness_does_not_report_service_success(unit_cli, rc, stdout):
    data = {'units': {}, 'operations': [], 'faults': {
        json.dumps(['is-active', 'named']): {'rc': rc, 'stdout': stdout}}}
    unit_cli.write_text(json.dumps(data), encoding='utf-8')
    status, result = NativeServiceRun('tasks.d/deploy/service.yml', {}).run()
    assert status == 2 and result.failed == ['Check named is active']
    assert 'Check named is active' not in result.ok

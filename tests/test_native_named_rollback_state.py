"""Deployment failures must restore both BIND activity and boot enablement."""
import json
from pathlib import Path

import pytest

from native_ansible_runner import NativeRecordedBoundary, NativeSnapshotRun
from native_transaction_runner import NativeTransactionRun
from test_native_dns_transaction import ORIGINAL, replies, variables, transaction as transaction_fixture
from test_native_dns_transaction import pytestmark as native_marks

pytestmark = native_marks
transaction = transaction_fixture


@pytest.mark.parametrize('active,enabled', [(False, False), (False, True), (True, False), (True, True)])
@pytest.mark.parametrize('renewal_active', [False, True])
def test_failed_deployment_restores_exact_named_service_state(transaction, active, enabled, renewal_active):
    config, units, commands = transaction
    prior = dict(active=active, enabled=enabled)
    timer = 'named-private-dnssec.timer'
    # The snapshot observes timer state only for an actually installed unit.
    unit = Path('/etc/systemd/system') / timer
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text('[Timer]\nOnBootSec=60\n', encoding='ascii')
    unit.chmod(0o644)
    units.write_text(json.dumps(dict(units={'named': prior,
        timer: dict(active=renewal_active, enabled=True)}, operations=[])), encoding='utf-8')
    values, hosts, groups = variables(False)
    with NativeRecordedBoundary(commands, NativeTransactionRun, replies('health')):
        status, result = NativeTransactionRun('tasks.d/deploy/main.yml', values,
            inventory_hosts=hosts, inventory_groups=groups).run()
    assert status == 2 and result.failed[-1] == 'Stop deployment after rollback'
    assert config.read_bytes() == ORIGINAL
    assert json.loads(units.read_text())['units']['named'] == prior
    assert json.loads(units.read_text())['units'][timer]['active'] is renewal_active
    assert ('Resume previously active private DNSSEC renewal' in result.ok) is renewal_active
    assert result.facts['bind_deployment_retain_lease'] is False


@pytest.mark.parametrize('operation,rc,stdout', [
    ('is-active', 1, 'active\n'), ('is-active', 3, 'failed\n'),
    ('is-enabled', 4, 'not-found\n'), ('is-enabled', 1, 'masked\n'),
])
def test_unobservable_named_state_refuses_snapshot_before_service_changes(transaction, operation, rc, stdout):
    _, units, _ = transaction
    state = json.loads(units.read_text())
    state['faults'] = {json.dumps([operation, 'named']): dict(rc=rc, stdout=stdout)}
    units.write_text(json.dumps(state), encoding='utf-8')
    before = units.read_bytes()
    status, result = NativeSnapshotRun('tasks.d/runtime_snapshot.yml', {}).run()
    assert status == 2
    assert result.facts['bind_runtime_snapshot_ready'] is False
    assert units.read_bytes() == before

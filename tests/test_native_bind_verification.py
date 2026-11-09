"""Native BIND validation orchestration with exact, independently defined replies."""
import json
import os

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeRecordedBoundary, NativeVerifyRun

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')


def replies(configuration):
    return {json.dumps(argv): {'rc': 0, 'stdout': stdout} for argv, stdout in [
        (['named-checkconf', '-z', '/etc/named.conf'], 'zone loading succeeded'),
        (['named-checkconf', '-p', '/etc/named.conf'], configuration),
        (['named-checkzone', 'example.test', '/var/named/zones/example.test.zone'], 'zone example.test/IN: loaded serial 1')]}


@pytest.mark.parametrize('primary', [False, True])
@pytest.mark.parametrize('populated', [False, True])
def test_native_verification_preserves_role_and_empty_zone_loop_behavior(tmp_path, primary, populated):
    with NativeFilesystemScope(NativeVerifyRun):
        with NativeRecordedBoundary(tmp_path, NativeVerifyRun, replies('options { allow-transfer { none; }; };')) as state:
            status, result = NativeVerifyRun('tasks.d/deploy/verify.yml', {
                'bind_managed_zones': [{'domain': 'example.test'}] if populated else [],
                'bind_primary_group': 'dns_primary'},
                inventory_groups={'dns_primary' if primary else 'dns_secondary': ['localhost']}).run()
            assert status == 0 and not result.failed
            calls = json.loads(state.read_text())['calls']
            assert len(calls) == 2 + int(primary and populated)
            assert calls[:2] == [['named-checkconf', '-z', '/etc/named.conf'],
                                 ['named-checkconf', '-p', '/etc/named.conf']]
            assert 'allow-transfer' not in json.dumps(result.registers)


@pytest.mark.parametrize('configuration,safe', [
    ('options { allow-transfer { 192.0.2.2; any; }; };', False),
    ('options { allow-transfer { key "site-transfer"; "any"; }; };', False),
    ('options { allow-transfer { !{ !bind_secondary_back_ip; any; }; key "transfer"; }; };', True),
    ('options { allow-transfer { 192.0.2.2; key "any"; }; };', True),
    ('options { allow-transfer { !{ !bind_secondary_back_ip; any; }; any; }; };', False)])
def test_native_verification_applies_the_direct_transfer_guard_before_zone_checks(tmp_path, configuration, safe):
    with NativeFilesystemScope(NativeVerifyRun):
        with NativeRecordedBoundary(tmp_path, NativeVerifyRun, replies(configuration)) as state:
            status, result = NativeVerifyRun('tasks.d/deploy/verify.yml', {
                'bind_managed_zones': [], 'bind_primary_group': 'dns_primary'}).run()
            assert status == (0 if safe else 2)
            assert result.failed == ([] if safe else ['Ensure no wildcard zone transfers are enabled'])
            assert len(json.loads(state.read_text())['calls']) == 2
            assert configuration not in json.dumps(result.registers)


@pytest.mark.parametrize('stage', ['load', 'canonical', 'zone'])
def test_native_verification_propagates_each_validator_failure(tmp_path, stage):
    data = replies('options { allow-transfer { none; }; };')
    key = list(data)[{'load': 0, 'canonical': 1, 'zone': 2}[stage]]
    data[key] = {'rc': 17, 'stderr': 'synthetic validator refusal'}
    with NativeFilesystemScope(NativeVerifyRun):
        with NativeRecordedBoundary(tmp_path, NativeVerifyRun, data) as state:
            status, result = NativeVerifyRun('tasks.d/deploy/verify.yml', {
                'bind_managed_zones': [{'domain': 'example.test'}], 'bind_primary_group': 'dns_primary'},
                inventory_groups={'dns_primary': ['localhost']}).run()
            assert status == 2 and len(result.failed) == 1
            assert len(json.loads(state.read_text())['calls']) == {'load': 1, 'canonical': 2, 'zone': 3}[stage]

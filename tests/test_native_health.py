"""Native health orchestration, retry decisions and completion barriers.

Commands use exact private replies; these tests qualify YAML and filter wiring,
not DNS packets, replication, a real named daemon or upstream availability.
"""
import copy
import json
import os
from pathlib import Path

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeHealthRun, NativeRecordedBoundary

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')


def answer(owner, kind, data, authoritative=True):
    return (';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 7\n;; flags: qr ' +
            ('aa' if authoritative else 'ra') + '; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 0\n' +
            owner + '. 300 IN ' + kind + ' ' + data + '\n')


def replies(populated=True):
    data, keys = {}, {}

    def put(label, argv, stdout):
        key = json.dumps(argv)
        keys[label] = key
        data[key] = {'rc': 0, 'stdout': stdout}

    for transport in ('+notcp', '+tcp'):
        for name, kind, value in [('localhost', 'A', '127.0.0.1'),
                                 ('1.0.0.127.in-addr.arpa', 'PTR', 'localhost.')]:
            put('loopback_' + transport + kind, ['dig', '-r', '-4', '+ignore', transport, '+time=3',
                '+tries=1', '+norecurse', '+noall', '+comments', '+answer', '-b', '127.0.0.1', '@127.0.0.1',
                name, kind], answer(name, kind, value))
    if populated:
        soa = 'ns1.example.test. hostmaster.example.test. 7 3600 900 1209600 300'
        put('checker', ['named-checkzone', 'example.test', '/var/named/zones/example.test.zone'],
            'zone example.test/IN: loaded serial 7\nOK\n')
        put('soa', ['dig', '-r', '-4', '+tcp', '+time=3', '+tries=2', '+norecurse', '+noall', '+comments',
            '+answer', '-b', '192.0.2.2', '@192.0.2.2', 'example.test', 'SOA'], answer('example.test', 'SOA', soa))
        for transport in ('+notcp', '+tcp'):
            put('forward_' + transport, ['dig', '-r', '-4', '+ignore', transport, '+time=3', '+tries=1',
                '+noall', '+comments', '+answer', '-b', '127.0.0.1', '@127.0.0.1', 'upstream.test', 'SOA'],
                answer('upstream.test', 'SOA', soa, False))
    return data, keys


def variables(populated=True):
    return {'bind_primary_host': 'localhost', 'bind_back_ip': '192.0.2.2',
            'bind_managed_zones': [{'domain': 'example.test'}] if populated else [],
            'bind_forward_zones': [{'domain': 'upstream.test'}] if populated else []}


@pytest.mark.parametrize('fault', [None, 'loopback_rc', 'loopback_status', 'loopback_aa', 'loopback_answer',
                                  'soa_rc', 'soa_serial', 'forward_rc', 'forward_status', 'forward_answer'])
def test_native_health_retries_actual_predicates_before_recording_success(tmp_path, fault):
    data, keys = replies()
    if fault:
        stage, defect = fault.split('_', 1)
        key = keys[{'loopback': 'loopback_+notcpA', 'soa': 'soa', 'forward': 'forward_+notcp'}[stage]]
        invalid, valid = copy.deepcopy(data[key]), copy.deepcopy(data[key])
        if defect == 'rc': invalid['rc'] = 17
        elif defect == 'status': invalid['stdout'] = invalid['stdout'].replace('status: NOERROR', 'status: SERVFAIL')
        elif defect == 'aa': invalid['stdout'] = invalid['stdout'].replace('flags: qr aa;', 'flags: qr;')
        elif defect == 'serial': invalid['stdout'] = invalid['stdout'].replace(' 7 3600', ' 8 3600')
        elif stage == 'loopback': invalid['stdout'] = invalid['stdout'].replace(' IN A 127.0.0.1', ' IN A 127.0.0.2')
        else: invalid['stdout'] = invalid['stdout'].replace('upstream.test.', 'other.test.')
        data[key] = {'sequence': [invalid, valid]}
    with NativeFilesystemScope(NativeHealthRun):
        with NativeRecordedBoundary(tmp_path, NativeHealthRun, data) as state:
            status, result = NativeHealthRun('tasks.d/deploy/health.yml', variables(), checks=[
                "bind_expected_soa_serials == {'example.test': 7}", 'bind_deployment_health_completed']).run()
            assert status == 0 and not result.failed
            assert result.facts['bind_deployment_health_completed'] is True
            assert result.facts['bind_expected_soa_serials'] == {'example.test': 7}
            calls = json.loads(state.read_text())['calls']
            assert len(calls) == 8 + int(fault is not None)
            assert sum(call[0] == 'named-checkzone' for call in calls) == 1


def test_native_health_empty_zone_lists_preserve_loopback_checks_and_reset_stale_serial_facts(tmp_path):
    data, keys = replies(False)
    with NativeFilesystemScope(NativeHealthRun):
        with NativeRecordedBoundary(tmp_path, NativeHealthRun, data) as state:
            status, result = NativeHealthRun('tasks.d/deploy/health.yml', variables(False),
                inventory_hosts={'localhost': {'bind_expected_soa_serials': {'stale.test': 42}}},
                checks=['bind_expected_soa_serials == {}']).run()
            assert status == 0 and not result.failed
            assert result.facts['bind_expected_soa_serials'] == {}
            assert result.facts['bind_deployment_health_completed'] is True
            assert len(json.loads(state.read_text())['calls']) == 4


@pytest.mark.parametrize('kind', ['command-failure', 'missing-serial'])
def test_native_health_primary_serial_refusal_prevents_soa_probes_and_completion(tmp_path, kind):
    data, keys = replies()
    data[keys['checker']] = {'rc': 17} if kind == 'command-failure' else {'rc': 0, 'stdout': 'not a loaded serial'}
    with NativeFilesystemScope(NativeHealthRun):
        with NativeRecordedBoundary(tmp_path, NativeHealthRun, data) as state:
            status, result = NativeHealthRun('tasks.d/deploy/health.yml', variables()).run()
            assert status == 2 and result.failed
            assert 'bind_deployment_health_completed' not in result.facts
            assert len(json.loads(state.read_text())['calls']) == 5


def test_native_health_fixture_refuses_remote_delegation_before_any_command():
    with NativeFilesystemScope(NativeHealthRun):
        with pytest.raises(ValueError, match='remote delegate'):
            NativeHealthRun('tasks.d/deploy/health.yml', dict(variables(), bind_primary_host='peer')).run()
        assert not Path('/usr/bin/dig').exists()


def test_native_health_fixture_refuses_path_shadowing_of_its_bare_command(tmp_path, monkeypatch):
    data, keys = replies()
    shadow = tmp_path / 'shadow'
    shadow.mkdir()
    executable = shadow / 'dig'
    executable.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    executable.chmod(0o700)
    with NativeFilesystemScope(NativeHealthRun):
        with NativeRecordedBoundary(tmp_path, NativeHealthRun, data) as state:
            monkeypatch.setenv('PATH', str(shadow) + ':' + os.environ['PATH'])
            with pytest.raises(ValueError, match='exact owned fixture'):
                NativeHealthRun('tasks.d/deploy/health.yml', variables()).run()
            assert json.loads(state.read_text())['calls'] == []

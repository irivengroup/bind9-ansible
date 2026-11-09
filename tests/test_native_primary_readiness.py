"""Real local TCP readiness and canonical SOA command decision barriers.

The listener belongs exclusively to a disconnected container. The exact dig
replies qualify orchestration, not a live DNS server or intersite reachability.
"""
from contextlib import contextmanager
import json
import os
import socket
import threading

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeReadinessRun, NativeRecordedBoundary
from test_native_health import answer

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')

SOA = 'ns1.example.test. hostmaster.example.test. 7 3600 900 1209600 300'


@contextmanager
def listener():
    """Accept native wait_for connections; refuse an occupied loopback port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(('127.0.0.1', 53))
        server.listen(8)
        server.settimeout(0.1)
        stopped = threading.Event()
        accepted, failures = [], []
        def accept():
            while not stopped.is_set():
                try:
                    client, peer = server.accept()
                except socket.timeout:
                    continue
                except OSError as error:
                    failures.append(str(error))
                    return
                with client:
                    accepted.append(peer[0])
        worker = threading.Thread(target=accept)
        worker.start()
        try:
            yield accepted
        finally:
            stopped.set()
            worker.join(timeout=2)
            assert not worker.is_alive() and not failures


def command(domain):
    return ['dig', '-r', '-4', '+tcp', '+time=3', '+tries=1', '+norecurse', '-b',
            '127.0.0.1', '+noall', '+comments', '+answer', '@127.0.0.1', domain, 'SOA']


def execute(tmp_path, domains, responses, available=True):
    variables = dict(bind_primary_host='localhost', bind_back_ip='127.0.0.1',
                     bind_managed_zones=[{'domain': domain} for domain in domains])
    with NativeFilesystemScope(NativeReadinessRun):
        with NativeRecordedBoundary(tmp_path, NativeReadinessRun, responses) as state:
            if available:
                with listener() as connections:
                    result = NativeReadinessRun('tasks.d/deploy/primary_readiness.yml', variables).run()
                assert connections and set(connections) == {'127.0.0.1'}
            else:
                result = NativeReadinessRun('tasks.d/deploy/primary_readiness.yml', variables).run()
            return result, json.loads(state.read_text())['calls']


@pytest.mark.parametrize('domains', [[], ['example.test'], ['example.test', 'second.test']])
def test_native_primary_readiness_waits_on_real_tcp_and_checks_each_zone(tmp_path, domains):
    responses = {json.dumps(command(domain)): {'rc': 0, 'stdout': answer(domain, 'SOA', SOA)}
                 for domain in domains}
    (status, observed), calls = execute(tmp_path, domains, responses)
    assert status == 0 and not observed.failed and calls == [command(domain) for domain in domains]
    assert 'Wait for local primary DNS TCP listener on back interface' in observed.ok
    assert ('Verify primary SOA from secondary over back network' in observed.skipped) is (not domains)


@pytest.mark.parametrize('fault', ['command', 'incomplete', 'authority', 'owner'])
def test_native_primary_readiness_rejects_failed_or_wrong_primary_answers(tmp_path, fault):
    response = answer('example.test', 'SOA', SOA)
    if fault == 'incomplete': response = response.replace('NOERROR', 'SERVFAIL')
    elif fault == 'authority': response = answer('example.test', 'SOA', SOA, authoritative=False)
    elif fault == 'owner': response = answer('foreign.test', 'SOA', SOA)
    responses = {json.dumps(command('example.test')): {'rc': 1 if fault == 'command' else 0, 'stdout': response}}
    (status, observed), calls = execute(tmp_path, ['example.test'], responses)
    assert status == 2 and observed.failed == ['Verify primary SOA from secondary over back network']
    assert calls == [command('example.test')]


def test_native_primary_readiness_stops_before_dns_when_the_tcp_listener_is_absent(tmp_path):
    (status, observed), calls = execute(tmp_path, ['example.test'], {}, available=False)
    assert status == 2 and observed.failed == ['Wait for local primary DNS TCP listener on back interface']
    assert calls == []


@pytest.mark.parametrize('kind,accepted', [('owner-case', True), ('rdata', False),
    ('extra-record', False), ('serial-overflow', False)])
def test_native_primary_readiness_checks_a_single_complete_soa_with_dns_case_folding(tmp_path, kind, accepted):
    response = answer('EXAMPLE.TEST' if kind == 'owner-case' else 'example.test', 'SOA', SOA)
    if kind == 'rdata': response = response.replace(SOA, 'not-an-soa')
    elif kind == 'extra-record':
        response = response.replace('ANSWER: 1', 'ANSWER: 2') + 'unrelated.test. 300 IN A 192.0.2.1\n'
    elif kind == 'serial-overflow': response = response.replace(' 7 3600 ', ' 4294967296 3600 ')
    (status, observed), calls = execute(tmp_path, ['example.test'],
        {json.dumps(command('example.test')): {'rc': 0, 'stdout': response}})
    assert status == (0 if accepted else 2)
    assert observed.failed == ([] if accepted else ['Verify primary SOA from secondary over back network'])
    assert calls == [command('example.test')]


@pytest.mark.parametrize('host,address', [('remote', '127.0.0.1'), ('localhost', '192.0.2.1')])
def test_native_primary_readiness_fixture_refuses_remote_network_access(host, address):
    runner = NativeReadinessRun('tasks.d/deploy/primary_readiness.yml',
        dict(bind_primary_host=host, bind_back_ip=address))
    with pytest.raises(ValueError, match='private loopback'):
        runner.run()

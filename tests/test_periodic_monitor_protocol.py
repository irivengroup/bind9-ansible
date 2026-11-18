"""Run unchanged periodic Bash against exact independent DNS/service replies.

Only external commands are controlled. The interpreter, parsing, loops, helper
functions, decisions, output and exit statuses are the production implementation.
"""
import copy
import json
import os
from pathlib import Path
import subprocess

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeMonitorRun, NativeRecordedBoundary
from test_native_health import answer

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')
SCRIPT = Path(__file__).resolve().parents[1] / 'roles/bind9/files/named-healthcheck.sh'
BACK = '192.0.2.11'


def protocol(zones):
    replies, keys = {}, {}

    def put(label, argv, text='', rc=0):
        key = json.dumps(argv)
        keys[label], replies[key] = key, {'rc': rc, 'stdout': text}

    put('service', ['systemctl', 'is-active', '--quiet', 'named'])
    for transport in ('+notcp', '+tcp'):
        for owner, kind, rdata in [('localhost', 'A', '127.0.0.1'),
                                   ('1.0.0.127.in-addr.arpa', 'PTR', 'localhost.')]:
            put('loopback_' + transport + kind, ['dig', '-r', '-4', '-b', '127.0.0.1', '@127.0.0.1',
                transport, '+ignore', '+time=3', '+tries=1', '+norecurse', '+noall', '+comments', '+answer',
                owner, kind], answer(owner, kind, rdata))
    for zone in zones:
        put(zone, ['dig', '-r', '-4', '-b', BACK, '@' + BACK, '+tcp', '+time=3', '+tries=1',
            '+norecurse', '+noall', '+comments', '+answer', zone, 'SOA'],
            answer(zone, 'SOA', 'ns1.example.test. hostmaster.example.test. 7 3600 900 1209600 300'))
    return replies, keys


def invoke(tmp_path, replies, arguments):
    with NativeFilesystemScope(NativeMonitorRun):
        with NativeRecordedBoundary(tmp_path, NativeMonitorRun, replies) as state:
            result = subprocess.run(['bash', str(SCRIPT)] + arguments, env=dict(os.environ),
                capture_output=True, text=True, timeout=10)
            calls = json.loads(state.read_text(encoding='utf-8'))['calls']
    return result, calls


@pytest.mark.parametrize('zones', [[], ['example.test'], ['example.test', 'second.test']])
def test_periodic_monitor_checks_all_transports_and_every_requested_authority(tmp_path, zones):
    replies, keys = protocol(zones)
    result, calls = invoke(tmp_path, replies, [BACK] + zones)
    assert result.returncode == 0 and result.stderr == ''
    assert result.stdout == 'OK named active, native loopback UDP/TCP, ' + str(len(zones)) + ' authoritative zone(s)\n'
    assert len(calls) == 5 + len(zones)
    assert {call[6] for call in calls if call[0] == 'dig' and call[5] == '@127.0.0.1'} == {'+notcp', '+tcp'}


def test_periodic_monitor_refuses_missing_address_before_any_external_command(tmp_path):
    replies, keys = protocol([])
    result, calls = invoke(tmp_path, replies, [])
    assert result.returncode == 2 and result.stdout == ''
    assert result.stderr.startswith('usage: named-healthcheck ')
    assert calls == []


def test_periodic_monitor_refuses_an_inactive_service_before_dns_queries(tmp_path):
    replies, keys = protocol(['example.test'])
    replies[keys['service']]['rc'] = 3
    result, calls = invoke(tmp_path, replies, [BACK, 'example.test'])
    assert result.returncode == 2 and result.stdout == 'CRITICAL named inactive\n'
    assert calls == [['systemctl', 'is-active', '--quiet', 'named']]


@pytest.mark.parametrize('scope', ['loopback', 'authority'])
def test_periodic_monitor_propagates_query_command_failures_before_success(tmp_path, scope):
    replies, keys = protocol(['example.test'])
    key = keys['loopback_+notcpA' if scope == 'loopback' else 'example.test']
    replies[key] = {'rc': 17, 'stderr': 'synthetic transport refusal'}
    result, calls = invoke(tmp_path, replies, [BACK, 'example.test'])
    assert result.returncode == 2
    assert result.stdout.startswith('CRITICAL loopback query failed:' if scope == 'loopback' else 'CRITICAL DNS query failed:')
    assert 'OK named active' not in result.stdout
    assert len(calls) == (2 if scope == 'loopback' else 6)


@pytest.mark.parametrize('defect', ['missing-status', 'duplicate-status', 'servfail', 'duplicate-header',
    'truncated', 'wrong-answer-count', 'extra-answer', 'missing-answer', 'non-authoritative',
    'wrong-owner', 'invalid-ttl', 'wrong-class', 'wrong-type', 'wrong-address'])
def test_periodic_monitor_refuses_each_invalid_loopback_protocol_field(tmp_path, defect):
    replies, keys = protocol(['example.test'])
    reply = copy.deepcopy(replies[keys['loopback_+notcpA']])
    text = reply['stdout']
    if defect == 'missing-status': text = '\n'.join(text.splitlines()[1:]) + '\n'
    elif defect == 'duplicate-status': text += ';; status: NOERROR\n'
    elif defect == 'servfail': text = text.replace('status: NOERROR', 'status: SERVFAIL')
    elif defect == 'duplicate-header': text += text.splitlines()[1] + '\n'
    elif defect == 'truncated': text = text.replace('flags: qr aa;', 'flags: qr aa tc;')
    elif defect == 'wrong-answer-count': text = text.replace('ANSWER: 1', 'ANSWER: 2')
    elif defect == 'extra-answer': text += 'other.test. 300 IN A 127.0.0.2\n'
    elif defect == 'missing-answer': text = '\n'.join(text.splitlines()[:2]) + '\n'
    elif defect == 'non-authoritative': text = text.replace('flags: qr aa;', 'flags: qr ra;')
    elif defect == 'wrong-owner': text = text.replace('localhost.', 'other.test.')
    elif defect == 'invalid-ttl': text = text.replace('. 300 IN ', '. invalid IN ')
    elif defect == 'wrong-class': text = text.replace(' IN A ', ' CH A ')
    elif defect == 'wrong-type': text = text.replace(' IN A ', ' IN TXT ')
    else: text = text.replace(' IN A 127.0.0.1', ' IN A 127.0.0.2')
    reply['stdout'] = text
    replies[keys['loopback_+notcpA']] = reply
    result, calls = invoke(tmp_path, replies, [BACK, 'example.test'])
    assert result.returncode == 2 and result.stdout == 'CRITICAL loopback answer invalid: localhost A +notcp\n'
    assert len(calls) == 2


@pytest.mark.parametrize('defect,diagnostic', [('servfail', 'CRITICAL DNS incomplete/status:'),
    ('non-authoritative', 'CRITICAL non-authoritative:'), ('wrong-owner', 'CRITICAL SOA owner/type:'),
    ('invalid-ttl', 'CRITICAL SOA owner/type:')])
def test_periodic_monitor_refuses_invalid_authority_answers_after_valid_loopback(tmp_path, defect, diagnostic):
    replies, keys = protocol(['example.test'])
    text = replies[keys['example.test']]['stdout']
    if defect == 'servfail': text = text.replace('status: NOERROR', 'status: SERVFAIL')
    elif defect == 'non-authoritative': text = text.replace('flags: qr aa;', 'flags: qr ra;')
    elif defect == 'wrong-owner': text = text.replace('example.test. 300 IN SOA', 'other.test. 300 IN SOA')
    else: text = text.replace('. 300 IN SOA', '. invalid IN SOA')
    replies[keys['example.test']]['stdout'] = text
    result, calls = invoke(tmp_path, replies, [BACK, 'example.test'])
    assert result.returncode == 2 and result.stdout == diagnostic + ' example.test\n'
    assert len(calls) == 6

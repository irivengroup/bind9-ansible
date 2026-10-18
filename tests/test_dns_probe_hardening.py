"""Reject misleading DNS probe output and isolate probes from operator profiles."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from test_soa_health import SoaHealth
from route_task_expressions import TaskExpressions

ROLE = Path(__file__).resolve().parents[1] / 'roles/bind9'
ANSWER = (';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n'
          ';; flags: qr aa; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 0\n'
          'example.test. 300 IN SOA ns.example.test. host.example.test. 12 3600 900 604800 300\n')


@pytest.mark.parametrize('scenario', ['valid', 'tc', 'missing_row', 'extra_row', 'wrong_count',
                                    'duplicate_status', 'contradictory_status', 'duplicate_header',
                                    'missing_header', 'missing_count', 'nul', 'oversized', 'not_string'])
def test_incomplete_probe_is_refused_by_real_filters(scenario):
    output = ANSWER
    if scenario == 'tc':
        output = output.replace('qr aa;', 'qr aa tc;')
    elif scenario == 'missing_row':
        output = '\n'.join(output.splitlines()[:2])
    elif scenario == 'extra_row':
        output += 'other.test. 300 IN A 192.0.2.10\n'
    elif scenario == 'wrong_count':
        output = output.replace('ANSWER: 1', 'ANSWER: 2')
    elif scenario == 'duplicate_status':
        output += ';; status: NOERROR\n'
    elif scenario == 'contradictory_status':
        output += ';; status: SERVFAIL\n'
    elif scenario == 'duplicate_header':
        output += output.splitlines()[1] + '\n'
    elif scenario == 'missing_header':
        output = output.replace(output.splitlines()[1], '')
    elif scenario == 'missing_count':
        output = output.replace('ANSWER: 1,', '')
    elif scenario == 'nul':
        output += '\x00'
    elif scenario == 'oversized':
        output += ';' * 1048576
    elif scenario == 'not_string':
        output = None
    expected = scenario == 'valid'
    assert SoaHealth.response_complete(output) is expected
    assert SoaHealth.matches(output, 'example.test', 12) is expected


@pytest.mark.parametrize('kind', ['loopback', 'primary', 'forward', 'authoritative'])
def test_real_ansible_health_conditions_refuse_a_truncated_response(kind):
    if kind == 'primary':
        task = yaml.safe_load((ROLE / 'tasks/tasks.d/deploy/primary_readiness.yml').read_text())[1]
        item = {'domain': 'example.test'}
    else:
        tasks = yaml.safe_load((ROLE / 'tasks/tasks.d/deploy/health.yml').read_text())
        register = {'loopback': 'bind_loopback_check', 'forward': 'bind_forward_health', 'authoritative': 'bind_soa_checks'}[kind]
        task = next(value for value in tasks if value.get('register') == register)
        item = ({'domain': 'example.test'}, '+notcp') if kind == 'forward' else {'domain': 'example.test'}
        if kind == 'loopback':
            item = task['loop'][0]
    output = ANSWER.replace('qr aa;', 'qr aa tc;')
    if kind == 'loopback':
        output = output[:output.rfind('example.test. 300')] + 'localhost. 300 IN A 127.0.0.1\n'
    scope = {task['register']: {'stdout': output, 'rc': 0}, 'item': item,
             'bind_expected_soa_serials': {'example.test': 12}}
    assert TaskExpressions(scope).condition([task['failed_when']], scope)
    if 'until' in task:
        assert not TaskExpressions(scope).condition([task['until']], scope)


def test_all_ansible_dns_probes_ignore_digrc_and_select_ipv4():
    for path in (ROLE / 'tasks').rglob('*.yml'):
        # Walk YAML rather than matching line layout.
        def commands(value):
            if isinstance(value, dict):
                if 'ansible.builtin.command' in value:
                    yield value['ansible.builtin.command'].get('argv', [])
                for child in value.values():
                    yield from commands(child)
            elif isinstance(value, list):
                for child in value:
                    yield from commands(child)
        for argv in commands(yaml.safe_load(path.read_text())):
            if argv and isinstance(argv, list) and Path(argv[0]).name == 'dig':
                assert '-r' in argv and '-4' in argv, path


@pytest.mark.skipif(not shutil.which('bash'), reason='Requires Linux shell')
@pytest.mark.parametrize('scenario', ['valid', 'tc', 'missing', 'duplicate', 'wrong_owner'])
def test_periodic_monitor_rejects_corrupt_replies_and_preserves_transport(tmp_path, scenario):
    systemctl = tmp_path / 'systemctl'
    systemctl.write_text('#!/bin/sh\nexit 0\n')
    systemctl.chmod(0o755)
    dig = tmp_path / 'dig'
    # Fail on options influenced by .digrc unless production explicitly ignores it.
    dig.write_text('''#!/bin/bash
[[ $1 == -r && $2 == -4 ]] || exit 91
name=${@: -2:1}; kind=${@: -1}
flags="qr aa"; [[ $PROBE_SCENARIO != tc ]] || flags="qr aa tc"
printf ';; status: NOERROR\\n;; flags: %s; QUERY: 1, ANSWER: 1\\n' "$flags"
[[ $PROBE_SCENARIO != duplicate ]] || printf ';; status: NOERROR\\n'
[[ $PROBE_SCENARIO != missing ]] || exit 0
[[ $PROBE_SCENARIO != wrong_owner ]] || name=other.test
case $kind in
 A) printf '%s. 300 IN A 127.0.0.1\\n' "$name" ;;
 PTR) printf '%s. 300 IN PTR localhost.\\n' "$name" ;;
 SOA) printf '%s. 300 IN SOA ns.test. host.test. 12 3600 900 604800 300\\n' "$name" ;;
esac
''')
    dig.chmod(0o755)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'], PROBE_SCENARIO=scenario)
    result = subprocess.run(['bash', str(ROLE / 'files/named-healthcheck.sh'), '192.0.2.11', 'example.test'],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
    assert result.returncode == (0 if scenario == 'valid' else 2), result.stdout + result.stderr

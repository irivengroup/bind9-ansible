"""Exercise health identity and failure propagation through the real entry points."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

from test_management import M

ROOT = Path(__file__).resolve().parents[1]
ROLE = ROOT / 'roles/bind9'


@pytest.mark.parametrize('scenario', ['missing_flags', 'missing_count', 'extra_row', 'extra_answer',
                                    'duplicate_flags', 'nul', 'not_text', 'oversized',
                                    'missing_status', 'truncated', 'valid_local', 'valid_forward'])
def test_management_probe_requires_a_complete_single_answer(scenario):
    """Use dig's real comments/answer shape, then corrupt one independent field."""
    response = (';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 1\n'
                ';; flags: qr aa; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 0\n'
                'example.test. 60 IN SOA ns.test. host.test. 42 3600 900 604800 300\n')
    if scenario == 'missing_flags':
        response = '\n'.join(line for line in response.splitlines() if not line.startswith(';; flags:'))
    elif scenario == 'missing_count':
        response = response.replace('ANSWER: 1, ', '')
    elif scenario == 'extra_row':
        response += 'unparsed answer data\n'
    elif scenario == 'extra_answer':
        response = response.replace('ANSWER: 1', 'ANSWER: 2') + response.splitlines()[-1] + '\n'
    elif scenario == 'duplicate_flags':
        response += response.splitlines()[1] + '\n'
    elif scenario == 'nul':
        response += '; hidden\x00bytes\n'
    elif scenario == 'not_text':
        response = None
    elif scenario == 'oversized':
        response += ';' * 1048576
    elif scenario == 'missing_status':
        response = '\n'.join(response.splitlines()[1:])
    elif scenario == 'truncated':
        response = response.replace('qr aa;', 'qr aa tc;')
    elif scenario == 'valid_forward':
        response = response.replace('qr aa;', 'qr;')
    expected = None if scenario in ('missing_flags', 'valid_forward') else '42'
    assert M.DnsReply(response).matches('example.test.', 'SOA', expected) is scenario.startswith('valid_')


@pytest.mark.skipif(os.name != 'posix' or not shutil.which('bash'), reason='Requires Bash')
@pytest.mark.parametrize('kind', ['A', 'PTR', 'none'])
def test_loopback_wrong_owner_is_fatal_without_authoritative_zone_arguments(tmp_path, kind):
    """A later SOA check must not conceal an earlier false-positive loopback check."""
    systemctl = tmp_path / 'systemctl'
    systemctl.write_text('#!/bin/sh\nexit 0\n')
    systemctl.chmod(0o755)
    dig = tmp_path / 'dig'
    dig.write_text('''#!/bin/bash
name=${@: -2:1}; kind=${@: -1}
[[ $kind != "$WRONG_OWNER_KIND" ]] || name=unrelated.test
printf ';; status: NOERROR\\n;; flags: qr aa; QUERY: 1, ANSWER: 1\\n'
case $kind in
 A) printf '%s. 300 IN A 127.0.0.1\\n' "$name" ;;
 PTR) printf '%s. 300 IN PTR localhost.\\n' "$name" ;;
esac
''')
    dig.chmod(0o755)
    environment = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'],
                       WRONG_OWNER_KIND=kind)
    result = subprocess.run(['bash', str(ROLE / 'files/named-healthcheck.sh'), '192.0.2.11'],
                            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, timeout=10)
    assert result.returncode == (0 if kind == 'none' else 2), result.stdout + result.stderr
    assert ('OK named active' if kind == 'none' else 'CRITICAL loopback answer invalid:') in result.stdout


@pytest.mark.skipif(os.name != 'posix' or not shutil.which('ansible-playbook'), reason='Requires Linux Ansible')
@pytest.mark.parametrize('tag', ['configure', 'zones', 'records', 'services', 'validate'])
def test_partial_deployment_failure_keeps_a_nonzero_exit_code(tmp_path, tag):
    """Execute the actual rescue: successful bookkeeping must not swallow a failure.

    The controlled failure replaces only the protected body. Deployment-only
    restoration stays unselected, so this test never runs system mutations.
    """
    main = yaml.safe_load((ROLE / 'tasks/tasks.d/deploy/main.yml').read_text())
    rescue = next(task['rescue'] for task in main if 'rescue' in task)
    playbook = tmp_path / 'partial.yml'
    playbook.write_text(yaml.safe_dump([{
        'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
        'vars': {'role_path': str(ROLE)},
        'tasks': [{'block': [{'name': 'Controlled partial deployment failure',
                            'ansible.builtin.fail': {'msg': 'Controlled partial deployment failure'},
                            'tags': [tag]}], 'rescue': rescue}]
    }], sort_keys=False))
    environment = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / 'ansible.cfg'))
    result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(playbook), '--tags', tag],
                            env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            universal_newlines=True, timeout=40)
    assert result.returncode != 0, result.stdout + result.stderr
    assert 'Controlled partial deployment failure' in result.stdout
    assert 'Stop deployment after rollback' in result.stdout
    assert 'partial deployment failed' in result.stdout

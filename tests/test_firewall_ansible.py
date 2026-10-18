"""Run additive firewalld workflows with CLI contract doubles, never host rules."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which('ansible-playbook') or os.name != 'posix',
                                reason='Requires a Linux Ansible controller')

DOUBLE = '''#!/usr/bin/env python3
import json, os, pathlib, sys
path = pathlib.Path(os.environ['BIND_FIREWALL_TEST_STATE'])
state = json.loads(path.read_text())
name, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
state['calls'].append([name] + args)
rc = 0
if name == 'ip':
    assert args == ['-j', '-4', 'address', 'show'], args
    print('' if state['scenario'] == 'empty_addresses' else json.dumps(state['addresses']))
elif args == ['--state']:
    print('not running' if state['scenario'] == 'inactive' else 'running')
    rc = 252 if state['scenario'] == 'inactive' else 0
elif args == ['--get-default-zone']:
    print('public')
elif args[0].startswith('--get-zone-of-interface='):
    interface = args[0].split('=', 1)[1]
    scope = 'permanent' if '--permanent' in args else 'runtime'
    zone = state['zones'][scope][interface]
    if zone:
        print(zone)
    else:
        print('no zone', file=sys.stderr)
        rc = 2
else:
    assert len(args) in (2, 3) and args[0].startswith('--zone='), args
    scope = 'permanent' if '--permanent' in args else 'runtime'
    zone = args[0].split('=', 1)[1]
    services = state['services'][scope].setdefault(zone, ['ssh'])
    if args[1] == '--query-service=dns':
        if state['scenario'] == 'query_error':
            print('query failed', file=sys.stderr)
            rc = 13
        else:
            print('yes' if 'dns' in services else 'no')
            rc = 0 if 'dns' in services else 1
    elif args[1] == '--add-service=dns':
        assert 'dns' not in services, 'duplicate service mutation'
        services.append('dns')
        print('success')
    else:
        sys.exit('unexpected mutation: ' + str(args))
path.write_text(json.dumps(state))
sys.exit(rc)
'''


class FirewallRunner:
    """Keep zones, interfaces, query errors and repeat execution observable."""

    def run(self, folder, scenario, native):
        addresses = [{'ifname': 'adm0', 'addr_info': [{'local': '10.99.0.10'}]},
                     {'ifname': 'front0', 'addr_info': [{'local': '192.0.2.100'}, {'local': '192.0.2.10'}]},
                     {'ifname': 'back0', 'addr_info': [{'local': '10.0.0.10'}]}]
        if scenario == 'missing_back':
            addresses.pop()
        if scenario == 'shared_default':
            addresses[1]['addr_info'].extend(addresses.pop()['addr_info'])
        state = {'scenario': scenario, 'calls': [], 'addresses': addresses,
                 'zones': {'runtime': {'front0': 'public', 'back0': 'internal'},
                           'permanent': {'front0': 'public', 'back0': 'dmz'}},
                 'services': {'runtime': {'public': ['ssh', 'dns'], 'internal': ['ssh']},
                              'permanent': {'public': ['ssh'], 'dmz': ['ssh']}}}
        if scenario == 'shared_default':
            state['zones'] = {'runtime': {'front0': ''}, 'permanent': {'front0': ''}}
        path = folder / 'state.json'
        path.write_text(json.dumps(state))
        binaries = folder / 'bin'
        binaries.mkdir()
        interpreter = binaries / 'controller-python'
        interpreter.symlink_to(sys.executable)
        double = binaries / 'firewall-double'
        double.write_text(DOUBLE)
        double.chmod(0o755)
        for name in ('firewall-cmd', 'ip'):
            (binaries / name).symlink_to(double)
        plays = []
        source_tasks = yaml.safe_load((ROOT / 'roles/bind9/tasks/tasks.d/deploy/firewall.yml').read_text())
        for repeat in (False, True):
            tasks = source_tasks
            if repeat:
                # Queries are authoritative; no mutation should recur after success.
                tasks = tasks + [{'assert': {'that': ['bind_dns_firewall_queries.results | selectattr("rc", "equalto", 1) | list | length == 0']}}]
            plays.append({'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
                          'vars': {'ansible_python_interpreter': str(interpreter),
                                   'bind_front_ip': '192.0.2.10', 'bind_back_ip': '10.0.0.10'}, 'tasks': tasks})
        playbook = folder / 'firewall.yml'
        playbook.write_text(yaml.safe_dump(plays, sort_keys=False))
        environment = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ['PATH'],
                           BIND_FIREWALL_TEST_STATE=str(path), ANSIBLE_CONFIG=str(ROOT / 'ansible.cfg'),
                           ANSIBLE_JINJA2_NATIVE=str(native).lower())
        result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(playbook)], env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        after = json.loads(path.read_text())
        mutations = [args for args in after['calls'] if '--add-service=dns' in args]
        assert after['zones'] == state['zones'] and after['addresses'] == state['addresses']
        assert not any('adm0' in ' '.join(args) for args in after['calls'])
        if scenario in ('empty_addresses', 'inactive', 'missing_back', 'query_error'):
            assert result.returncode != 0, result.stdout
            assert mutations == [], after
        else:
            assert result.returncode == 0, result.stdout
            assert len(mutations) == (1 if scenario == 'shared_default' else 3)
            assert after['services']['runtime']['public'] == ['ssh', 'dns']
            assert after['services']['permanent']['public'] == ['ssh', 'dns']
            if scenario != 'shared_default':
                assert after['services']['runtime']['internal'] == ['ssh', 'dns']
                assert after['services']['permanent']['dmz'] == ['ssh', 'dns']


@pytest.mark.parametrize('scenario,native', [('split', False), ('split', True), ('shared_default', False),
                                          ('shared_default', True), ('empty_addresses', False),
                                          ('inactive', False), ('missing_back', False), ('query_error', False)])
def test_firewalld_adds_only_missing_dns_rules_and_preserves_existing_bindings(tmp_path, scenario, native):
    FirewallRunner().run(tmp_path, scenario, native)

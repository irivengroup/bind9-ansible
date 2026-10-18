"""Exercise real file modules and SELinux orchestration with explicit CLI doubles.

The doubles do not implement an enforcing kernel policy. Actual SELinux access
and the named_t domain remain deployment-host acceptance checks.
"""
import copy
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
path = pathlib.Path(os.environ['BIND_LOG_TEST_STATE'])
state = json.loads(path.read_text())
tool, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
state['calls'].append([tool] + args)
if tool == 'semanage':
    if args == ['fcontext', '-l']:
        print('SELinux fcontext type Context')
        print('/var/log/named.* regular file system_u:object_r:named_log_t:s0')
        if state['type']:
            print('/var/log/named(/.*)? all files system_u:object_r:' + state['type'] + ':s0')
    else:
        assert args[0] == 'fcontext' and args[1] in ('-a', '-m'), args
        assert args[2:] == ['-f', 'a', '-t', 'named_log_t', '/var/log/named(/.*)?'], args
        if state['failure'] == 'mutation':
            path.write_text(json.dumps(state))
            sys.exit('policy mutation refused by test double')
        state['type'] = 'named_log_t'
elif tool == 'restorecon':
    assert args[0] == '-RF' and all(pathlib.Path(p).exists() for p in args[1:]), args
    state['restored'] = True
elif tool == 'ls':
    label = 'named_conf_t'
    if args[0] == '-Zd':
        label = state['type'] if state['restored'] and state['failure'] != 'labels' else 'var_log_t'
    for name in args[1:]:
        print('system_u:object_r:' + label + ':s0 ' + name)
else:
    sys.exit('unexpected command: ' + tool)
path.write_text(json.dumps(state))
'''


class LoggingTaskRunner:
    """Keep every target path and policy mutation inside a temporary test root."""

    def __init__(self, folder, initial, native):
        import grp
        import pwd
        self.folder, self.native = folder, native
        self.user = pwd.getpwuid(os.geteuid()).pw_name
        self.group = grp.getgrgid(os.getegid()).gr_name
        self.state = folder / 'state.json'
        self.state.write_text(json.dumps({'type': {'missing': '', 'wrong': 'var_log_t', 'mutation_failure': 'var_log_t'}.get(initial, 'named_log_t'),
                                         'failure': {'bad_labels': 'labels', 'mutation_failure': 'mutation'}.get(initial, ''),
                                         'restored': False, 'calls': []}))
        self.paths = {name: str(folder / 'target' / name.lstrip('/')) for name in [
            '/var/named/zones', '/var/named/slaves', '/var/named/data', '/var/named/dynamic',
            '/var/named', '/var/log/named', '/var/log/named/default.log',
            '/var/log/named/security.log', '/var/log/named/xfer.log',
            '/etc/named.conf', '/etc/profile.d/bind9-sbin-path.sh']}
        for name in ('/var/named', '/var/log/named'):
            Path(self.paths[name]).mkdir(parents=True)
        Path(self.paths['/etc/named.conf']).parent.mkdir(parents=True)
        Path(self.paths['/etc/named.conf']).write_text('')
        Path(self.paths['/etc/profile.d/bind9-sbin-path.sh']).parent.mkdir()
        self.existing = Path(self.paths['/var/log/named/default.log'])
        self.existing.write_text('historical log entry\n')
        self.existing_mtime = self.existing.stat().st_mtime_ns
        binaries = folder / 'bin'
        binaries.mkdir()
        self.interpreter = binaries / 'controller-python'
        self.interpreter.symlink_to(sys.executable)
        command = binaries / 'policy-double'
        command.write_text(DOUBLE)
        command.chmod(0o755)
        for name in ('semanage', 'restorecon', 'ls'):
            (binaries / name).symlink_to(command)
        self.environment = dict(os.environ, PATH=str(binaries) + os.pathsep + os.environ['PATH'],
                                BIND_LOG_TEST_STATE=str(self.state), ANSIBLE_CONFIG=str(ROOT / 'ansible.cfg'),
                                ANSIBLE_JINJA2_NATIVE=str(native).lower())
        self.initial = initial

    def rebase(self, value):
        if isinstance(value, dict):
            return {key: self.rebase(child) for key, child in value.items()}
        if isinstance(value, list):
            return [self.rebase(child) for child in value]
        return self.paths.get(value, value) if isinstance(value, str) else value

    def run(self):
        tasks_path = ROOT / 'roles/bind9/tasks/tasks.d'
        directories = self.rebase(yaml.safe_load((tasks_path / 'deploy/directories.yml').read_text()))
        for task in directories:
            task['ansible.builtin.file'].update(owner=self.user, group=self.group)
            task['register'] = 'logging_file_result' if task.get('name', '').startswith('Prepare private') else 'logging_dir_result'
        security = self.rebase(yaml.safe_load((tasks_path / 'deploy/security.yml').read_text()))
        security[0]['ansible.builtin.copy'].update(owner=self.user, group=self.group)
        for task in security:
            if task['name'].startswith('Persist the SELinux log context'):
                task['register'] = 'logging_policy_result'
        plays = []
        for repeat in (False, True):
            assertions = [] if not repeat else [{'assert': {'that': [
                'not logging_file_result.changed', 'not logging_dir_result.changed',
                'not logging_policy_result.changed | default(false)']}}]
            plays.append({'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
                          'vars': {'ansible_python_interpreter': str(self.interpreter), 'bind_dnssec_active': False,
                                   'ansible_selinux': {'status': 'disabled' if self.initial == 'disabled' else 'enabled'}},
                          'tasks': copy.deepcopy(directories + security) + assertions})
        playbook = self.folder / 'logging.yml'
        playbook.write_text(yaml.safe_dump(plays, sort_keys=False))
        result = subprocess.run(['ansible-playbook', '-i', 'localhost,', str(playbook)], env=self.environment,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=90)
        state = json.loads(self.state.read_text())
        assert self.existing.read_text() == 'historical log entry\n'
        assert self.existing.stat().st_mtime_ns == self.existing_mtime
        if self.initial in ('bad_labels', 'mutation_failure'):
            assert result.returncode != 0, result.stdout
            assert ('named_log_t' if self.initial == 'bad_labels' else 'policy mutation refused') in result.stdout
        else:
            assert result.returncode == 0, result.stdout
            mutations = [call for call in state['calls'] if call[0] == 'semanage' and call[2] in ('-a', '-m')]
            assert len(mutations) == (1 if self.initial in ('missing', 'wrong') else 0)
            assert (Path(self.paths['/var/log/named']).stat().st_mode & 0o777) == 0o750
            for name in ('default.log', 'security.log', 'xfer.log'):
                assert (Path(self.paths['/var/log/named/' + name]).stat().st_mode & 0o777) == 0o640
            if self.initial == 'disabled':
                assert not any(call[0] == 'semanage' for call in state['calls'])


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('initial', ['missing', 'wrong', 'correct', 'disabled', 'bad_labels', 'mutation_failure'])
def test_real_logging_tasks_preserve_data_and_policy_idempotence(tmp_path, initial, native):
    LoggingTaskRunner(tmp_path, initial, native).run()

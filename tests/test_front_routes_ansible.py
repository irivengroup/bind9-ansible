"""Execute actual route task workflows with isolated native CLI contract doubles.

These tests prove Ansible 2.9 execution, failure handling and journaling. They do
not certify NetworkManager behavior on an actual RHEL server.
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

from test_native_front_routes import ADDRESSES, BASE, NEW, RULES, UUID, plan

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != 'posix' or not shutil.which('ansible-playbook')
                                or (hasattr(os, 'geteuid') and os.geteuid() != 0),
                                reason='Requires Linux root and a local Ansible test controller; no server access')


class AnsibleRouteSandbox:
    """Rebase only role state paths and keep every workflow file otherwise intact."""

    def __init__(self, folder):
        self.folder = folder
        self.state_root = folder / 'route-state'
        role = folder / 'roles/bind9'
        shutil.copytree(ROOT / 'roles/bind9', role, ignore=shutil.ignore_patterns('__pycache__'))
        for path in (role / 'tasks/tasks.d').glob('front*.yml'):
            path.write_text(path.read_text().replace('/var/lib/Bind9-Ansible', str(self.state_root)))
        binaries = folder / 'bin'
        binaries.mkdir()
        # Ansible 2.9 treats interpreter as a shell command: use a simple local
        # symlink when the validation venv lives below a Windows path with spaces.
        self.interpreter = binaries / 'python-test'
        self.interpreter.symlink_to(sys.executable)
        fixture = ROOT / 'tests/fixtures/native_network_cli.py'
        for command in ('ip', 'nmcli'):
            path = binaries / command
            shutil.copyfile(fixture, path)
            path.chmod(0o755)
        self.data_file = folder / 'network.json'
        self.data = {'uuid': UUID, 'gateway': '198.51.100.1', 'addresses': json.loads(ADDRESSES),
                     'rules': json.loads(RULES), 'runtime': copy.deepcopy(BASE),
                     'persistent': [], 'operations': []}
        self.save()
        self.inventory = folder / 'hosts.ini'
        self.inventory.write_text('[dns_primary]\nlocalhost ansible_connection=local bind_back_ip=10.0.0.2\n'
                                  '[dns_secondary]\nsecondary bind_back_ip=10.0.0.3\n')
        config = folder / 'ansible.cfg'
        config.write_text('[defaults]\nretry_files_enabled=False\nhost_key_checking=False\n')
        self.env = dict(os.environ, ANSIBLE_CONFIG=str(config), ANSIBLE_ROLES_PATH=str(folder / 'roles'),
                        BIND_NETWORK_DOUBLE_STATE=str(self.data_file),
                        PATH=str(binaries) + os.pathsep + os.environ['PATH'])

    def save(self):
        self.data_file.write_text(json.dumps(self.data))

    def read(self):
        return json.loads(self.data_file.read_text())

    def run(self, tasks_from='tasks.d/front_return_routes.yml', check=False, extra_vars=None, tags=None):
        variables = {'ansible_python_interpreter': str(self.interpreter),
                     'ansible_host': '192.0.2.2', 'ansible_env': {'SSH_CONNECTION': ''},
                     'bind_front_ip': '198.51.100.2', 'bind_back_ip': '10.0.0.2',
                     'bind_primary_host': 'localhost', 'bind_secondary_host': 'secondary',
                     'bind_frontend_clients': ['203.0.113.0/24'], 'bind_front_route_metric': 100}
        variables.update(extra_vars or {})
        play = [{'hosts': 'localhost', 'gather_facts': False, 'vars': variables,
                 'tasks': [{'name': 'Run actual route workflow', 'include_role': {
                     'name': 'bind9', 'tasks_from': tasks_from}, 'tags': ['always']}]}]
        playbook = self.folder / 'play.yml'
        playbook.write_text(yaml.safe_dump(play))
        command = ['ansible-playbook', '-i', str(self.inventory), str(playbook)]
        if check:
            command.append('--check')
        if tags:
            command.extend(['--tags', tags])
        return subprocess.run(command, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              universal_newlines=True, timeout=120)


class TestAnsibleNativeFrontRoutes:
    @pytest.mark.parametrize('failure_phase', [None, 'health', 'reload'])
    def test_routes_commit_after_dns_health_or_are_removed_by_dns_rollback(self, tmp_path, failure_phase):
        """Execute the real deployment block with only DNS operations substituted.

        A DNS failure must remove newly created routes, retaining preexisting
        persistent/runtime routes. The BIND restore itself is not exercised.
        """
        sandbox = AnsibleRouteSandbox(tmp_path)
        existing = dict(NEW, dst='172.16.0.0/24', metric=150)
        sandbox.data['runtime'].append(existing)
        sandbox.data['persistent'].append({'network': existing['dst'], 'gateway': existing['gateway'], 'metric': 150})
        sandbox.save()
        initial = copy.deepcopy(sandbox.data)
        main = yaml.safe_load((ROOT / 'roles/bind9/tasks/main.yml').read_text())
        deployment = next(task for task in main if task['name'] == 'Apply DNS changes with rollback on failure')
        if failure_phase == 'reload':
            handlers = tmp_path / 'roles/bind9/handlers/main.yml'
            handlers.write_text(yaml.safe_dump([{'name': 'Reload named configuration',
                                'ansible.builtin.fail': {'msg': 'Injected named reload failure'}}]))
        for task in deployment['block']:
            if 'ansible.builtin.import_tasks' in task and '/front_' not in task['ansible.builtin.import_tasks']:
                del task['ansible.builtin.import_tasks']
                if failure_phase == 'health' and task['name'] == 'Verify DNS queries and SOA consistency':
                    task['ansible.builtin.fail'] = {'msg': 'Injected DNS health failure'}
                elif failure_phase == 'reload' and task['name'] == 'Start or reload BIND':
                    # Keep the real flush_handlers entry point. Only systemd
                    # operations are replaced; the failure comes from a handler.
                    service = yaml.safe_load((ROOT / 'roles/bind9/tasks/tasks.d/service.yml').read_text())
                    task['block'] = [entry for entry in service if 'ansible.builtin.meta' in entry]
                else:
                    task['ansible.builtin.debug'] = {'msg': 'Isolated DNS operation substitute'}
                    if failure_phase == 'reload' and task['name'] == 'Install canonical named.conf':
                        task['changed_when'] = True
                        task['notify'] = 'Reload named configuration'
        for task in deployment['rescue']:
            if 'ansible.builtin.command' in task:
                del task['ansible.builtin.command']
                task['ansible.builtin.command'] = {'argv': ['/bin/true']}
            if 'ansible.builtin.systemd' in task:
                del task['ansible.builtin.systemd']
                task['ansible.builtin.debug'] = {'msg': 'BIND restore substitute'}
        path = tmp_path / 'roles/bind9/tasks/tests_dns_rollback.yml'
        path.write_text(yaml.safe_dump([deployment]))
        result = sandbox.run('tests_dns_rollback.yml', tags='deploy', extra_vars={'bind_front_routes_defer_commit': True,
                              'bind_backup_root': '/unused', 'bind_recovery_archive': 'unused.tar.gz'})
        assert (result.returncode != 0) == bool(failure_phase), result.stdout
        data = sandbox.read()
        if failure_phase:
            assert data['persistent'] == initial['persistent'], result.stdout
            assert data['runtime'] == initial['runtime'], result.stdout
            assert 'Remove owned persistent additions' in result.stdout
        else:
            assert data['runtime'] == initial['runtime'] + [NEW], result.stdout
            assert len(data['persistent']) == 2
            assert 'Commit successfully validated owned FRONT additions' in result.stdout
        assert not (sandbox.state_root / 'front-routes.lock').exists()

    def test_empty_mutation_stdout_and_second_run_idempotence(self, tmp_path):
        sandbox = AnsibleRouteSandbox(tmp_path)
        first = sandbox.run()
        assert first.returncode == 0, first.stdout
        data = sandbox.read()
        assert data['runtime'] == BASE + [NEW]
        assert len(data['operations']) == 2
        assert not (sandbox.state_root / 'front-routes.lock').exists()
        second = sandbox.run()
        assert second.returncode == 0, second.stdout
        assert sandbox.read()['operations'] == data['operations']
        assert 'changed=0' in second.stdout

    def test_check_mode_computes_a_plan_without_mutating_routes_or_state(self, tmp_path):
        sandbox = AnsibleRouteSandbox(tmp_path)
        result = sandbox.run(check=True)
        assert result.returncode == 0, result.stdout
        assert sandbox.read() == sandbox.data
        assert not sandbox.state_root.exists()

    def test_native_active_failure_rolls_back_only_added_profile_entry(self, tmp_path):
        sandbox = AnsibleRouteSandbox(tmp_path)
        sandbox.data['fail_active_once'] = True
        sandbox.save()
        result = sandbox.run()
        assert result.returncode != 0, result.stdout
        data = sandbox.read()
        assert data['persistent'] == [] and data['runtime'] == BASE
        assert not (sandbox.state_root / 'front-routes.lock').exists()
        assert len(data['operations']) == 3  # add persistent, failed active, remove persistent.

    def test_verification_failure_rolls_back_profile_and_active_additions(self, tmp_path):
        sandbox = AnsibleRouteSandbox(tmp_path)
        sandbox.data['bad_lookup'] = True
        sandbox.save()
        result = sandbox.run()
        assert result.returncode != 0, result.stdout
        data = sandbox.read()
        assert data['persistent'] == [] and data['runtime'] == BASE
        assert not (sandbox.state_root / 'front-routes.lock').exists()
        assert len(data['operations']) == 4

    def test_interrupted_operation_requires_explicit_recovery(self, tmp_path):
        sandbox = AnsibleRouteSandbox(tmp_path)
        journal = sandbox.state_root / 'front-routes.lock'
        journal.mkdir(parents=True, mode=0o700)
        sandbox.state_root.chmod(0o700)
        file = journal / 'plan.json'
        file.write_text(json.dumps(plan()))
        file.chmod(0o600)
        sandbox.data['runtime'].append(NEW)
        sandbox.data['persistent'] = [{'network': NEW['dst'], 'gateway': NEW['gateway'], 'metric': NEW['metric']}]
        sandbox.save()
        blocked = sandbox.run()
        assert blocked.returncode != 0
        assert 'routes-recover' in blocked.stdout
        recovered = sandbox.run('tasks.d/front_routes_recover.yml')
        assert recovered.returncode == 0, recovered.stdout
        assert sandbox.read()['runtime'] == BASE
        assert sandbox.read()['persistent'] == []
        assert not journal.exists()

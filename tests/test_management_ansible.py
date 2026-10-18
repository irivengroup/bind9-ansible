"""Run real Ansible role/module transport against a private relocated test tree.

Only paths, named's test GID and external runtime executables are substituted.
No host DNS/network state is modified. CLI doubles do not qualify SELinux/RNDC.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from test_management import Lab

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != 'posix' or os.geteuid() != 0 or not shutil.which('ansible-playbook'), reason='Requires isolated Linux root and Ansible')


class Runner:
    """Relocate privileged paths, keeping actual Ansible module/task behavior."""

    def __init__(self, folder, native):
        self.folder = folder
        (folder / 'host').mkdir()
        self.lab = Lab(folder / 'host')
        self.role = folder / 'roles/bind9'
        shutil.copytree(ROOT / 'roles/bind9', self.role, ignore=shutil.ignore_patterns('__pycache__'))
        utility = self.role / 'module_utils/bind9_management.py'
        utility.write_text(utility.read_text().replace("root='/'", 'root=' + repr(str(self.lab.root))))
        library = self.role / 'library/bind9_manage.py'
        library.write_text(library.read_text().replace('import grp\n', '').replace("grp.getgrnam('named').gr_gid", '0'))
        self.bin = folder / 'bin'
        self.bin.mkdir()
        self.python = self.bin / 'python'
        self.python.symlink_to(sys.executable)
        for executable, content in [('checkconf', 'exit 0'), ('rndc', 'exit 0'), ('restorecon', 'exit 0'), ('sync', 'exit 0'), ('dig', "printf ';; status: NOERROR\\nlocalhost. 300 IN A 127.0.0.1\\n'")]:
            path = self.bin / executable
            path.write_text('#!/bin/sh\n' + content + '\n')
            path.chmod(0o755)
        utility.write_text(utility.read_text().replace("'/usr/sbin/named-checkconf'", repr(str(self.bin / 'checkconf'))).replace("'/usr/sbin/rndc'", repr(str(self.bin / 'rndc'))).replace("'/sbin/restorecon'", repr(str(self.bin / 'restorecon'))).replace("'/usr/bin/sync'", repr(str(self.bin / 'sync'))).replace("'/usr/bin/dig'", repr(str(self.bin / 'dig'))))
        self.lab.put('/usr/local/sbin/bind-configtool', '#!/bin/sh\necho BACKUP_CREATED /var/backups/Bind9-Ansible/bind/test.tar.gz\n', 0o700)
        for relative in ('main.yml', 'tasks.d/deploy/guard.yml', 'tasks.d/deploy/seal.yml', 'tasks.d/deploy/lease_release.yml', 'tasks.d/deploy/hibernate.yml'):
            path = self.role / 'tasks' / relative
            text = path.read_text()
            for prefix in ('/var/lib/Bind9-Ansible', '/etc/named.conf', '/var/named'):
                text = text.replace(prefix, str(self.lab.root) + prefix)
            text = text.replace('/usr/sbin/named-checkconf', str(self.bin / 'checkconf'))
            path.write_text(text)
        self.env = dict(os.environ, ANSIBLE_CONFIG=str(ROOT / 'ansible.cfg'), ANSIBLE_ROLES_PATH=str(folder / 'roles'), ANSIBLE_JINJA2_NATIVE=str(native).lower())

    def role_task(self, tasks_from):
        return {'include_role': {'name': 'bind9', 'tasks_from': tasks_from, 'public': True}}

    def run(self, tasks, variables=None, check=False, tags=None, success=True):
        scope = dict(ansible_python_interpreter=str(self.python), ansible_distribution='RedHat', ansible_distribution_major_version='8',
                     bind_primary_host='localhost', bind_secondary_host='peer', bind_tsig_key_name='site-transfer')
        scope.update(variables or {})
        inventory = self.folder / 'hosts.ini'
        inventory.write_text('[dns_primary]\nlocalhost ansible_connection=local bind_front_ip=192.0.2.11 bind_back_ip=10.0.0.11\n[dns_secondary]\npeer bind_front_ip=192.0.2.12 bind_back_ip=10.0.0.12\n')
        play = self.folder / 'test.yml'
        play.write_text(yaml.safe_dump([dict(hosts='localhost', gather_facts=False, vars=scope, tasks=tasks)], sort_keys=False))
        command = ['ansible-playbook', '-i', str(inventory), str(play)]
        if check:
            command.append('--check')
        if tags:
            command.extend(['--tags', tags])
        result = subprocess.run(command, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        assert (result.returncode == 0) is success, result.stdout
        return result.stdout


@pytest.mark.parametrize('native', [False, True])
def test_real_module_crud_transport_and_safe_conflict_message(tmp_path, native):
    runner = Runner(tmp_path, native)
    request = dict(resource='option', action='read', name='minimal-responses')
    tasks = [runner.role_task('tasks.d/manage/main.yml'),
             {'assert': {'that': ["bind_management_result.checksum | length == 64", 'not bind_management_result.changed']}},
             {'set_fact': {'test_previous_revision': '{{ bind_management_result.checksum }}', 'bind_manage': dict(request, action='update', value='no', expected_checksum='{{ bind_management_result.checksum }}')}},
             runner.role_task('tasks.d/manage/main.yml'),
             {'assert': {'that': ['bind_management_result.changed', 'bind_management_result.checksum != test_previous_revision']}},
             {'block': [runner.role_task('tasks.d/manage/main.yml'), {'fail': {'msg': 'stale checksum was accepted'}}],
              'rescue': [{'assert': {'that': ["'Checksum missing or changed' in ansible_failed_result.msg"]}}]}]
    output = runner.run(tasks, {'bind_manage': request})
    assert 'secret "' not in output
    assert 'minimal-responses no;' in (runner.lab.root / 'etc/named.conf').read_text()


@pytest.mark.parametrize('native', [False, True])
def test_real_deployment_guard_seal_lock_and_busy_lease(tmp_path, native):
    runner = Runner(tmp_path, native)
    state = runner.lab.root / 'var/lib/Bind9-Ansible'
    guard, seal = runner.role_task('tasks.d/deploy/guard.yml'), runner.role_task('tasks.d/deploy/seal.yml')
    release = runner.role_task('tasks.d/deploy/lease_release.yml')
    rejection = {'block': [guard, {'fail': {'msg': 'guard unexpectedly accepted'}}], 'rescue': [{'assert': {'that': ['not bind_deployment_lease_acquired']}}]}
    runner.run([guard, {'set_fact': {'bind_deployment_health_completed': True}}, seal, release, rejection], {'bind_managed_zones': [], 'hibernate_deployment': True})
    payload = json.loads((state / '.deployment.lock').read_text())
    assert len(payload['sha256']) == 64 and payload['payload']['host'] == 'localhost'
    # Disabling future seal generation never bypasses an already published seal.
    runner.run([rejection], {'hibernate_deployment': False})
    (state / '.deployment.lock').unlink()
    runner.run([guard, release], {'hibernate_deployment': False})
    (state / '.operation.lock').mkdir(mode=0o700)
    runner.run([rejection], {'hibernate_deployment': False})
    assert (state / '.operation.lock').is_dir()


def test_seal_requires_successful_health(tmp_path):
    runner = Runner(tmp_path, False)
    tasks = [{'block': [runner.role_task('tasks.d/deploy/seal.yml'), {'fail': {'msg': 'unverified deployment sealed'}}],
              'rescue': [{'assert': {'that': ["'health checks were not completed' in ansible_failed_result.msg"]}}]}]
    runner.run(tasks, {'bind_managed_zones': [], 'hibernate_deployment': True})
    assert not (runner.lab.root / 'var/lib/Bind9-Ansible/.deployment.lock').exists()

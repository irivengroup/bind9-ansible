"""Execute the actual two-phase playbook on two isolated local host trees.

Only privileged roots, named GID and runtime commands are relocated/doubled.
This verifies orchestration and transport, not system RHEL or TSIG replication.
"""
import json
import os
import shutil
import subprocess

import pytest
import yaml

from test_management import CONF, Lab
from test_management_ansible import ROOT, Runner
from test_management_batch import mixed

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.geteuid() != 0 or not shutil.which('ansible-playbook'),
                                reason='Requires isolated Linux root and Ansible')


class PairRunner(Runner):
    """Use one real Ansible process and separate protected files per alias."""

    def __init__(self, folder, native):
        super(PairRunner, self).__init__(folder, native)
        self.secondary = Lab(folder / 'secondary')
        self.secondary.put('/etc/named.conf', CONF.replace('zone "example.test" { type master;', 'zone "example.test" { type slave;'))
        self.secondary.put('/usr/local/sbin/bind-configtool', '#!/bin/sh\necho BACKUP_CREATED /var/backups/Bind9-Ansible/bind/test.tar.gz\n', 0o700)
        library = self.role / 'library/bind9_manage.py'
        library.write_text(library.read_text().replace('context, check=module.check_mode)',
            'context, root=context["test_root"], check=module.check_mode)'))
        tasks = self.role / 'tasks/tasks.d/manage/main.yml'
        tasks.write_text(tasks.read_text().replace('context:\n', 'context:\n          test_root: "{{ bind_test_root }}"\n')
                         .replace('/usr/bin/dig', str(self.bin / 'dig')))
        dig = self.bin / 'dig'
        dig.write_text('#!' + str(self.python) + '\nimport pathlib,re,sys\n'
            'name,kind=sys.argv[-2:]\n'
            'if kind=="A": print(";; status: NOERROR\\nlocalhost. 60 IN A 127.0.0.1")\n'
            'else:\n'
            ' path=pathlib.Path(' + repr(str(self.lab.root / 'var/named')) + ') / ("empty-root.zone" if name=="." else "zones/"+name.rstrip(".")+".zone")\n'
            ' serial=re.search(r"\\(\\s*(\\d+)",path.read_text()).group(1)\n'
            ' print(";; status: NOERROR\\n;; flags: qr aa; QUERY: 1, ANSWER: 1\\n"+name+" 60 IN SOA ns. host. "+serial+" 3600 900 604800 300")\n')
        dig.chmod(0o755)
        self.inventory = folder / 'pair.ini'
        self.inventory.write_text('[site1]\nprimary\nsecondary\n[dns_primary]\nprimary\n[dns_secondary]\nsecondary\n'
            '[dns_deployment_order]\nprimary\nsecondary\n[all:vars]\nansible_connection=local\n'
            'bind_environment=test\nbind_site_name=site1\n'
            '[primary_vars]\nprimary bind_front_ip=192.0.2.11 bind_back_ip=10.0.0.11 bind_test_root=' + str(self.lab.root) + '\n'
            '[secondary_vars]\nsecondary bind_front_ip=192.0.2.12 bind_back_ip=10.0.0.12 bind_test_root=' + str(self.secondary.root) + '\n')
        play = yaml.safe_load((ROOT / 'playbooks/bind9-management.yml').read_text())
        play[-1]['post_tasks'] = [{'copy': {'content': '{{ bind_management_result | to_json }}',
            'dest': '{{ bind_test_root }}/result.json', 'mode': '0600'}, 'no_log': True}]
        self.play = folder / 'pair.yml'
        self.play.write_text(yaml.safe_dump(play, sort_keys=False))

    def invoke(self, request, approvals=None, check=False, limit=None, success=True):
        variables = dict(bind_manage=request, ansible_distribution='RedHat', ansible_distribution_major_version='8',
                         ansible_python_interpreter=str(self.python), bind_tsig_key_name='site-transfer', ansible_become=False)
        variables.update(approvals or {})
        extra = self.folder / 'variables.json'
        extra.write_text(json.dumps(variables))
        command = ['ansible-playbook', '-i', str(self.inventory), str(self.play), '-e', '@' + str(extra)]
        if check:
            command.append('--check')
        if limit:
            command += ['--limit', limit]
        result = subprocess.run(command, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True, timeout=180)
        assert (result.returncode == 0) is success, result.stdout
        return result.stdout

    def approval(self):
        results = {host: json.loads((root / 'result.json').read_text())
                   for host, root in [('primary', self.lab.root), ('secondary', self.secondary.root)]}
        return dict(bind_manage_checksums={host: value['checksum'] for host, value in results.items()},
                    bind_manage_plan_checksums={host: value['plan_checksum'] for host, value in results.items()})


@pytest.mark.parametrize('native', [False, True])
def test_actual_playbook_applies_mixed_batch_and_preserves_legacy_transport(tmp_path, native):
    runner = PairRunner(tmp_path, native)
    operations = mixed()[:4] + [dict(resource='record', action='create', zone='new.test', name='note', type='TXT', values=['"texte é"'])]
    request = dict(resource='batch', action='read', operations=operations)
    runner.invoke(request)
    approvals = runner.approval()
    runner.invoke(dict(request, action='apply'), approvals, check=True)
    assert (runner.lab.root / 'etc/named.conf').read_text() == CONF
    runner.invoke(dict(request, action='apply'), approvals)
    assert 'minimal-responses no;' in (runner.lab.root / 'etc/named.conf').read_text()
    assert '192.0.2.90' in (runner.lab.root / 'var/named/zones/new.test.zone').read_text()
    assert not (runner.secondary.root / 'var/named/slaves/new.test.zone').exists()
    assert 'type slave;' in (runner.secondary.root / 'etc/named.conf').read_text()
    assert len(list((runner.lab.root / 'var/backups').rglob('manifest.json'))) == 1
    assert len(list((runner.secondary.root / 'var/backups').rglob('manifest.json'))) == 1


@pytest.mark.parametrize('failure', ['revision', 'plan'])
def test_secondary_preflight_failure_prevents_every_primary_write(tmp_path, failure):
    runner = PairRunner(tmp_path, False)
    request = dict(resource='batch', action='read', operations=mixed()[:4])
    runner.invoke(request)
    approvals = runner.approval()
    key = 'bind_manage_checksums' if failure == 'revision' else 'bind_manage_plan_checksums'
    approvals[key]['secondary'] = '0' * 64
    output = runner.invoke(dict(request, action='apply'), approvals, success=False)
    assert 'checksum' in output.lower()
    assert (runner.lab.root / 'etc/named.conf').read_text() == CONF
    assert not (runner.lab.root / 'var/named/zones/new.test.zone').exists()
    assert not (runner.lab.root / 'var/backups').exists()
    assert not (runner.secondary.root / 'var/backups').exists()


def test_secondary_only_batch_with_records_is_refused_before_mutation(tmp_path):
    runner = PairRunner(tmp_path, False)
    output = runner.invoke(dict(resource='batch', action='read', operations=mixed()[:4]), limit='secondary', success=False)
    assert 'must include its primary first' in output
    assert not (runner.secondary.root / 'var/backups').exists()


@pytest.mark.parametrize('native', [False, True])
def test_delivered_batch_example_uses_the_same_operations_for_read_and_check(tmp_path, native):
    runner = Runner(tmp_path, native)
    example = yaml.safe_load((ROOT / 'examples/management/batch.yml').read_text())
    tasks = [runner.role_task('tasks.d/manage/main.yml'),
        {'assert': {'that': ['bind_management_result.operations_count == 5',
                            "bind_management_result.action == 'read'"]}},
        {'set_fact': {'bind_manage_batch_action': 'apply',
                      'bind_manage_checksums': {'localhost': '{{ bind_management_result.checksum }}'},
                      'bind_manage_plan_checksums': {'localhost': '{{ bind_management_result.plan_checksum }}'}}},
        {'block': [runner.role_task('tasks.d/manage/main.yml')], 'check_mode': True},
        {'assert': {'that': ['bind_management_result.check_mode', 'bind_management_result.changed']}}]
    runner.run(tasks, example)
    assert (runner.lab.root / 'etc/named.conf').read_text() == CONF
    assert not (runner.lab.root / 'var/backups').exists()

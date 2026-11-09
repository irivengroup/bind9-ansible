"""Native public deployment dispatch restricted to non-provisioning tag paths."""
from pathlib import Path

import yaml

from native_ansible_runner import NativeContainerRun, NativeTaskRun


class NativeDeploymentPlayRun(NativeContainerRun):
    """Exercise original role/tag dispatch without permitting a DNS deployment.

    Every source action is inspected, but runtime admission is limited to
    preflight, routes-disabled, reserved-never and already-sealed hibernation paths. A private operation lease may be
    acquired and released. DNS publication, network changes, package installs
    and service operations are not qualified by these dispatch scenarios.
    """

    FILES = {'main.yml'}
    TARGETS = ('/var/lib/Bind9-Ansible',)
    CANONICAL_ROLE_SEARCH = True
    SAFE_ACTIONS = NativeTaskRun.SAFE_ACTIONS | {
        'ansible.builtin.command', 'ansible.builtin.stat', 'ansible.builtin.file',
        'ansible.builtin.copy', 'ansible.builtin.template', 'ansible.builtin.dnf',
        'ansible.builtin.find', 'ansible.builtin.systemd', 'ansible.builtin.script',
        'ansible.builtin.slurp', 'ansible.builtin.wait_for', 'ansible.builtin.fail',
        'ansible.builtin.include_tasks'}
    SAFE_META = NativeTaskRun.SAFE_META | {'flush_handlers'}

    def __init__(self, filename, variables, **options):
        super().__init__(filename, variables, **options)
        self.native_playbook = self.root / 'playbooks/bind9-deploy.yml'
        plays = yaml.safe_load(self.native_playbook.read_text(encoding='utf-8'))
        if (len(plays) != 1 or set(plays[0]) != {
                'name', 'hosts', 'become', 'gather_facts', 'order', 'serial', 'any_errors_fatal', 'roles'} or
                plays[0]['hosts'] != 'dns_deployment_order' or plays[0]['roles'] != ['bind9'] or
                plays[0]['order'] != 'inventory' or type(plays[0]['serial']) is not int or
                plays[0]['serial'] != 1 or plays[0]['any_errors_fatal'] is not True):
            raise ValueError('Native dispatch fixture refuses an unreviewed deployment play envelope')

    def safe_context(self, task):
        return (task.get('become', False) is False and task.get('delegate_to', 'localhost') in (
            'localhost', '{{ bind_primary_host }}', '{{ inventory_hostname }}'))

    def check_tasks(self, tasks, seen, basedir):
        super().check_tasks(tasks, seen, basedir)
        for task in tasks:
            if 'ansible.builtin.include_tasks' in task and task['ansible.builtin.include_tasks'] != {
                    'file': '{{ role_path }}/tasks/tasks.d/deploy/front_return_routes.yml',
                    'apply': {'tags': ['deploy']}}:
                raise ValueError('Native dispatch fixture refuses another dynamic import')

    def run(self):
        admitted_hibernation = (self.run_tags == ['hibernate'] and
            self.variables.get('hibernate_deployment') is True and
            Path('/var/lib/Bind9-Ansible/.deployment.lock').is_file())
        if ((self.run_tags not in (['preflight'], ['routes'], ['never']) and not admitted_hibernation) or
                self.variables.get('bind_front_routes_enabled') is not False):
            raise ValueError('Native dispatch fixture permits only preflight, disabled routes, reserved never or an existing hibernation seal')
        status, result = super().run()
        if any(Path(name).exists() for name in ('/etc/named.conf', '/var/named', '/etc/rndc.key')):
            raise AssertionError('A non-provisioning tag path published DNS files')
        return status, result

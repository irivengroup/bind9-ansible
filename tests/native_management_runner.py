"""Run the canonical management orchestration and module without relocation."""
import grp
import yaml

from ansible.plugins.loader import module_loader, module_utils_loader

from native_ansible_runner import NativeCliRun


class NativeManagementRun(NativeCliRun):
    """Own management files and its DNS command boundary in a disposable root.

    The real Ansible module packages the canonical utility. No task, module,
    privileged path, group lookup or request/result is rewritten or doubled.
    Tests admit only read, unchanged plans or refusals before publication; the
    existing separate native integration suite qualifies actual DNS commits.
    """

    SAFE_ACTIONS = NativeCliRun.SAFE_ACTIONS | {'bind9_manage', 'ansible.builtin.fail'}
    FILES = {'tasks.d/manage/main.yml', 'manage.yml'}
    COMMANDS = ('/usr/bin/dig',)
    TARGETS = COMMANDS + ('/etc/named.conf', '/var/named', '/var/lib/Bind9-Ansible',
                          '/var/backups/Bind9-Ansible')

    def run(self):
        grp.getgrnam('named')
        return super().run()

    def execute_native(self, runner):
        """Use the native module resolver and AnsiballZ dependency packaging."""
        module_loader.add_directory(str(self.role / 'library'))
        module_utils_loader.add_directory(str(self.role / 'module_utils'))
        return super().execute_native(runner)


class NativeManagementPlayRun(NativeManagementRun):
    """Execute the unchanged public two-host playbook in its real queue manager.

    Both inventory aliases use this disposable root, so scenarios are limited
    to reads, unchanged approvals and refusals. No independent-host filesystem
    or DNS replication claim follows from these orchestration observations.
    """

    FILES = {'manage.yml'}
    CANONICAL_ROLE_SEARCH = True

    def __init__(self, filename, variables, **options):
        super().__init__(filename, variables, **options)
        self.native_playbook = self.root / 'playbooks/bind9-management.yml'
        plays = yaml.safe_load(self.native_playbook.read_text(encoding='utf-8'))
        allowed = {'name', 'hosts', 'become', 'gather_facts', 'order', 'serial', 'any_errors_fatal', 'tasks'}
        if len(plays) != 2 or any(set(play) - allowed or play['hosts'] != 'dns_deployment_order' or
                play['serial'] != 1 or play['order'] != 'inventory' or play['any_errors_fatal'] is not True
                for play in plays):
            raise ValueError('Native management play fixture refuses an unreviewed host/order envelope')
        for play in plays:
            self.check_tasks(play['tasks'], set(), self.native_playbook.parent)

    SAFE_ACTIONS = NativeManagementRun.SAFE_ACTIONS | {'ansible.builtin.setup', 'ansible.builtin.include_role'}

    def check_tasks(self, tasks, seen, basedir):
        super().check_tasks(tasks, seen, basedir)
        for task in tasks:
            if 'ansible.builtin.include_role' in task and task['ansible.builtin.include_role'] != {
                    'name': 'bind9', 'tasks_from': 'manage.yml'}:
                raise ValueError('Native management play fixture refuses another dynamic role')


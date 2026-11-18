"""Execute the canonical protected DNS transaction in a disconnected root.

Only the five original mappings from backup through protected deployment are
selected. Their source positions, imports, tags and rescue remain untouched;
the other mappings remain in the complete production coverage denominator.
"""
import json
import os
from pathlib import Path
import shutil

from ansible.playbook.play import Play
import yaml

from native_ansible_runner import NativeContainerRun, NativeMigrationRun, NativeProvisionRun, NativeTaskRun


class NativeTransactionRun(NativeContainerRun):
    """Use real Ansible/file/archive operations and explicit private CLI replies.

    This exercises transaction ordering and recovery. BIND parsing, DNS packets,
    SELinux enforcement and service state have separate native acceptance tests.
    """

    FILES = {'tasks.d/deploy/main.yml'}
    ROLE_ASSETS = True
    LOAD_HANDLERS = True
    SAFE_ACTIONS = NativeProvisionRun.SAFE_ACTIONS | {
        'ansible.builtin.find', 'ansible.builtin.slurp', 'ansible.builtin.script',
        'ansible.builtin.fail', 'ansible.builtin.include_tasks'}
    SAFE_META = NativeTaskRun.SAFE_META | {'flush_handlers'}
    COMMANDS = ('/usr/sbin/named-checkconf', '/usr/sbin/named-checkzone', '/usr/bin/dig',
                '/usr/local/bin/semanage', '/usr/local/bin/restorecon', '/usr/local/bin/ls')
    TARGETS = tuple(dict.fromkeys(NativeProvisionRun.TARGETS + NativeMigrationRun.TARGETS + COMMANDS + (
        '/usr/local/sbin/named-transaction', '/usr/local/sbin/reverse-serial',
        '/var/log/named', '/var/lib/Bind9-Ansible', '/etc/profile.d/bind9-sbin-path.sh')))
    NAMES = (
        'Back up full BIND configuration before changes',
        'Prepare managed directories',
        'Find existing reverse zone files before deployment',
        'Snapshot managed runtime files before deployment',
        'Apply DNS changes with rollback on failure',
    )

    @classmethod
    def selected(cls, mappings):
        selected = [task for task in mappings if task.get('name') in cls.NAMES]
        if tuple(task['name'] for task in selected) != cls.NAMES:
            raise ValueError('Native transaction requires the exact original ordered mappings')
        return selected

    def check_file(self, path, seen):
        main = self.role / 'tasks/tasks.d/deploy/main.yml'
        if path.resolve() != main:
            return super().check_file(path, seen)
        self.check_tasks(self.selected(yaml.safe_load(path.read_text(encoding='utf-8'))), seen | {main}, main.parent)

    def safe_context(self, task):
        if super().safe_context(task):
            return True
        return ('become' not in task and task.get('delegate_to') == '{{ bind_primary_host }}' and
            task.get('name') == 'Read actual reconciled SOA serials from primary zone files') or (
            task.get('become') is False and 'delegate_to' not in task and
            task.get('name') == 'Read the real SSH peer before any route mutation' and
            task.get('ansible.builtin.command', {}).get('argv') == ['printenv', 'SSH_CONNECTION'])

    def check_tasks(self, tasks, seen, basedir):
        super().check_tasks(tasks, seen, basedir)
        for task in tasks:
            include = task.get('ansible.builtin.include_tasks')
            if include is not None and include != {
                    'file': '{{ role_path }}/tasks/tasks.d/deploy/front_return_routes.yml', 'apply': {'tags': ['deploy']}}:
                raise ValueError('Native transaction refuses an unreviewed dynamic import')
            script = task.get('ansible.builtin.script')
            if script is not None and script != {
                    'cmd': '{{ (role_path ~ "/files/bind-front-routes.sh") | quote }} {{ bind_front_arguments | map("quote") | join(" ") }}',
                    'executable': '/bin/bash'}:
                raise ValueError('Native transaction requires the native FRONT interpreter')
            command = task.get('ansible.builtin.command')
            if command is None:
                continue
            argv = command.get('argv')
            permitted = self.COMMANDS + ('systemctl', 'dig', 'semanage', 'restorecon', 'ls', '/usr/local/sbin/bind-configtool')
            if not ((isinstance(argv, list) and argv and argv[0] in permitted) or argv == ['printenv', 'SSH_CONNECTION'] or
                    (isinstance(argv, str) and argv.strip().startswith("{{ ['/usr/local/sbin/reverse-serial',"))):
                raise ValueError('Native transaction refuses an unreviewed command envelope')

    def transport_variables(self):
        return dict(super().transport_variables(), ansible_host='127.0.0.1')

    def run(self):
        effective = dict(self.inventory_hosts.get('localhost', {}), **self.variables)
        if (effective.get('bind_backup_root') != NativeMigrationRun.ROOT or
                effective.get('bind_primary_host') != 'localhost'):
            raise ValueError('Native transaction requires its owned backup root and local primary')
        if set(self.variables).intersection({'bind_recovery_archive', 'bind_existing_reverse_files',
                'bind_runtime_files_before', 'bind_runtime_content_before', 'bind_front_transaction_token',
                'bind_front_route_transaction_pending', 'bind_front_route_rollback_attempted'}):
            raise ValueError('Native transaction refuses extra-vars that override its own runtime facts')
        for name in ('bind_authoritative_zones', 'bind_generated_reverse_zones'):
            for zone in effective.get(name, []):
                domain = zone.get('domain', '')
                if not domain or '..' in domain or any(character not in
                        'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for character in domain):
                    raise ValueError('Native transaction requires literal owned DNS zone names')
        for command in self.COMMANDS:
            path = Path(command).resolve(strict=True)
            path.relative_to('/tmp')
            info = path.stat()
            if not path.is_file() or info.st_uid != 0 or info.st_mode & 0o022:
                raise ValueError('Native transaction requires private exact command boundaries')
        if shutil.which('systemctl') != '/usr/local/bin/systemctl':
            raise ValueError('Native transaction requires its owned service boundary')
        if effective.get('bind_front_routes_enabled'):
            from canonical_shell_lab import CanonicalFrontEnvironment
            if (os.environ.get('BIND9_CANONICAL_BASH_LAB') != '1' or
                    not CanonicalFrontEnvironment.links or
                    any(not CanonicalFrontEnvironment.owned(path) for path in CanonicalFrontEnvironment.links)):
                raise ValueError('Native transaction requires the canonical FRONT command boundaries')
            network = Path(os.environ.get('BIND_NETWORK_DOUBLE_STATE', '/missing')).resolve(strict=True)
            network.relative_to('/tmp')
            if network.stat().st_dev != Path('/').stat().st_dev or not isinstance(json.loads(network.read_text()), dict):
                raise ValueError('Native transaction refuses an unowned network fixture')
        return super().run()

    def execute_native(self, runner):
        source = str(self.role / 'tasks/tasks.d/deploy/main.yml')
        selected = self.selected(runner._loader.load_from_file(source))
        if not all(task.ansible_pos[0] == source for task in selected):
            raise ValueError('Native transaction requires original production source positions')
        self.check_tasks(selected, set(), Path(source).parent)
        # The normal PlaybookExecutor sets this while loading its playbook.
        # Preserve that same play-relative files/templates lookup for the
        # directly loaded original transaction mappings.
        runner._loader.set_basedir(str(Path(runner._playbooks[0]).parent))
        play = Play.load({'hosts': 'localhost', 'gather_facts': False, 'tasks': selected,
            'handlers': [{'ansible.builtin.import_tasks': str(self.role / 'handlers/main.yml')}]},
            loader=runner._loader, variable_manager=runner._variable_manager)
        return runner._tqm.run(play)

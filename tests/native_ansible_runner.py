"""Run canonical read-only task files through genuine local Ansible workers."""
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import tempfile

from ansible import constants, context, release
from ansible.executor.playbook_executor import PlaybookExecutor
from ansible.inventory.manager import InventoryManager
from ansible.module_utils.common.collections import ImmutableDict
from ansible.parsing.dataloader import DataLoader
from ansible.playbook.play import Play
from ansible.plugins.callback import CallbackBase
from ansible.plugins.loader import filter_loader
try:
    from ansible.plugins.loader import init_plugin_loader
except ImportError:
    # These inspected controllers expose different native startup APIs.
    # Keep real collection loading; an unknown API/version still fails closed.
    if release.__version__ == '2.9.27':
        init_plugin_loader = None
    elif release.__version__ in ('2.13.13', '2.14.18'):
        from ansible.plugins.loader import _configure_collection_loader as init_plugin_loader
    else:
        raise
from ansible.vars.manager import VariableManager
from ansible.utils.vars import load_options_vars
import yaml


class NativeResults(CallbackBase):
    """Record outcomes and synthetic facts; never dump invocation arguments."""

    def __init__(self):
        super().__init__()
        self.ok, self.failed, self.skipped, self.facts = [], [], [], {}
        self.registers = {}

    def v2_runner_on_ok(self, result):
        self.ok.append(result._task.name)
        self.record_facts(result._result)
        self.record_register(result)

    def record_register(self, result):
        """Preserve actual success/refusal registers after native censorship."""
        data = result._result
        if result._task.register and not (data.get('_ansible_no_log') and 'censored' not in data):
            self.registers[result._task.register] = copy.deepcopy(data)

    def record_facts(self, data):
        """Observe native public facts; protected callback data stays protected."""
        if not data.get('_ansible_no_log') and 'censored' not in data:
            facts = data.get('ansible_facts', {})
            if isinstance(facts, dict):
                self.facts.update(copy.deepcopy(facts))

    def v2_runner_item_on_ok(self, result):
        # Ansible reports set_fact loop results through item callbacks, not the
        # aggregate's top-level ansible_facts. Keep actual event ordering.
        self.record_facts(result._result)

    def v2_runner_on_failed(self, result, ignore_errors=False):
        self.failed.append(result._task.name)
        self.record_register(result)

    def v2_runner_on_skipped(self, result):
        self.skipped.append(result._task.name)


class NativeTaskRun:
    """Use the native loader, iterator, executor and action plugins unchanged.

    Only assert/set_fact/debug are admitted here. This integration harness has
    no package, command, service, remote host, filesystem-publication or task
    interpretation double. The callbacks observe the actual native results.
    """

    SAFE_ACTIONS = {'ansible.builtin.assert', 'ansible.builtin.set_fact', 'ansible.builtin.debug'}
    SAFE_META = {'noop', 'end_play', 'clear_facts'}
    ROLE_ASSETS = False
    LOAD_HANDLERS = False
    CANONICAL_ROLE_SEARCH = False

    def __init__(self, filename, variables, root=None, inventory_hosts=None, inventory_groups=None,
                 run_tags=None, check_mode=False, checks=(), notify_handler=False):
        self.root = Path(root or Path(__file__).resolve().parents[1]).resolve()
        self.role = self.root / 'roles/bind9'
        self.path = (self.role / 'tasks' / filename).resolve()
        self.path.relative_to(self.role / 'tasks')
        self.variables = copy.deepcopy(variables)
        self.inventory_hosts = copy.deepcopy(inventory_hosts or {})
        self.inventory_groups = copy.deepcopy(inventory_groups or {})
        self.run_tags, self.check_mode = list(run_tags) if run_tags is not None else ['all'], check_mode
        self.checks = list(checks)
        if type(notify_handler) is not bool or (notify_handler and not self.LOAD_HANDLERS):
            raise ValueError('Handler notification requires the inspected native handler fixture')
        self.notify_handler = notify_handler
        if not all(isinstance(value, str) for value in self.checks):
            raise ValueError('Native integration postconditions must be Ansible expressions')
        self.check_file(self.path, set())

    def check_file(self, path, seen):
        path = path.resolve()
        path.relative_to(self.role / 'tasks')
        if path in seen or len(seen) >= 32:
            raise ValueError('Native read-only harness refuses cyclic or excessive imports')
        self.check_tasks(yaml.safe_load(path.read_text(encoding='utf-8')), seen | {path}, path.parent)

    def check_tasks(self, tasks, seen, basedir):
        for task in tasks:
            actions = set(task).intersection(self.SAFE_ACTIONS | {'ansible.builtin.import_tasks',
                'ansible.builtin.import_role', 'ansible.builtin.meta'})
            if (not self.safe_context(task) or
                    (not actions and 'block' not in task) or len(actions) > 1):
                raise ValueError('Native read-only harness refuses task side effects')
            if 'ansible.builtin.import_tasks' in task:
                value = task['ansible.builtin.import_tasks']
                if not isinstance(value, str):
                    raise ValueError('Native read-only imports require a literal safe path')
                value = value.replace('{{ role_path }}', str(self.role))
                if '{{' in value or '{%' in value:
                    raise ValueError('Native read-only imports refuse dynamic paths')
                self.check_file(basedir / value, seen)
            if 'ansible.builtin.import_role' in task:
                value = task['ansible.builtin.import_role']
                if not isinstance(value, dict) or Path(value.get('name', '')).resolve() != self.role:
                    raise ValueError('Native read-only imports refuse another role')
                self.check_file(self.role / 'tasks' / value.get('tasks_from', 'main.yml'), seen)
            if 'ansible.builtin.meta' in task and task['ansible.builtin.meta'] not in self.SAFE_META:
                raise ValueError('Native read-only harness refuses meta side effects')
            for field in ('block', 'rescue', 'always'):
                if field in task:
                    self.check_tasks(task[field], seen, basedir)

    def safe_context(self, task):
        """Keep transport/privilege changes opt-in for inspected fixtures."""
        return 'delegate_to' not in task and 'become' not in task

    def transport_variables(self):
        """Keep local execution mandatory unless an inspected adapter opts in."""
        return dict(ansible_connection='local', ansible_host='localhost', ansible_become=False,
                    role_path=str(self.role), ansible_python_interpreter=sys.executable)

    def run(self):
        previous = context.CLIARGS
        previous_options = getattr(load_options_vars, 'options_vars', None)
        context.CLIARGS = ImmutableDict(connection='local', module_path=None, forks=1, become=False,
            become_method='sudo', become_user=None, check=self.check_mode, diff=False, verbosity=0,
            start_at_task=None, syntax=False, listtasks=False, listtags=False, listhosts=False,
            tags=self.run_tags, skip_tags=[])
        # The CLI initializes this cache once per process. A library-based
        # integration harness must give each invocation its own native cache,
        # otherwise later tag/check-mode cases inherit the first invocation.
        load_options_vars.options_vars = None
        try:
            return self._run_native()
        finally:
            context.CLIARGS = previous
            load_options_vars.options_vars = previous_options

    def _run_native(self):
        if init_plugin_loader is not None:
            init_plugin_loader()
        filter_loader.add_directory(str(self.role / 'filter_plugins'))
        loader = DataLoader()
        inventory = InventoryManager(loader=loader, sources=['localhost,'])
        for name, variables in self.inventory_hosts.items():
            inventory.add_host(name)
            for key, value in variables.items():
                inventory.get_host(name).set_variable(key, value)
        for name, members in self.inventory_groups.items():
            inventory.add_group(name)
            for member in members:
                inventory.add_host(member, group=name)
        host = inventory.get_host('localhost')
        host.set_variable('ansible_connection', 'local')
        host.set_variable('ansible_python_interpreter', sys.executable)
        manager = VariableManager(loader=loader, inventory=inventory)
        defaults = self.role / 'defaults/main.yml'
        default_values = yaml.safe_load(defaults.read_text(encoding='utf-8')) if defaults.exists() else {}
        # Imported task files do not load role defaults themselves. Supply
        # these only below explicitly assigned host variables; treating every
        # default as a global extra-var would mask peer inventory differences.
        for key, value in default_values.items():
            if key not in host.vars:
                host.set_variable(key, value)
        values = self.variables.copy()
        values.update(self.transport_variables())
        manager._extra_vars = values
        callbacks, runner = NativeResults(), None
        try:
            with tempfile.TemporaryDirectory(prefix='bind9-native-readonly-') as folder:
                play = Path(folder) / 'play.yml'
                if self.ROLE_ASSETS:
                    # Use Ansible's normal play-relative asset lookup. The
                    # canonical files and YAML remain byte-for-byte unchanged.
                    for name in ('files', 'templates'):
                        (Path(folder) / name).symlink_to(self.role / name, target_is_directory=True)
                tasks = [{'ansible.builtin.import_tasks': str(self.path), 'tags': ['always']}]
                if self.notify_handler:
                    # Simulate a caller's change notification, not a production
                    # action result. The real handler and its conditions run.
                    tasks.insert(0, {'name': 'Notify the inspected native handler from its unit fixture',
                        'ansible.builtin.debug': {'msg': 'Synthetic handler notification'},
                        'changed_when': True, 'notify': 'Reload named configuration'})
                if self.checks:
                    # no_log results are censored before callbacks. Assert
                    # their contracts inside Ansible, without bypassing it.
                    tasks.append({'name': 'Verify native integration postconditions',
                                  'ansible.builtin.assert': {'that': self.checks}, 'no_log': True})
                definition = {'hosts': 'localhost', 'gather_facts': False, 'tasks': tasks}
                if self.LOAD_HANDLERS:
                    definition['handlers'] = [{'ansible.builtin.import_tasks': str(self.role / 'handlers/main.yml')}]
                play.write_text(yaml.safe_dump([definition]), encoding='utf-8')
                play = getattr(self, 'native_playbook', play)
                runner = PlaybookExecutor(playbooks=[str(play)], inventory=inventory, variable_manager=manager,
                                          loader=loader, passwords={})
                runner._tqm._stdout_callback = callbacks
                status = self.execute_native(runner)
            return status, callbacks
        finally:
            if runner is not None and runner._tqm is not None:
                runner._tqm.cleanup()

    def execute_native(self, runner):
        """Keep the normal playbook executor unless an inspected scope opts in."""
        if not self.CANONICAL_ROLE_SEARCH:
            return runner.run()
        # Public-entry fixtures use native role discovery without relocating or
        # rewriting their playbooks. Do not change another case's search path.
        previous = constants.DEFAULT_ROLES_PATH
        constants.DEFAULT_ROLES_PATH = [str(self.role.parent)]
        try:
            return runner.run()
        finally:
            constants.DEFAULT_ROLES_PATH = previous


class NativePlayRun(NativeTaskRun):
    """Use a source-attributed playbook after recursively checking its actions."""

    def __init__(self, root, variables):
        super().__init__('main.yml', variables, root=root)
        self.native_playbook = self.root / 'playbooks/main.yml'
        plays = yaml.safe_load(self.native_playbook.read_text(encoding='utf-8'))
        for play in plays:
            if (play.get('hosts') != 'localhost' or play.get('gather_facts') is not False or
                    set(play) - {'name', 'hosts', 'gather_facts', 'tasks', 'roles'}):
                raise ValueError('Native read-only harness refuses unsafe play settings')
            self.check_tasks(play.get('tasks', []), set(), self.native_playbook.parent)
            for role in play.get('roles', []):
                if set(role) - {'role', 'when'} or Path(role.get('role', '')).resolve() != self.role:
                    raise ValueError('Native read-only harness refuses another role')


class NativeContainerRun(NativeTaskRun):
    """Share isolation checks for inspected native modules in disposable roots."""

    FILES = set()
    TARGETS = ()

    def __init__(self, filename, variables, check_mode=False, checks=(), notify_handler=False,
                 inventory_hosts=None, inventory_groups=None):
        if (filename not in self.FILES or os.name != 'posix' or os.geteuid() != 0 or
                os.environ.get('BIND9_NATIVE_STATE_LAB') != '1' or not Path('/.dockerenv').is_file() or
                {path.name for path in Path('/sys/class/net').iterdir()} != {'lo'}):
            raise ValueError('Native integration requires an explicitly enabled disconnected disposable root')
        for line in Path('/proc/self/mountinfo').read_text(encoding='utf-8').splitlines():
            mount = line.split()[4]
            if mount != '/' and any(target == mount or target.startswith(mount + '/') or mount.startswith(target + '/')
                                    for target in self.TARGETS):
                raise ValueError('Native integration refuses host/volume-backed test targets')
        values = variables.copy()
        run_tags = values.pop('ansible_run_tags', None)
        super().__init__(filename, values, run_tags=run_tags, check_mode=check_mode, checks=checks,
                         notify_handler=notify_handler, inventory_hosts=inventory_hosts,
                         inventory_groups=inventory_groups)


class NativeFilesystemScope:
    """Own exact fixture trees in a clean, disconnected disposable image.

    Refuse existing assets and mounted targets rather than moving distribution
    files or relying on overlay-filesystem rename behavior.
    """

    def __init__(self, runner):
        runner(next(iter(runner.FILES)), {})
        self.roots = tuple(Path(name) for name in runner.TARGETS)
        if any(left != right and left in right.parents for left in self.roots for right in self.roots):
            raise ValueError('Native fixture roots must not overlap')
        if any(root.exists() or root.is_symlink() for root in self.roots):
            raise ValueError('Native fixture requires an image with empty test-owned roots')

    def __enter__(self):
        return self.roots

    def __exit__(self, exc_type, exc_value, traceback):
        for root in self.roots:
            if root.is_symlink(): root.unlink()
            elif root.is_dir(): shutil.rmtree(str(root))
            elif root.exists(): root.unlink()


class NativeLeaseRun(NativeContainerRun):
    """Execute the two inspected lease files against a disposable Linux root.

    This integration adapter opts into native stat/file/mkdir/rmdir operations;
    it refuses hosts with network devices, unknown files and command arguments.
    It shares the genuine Ansible runner rather than interpreting any task.
    """

    SAFE_ACTIONS = NativeTaskRun.SAFE_ACTIONS | {'ansible.builtin.stat', 'ansible.builtin.file', 'ansible.builtin.command'}
    STATE = Path('/var/lib/Bind9-Ansible')
    FILES = {'tasks.d/deploy/guard.yml', 'tasks.d/deploy/lease_release.yml'}
    TARGETS = (str(STATE),)

    def check_tasks(self, tasks, seen, basedir):
        super().check_tasks(tasks, seen, basedir)
        for task in tasks:
            command = task.get('ansible.builtin.command')
            if command is not None and command.get('argv') not in (
                    ['mkdir', '-m', '0700', str(self.STATE / '.operation.lock')],
                    ['rmdir', str(self.STATE / '.operation.lock')]):
                raise ValueError('Native lease integration refuses an unreviewed command')
            action = task.get('ansible.builtin.file')
            if action is not None and (action.get('path') != str(self.STATE) or action.get('state') != 'directory'):
                raise ValueError('Native lease integration refuses an unreviewed write')


class NativePathRun(NativeContainerRun):
    """Read genuine stat metadata; all fixture writes belong to its container."""

    SAFE_ACTIONS = NativeTaskRun.SAFE_ACTIONS | {'ansible.builtin.stat'}
    FILES = {'tasks.d/path_safety.yml'}
    TARGETS = ('/etc/named.conf', '/etc/named', '/var/named', '/var/log/named',
               '/usr/local/sbin/bind-backup.sh', '/usr/local/sbin/bind-configtool.sh',
               '/usr/local/sbin/named-transaction')


class NativeVendorRun(NativeContainerRun):
    """Inspect distribution file contracts against real disposable inodes."""

    SAFE_ACTIONS = NativeTaskRun.SAFE_ACTIONS | {'ansible.builtin.stat'}
    FILES = {'tasks.d/native_loopback.yml'}
    TARGETS = ('/etc/named.rfc1912.zones', '/etc/named.root.key', '/var/named', '/etc/crypto-policies')


class NativeSnapshotRun(NativeContainerRun):
    """Snapshot actual files; only the read-only systemctl boundary is doubled."""

    SAFE_ACTIONS = NativeTaskRun.SAFE_ACTIONS | {'ansible.builtin.stat', 'ansible.builtin.slurp',
                                               'ansible.builtin.command'}
    FILES = {'tasks.d/runtime_snapshot.yml'}
    TARGETS = ('/etc/systemd/system/named.service.d', '/usr/local/sbin/bind-private-dnssec',
               '/etc/systemd/system/named-private-dnssec.service',
               '/etc/systemd/system/named-private-dnssec.timer', '/usr/local/bin/systemctl')

    def check_tasks(self, tasks, seen, basedir):
        super().check_tasks(tasks, seen, basedir)
        for task in tasks:
            command = task.get('ansible.builtin.command')
            if command is not None and command.get('argv') not in (
                    ['systemctl', 'is-active', 'named'],
                    ['systemctl', 'is-enabled', 'named'],
                    ['systemctl', 'is-active', 'named-private-dnssec.timer'],
                    ['systemctl', 'is-enabled', 'named-private-dnssec.timer']):
                raise ValueError('Native snapshot integration refuses an unreviewed command')


class NativeServiceRun(NativeContainerRun):
    """Use the native systemd module with an explicit isolated CLI boundary."""

    SAFE_ACTIONS = NativeTaskRun.SAFE_ACTIONS | {'ansible.builtin.systemd', 'ansible.builtin.command'}
    SAFE_META = NativeTaskRun.SAFE_META | {'flush_handlers'}
    FILES = {'tasks.d/deploy/service.yml', 'tasks.d/dnssec_activate.yml'}
    TARGETS = ('/usr/local/bin/systemctl',)

    def check_tasks(self, tasks, seen, basedir):
        super().check_tasks(tasks, seen, basedir)
        for task in tasks:
            command = task.get('ansible.builtin.command')
            if command is not None and command.get('argv') != ['systemctl', 'is-active', 'named']:
                raise ValueError('Native unit integration refuses an unreviewed command')

    def run(self):
        if shutil.which('systemctl') != '/usr/local/bin/systemctl':
            raise ValueError('Native unit integration requires its private CLI boundary')
        return super().run()


class NativeUnitBoundary:
    """Share one guarded CLI facade and unit model across native file tests."""

    def __init__(self, folder, units=None):
        NativeServiceRun('tasks.d/dnssec_activate.yml', {})
        self.folder = Path(folder).resolve()
        self.folder.relative_to('/tmp')
        if self.folder.stat().st_dev != Path('/').stat().st_dev:
            raise ValueError('Native unit fixtures require a private container filesystem')
        self.state, self.command = self.folder / 'units.json', Path('/usr/local/bin/systemctl')
        self.units = copy.deepcopy(units or {})

    def __enter__(self):
        if any(path.exists() or path.is_symlink() for path in (self.state, self.command)):
            raise ValueError('Native unit fixtures refuse existing CLI or state files')
        self.state.write_text(json.dumps({'units': self.units, 'operations': []}), encoding='utf-8')
        self.state.chmod(0o600)
        facade = Path(__file__).parent / 'fixtures/native_systemctl.py'
        self.command.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' +
            shlex.quote(str(facade)) + ' ' + shlex.quote(str(self.state)) + ' "$@"\n', encoding='utf-8')
        self.command.chmod(0o700)
        return self.state

    def __exit__(self, exc_type, exc_value, traceback):
        self.command.unlink()


class NativeProvisionRun(NativeServiceRun):
    """Exercise inspected runtime file transitions and their actual handlers."""

    SAFE_ACTIONS = NativeServiceRun.SAFE_ACTIONS | {'ansible.builtin.stat', 'ansible.builtin.file',
                                                  'ansible.builtin.copy', 'ansible.builtin.template'}
    FILES = {'tasks.d/named_runtime.yml', 'tasks.d/dnssec_prepare.yml', 'tasks.d/deploy/monitoring.yml'}
    TARGETS = NativeSnapshotRun.TARGETS + ('/etc/rndc.key', '/usr/local/sbin/named-healthcheck',
        '/etc/systemd/system/named-healthcheck.service', '/etc/systemd/system/named-healthcheck.timer')
    ROLE_ASSETS = True
    LOAD_HANDLERS = True

    def __init__(self, filename, variables, **options):
        super().__init__(filename, variables, **options)
        handlers = self.role / 'handlers/main.yml'
        self.check_tasks(yaml.safe_load(handlers.read_text(encoding='utf-8')), set(), handlers.parent)


class NativeRestoreRun(NativeProvisionRun):
    """Restore real runtime inodes with the shared isolated systemd boundary."""

    FILES = {'tasks.d/runtime_restore.yml'}


class NativeConfigurationRun(NativeProvisionRun):
    """Publish the real canonical template using native filesystem modules."""

    FILES = {'tasks.d/deploy/configuration.yml'}
    TARGETS = ('/etc/named.conf', '/usr/local/sbin/named-transaction', '/usr/local/bin/systemctl')


class NativeSealRun(NativeContainerRun):
    """Exercise metadata guards and the real atomic completion-seal writer."""

    SAFE_ACTIONS = NativeTaskRun.SAFE_ACTIONS | {'ansible.builtin.stat', 'ansible.builtin.file', 'ansible.builtin.copy'}
    FILES = {'tasks.d/deploy/seal.yml'}
    TARGETS = ('/etc/named.conf', '/var/named', '/var/lib/Bind9-Ansible')


class NativeZoneRun(NativeProvisionRun):
    """Use native template/copy/file modules and the genuine serial helper."""

    FILES = {'tasks.d/deploy/zones.yml'}
    TARGETS = ('/var/named', '/usr/local/sbin/reverse-serial', '/usr/local/bin/systemctl')
    SERIAL_COMMAND = ("{{ ['/usr/local/sbin/reverse-serial', '--backup', bind_recovery_archive] + "
                      "(bind_generated_reverse_zones | map(attribute='domain') | list) }}")

    def check_tasks(self, tasks, seen, basedir):
        NativeTaskRun.check_tasks(self, tasks, seen, basedir)
        for task in tasks:
            command = task.get('ansible.builtin.command')
            if command is not None and command != {'argv': self.SERIAL_COMMAND}:
                raise ValueError('Native zone integration refuses an unreviewed command')

    def __init__(self, filename, variables, **options):
        # These fixture values enter canonical filesystem paths. Keep every
        # real write inside this runner's independently owned container roots.
        for zone in variables.get('bind_authoritative_zones', []) + variables.get('bind_generated_reverse_zones', []):
            name = zone.get('domain', '')
            if not name or any(character not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-' for character in name) or '..' in name:
                raise ValueError('Native zone fixtures require literal DNS names')
        for item in variables.get('bind_existing_reverse_files', {}).get('files', []):
            path = Path(item['path'])
            if path.parent != Path('/var/named/zones') or path.suffix != '.zone':
                raise ValueError('Native zone fixtures refuse a foreign removal path')
        archive = variables.get('bind_recovery_archive')
        if archive is not None:
            Path(archive).resolve().relative_to('/tmp')
        super().__init__(filename, variables, **options)


class NativeTsigRun(NativeContainerRun):
    """Run the actual controller secret helper against owned private paths."""

    SAFE_ACTIONS = NativeTaskRun.SAFE_ACTIONS | {'ansible.builtin.command'}
    FILES = {'tasks.d/deploy/tsig.yml'}
    TARGETS = ('/etc/named.conf',)
    REFUSED_PATHS = {'native-tsig-relative', '/etc/named.conf'}

    def safe_context(self, task):
        return super().safe_context(task) or (task.get('delegate_to') == 'localhost' and
            task.get('become') is False and task.get('name') ==
            'Generate or verify persistent site TSIG secret atomically on controller')

    def check_tasks(self, tasks, seen, basedir):
        super().check_tasks(tasks, seen, basedir)
        for task in tasks:
            command = task.get('ansible.builtin.command')
            if command is not None and command != {'argv': ['{{ ansible_playbook_python }}',
                    '{{ role_path }}/files/tsig-secret.py', '{{ bind_tsig_secret_file }}', '--create-parent']}:
                raise ValueError('Native TSIG fixture refuses an unreviewed command')

    def run(self):
        value = self.variables.get('bind_tsig_secret_file')
        if not isinstance(value, str) or not value:
            raise ValueError('Native TSIG fixture requires its own explicit path')
        if value not in self.REFUSED_PATHS:
            path = Path(value).resolve()
            path.relative_to('/tmp')
            if path.parent.stat().st_dev != Path('/').stat().st_dev:
                raise ValueError('Native TSIG fixture refuses mounted secret parents')
        return super().run()


class NativeHibernationRun(NativeSealRun):
    """Run real hibernation metadata, recovery and seal actions with a timeout."""

    SAFE_ACTIONS = NativeSealRun.SAFE_ACTIONS | {'ansible.builtin.command', 'ansible.builtin.fail'}
    FILES = {'tasks.d/deploy/hibernate.yml'}
    COMMANDS = ('/usr/sbin/named-checkconf',)

    def check_tasks(self, tasks, seen, basedir):
        super().check_tasks(tasks, seen, basedir)
        for task in tasks:
            command = task.get('ansible.builtin.command')
            if command is not None and command != {'argv': ['/usr/bin/timeout', '--signal=TERM',
                    '--kill-after=5s', '60s', '/usr/sbin/named-checkconf', '/etc/named.conf']}:
                raise ValueError('Native hibernation fixture refuses an unreviewed checker command')


class NativeMigrationRun(NativeContainerRun):
    """Install the canonical backup utility and execute its real backup path."""

    SAFE_ACTIONS = NativeTaskRun.SAFE_ACTIONS | {'ansible.builtin.copy', 'ansible.builtin.file', 'ansible.builtin.command'}
    FILES = {'tasks.d/deploy/migration.yml'}
    TARGETS = ('/etc/named.conf', '/etc/named', '/var/named', '/var/backups/Bind9-Ansible',
               '/usr/local/sbin/bind-configtool', '/usr/local/sbin/bind-backup.sh', '/usr/local/sbin/bind-configtool.sh')
    ROOT = '/var/backups/Bind9-Ansible/bind'
    ROLE_ASSETS = True

    def check_tasks(self, tasks, seen, basedir):
        super().check_tasks(tasks, seen, basedir)
        for task in tasks:
            command = task.get('ansible.builtin.command')
            if command is not None and command != {'argv': ['/usr/local/sbin/bind-configtool',
                    'backup', '--force', '--root', '{{ bind_backup_root }}']}:
                raise ValueError('Native migration fixture refuses an unreviewed backup command')

    def run(self):
        value = self.variables.get('bind_backup_root')
        if isinstance(value, str) and value not in (self.ROOT, 'relative-fixture'):
            raise ValueError('Native migration fixture refuses an unowned backup root')
        return super().run()


class NativeRecoveryRun(NativeContainerRun):
    """Execute original rescue mappings after an external synthetic failure.

    Retain canonical Ansible source positions without serializing task YAML.
    The fixture's triggering block is outside production coverage; unvisited
    production blocks remain in the full-file denominator. Network rollback is
    explicitly excluded here and has its own genuine NetworkManager tests.
    """

    SAFE_ACTIONS = NativeProvisionRun.SAFE_ACTIONS | {'ansible.builtin.fail', 'ansible.builtin.include_tasks'}
    FILES = {'tasks.d/runtime_restore.yml'}
    TARGETS = NativeProvisionRun.TARGETS + ('/usr/local/sbin/bind-configtool',)

    def __init__(self, filename, variables, **options):
        if variables.get('bind_front_route_transaction_pending', False) is not False:
            raise ValueError('Native DNS rescue fixture refuses network rollback side effects')
        super().__init__(filename, variables, **options)

    def check_tasks(self, tasks, seen, basedir):
        super().check_tasks(tasks, seen, basedir)
        for task in tasks:
            command = task.get('ansible.builtin.command')
            if command is not None and command.get('argv') != [
                    '/usr/local/sbin/bind-configtool', 'restore', '--root', '{{ bind_backup_root }}',
                    '--archive', '{{ bind_recovery_archive | basename }}']:
                raise ValueError('Native DNS rescue refuses an unreviewed command')
            include = task.get('ansible.builtin.include_tasks')
            if include is not None and (include.get('file') !=
                    '{{ role_path }}/tasks/tasks.d/deploy/front_return_routes.yml' or task.get('when') != [
                    'bind_front_route_transaction_pending | default(false) | bool',
                    'not (bind_front_route_rollback_attempted | default(false) | bool)']):
                raise ValueError('Native DNS rescue refuses an unguarded dynamic import')

    def execute_native(self, runner):
        source = str(self.role / 'tasks/tasks.d/deploy/main.yml')
        mappings = runner._loader.load_from_file(source)
        rescue = next(task['rescue'] for task in mappings if 'rescue' in task)
        if not all(task.ansible_pos[0] == source for task in rescue):
            raise ValueError('Native recovery requires original production source positions')
        self.check_tasks(rescue, set(), Path(source).parent)
        play = Play.load({'hosts': 'localhost', 'gather_facts': False, 'tasks': [{
            'name': 'Inject a failure from the external recovery fixture',
            'block': [{'ansible.builtin.fail': {'msg': 'Synthetic original deployment failure'}}],
            'rescue': rescue}]}, loader=runner._loader, variable_manager=runner._variable_manager)
        return runner._tqm.run(play)


class NativeDirectoryRun(NativeContainerRun):
    """Exercise real directory/log metadata inside an empty disposable root."""

    SAFE_ACTIONS = NativeTaskRun.SAFE_ACTIONS | {'ansible.builtin.file'}
    FILES = {'tasks.d/deploy/directories.yml'}
    TARGETS = ('/var/named', '/var/log/named')


class NativePackageRun(NativeContainerRun):
    """Execute the one literal dependency task only in a disposable package DB."""

    SAFE_ACTIONS = NativeTaskRun.SAFE_ACTIONS | {'ansible.builtin.dnf'}
    FILES = {'tasks.d/deploy/packages.yml'}
    TARGETS = ('/var/lib/rpm', '/var/lib/dnf', '/var/cache/dnf', '/etc/yum.repos.d')


class NativeCliRun(NativeContainerRun):
    """Share native command dispatch guarded by private CLI fixture identity."""

    SAFE_ACTIONS = NativeTaskRun.SAFE_ACTIONS | {'ansible.builtin.command'}
    COMMANDS = ()
    JINJA_ARGV_PREFIX = None
    BARE_ALIASES = {}

    def run(self):
        for alias, target in self.BARE_ALIASES.items():
            if shutil.which(alias) != target or target not in self.COMMANDS:
                raise ValueError('Native command alias must resolve to its exact owned fixture')
        for name in self.COMMANDS:
            path = Path(shutil.which(name) or '/missing').resolve()
            path.relative_to('/tmp')
            info = path.stat()
            if (not path.is_file() or info.st_uid != os.geteuid() or info.st_mode & 0o022 or
                    info.st_size > 1048576 or info.st_dev != Path('/').stat().st_dev):
                raise ValueError('Native command integration requires private command fixtures')
        return super().run()

    def check_tasks(self, tasks, seen, basedir):
        super().check_tasks(tasks, seen, basedir)
        for task in tasks:
            command = task.get('ansible.builtin.command')
            if command is None:
                continue
            argv = command.get('argv')
            if not ((isinstance(argv, list) and argv and argv[0] in (*self.COMMANDS, *self.BARE_ALIASES)) or
                    (isinstance(argv, str) and self.JINJA_ARGV_PREFIX and
                     argv.strip().startswith(self.JINJA_ARGV_PREFIX))):
                raise ValueError('Native command integration refuses an unreviewed command')


class NativeFirewallRun(NativeCliRun):
    """Execute canonical firewall orchestration against the existing CLI model."""

    FILES = {'tasks.d/deploy/firewall.yml'}
    COMMANDS = ('ip', 'firewall-cmd')
    JINJA_ARGV_PREFIX = "{{ ['firewall-cmd',"


class NativeVerifyRun(NativeCliRun):
    """Check BIND validation orchestration; real validators run separately."""

    FILES = {'tasks.d/deploy/verify.yml'}
    COMMANDS = ('/usr/sbin/named-checkconf', '/usr/sbin/named-checkzone')
    TARGETS = COMMANDS


class NativeHealthRun(NativeCliRun):
    """Observe genuine DNS-health orchestration with exact private CLI replies."""

    FILES = {'tasks.d/deploy/health.yml'}
    COMMANDS = ('/usr/bin/dig', '/usr/sbin/named-checkzone')
    BARE_ALIASES = {'dig': '/usr/bin/dig'}
    TARGETS = COMMANDS

    def safe_context(self, task):
        return super().safe_context(task) or ('become' not in task and
            task.get('delegate_to') == '{{ bind_primary_host }}' and task.get('name') ==
            'Read actual reconciled SOA serials from primary zone files')

    def run(self):
        if self.variables.get('bind_primary_host') != 'localhost':
            raise ValueError('Native health fixture refuses a remote delegate')
        return super().run()


class NativeMonitorRun(NativeHealthRun):
    """Own the same DNS CLI boundary plus the periodic Bash service probe."""

    COMMANDS = NativeHealthRun.COMMANDS + ('/usr/local/bin/systemctl',)
    TARGETS = COMMANDS


class NativeSecurityRun(NativeCliRun):
    """Run original SELinux orchestration and publish its real login script.

    Exact command replies qualify task decisions, not an enforcing SELinux
    kernel. The login file and its repeat behavior use native copy operations.
    """

    SAFE_ACTIONS = NativeCliRun.SAFE_ACTIONS | {'ansible.builtin.copy'}
    ROLE_ASSETS = True
    FILES = {'tasks.d/deploy/security.yml'}
    COMMANDS = tuple('/usr/local/bin/' + name for name in ('semanage', 'restorecon', 'ls'))
    BARE_ALIASES = dict(zip(('semanage', 'restorecon', 'ls'), COMMANDS))
    TARGETS = COMMANDS + ('/etc/profile.d/bind9-sbin-path.sh',)


class NativeReadinessRun(NativeCliRun):
    """Exercise the real TCP wait and source-bound DNS readiness task file."""

    SAFE_ACTIONS = NativeCliRun.SAFE_ACTIONS | {'ansible.builtin.wait_for'}
    FILES = {'tasks.d/deploy/primary_readiness.yml'}
    COMMANDS = ('/usr/bin/dig',)
    BARE_ALIASES = {'dig': '/usr/bin/dig'}
    TARGETS = COMMANDS

    def safe_context(self, task):
        return super().safe_context(task) or ('become' not in task and
            task.get('delegate_to') == '{{ inventory_hostname }}' and task.get('name') in (
                'Wait for local primary DNS TCP listener on back interface',
                'Verify primary SOA from secondary over back network'))

    def run(self):
        if (self.variables.get('bind_primary_host') != 'localhost' or
                self.variables.get('bind_back_ip') != '127.0.0.1'):
            raise ValueError('Native readiness fixture requires only its private loopback listener')
        return super().run()


class NativeRecordedBoundary:
    """Install exact argv/reply fixtures without executing real host commands."""

    def __init__(self, folder, runner, replies):
        runner(next(iter(runner.FILES)), {})
        self.folder = Path(folder).resolve()
        self.folder.relative_to('/tmp')
        if self.folder.stat().st_dev != Path('/').stat().st_dev:
            raise ValueError('Recorded native commands require a private container filesystem')
        self.commands = tuple(Path(name) for name in runner.COMMANDS)
        if any(not path.is_absolute() for path in self.commands):
            raise ValueError('Recorded native command targets must be absolute')
        self.replies, self.links = copy.deepcopy(replies), []
        self.state = self.folder / 'commands.json'

    def __enter__(self):
        if self.state.exists() or self.state.is_symlink() or any(path.exists() or path.is_symlink() for path in self.commands):
            raise ValueError('Recorded command fixtures refuse occupied targets')
        self.state.write_text(json.dumps({'replies': self.replies, 'calls': []}), encoding='utf-8')
        self.state.chmod(0o600)
        facade = Path(__file__).parent / 'fixtures/native_recorded_cli.py'
        try:
            for command in self.commands:
                private = self.folder / command.name
                if private.exists() or private.is_symlink():
                    raise ValueError('Recorded command fixtures refuse an occupied private executable')
                private.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' +
                    shlex.quote(str(facade)) + ' ' + shlex.quote(str(self.state)) + ' ' +
                    shlex.quote(command.name) + ' "$@"\n', encoding='utf-8')
                private.chmod(0o700)
                command.symlink_to(private)
                self.links.append((command, command.lstat().st_ino))
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self.state

    def __exit__(self, exc_type, exc_value, traceback):
        for command, inode in reversed(self.links):
            if not command.is_symlink() or command.lstat().st_ino != inode:
                raise ValueError('Recorded command fixture identity changed; preserve it for inspection')
            command.unlink()

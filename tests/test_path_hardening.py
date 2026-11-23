"""Evaluate the actual Ansible path and runtime guards against unsafe stat data."""
from pathlib import Path

import pytest
import yaml

pytest.importorskip('ansible')
from route_task_expressions import TaskExpressions

TASKS = Path(__file__).resolve().parents[1] / 'roles/bind9/tasks/tasks.d'


class TestManagedPathGuards:
    """Prevent an unexpected object from reaching a privileged modifying task."""

    @staticmethod
    def accepts(filename, task_name, path, info):
        tasks = yaml.safe_load((TASKS / filename).read_text())
        task = next(row for row in tasks if row['name'] == task_name)
        values = {'item': {'item': path, 'stat': info}}
        values.update(task.get('vars', {}))
        return TaskExpressions(values).condition(task['ansible.builtin.assert']['that'], values)

    @pytest.mark.parametrize('path,is_directory', [
        ('/etc/named.conf', True), ('/var/log/named/default.log', True),
        ('/var/named/zones', False), ('/var/named/slaves', False),
    ])
    def test_file_and_directory_types_are_not_interchangeable(self, path, is_directory):
        info = {'exists': True, 'islnk': False, 'isreg': not is_directory,
                'isdir': is_directory, 'nlink': 1}
        assert not self.accepts('path_safety.yml', 'Refuse symlinks and non-regular managed files', path, info)

    def test_shared_inode_log_is_rejected(self):
        info = {'exists': True, 'islnk': False, 'isreg': True, 'isdir': False, 'nlink': 2}
        assert not self.accepts('path_safety.yml', 'Refuse symlinks and non-regular managed files',
                                '/var/log/named/default.log', info)

    @pytest.mark.parametrize('changes', [{'mode': '0664'}, {'nlink': 2}, {'size': 1048577}])
    def test_unsafe_runtime_is_not_snapshotted(self, changes):
        info = {'exists': True, 'islnk': False, 'isreg': True, 'uid': 0,
                'mode': '0644', 'nlink': 1, 'size': 100}
        info.update(changes)
        assert not self.accepts('runtime_snapshot.yml', 'Refuse unsafe managed runtime paths before making changes',
                                '/etc/systemd/system/named.service.d/bind9-runtime.conf', info)

"""Run the backup and fault-injection workflows in a disposable container root.

Alternative isolation for a standard container without mount-namespace capability.
This resets /var/named and /var/backups/bind-test and overwrites /etc/named.conf;
never run on a host or DNS server. The original test bodies/assertions are kept.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from test_configtool_workflows import SCRIPT, SETUP, TestConfigtoolWorkflows


class BackupContainerIntegration(TestConfigtoolWorkflows):
    """Use the already isolated container filesystem instead of nested mounts."""

    @staticmethod
    def execute(tmp_path, body):
        setup = '\n'.join(line for line in SETUP.splitlines() if not line.startswith('mount '))
        result = subprocess.run(['bash', '-s', '--', str(SCRIPT), str(tmp_path)],
                                input=setup + '\n' + body, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr

    def run(self):
        if os.environ.get('BIND_MANAGEMENT_ISOLATED') != '1' or not Path('/.dockerenv').exists() or os.geteuid() != 0:
            raise RuntimeError('Requires an explicitly authorized disposable root container')
        if set(path.name for path in Path('/sys/class/net').iterdir()) != {'lo'}:
            raise RuntimeError('Disconnect external networks before this destructive laboratory test')
        targets = ('/etc/named.conf', '/var/named', '/var/backups/bind-test')
        # Refuse host/volume-backed ancestors or submounts of the test targets.
        for line in Path('/proc/self/mountinfo').read_text().splitlines():
            mount = line.split()[4]
            if mount != '/' and any(target == mount or target.startswith(mount + '/') or mount.startswith(target + '/') for target in targets):
                raise RuntimeError('A test target must not be a mounted host or volume path')
        completed, failures = [], []
        for name in TestConfigtoolWorkflows.__dict__:
            if not name.startswith('test_'):
                continue
            for target in targets[1:]:
                path = Path(target)
                if path.is_symlink():
                    raise RuntimeError('Refuse an unexpected test-root symlink')
                if path.exists():
                    shutil.rmtree(str(path))
            with tempfile.TemporaryDirectory(prefix='bind9-backup-container-') as temporary:
                try:
                    getattr(self, name)(Path(temporary))
                except AssertionError as error:
                    failures.append({'name': name, 'cause': str(error)})
                else:
                    completed.append(name)
        assert len(completed) + len(failures) == 9
        print(json.dumps({'existing_workflow_assertions_executed': completed,
                          'failures': failures,
                          'isolation': 'disposable standard container root; no nested mounts',
                          'systemd_bind_selinux_doubles': True}), flush=True)
        assert not failures, 'Backup workflow failure(s): ' + ', '.join(item['name'] for item in failures)


if __name__ == '__main__':
    BackupContainerIntegration().run()

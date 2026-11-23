#!/usr/bin/env python3
"""Container-only systemd contract: execute native BIND/RNDC and unit hooks.

This is not a systemd implementation. It exercises the service calls used by
the deployment in a disposable EL8 container, without controlling the host.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time


class DeploymentServices:
    """Model the small unit surface while keeping DNS operations native."""

    def __init__(self):
        if os.environ.get('BIND_DEPLOY_ISOLATED') != '1' or not Path('/.dockerenv').exists():
            raise RuntimeError('Only an explicitly isolated container is supported')
        self.path = Path(os.environ['BIND_DEPLOY_UNIT_STATE'])
        self.owner = Path(os.environ['BIND_DEPLOY_NAMED_OWNER'])
        self.data = json.loads(self.path.read_text())

    @staticmethod
    def command(argv):
        result = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=30)
        if result.returncode:
            raise RuntimeError('Native command failed: ' + argv[0] + ': ' + result.stderr[-1000:])
        return result.stdout

    @staticmethod
    def running():
        result = subprocess.run(['/usr/sbin/rndc', '-s', '127.0.0.1', '-k', '/etc/rndc.key', 'status'],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
        return result.returncode == 0

    def hooks(self, operation):
        for path in sorted(Path('/etc/systemd/system/named.service.d').glob('*.conf')):
            for line in path.read_text().splitlines():
                if line.startswith(operation + '=') and line.split('=', 1)[1]:
                    self.command(shlex.split(line.split('=', 1)[1]))

    def named(self, state):
        active = self.running()
        changed = False
        if state in ('stopped', 'restarted') and active:
            if (not self.owner.exists() or self.owner.is_symlink() or
                    self.owner.read_text().strip() != Path('/run/named/named.pid').read_text().strip()):
                raise RuntimeError('Refusing to stop a named process not owned by this laboratory')
            self.command(['/usr/sbin/rndc', '-s', '127.0.0.1', '-k', '/etc/rndc.key', 'stop'])
            for _ in range(100):
                if not Path('/run/named/named.pid').exists():
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError('Native named did not stop')
            self.owner.unlink()
            changed, active = True, False
        if state in ('started', 'restarted') and not active:
            self.command(['/usr/sbin/named-checkconf', '-z', '/etc/named.conf'])
            self.command(['/usr/sbin/named', '-u', 'named', '-c', '/etc/named.conf', '-n', '1', '-U', '1'])
            self.owner.write_text(Path('/run/named/named.pid').read_text())
            for _ in range(100):
                if self.running():
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError('Native named did not start')
            self.hooks('ExecStartPost')
            changed = True
        elif state == 'reloaded':
            if not active:
                raise RuntimeError('Cannot reload inactive named')
            self.hooks('ExecReload')
            changed = True
        return changed

    def apply(self, request):
        name = request.get('name')
        changed = False
        if name:
            unit = self.data['units'].setdefault(name, {'active': False, 'enabled': False})
            state = request.get('state')
            if name == 'named':
                changed = self.named(state)
                unit['active'] = self.running()
            elif name == 'named-setup-rndc.service':
                before = Path('/etc/rndc.key').exists()
                self.command(['/bin/bash', '/usr/libexec/generate-rndc-key.sh'])
                changed = not before
                unit['active'] = False
            elif name == 'named-private-dnssec.service':
                if state in ('started', 'restarted'):
                    self.command(['/usr/bin/python3', '/usr/local/sbin/bind-private-dnssec'])
                    changed = True
                unit['active'] = False  # Native Type=oneshot has no RemainAfterExit.
            elif name == 'named-private-dnssec.timer':
                active = state in ('started', 'restarted') if state else unit['active']
                changed = active != unit['active']
                unit['active'] = active
            else:
                raise RuntimeError('Unsupported test unit: ' + name)
            if request.get('enabled') is not None:
                changed = changed or unit['enabled'] != request['enabled']
                unit['enabled'] = request['enabled']
        self.data['operations'].append(request)
        self.path.write_text(json.dumps(self.data, sort_keys=True))
        return changed

    def query(self, argv):
        if len(argv) != 2 or argv[0] not in ('is-active', 'is-enabled'):
            raise RuntimeError('Unsupported systemctl query')
        operation, name = argv
        unit = self.data['units'].get(name, {'active': False, 'enabled': False})
        if operation == 'is-active':
            active = self.running() if name == 'named' else unit['active']
            print('active' if active else 'inactive')
            return 0 if active else 3
        print('enabled' if unit['enabled'] else 'disabled')
        return 0 if unit['enabled'] else 1


def main():
    if sys.argv[1:2] == ['--query']:
        return DeploymentServices().query(sys.argv[2:])
    from ansible.module_utils.basic import AnsibleModule
    module = AnsibleModule(argument_spec=dict(
        name=dict(type='str'), state=dict(type='str', choices=['started', 'stopped', 'restarted', 'reloaded']),
        enabled=dict(type='bool'), daemon_reload=dict(type='bool', default=False)), supports_check_mode=False)
    try:
        changed = DeploymentServices().apply(module.params)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        module.fail_json(msg=str(error))
    module.exit_json(changed=changed)
    return 0


if __name__ == '__main__':
    sys.exit(main())

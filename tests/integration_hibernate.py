"""Exercise the real hibernate tag in a disposable, networkless Linux container.

This creates a seal under /var/lib inside the container. Never run on a target.
Template matrix and wire probes use private files/ports; systemctl is doubled
only for the periodic monitor. The hibernate path performs no service operation.
"""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import tempfile
import time

import yaml

from integration_loopback import LoopbackIntegration


class HibernateIntegration(LoopbackIntegration):
    """Own native syntax, DNS probe and persistent-lock checks in the container."""

    def invoke(self, command, env=None, success=True):
        result = subprocess.run(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True, timeout=120)
        if (result.returncode == 0) is not success:
            raise AssertionError(result.stdout)
        return result.stdout

    def matrix(self, folder):
        """Check both roles, client combinations, zones and DNSSEC modes."""
        count = 0
        for name, context in self.contexts():
            self.render(folder / name, context)
            count += 1
        _, initial = next(self.contexts())
        for validation in (False, True, 'auto'):
            for role in ('dns_primary', 'dns_secondary'):
                for cloud, internal in (([], []), (['192.0.2.10'], []), ([], ['10.30.1.20']), (['192.0.2.0/24'], ['10.30.1.20'])):
                    for authority, forward in ((True, True), (True, False), (False, True), (False, False)):
                        context = copy.deepcopy(initial)
                        context.update(bind_dnssec_active=validation, group_names=[role],
                                       bind_cloud_clients=cloud, bind_internal_clients=internal)
                        if not authority:
                            context.update(bind_authoritative_zones=[], bind_managed_zones=[])
                        if not forward:
                            context['bind_forward_zones'] = []
                        self.render(folder / ('matrix-' + str(count)), context)
                        count += 1
        return count

    def probes(self, folder):
        """Run the exact Ansible loopback task against native UDP/TCP replies."""
        _, context = next(self.contexts())
        context.update(bind_dnssec_active=False, bind_forward_zones=[],
                       bind_authoritative_zones=[], bind_managed_zones=[])
        config = self.render(folder / 'server', context)
        with socket.socket() as reservation:
            reservation.bind(('127.0.0.1', 0))
            port = reservation.getsockname()[1]
        self.sandbox_config(config, port)
        binaries = folder / 'bin'
        binaries.mkdir()
        dig = binaries / 'dig'
        dig.write_text('#!/bin/sh\nexec ' + shlex.quote(str(self.bin_dir / 'dig')) + ' -p ' + str(port) + ' "$@"\n')
        dig.chmod(0o755)
        systemctl = binaries / 'systemctl'
        systemctl.write_text('#!/bin/sh\nexit 0\n')
        systemctl.chmod(0o755)
        home = folder / 'home'
        home.mkdir(mode=0o700)
        (home / '.digrc').write_text('+short\n+multiline\n')
        environment = dict(os.environ, HOME=str(home), PATH=str(binaries) + os.pathsep + os.environ['PATH'],
                           ANSIBLE_CONFIG=str(self.root / 'ansible.cfg'),
                           ANSIBLE_FILTER_PLUGINS=str(self.role / 'filter_plugins'))
        task = yaml.safe_load((self.role / 'tasks/tasks.d/deploy/health.yml').read_text())[0]
        play = folder / 'probes.yml'
        play.write_text(yaml.safe_dump([{'hosts': 'localhost', 'connection': 'local', 'gather_facts': False,
                                        'vars': {'ansible_python_interpreter': os.sys.executable}, 'tasks': [task]}]))
        with (folder / 'daemon-output.txt').open('w+') as output:
            daemon = subprocess.Popen([str(self.named), '-g', '-c', str(config), '-n', '1', '-U', '1'], stdout=output, stderr=output)
            try:
                for _ in range(100):
                    if daemon.poll() is not None:
                        output.seek(0)
                        raise AssertionError(output.read())
                    try:
                        with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                            break
                    except OSError:
                        time.sleep(0.1)
                else:
                    raise AssertionError('Native probe daemon did not start')
                for native in ('false', 'true'):
                    environment['ANSIBLE_JINJA2_NATIVE'] = native
                    self.invoke(['ansible-playbook', '-i', 'localhost,', str(play)], environment)
                self.invoke(['bash', str(self.role / 'files/named-healthcheck.sh'), '127.0.0.1', '.'], environment)
            finally:
                if daemon.poll() is None:
                    daemon.terminate()
                try:
                    daemon.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    daemon.kill()
                    daemon.wait()
        return 8

    def hibernate(self, folder):
        """Use the actual public playbook, native checker and real container paths."""
        canonical = Path('/etc/named.conf')
        original = canonical.read_bytes()
        seal = Path('/var/lib/Bind9-Ansible/.deployment.lock')
        if seal.exists() or seal.is_symlink():
            raise RuntimeError('The disposable container must start without a completion seal')
        inventory = folder / 'hosts.ini'
        inventory.write_text('[dns_deployment_order]\nlocalhost ansible_connection=local ansible_python_interpreter=' + os.sys.executable + '\n')
        environment = dict(os.environ, ANSIBLE_CONFIG=str(self.root / 'ansible.cfg'), ANSIBLE_ROLES_PATH=str(self.root / 'roles'))
        command = ['ansible-playbook', '-i', str(inventory), str(self.root / 'playbooks/bind9-deploy.yml'),
                   '--tags', 'hibernate', '-e', 'hibernate_deployment=yes']
        self.invoke(command + ['--check'], environment)
        assert not seal.exists()
        self.invoke(command, environment)
        payload = json.loads(seal.read_text())
        assert payload['payload']['kind'] == 'hibernate'
        assert payload['payload']['files'] == {'/etc/named.conf': hashlib.sha256(original).hexdigest()}
        assert payload['sha256'] == hashlib.sha256(json.dumps(payload['payload'], sort_keys=True).encode()).hexdigest()
        observed = seal.read_bytes(), seal.stat().st_mtime_ns
        repeated = self.invoke(command, environment)
        assert 'changed=0' in repeated and 'Existing deployment seal preserved' in repeated
        assert (seal.read_bytes(), seal.stat().st_mtime_ns) == observed
        blocked = self.invoke(command[:-4] + ['--tags', 'configure', '-e', 'hibernate_deployment=no'], environment, success=False)
        assert 'completed deployment seal' in blocked
        assert canonical.read_bytes() == original and not seal.with_name('.operation.lock').exists()

    def run(self):
        if os.environ.get('BIND_MANAGEMENT_ISOLATED') != '1' or not Path('/.dockerenv').exists() or os.geteuid() != 0:
            raise RuntimeError('Requires an explicitly authorized disposable root container')
        if set(path.name for path in Path('/sys/class/net').iterdir()) != {'lo'}:
            raise RuntimeError('Disconnect external networks before this native audit')
        native = {path.name: path.read_bytes() for path in self.native_dir.iterdir() if path.is_file()}
        with tempfile.TemporaryDirectory(prefix='bind9-hibernate-') as temporary:
            folder = Path(temporary)
            count = self.matrix(folder / 'renders')
            probes = self.probes(folder / 'probes')
            self.hibernate(folder)
        assert native == {path.name: path.read_bytes() for path in self.native_dir.iterdir() if path.is_file()}
        print(json.dumps({'configurations_loaded': count, 'ansible_native_loopback_probes': probes,
                          'periodic_monitor_wire_check': True, 'systemctl_monitor_double': True,
                          'hibernate_public_playbook_native_checker': True, 'check_then_create_then_idempotent': True,
                          'disabled_setting_cannot_bypass_seal': True, 'native_files_unchanged': True}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, required=True)
    parser.add_argument('--native-dir', type=Path, required=True)
    parser.add_argument('--named', type=Path, required=True)
    arguments = parser.parse_args()
    HibernateIntegration(arguments.bin_dir, arguments.native_dir, arguments.named).run()

"""Deploy the public playbook with native EL8 DNS and FRONT routing together.

Requires a disposable, disconnected root container with NET_ADMIN/SYS_ADMIN,
NetworkManager, D-Bus, BIND and the controller dependencies already installed.
Only package installation, systemd transport and firewall/SELinux are adapted;
actual role ordering, files, backups, RNDC, DNSSEC NTAs and routing remain real.
Never execute on a DNS server or a container sharing the host network/bus.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time

import yaml


class CombinedDeployment:
    """Own the disposable daemons, network namespaces and deployment evidence."""

    def __init__(self, role):
        self.role = role
        self.root = Path(__file__).resolve().parents[1]
        self.work = Path('/tmp/combined-deploy')
        self.project = self.work / 'project'
        self.processes = []
        self.outputs = []
        self.isolated = False
        self.owned_named = self.work / 'named-owned.pid'
        self.native_named = Path('/run/named/named.pid')
        self.last = '2' if role == 'primary' else '3'
        self.front = '198.51.100.' + self.last
        self.back = '10.0.0.' + self.last
        self.adm = '192.0.2.' + self.last
        self.runs = []

    @staticmethod
    def command(argv, timeout=30, success=True, env=None):
        result = subprocess.run(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True, timeout=timeout)
        if success and result.returncode:
            raise RuntimeError('Command failed: ' + argv[0] + '\n' + result.stdout[-10000:])
        return result

    def daemon(self, argv, name):
        stream = (self.work / (name + '.log')).open('w')
        self.outputs.append(stream)
        process = subprocess.Popen(argv, stdout=stream, stderr=stream)
        self.processes.append(process)
        return process

    def prepare(self):
        if (os.environ.get('BIND_DEPLOY_ISOLATED') != '1' or not Path('/.dockerenv').exists()
                or os.geteuid() != 0 or set(p.name for p in Path('/sys/class/net').iterdir()) != {'lo'}):
            raise RuntimeError('Explicitly isolated root container with only lo is required')
        if self.native_named.exists() or self.native_named.is_symlink():
            raise RuntimeError('The fresh laboratory must not contain an existing named process receipt')
        for kind, port in ((socket.SOCK_STREAM, 53), (socket.SOCK_DGRAM, 53), (socket.SOCK_STREAM, 953)):
            with socket.socket(socket.AF_INET, kind) as reservation:
                reservation.bind(('0.0.0.0', port))
        self.work.mkdir(mode=0o700)
        self.isolated = True
        shutil.copytree(str(self.root), str(self.project))
        self.project.chmod(0o700)
        self.command(['rpm', '-q', 'bind', 'bind-utils', 'NetworkManager'])
        binaries = self.work / 'bin'
        binaries.mkdir()
        interpreter = binaries / 'python3'
        interpreter.symlink_to(sys.executable)
        for name in ('ansible-playbook', 'ansible-inventory', 'ansible-config'):
            wrapper = binaries / name
            wrapper.write_text('#!/bin/sh\nexec ' + sys.executable + ' /runtime/bin/' + name + ' "$@"\n')
            wrapper.chmod(0o700)
        os.environ.update(PATH=str(binaries) + ':' + os.environ['PATH'],
            PYTHONDONTWRITEBYTECODE='1', ANSIBLE_LOCAL_TEMP='/tmp/ansible-local',
            ANSIBLE_REMOTE_TEMP='/tmp/ansible-remote', BIND_DEPLOY_UNIT_STATE=str(self.work / 'units.json'),
            BIND_DEPLOY_NAMED_OWNER=str(self.owned_named))
        (self.work / 'units.json').write_text(json.dumps({'units': {}, 'operations': []}))
        library = self.work / 'library'
        library.mkdir()
        fixture = self.root / 'tests/fixtures/deployment_systemd.py'
        shutil.copyfile(str(fixture), str(library / 'bind_lab_systemd.py'))
        for path in list((self.project / 'roles/bind9/tasks').rglob('*.yml')) + list((self.project / 'roles/bind9/handlers').glob('*.yml')):
            text = path.read_text()
            if 'ansible.builtin.systemd:' in text:
                path.write_text(text.replace('ansible.builtin.systemd:', 'bind_lab_systemd:'))
        package_task = self.project / 'roles/bind9/tasks/tasks.d/deploy/packages.yml'
        package_task.write_text(yaml.safe_dump([{'name': 'Laboratory: verify preinstalled native packages',
            'ansible.builtin.command': {'argv': ['rpm', '-q', 'bind', 'bind-utils', 'NetworkManager']}, 'changed_when': False}]))
        # Inject failure only in the disposable test copy, after routes/NTA setup.
        health = self.project / 'roles/bind9/tasks/tasks.d/deploy/health.yml'
        tasks = yaml.safe_load(health.read_text())
        tasks.insert(0, {'name': 'Laboratory: inject a deployment health failure',
            'ansible.builtin.fail': {'msg': 'Controlled combined DNS and route rollback'},
            'when': 'bind_lab_fail_health | default(false) | bool'})
        health.write_text(yaml.safe_dump(tasks, sort_keys=False))
        sys.path.insert(0, str(self.root / 'tests'))
        from test_firewall_ansible import DOUBLE
        firewall = binaries / 'firewall-cmd'
        firewall.write_text(DOUBLE)
        firewall.chmod(0o700)
        firewall_state = self.work / 'firewall.json'
        firewall_state.write_text(json.dumps({'scenario': 'normal', 'calls': [],
            'zones': {scope: {'front0': 'external', 'back0': 'internal'} for scope in ('runtime', 'permanent')},
            'services': {scope: {} for scope in ('runtime', 'permanent')}}))
        os.environ['BIND_FIREWALL_TEST_STATE'] = str(firewall_state)
        query = binaries / 'systemctl'
        query.write_text('#!/bin/sh\nexec ' + sys.executable + ' ' + str(fixture) + ' --query "$@"\n')
        query.chmod(0o700)
        restore = binaries / 'restorecon'
        restore.write_text('#!/bin/sh\n# SELinux is disabled in this disposable container.\nexit 0\n')
        restore.chmod(0o700)
        config = self.work / 'ansible.cfg'
        config.write_text('[defaults]\nretry_files_enabled=False\nhost_key_checking=False\nroles_path=' +
            str(self.project / 'roles') + '\nlibrary=' + str(library) + '\n')
        os.environ['ANSIBLE_CONFIG'] = str(config)
        os.environ['ANSIBLE_LIBRARY'] = str(library)
        self.native = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in
            [Path('/etc/named.rfc1912.zones'), Path('/etc/named.root.key')] +
            [Path('/var/named') / name for name in ('named.localhost', 'named.loopback', 'named.empty')]}

    def network(self):
        self.command(['ip', 'link', 'set', 'lo', 'up'])
        cloud = self.daemon(['unshare', '--net', '/bin/sleep', '1800'], 'cloud-namespace')
        for _ in range(100):
            if cloud.poll() is not None:
                raise RuntimeError('Cloud network namespace failed')
            if os.readlink('/proc/' + str(cloud.pid) + '/ns/net') != os.readlink('/proc/self/ns/net'):
                break
            time.sleep(0.02)
        else:
            raise RuntimeError('Cloud network namespace was not created')
        self.cloud = ['nsenter', '-t', str(cloud.pid), '-n']
        self.command(['ip', 'link', 'add', 'front0', 'type', 'veth', 'peer', 'name', 'cloud0'])
        self.command(['ip', 'link', 'set', 'cloud0', 'netns', str(cloud.pid)])
        for args in (['link', 'set', 'lo', 'up'], ['link', 'set', 'cloud0', 'up'],
                     ['address', 'add', '198.51.100.1/24', 'dev', 'cloud0'],
                     ['address', 'add', '203.0.113.23/32', 'dev', 'lo']):
            self.command(self.cloud + ['ip'] + args)
        Path('/run/dbus').mkdir(parents=True, exist_ok=True)
        self.daemon(['dbus-daemon', '--session', '--address=unix:path=/run/dbus/system_bus_socket',
                     '--nofork', '--nopidfile'], 'dbus')
        os.environ['DBUS_SYSTEM_BUS_ADDRESS'] = 'unix:path=/run/dbus/system_bus_socket'
        nm_config = self.work / 'NetworkManager.conf'
        nm_config.write_text('[main]\nplugins=keyfile\ndns=none\nrc-manager=unmanaged\nno-auto-default=*\n')
        self.daemon(['NetworkManager', '--no-daemon', '--debug', '--config=' + str(nm_config),
                     '--pid-file=' + str(self.work / 'nm.pid')], 'networkmanager')
        for _ in range(100):
            if self.command(['nmcli', '--wait', '1', '-g', 'VERSION', 'general'], success=False).returncode == 0:
                break
            time.sleep(0.1)
        else:
            raise RuntimeError('NetworkManager did not become ready')
        for kind, name, address, gateway, metric in (
                ('ethernet', 'front', '198.51.100.10/24,' + self.front + '/24', '198.51.100.1', '500'),
                ('dummy', 'back', self.back + '/24', '10.0.0.1', '100'),
                ('dummy', 'adm', self.adm + '/24', '', '600')):
            self.command(['nmcli', 'connection', 'add', 'type', kind, 'ifname', name + '0', 'con-name', name,
                'ipv4.method', 'manual', 'ipv4.addresses', address, 'ipv4.gateway', gateway,
                'ipv4.route-metric', metric, 'ipv6.method', 'disabled'])
            self.command(['nmcli', 'connection', 'up', name])
        self.command(['nmcli', 'connection', 'modify', 'front', '+ipv4.routes', '172.16.0.0/24 198.51.100.1 150'])
        self.command(['nmcli', 'device', 'modify', 'front0', '+ipv4.routes', '172.16.0.0/24 198.51.100.1 150'])
        self.command(['ip', '-4', 'route', 'del', 'default', 'via', '198.51.100.1', 'dev', 'front0', 'metric', '500'])
        # Pending unrelated profile changes must not be activated by route work.
        self.active_dns = self.command(['nmcli', '-g', 'IP4.DNS', 'device', 'show', 'front0']).stdout
        self.command(['nmcli', 'connection', 'modify', 'front', 'ipv4.dns', '198.51.100.53'])
        self.protected = {name: self.profile(name) for name in ('back', 'adm')}
        self.before_routes = self.routes()

    def upstream(self):
        folder = self.work / 'upstream'
        folder.mkdir()
        zones = []
        for domain in ('private.test', 'second.test'):
            data = '$TTL 7200\n@ IN SOA ns.' + domain + '. hostmaster.' + domain + '. (1 60 60 3600 900)\n@ IN NS ns\nns IN A 127.0.0.1\nwww IN A 192.0.2.101\n'
            path = folder / (domain + '.zone')
            path.write_text(data)
            zones.append('zone "' + domain + '" { type master; file "' + str(path) + '"; };')
        config = folder / 'named.conf'
        config.write_text('options { directory "' + str(folder) + '"; listen-on port 1053 { 127.0.0.1; }; listen-on-v6 { none; }; recursion no; pid-file "' + str(folder / 'named.pid') + '"; };\n' + '\n'.join(zones))
        self.daemon(['/usr/sbin/named', '-g', '-c', str(config), '-n', '1', '-U', '1'], 'upstream')
        for _ in range(100):
            result = self.command(['dig', '-r', '@127.0.0.1', '-p', '1053', 'private.test', 'SOA', '+time=1', '+tries=1'], success=False)
            if result.returncode == 0 and 'status: NOERROR' in result.stdout:
                return
            time.sleep(0.1)
        raise RuntimeError('Upstream did not become ready')

    def inventory(self):
        self.inventory_path = self.work / 'hosts.ini'
        self.inventory_path.write_text('[dns_primary]\nprimary ansible_connection=local ansible_host=192.0.2.2 bind_front_ip=198.51.100.2 bind_back_ip=10.0.0.2\n'
            '[dns_secondary]\nsecondary ansible_connection=local ansible_host=192.0.2.3 bind_front_ip=198.51.100.3 bind_back_ip=10.0.0.3\n'
            '[dns_deployment_order]\nprimary\nsecondary\n[lab]\nprimary\nsecondary\n')
        self.variables = dict(ansible_python_interpreter=sys.executable, ansible_distribution='RedHat',
            ansible_distribution_major_version='8', ansible_selinux={'status': 'disabled'},
            bind_dnssec_active='auto', bind_front_routes_enabled=True, bind_operation_lock_enabled=False,
            hibernate_deployment=False, bind_environment='lab', bind_site_name='lab', bind_primary_host='primary', bind_secondary_host='secondary',
            bind_cloud_clients=['203.0.113.0/24', '172.21.7.23'], bind_internal_clients=['10.30.1.20'],
            bind_tsig_key_name='lab-transfer', bind_tsig_algorithm='hmac-sha256',
            bind_tsig_secret_file=str(self.work / 'secrets/site.key'), bind_authoritative_zones=[],
            bind_forward_zones=[{'domain': domain, 'dnssec_validate': False,
                'forwarders': [{'address': '127.0.0.1', 'port': 1053}]} for domain in ('private.test', 'second.test')])

    def profile(self, name):
        return self.command(['nmcli', '-g', 'ipv4.addresses,ipv4.gateway,ipv4.routes,ipv4.dns,ipv4.route-metric', 'connection', 'show', name]).stdout

    def routes(self):
        return json.loads(self.command(['ip', '-j', '-4', 'route', 'show', 'table', 'main']).stdout)

    def deploy(self, label, variables=None, success=True, tags=None, check=False):
        print('DEPLOY_STAGE=' + label, flush=True)
        request = dict(self.variables, **(variables or {}))
        parameters = self.work / (label + '.json')
        parameters.write_text(json.dumps(request))
        argv = ['ansible-playbook', '-i', str(self.inventory_path),
            str(self.project / 'playbooks/bind9-deploy.yml'), '--limit', self.role,
            '-e', '@' + str(parameters)]
        if tags:
            argv.extend(['--tags', tags])
        if check:
            argv.append('--check')
        result = self.command(argv, timeout=600, success=False)
        (self.work / (label + '.log')).write_text(result.stdout)
        if (result.returncode == 0) is not success:
            raise RuntimeError(label + ' failed unexpectedly\n' + result.stdout[-16000:])
        self.runs.append({'label': label, 'rc': result.returncode, 'tags': tags,
                          'check': check, 'recap': result.stdout.split('PLAY RECAP')[-1].strip()})
        return result.stdout

    def dns_snapshot(self):
        """Freeze static DNS assets and service identity, excluding daemon caches/logs."""
        paths = set(Path('/etc').glob('named*')) | set(Path('/etc').glob('rndc*'))
        paths |= set(Path('/etc/systemd/system').glob('named*'))
        paths |= set(Path('/usr/local/sbin').glob('*'))
        paths |= {Path(name) for name in self.native}
        paths |= {Path('/var/named/empty-root.zone'), self.work / 'units.json',
                  self.native_named, Path(self.variables['bind_tsig_secret_file'])}
        paths |= set(Path('/etc/named').rglob('*'))
        paths |= set(Path('/var/lib/Bind9-Ansible').rglob('*'))
        for parent in list(paths):
            if parent.is_dir():
                paths.update(parent.rglob('*'))
        snapshot = {}
        for path in paths:
            # The route helper owns this empty flock inode, including in plan mode.
            if str(path) == '/var/lib/Bind9-Ansible/operations.flock':
                continue
            if path.is_file() and not path.is_symlink():
                metadata = path.stat()
                snapshot[str(path)] = (hashlib.sha256(path.read_bytes()).hexdigest(),
                    metadata.st_ino, metadata.st_mtime_ns, metadata.st_mode,
                    metadata.st_uid, metadata.st_gid)
        return snapshot

    def routes_only(self):
        """Prepare an existing DNS fixture, then exercise only the public routes tag."""
        self.deploy('fixture-dns-without-routes', {'bind_front_routes_enabled': False})
        self.command(['rndc', '-s', '127.0.0.1', '-k', '/etc/rndc.key',
                      'nta', '-force', '-lifetime', '1w', 'manual.test'])
        self.manual_nta = True
        # A genuine completion seal must not prevent route maintenance.
        self.deploy('fixture-hibernate', {'hibernate_deployment': True}, tags='hibernate')
        old_lease = Path('/var/lib/Bind9-Ansible/.operation.lock')
        old_lease.mkdir(mode=0o700)
        (old_lease / 'operator-evidence').write_text('Previous operation: preserve this evidence.\n')
        protected = self.dns_snapshot()
        nta = self.command(['rndc', '-s', '127.0.0.1', '-k', '/etc/rndc.key', 'nta', '-dump']).stdout
        before, profile = self.routes(), self.profile('front')
        assert not any(row.get('dst') in ('203.0.113.0/24', '172.21.7.23') for row in before)
        preview = self.deploy('routes-check', tags='routes', check=True)
        assert self.routes() == before and self.profile('front') == profile
        assert self.dns_snapshot() == protected, 'Route preview altered deployed DNS assets'
        applied = self.deploy('routes-apply', tags='routes')
        self.probes()
        after, new_profile = self.routes(), self.profile('front')
        added = [row for row in after if row not in before]
        assert {row.get('dst') for row in added} == {'203.0.113.0/24', '172.21.7.23'}, added
        assert all(row.get('dev') == 'front0' and row.get('gateway') == '198.51.100.1' for row in added), added
        persistent = self.command(['nmcli', '-g', 'ipv4.routes', 'connection', 'show', 'front']).stdout
        assert '203.0.113.0/24' in persistent and '172.21.7.23/32' in persistent, persistent
        assert self.dns_snapshot() == protected, 'Route application altered deployed DNS assets'
        repeated = self.deploy('routes-repeat', tags='routes')
        self.probes()
        assert 'changed=0' in repeated, repeated[-3000:]
        assert self.routes() == after and self.profile('front') == new_profile
        assert self.dns_snapshot() == protected, 'Repeated routing altered deployed DNS assets'
        assert self.command(['rndc', '-s', '127.0.0.1', '-k', '/etc/rndc.key', 'nta', '-dump']).stdout == nta
        forbidden = ('Install canonical named.conf', 'Install authoritative zone files',
                     'Install BIND and SELinux tooling', 'Start or reload BIND')
        assert not any(task in output for task in forbidden for output in (preview, applied, repeated))
        print(json.dumps({'status': 'EXÉCUTÉ', 'scenario': 'routes-only', 'role': self.role,
            'runs': self.runs, 'routes_added': added, 'persistent_front_routes': persistent.strip(),
            'dns_files_inodes_mtimes_owners_permissions_unchanged': True,
            'service_pid_and_operations_unchanged': True, 'dnssec_auto_and_manual_nta_unchanged': True,
            'completion_seal_and_previous_lease_preserved': True,
            'check_writes_nothing': True, 'repeat_changed_zero': True,
            'cloud_udp_tcp_roundtrip': True, 'front_secondary_ip_gateway_not_system_default': True,
            'back_adm_existing_routes_pending_dns_preserved': True,
            'systemd_firewall_selinux_are_contracts_not_system_qualification': True}), flush=True)

    def probes(self):
        for transport in ('+notcp', '+tcp'):
            result = self.command(self.cloud + ['dig', '-r', '-4', '-b', '203.0.113.23', '@' + self.front,
                'www.private.test', 'A', transport, '+time=2', '+tries=1', '+noall', '+comments', '+answer'])
            assert 'status: NOERROR' in result.stdout and '192.0.2.101' in result.stdout, result.stdout
        assert 'dnssec-validation auto;' in Path('/etc/named.conf').read_text()
        nta = self.command(['rndc', '-s', '127.0.0.1', '-k', '/etc/rndc.key', 'nta', '-dump']).stdout
        assert 'private.test' in nta and 'second.test' in nta
        if getattr(self, 'manual_nta', False):
            assert 'manual.test' in nta, 'A manual exception must not be removed'
        assert self.protected == {name: self.profile(name) for name in self.protected}
        assert self.active_dns == self.command(['nmcli', '-g', 'IP4.DNS', 'device', 'show', 'front0']).stdout
        current = self.routes()
        assert all(row in current for row in self.before_routes)
        assert not any(row.get('dst') == 'default' and row.get('dev') == 'front0' for row in current)
        assert not Path('/var/lib/Bind9-Ansible/front-routes.pending').exists()

    def stop_owned_named(self):
        """Never stop a service unless this isolated run recorded its PID."""
        if not self.isolated or not self.owned_named.exists() or not self.native_named.exists():
            return
        if self.owned_named.is_symlink() or self.native_named.is_symlink():
            raise RuntimeError('Unsafe laboratory daemon ownership path')
        owned, current = self.owned_named.read_text().strip(), self.native_named.read_text().strip()
        if owned.isdigit() and int(owned) > 1 and owned == current:
            self.command(['/usr/sbin/rndc', '-s', '127.0.0.1', '-k', '/etc/rndc.key', 'stop'])

    def execute(self, scenario='combined'):
        try:
            self.prepare()
            print('PRODUCTION_SHA256=' + json.dumps({p.relative_to(self.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (self.root / 'roles').rglob('*') if p.is_file()}, sort_keys=True), flush=True)
            self.network()
            self.upstream()
            self.inventory()
            if scenario == 'routes':
                self.routes_only()
                return
            self.deploy('fresh')
            self.probes()
            self.command(['rndc', '-s', '127.0.0.1', '-k', '/etc/rndc.key',
                          'nta', '-force', '-lifetime', '1w', 'manual.test'])
            self.manual_nta = True
            files = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in
                [Path('/etc/named.conf'), Path('/var/named/empty-root.zone')] +
                list(Path('/etc/systemd/system/named.service.d').glob('*.conf'))}
            routes, profile = self.routes(), self.profile('front')
            self.deploy('repeat')
            self.probes()
            assert routes == self.routes() and profile == self.profile('front')
            assert files == {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in files}
            if self.role == 'primary':
                failed = self.deploy('rollback', {'bind_cloud_clients': self.variables['bind_cloud_clients'] + ['172.22.0.0/16'],
                                                  'bind_lab_fail_health': True}, success=False)
                assert 'Controlled combined DNS and route rollback' in failed and 'managed files restored' in failed
                self.probes()
                assert routes == self.routes() and profile == self.profile('front')
                assert files == {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in files}
            assert self.native == {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in self.native}
            print(json.dumps({'status': 'EXÉCUTÉ', 'role': self.role, 'runs': self.runs,
                'public_playbook': True, 'dnssec_auto': True, 'front_routes_enabled': True,
                'native_dns_rndc_nta_networkmanager_routes': True, 'cloud_udp_tcp_roundtrip': True,
                'secondary_front_address': True, 'system_default_only_back': True,
                'existing_routes_back_adm_and_pending_dns_preserved': True, 'semantic_idempotence': True,
                'rollback_new_routes_only': self.role == 'primary',
                'systemd_firewall_selinux_are_contracts_not_system_qualification': True}), flush=True)
        finally:
            self.stop_owned_named()
            for process in reversed(self.processes):
                if process.poll() is None:
                    process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for stream in self.outputs:
                stream.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--role', choices=['primary', 'secondary'], default='primary')
    parser.add_argument('--scenario', choices=['combined', 'routes'], default='combined')
    arguments = parser.parse_args()
    CombinedDeployment(arguments.role).execute(arguments.scenario)

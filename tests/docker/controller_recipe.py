"""Bounded real SSH/component recipe, explicitly distinct from RHEL qualification.

No distribution facts, production helpers, systemd or firewall commands are
replaced. Host keys are compared with public keys observed through Docker.
Private SSH and site TSIG material never leave this disposable controller.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

import yaml


# A reduced-assertion run cannot qualify privileged operations or private data.
if sys.flags.optimize:
    raise RuntimeError('Optimized Python is forbidden for the Docker validation recipe')


class RestartRouteExpectation:
    """Require NM's recorded static origin only for this recipe's new routes.

    `ip route add` defaults to the boot protocol; NM reinstalls the saved
    profile's routes as static after a cold start. This expected transition
    does not authorize any path, metric, flag, duplicate or baseline change.
    Transaction-time protocol/ownership checks in the role remain unchanged.
    """

    TARGETS = ('198.51.100.0/25', '198.51.100.200')

    @classmethod
    def expected(cls, before, initial):
        assert set(before) == set(initial)
        result = json.loads(json.dumps(before))
        for node, state in result.items():
            baseline = initial[node]['routes']
            assert all(row in state['routes'] for row in baseline)
            for target in cls.TARGETS:
                rows = [row for row in state['routes'] if row['dst'] == target]
                assert len(rows) == 1
                route = rows[0]
                if route in baseline:
                    continue  # Existing routes retain every recorded attribute.
                assert route.get('protocol') in (None, 'boot', 'static')
                assert {key:value for key,value in route.items() if key != 'protocol'} == {
                    'dst': target, 'gateway': '192.0.2.1', 'dev': 'eth1', 'metric': 100, 'flags': []}
                route['protocol'] = 'static'
        return result

    @classmethod
    def verify(cls, before, after, initial):
        assert cls.expected(before, initial) == after


class DockerComponentRecipe:
    """Retain every command outcome and refuse missing operational evidence."""

    def __init__(self):
        self.root = Path('/lab')
        self.out = self.root / 'results'
        self.source = Path('/snapshot')
        inventory = json.loads(Path('/source-manifest.json').read_text())
        self.manifest, self.source_count = inventory['files'], inventory['file_count']
        event_path = self.out / 'events.json'
        self.events = json.loads(event_path.read_text()) if event_path.exists() else []
        self.hosts = {'primary': '198.18.154.11', 'secondary': '198.18.154.12'}
        self.ssh_args = ['ssh', '-i', '/lab/private/id_ed25519', '-o', 'BatchMode=yes',
                         '-o', 'IdentitiesOnly=yes', '-o', 'StrictHostKeyChecking=yes',
                         '-o', 'UserKnownHostsFile=/lab/known_hosts',
                         '-o', 'ConnectTimeout=5', '-o', 'ConnectionAttempts=1']
        self.env = dict(os.environ, ANSIBLE_CONFIG='/snapshot/ansible.cfg',
                        ANSIBLE_ROLES_PATH='/snapshot/roles',
                        ANSIBLE_LIBRARY='/snapshot/roles/bind9/library',
                        ANSIBLE_MODULE_UTILS='/snapshot/roles/bind9/module_utils',
                        ANSIBLE_HOST_KEY_CHECKING='True',
                        ANSIBLE_LOCAL_TEMP='/lab/ansible-temp',
                        ANSIBLE_NOCOLOR='1', SYSTEMD_LOG_LEVEL='warning')

    def run(self, label, argv, expected=0, timeout=900):
        """Return only command data; retain diagnostics independently for audit."""
        start = time.monotonic()
        result = subprocess.run(argv, env=self.env, cwd='/lab', stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=timeout)
        log = self.out / (label + '.log')
        error_log = self.out / (label + '.stderr.log')
        with log.open('xb') as stream:
            stream.write(result.stdout)
        with error_log.open('xb') as stream:
            stream.write(result.stderr)
        self.events.append(dict(label=label, argv=argv, actual_exitcode=result.returncode,
                                expected_exitcode=expected, seconds=time.monotonic()-start,
                                log=log.name, log_sha256=hashlib.sha256(result.stdout).hexdigest(),
                                stderr_log=error_log.name,
                                stderr_log_sha256=hashlib.sha256(result.stderr).hexdigest()))
        self.save()
        assert result.returncode == expected, (label, result.returncode,
                                               result.stdout[-1800:], result.stderr[-1800:])
        return result.stdout.decode('utf-8')

    def save(self):
        (self.out / 'events.json').write_text(json.dumps(self.events, indent=2))

    def ssh(self, label, node, command, **options):
        # Machine protocols must not load an interactive RC file under nounset.
        return self.run(label, self.ssh_args + ['root@' + self.hosts[node],
                        'bash --noprofile --norc -euc ' + shlex.quote('export SYSTEMD_LOG_LEVEL=warning; ' + command)], timeout=90, **options)

    def play(self, label, path, extra=None, expected=0, tags=None):
        argv = ['ansible-playbook', '-i', '/lab/inventory.yml', path]
        if extra:
            file = self.root / (label + '-variables.json')
            file.write_text(json.dumps(extra))
            argv += ['-e', '@' + str(file)]
        if tags:
            argv += ['--tags', tags]
        return self.run(label, argv, expected=expected)

    def prepare(self):
        actual = {p.relative_to(self.source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.source.rglob('*') if p.is_file()}
        assert actual == self.manifest and len(actual) == self.source_count
        (self.out / 'source-identity.json').write_text(json.dumps(actual, indent=2))
        expected = json.loads((self.root / 'host-keys.json').read_text())
        known = []
        for node, address in self.hosts.items():
            scanned = self.run('host-key-scan-' + node, ['ssh-keyscan', '-T', '5', '-t', 'ed25519', address])
            rows = [line.split() for line in scanned.splitlines() if not line.startswith('#')]
            assert len(rows) == 1 and rows[0][0] == address
            assert rows[0][1:3] == expected[node].split()[:2], node
            known.append(' '.join(rows[0]))
        (self.root / 'known_hosts').write_text('\n'.join(known) + '\n')
        for node in self.hosts:
            self.ssh('ssh-facts-' + node, node,
                     'cat /etc/os-release; getenforce; systemctl is-active sshd; '
                     '/usr/libexec/platform-python --version; '
                     'sestatus; if test -e /sys/fs/selinux/enforce; then '
                     'cat /sys/fs/selinux/enforce; fi')
        site_key = self.root / 'private' / 'site-tsig'
        site_key.write_text(base64.b64encode(os.urandom(32)).decode('ascii') + '\n')
        site_key.chmod(0o600)
        variables = dict(
            ansible_user='root', ansible_python_interpreter='/usr/libexec/platform-python',
            ansible_ssh_private_key_file='/lab/private/id_ed25519',
            ansible_ssh_common_args='-o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=/lab/known_hosts',
            bind_environment='docker-recipe', bind_site_name='recipe_site',
            bind_tsig_key_name='docker-recipe-transfer', bind_tsig_algorithm='hmac-sha256',
            bind_tsig_secret_file='/lab/private/site-tsig',
            bind_tsig_secret="{{ lookup('ansible.builtin.file', '/lab/private/site-tsig') | trim }}",
            bind_primary_group='dns_primary', bind_secondary_group='dns_secondary',
            bind_primary_host='primary', bind_secondary_host='secondary',
            bind_front_route_metric=100, bind_operation_lock_enabled=False,
            bind_front_routes_enabled=True, bind_dnssec_active='auto',
            bind_monitoring_enabled=True,
            bind_trusted_networks=dict(internal_clients=['192.0.2.101', '10.154.0.101'], cloud_clients=['198.51.100.0/25', '198.51.100.200', '198.51.100.200/32', '198.51.100.0/25']),
            bind_resources_deploy=dict(resource='batch', action='create', operations=[dict(
                resource='zone', action='create', kind='authoritative', zone=dict(
                    domain='recipe.example.test', serial=2026110901, ttl=300,
                    records=[dict(name='app', type='A', value='198.51.100.50')]))]))
        inventory = dict(all=dict(vars=variables, children={
            'dns_deployment_order': dict(hosts={
                node: dict(ansible_host=self.hosts[node], bind_listen_interfaces=[
                    dict(name='eth1', ipaddress='192.0.2.' + suffix, scope='front'),
                    dict(name='eth2', ipaddress='10.154.0.' + suffix, scope='back')])
                for node, suffix in [('primary', '11'), ('secondary', '12')]}),
            'dns_primary': dict(hosts=dict(primary={})),
            'dns_secondary': dict(hosts=dict(secondary={})),
            'recipe_site': dict(hosts=dict(primary={}, secondary={}))}))
        (self.root / 'inventory.yml').write_text(yaml.safe_dump(inventory, sort_keys=False))

    def snapshot(self, label):
        result = {}
        for node in ('primary', 'secondary'):
            routes = json.loads(self.ssh(label + '-routes-' + node, node, 'ip -j -4 route show table main'))
            profile = self.ssh(label + '-profile-' + node, node,
                'uuid=$(nmcli -g GENERAL.CON-UUID device show eth1); '
                'test -z "$(nmcli -g ipv4.gateway connection show uuid "$uuid")"; '
                'nmcli -g ipv4.routes connection show uuid "$uuid"')
            assert len([r for r in routes if r['dst'] == 'default' and r.get('dev') == 'eth2' and
                        r.get('gateway') == '10.154.0.1']) == 1
            assert len([r for r in routes if r['dst'] == 'default']) == 1
            result[node] = dict(routes=routes, profile=profile)
        (self.out / (label + '.json')).write_text(json.dumps(result, indent=2))
        return result

    @staticmethod
    def require_unchanged(output):
        """Both hosts must report an unchanged, successful recap."""
        recaps = re.findall(r'(?m)^(primary|secondary)\s+:\s+ok=\d+\s+changed=(\d+)\s+unreachable=(\d+)\s+failed=(\d+)', output)
        assert {name for name, _, _, _ in recaps} == {'primary', 'secondary'}
        assert len(recaps) == 2 and all((changed, unreachable, failed) == ('0', '0', '0')
                                       for _, changed, unreachable, failed in recaps)

    @staticmethod
    def complete_dns(output):
        """Preserve the original exact flags, serial and no-truncation checks."""
        flags = re.findall(r'(?m)^;; flags: ([a-z ]+);', output)
        serials = re.findall(r'(?m)^recipe[.]example[.]test[.]\s+\d+\s+IN\s+SOA\s+\S+\s+\S+\s+(\d+)\s+', output)
        return (output.count('status: NOERROR') == 2 and len(flags) == 2 and
                all(set(value.split()) == {'qr', 'aa', 'ra'} for value in flags) and
                serials == ['2026110901', '2026110901'])

    def verify_dns(self, label, node):
        output = self.ssh(label, node,
            'rndc status; named-checkconf -z /etc/named.conf; '
            'dig -r -4 +tcp +time=2 +tries=1 +norecurse +noall +comments +answer @127.0.0.1 recipe.example.test SOA; '
            'dig -r -4 +notcp +time=2 +tries=1 +norecurse +noall +comments +answer @127.0.0.1 recipe.example.test SOA')
        assert self.complete_dns(output)

    def checkpoint(self, phase):
        """Compare persistent bytes and active paths around a Docker restart."""
        assert phase in ('before', 'after')
        state = self.snapshot('restart-' + phase + '-routes')
        for node in self.hosts:
            state[node]['files_and_profiles'] = self.ssh('restart-' + phase + '-files-' + node, node,
                'sha256sum /etc/NetworkManager/system-connections/bind9-lab-*.nmconnection '
                '/etc/NetworkManager/conf.d/90-bind9-lab.conf /etc/firewalld/firewalld.conf '
                '/etc/firewalld/zones/public.xml /etc/firewalld/zones/external.xml '
                '/etc/firewalld/zones/internal.xml /etc/named.conf /etc/rndc.key '
                '/etc/systemd/system/named.service.d/bind9-runtime.conf '
                '/etc/systemd/system/named.service.d/bind9-z-dnssec.conf '
                '/etc/systemd/system/named-private-dnssec.service '
                '/etc/systemd/system/named-private-dnssec.timer '
                '/usr/local/sbin/bind-healthcheck '
                '/etc/systemd/system/named-healthcheck.service '
                '/etc/systemd/system/named-healthcheck.timer '
                '/root/.ssh/authorized_keys; '
                'nmcli -g GENERAL.CON-UUID device show eth1; '
                'nmcli -g GENERAL.CON-UUID device show eth2; '
                'stat -c "%a %u %g %n" /etc/NetworkManager/system-connections/bind9-lab-*.nmconnection '
                '/etc/named.conf /var/log/named /var/log/named/default.log '
                '/var/log/named/security.log /var/log/named/xfer.log /usr/local/sbin/bind-healthcheck')
            self.ssh('restart-' + phase + '-native-services-' + node, node,
                'systemctl is-active named NetworkManager firewalld sshd named-private-dnssec.timer named-healthcheck.timer; '
                'systemctl is-enabled named NetworkManager firewalld sshd named-private-dnssec.timer named-healthcheck.timer; '
                'systemctl start named-healthcheck.service; '
                'test "$(systemctl show -p ExecMainStatus --value named-healthcheck.service)" = 0; '
                'test "$(firewall-cmd --state)" = running; firewall-cmd --check-config; '
                'ip6tables -t mangle -S PREROUTING | grep -F -- "-m rpfilter --validmark --invert -j DROP"; '
                '/usr/libexec/platform-python -c ' + shlex.quote(
                    'from pathlib import Path; p=Path("/root/bind9-write-probe"); p.write_text("probe"); p.unlink()'))
            self.verify_dns('restart-' + phase + '-dns-' + node, node)
        if phase == 'before':
            path = self.root / 'before-restart-checkpoint.json'
            with path.open('x', encoding='utf-8') as stream:
                stream.write(json.dumps(state, indent=2) + '\n')
        else:
            before = json.loads((self.root / 'before-restart-checkpoint.json').read_text())
            initial = json.loads((self.out / 'before-routes.json').read_text())
            expected = RestartRouteExpectation.expected(before, initial)
            (self.out / 'restart-after-expected-checkpoint.json').write_text(json.dumps(expected, indent=2))
            (self.out / 'restart-after-observed-checkpoint.json').write_text(json.dumps(state, indent=2))
            RestartRouteExpectation.verify(before, state, initial)
        (self.out / ('restart-' + phase + '-checkpoint.json')).write_text(json.dumps(state, indent=2))

    def execute(self):
        self.prepare()
        refusal = self.play('public-deployment-rocky-refusal', '/snapshot/playbooks/bind9-deploy.yml',
                            expected=2, tags='preflight')
        assert 'Validate supported platform and mandatory inputs' in refusal
        refusal = self.play('public-management-rocky-refusal', '/snapshot/playbooks/bind9-management.yml',
                            extra={'bind_resources_manage': dict(resource='option', action='read', name='minimal-responses')}, expected=2)
        assert 'Validate the management platform and explicit request envelope' in refusal
        initial = self.snapshot('before-routes')
        self.play('front-real-ssh-first-add', '/lab/routes.yml')
        added = self.snapshot('after-routes')
        for node, state in added.items():
            for dst in ('198.51.100.0/25', '198.51.100.200'):
                rows = [r for r in state['routes'] if r['dst'] == dst]
                assert len(rows) == 1 and rows[0]['dev'] == 'eth1' and rows[0]['gateway'] == '192.0.2.1'
            # Exact original kernel rows remain, including BACK/ADM and seed route.
            assert all(row in state['routes'] for row in initial[node]['routes'])
        repeat = self.play('front-real-ssh-idempotent', '/lab/routes.yml')
        self.require_unchanged(repeat)
        assert self.snapshot('after-repeat') == added
        self.play('front-real-ssh-controlled-rollback', '/lab/rollback.yml')
        assert self.snapshot('after-rollback') == added
        self.execute_components()

    def execute_components(self):
        """Resume only the failed compatibility phase, preserving earlier runs."""
        actual = {p.relative_to(self.source).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in self.source.rglob('*') if p.is_file()}
        assert actual == self.manifest and len(actual) == self.source_count
        self.play('canonical-components-real-systemd', '/lab/components.yml')
        for node in ('primary', 'secondary'):
            self.ssh('native-service-status-' + node, node,
                     'export SYSTEMD_LOG_LEVEL=warning; systemctl is-active named named-private-dnssec.timer NetworkManager sshd; '
                     'rndc status; named-checkconf -z /etc/named.conf; '
                     'dig -r -4 +tcp +time=2 +tries=1 +norecurse +noall +comments +answer @127.0.0.1 recipe.example.test SOA')
            self.verify_dns('native-service-dns-' + node, node)
        (self.out / 'component-recipe-result.json').write_text(json.dumps(dict(
            status='EXÉCUTÉ', platform='Rocky Linux 8 compatibility components',
            full_rhel_qualification=False, selinux_enforcing=False,
            production_source_modified=False, source_files=self.source_count,
            public_rhel_only_guards_preserved=True, ssh_host_keys_verified=True,
            real_networkmanager_systemd_named=True,
            firewall_qualification='separate native packet and persistence checks'), indent=2))


if __name__ == '__main__':
    recipe = DockerComponentRecipe()
    if sys.argv[1:] == ['--firewall']:
        recipe.play('firewall-canonical-first-add', '/lab/firewall.yml')
        repeated = recipe.play('firewall-canonical-idempotent', '/lab/firewall.yml')
        recipe.require_unchanged(repeated)
    elif sys.argv[1:] == ['--before-restart']:
        recipe.checkpoint('before')
    elif sys.argv[1:] == ['--after-restart']:
        recipe.checkpoint('after')
        stable = recipe.snapshot('after-cold-restart-before-repeat')
        recipe.require_unchanged(recipe.play('after-cold-restart-idempotence', '/lab/routes.yml'))
        recipe.play('after-cold-restart-controlled-rollback', '/lab/rollback.yml')
        assert recipe.snapshot('after-cold-restart-after-rollback') == stable
    else:
        assert not sys.argv[1:]
        recipe.execute()

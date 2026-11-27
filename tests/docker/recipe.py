"""Create an isolated EL8 component laboratory; never target a user server.

The node image must contain systemd 239, NetworkManager 1.40, firewalld 0.9,
BIND and OpenSSH. Images are addressed by immutable local Docker IDs. The
separate production playbooks retain their RHEL-only platform guards.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET


# Assertions are executable safety checks in this privileged QA tool.
if sys.flags.optimize:
    raise RuntimeError('Optimized Python is forbidden for the Docker validation recipe')


class DockerRecipe:
    """Own every resource by ID and label, with bounded, append-only evidence."""

    NODE_IMAGE = 'sha256:1fdda6986a3245edf8cc681cc3f990482906bcbc5c76e301e5924197597dfe78'
    CONTROLLER_IMAGE = 'sha256:6b6907453ddf497da914e4c108fa3692eac13158c8b4e0cbbb72becdeaf57393'
    SUBNETS = {'adm': '198.18.154.0/24', 'front': '192.0.2.0/24', 'back': '10.154.0.0/24'}
    READY = '''export SYSTEMD_LOG_LEVEL=warning
for attempt in {1..30}; do
    if systemctl is-active --quiet sshd NetworkManager firewalld &&
       [[ $(nmcli -g GENERAL.CONNECTION device show eth1) == recette-front-* ]] &&
       [[ $(nmcli -g GENERAL.CONNECTION device show eth2) == recette-back-* ]]; then
        firewall-cmd --state
        exit 0
    fi
    sleep 1
done
exit 1'''

    def __init__(self, output, node_image, controller_image):
        self.source = Path(__file__).resolve().parents[2]
        self.assets = Path(__file__).resolve().parent
        self.output = Path(output).resolve()
        if self.output.is_relative_to(self.source):
            raise ValueError('Evidence must remain outside the source tree')
        for image in (node_image, controller_image):
            if not re.fullmatch(r'sha256:[a-f0-9]{64}', image):
                raise ValueError('An immutable local image ID is required')
        self.node_image, self.controller_image = node_image, controller_image
        self.output.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.namespace = 'bind9-recipe-' + uuid.uuid4().hex[:12]
        self.resources = []
        self.nodes = {}
        self.sequence = 0
        self.controller = self.namespace + '-controller'
        self.controller_ready = False
        self.export_attempted = False
        self.networks = {role: self.namespace + '-' + role for role in self.SUBNETS}
        paths = list(self.source.rglob('*'))
        assert not any(path.is_symlink() for path in paths)
        self.manifest = {p.relative_to(self.source).as_posix(): self.sha(p)
                         for p in paths if p.is_file()}
        self.manifest_path = self.output / 'source-manifest.json'
        self.manifest_path.write_text(json.dumps(dict(files=self.manifest,
            file_count=len(self.manifest)), indent=2) + '\n', encoding='utf-8')

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def command(self, label, arguments, expected=0, timeout=120):
        self.sequence += 1
        result = subprocess.run(['docker'] + arguments, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=timeout, check=False)
        log = '%04d-%s.log' % (self.sequence, label)
        with (self.output / log).open('xb') as stream:
            stream.write(result.stdout)
        event = dict(label=label, arguments=arguments, actual_exitcode=result.returncode,
                     expected_exitcode=expected, log=log,
                     log_sha256=hashlib.sha256(result.stdout).hexdigest())
        with (self.output / 'docker-events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(event) + '\n')
        print(json.dumps(dict(label=label, rc=result.returncode)), flush=True)
        assert result.returncode == expected, (label, result.stdout[-1500:])
        return result.stdout.decode('utf-8')

    def create(self, kind, name, arguments):
        identity = self.command('create-' + name, arguments).strip()
        assert re.fullmatch(r'[0-9a-f]{64}', identity)
        resource = dict(kind=kind, name=name, id=identity, label=self.namespace)
        self.resources.append(resource)
        (self.output / 'owned-resources.json').write_text(
            json.dumps(self.resources, indent=2) + '\n', encoding='utf-8')
        return identity

    def copy(self, label, source, container, destination):
        self.command(label, ['cp', str(source), container + ':' + destination])

    def seed_profiles(self, node, suffix):
        directory = self.output / ('seed-' + node)
        directory.mkdir()
        for role, device, network, zone in (
                ('front', 'eth1', '192.0.2', 'external'),
                ('back', 'eth2', '10.154.0', 'internal')):
            identity = str(uuid.uuid5(uuid.NAMESPACE_URL, self.namespace + '/' + node + '/' + role))
            mac_role = '01' if role == 'front' else '02'
            ipv4 = ('never-default=true\nroute-metric=100\n'
                    'route1=203.0.113.0/24,192.0.2.1,70\n' if role == 'front'
                    else 'gateway=10.154.0.1\nnever-default=false\nroute-metric=500\n')
            content = ('[connection]\nid=recette-%s-%s\nuuid=%s\n'
                       'type=ethernet\ninterface-name=%s\nautoconnect=true\n'
                       'autoconnect-priority=1000\nzone=%s\n'
                       '[ethernet]\nmac-address=02:42:9a:00:%s:%s\n'
                       '[ipv4]\nmethod=manual\naddress1=%s.%s/24\n%s'
                       '[ipv6]\nmethod=disabled\n') % (
                           role, suffix, identity, device, zone, mac_role, suffix,
                           network, suffix, ipv4)
            file = directory / ('bind9-lab-' + role + '.nmconnection')
            file.write_text(content, encoding='utf-8')
            self.copy('seed-profile-' + node + '-' + role, file, self.nodes[node],
                      '/etc/NetworkManager/system-connections/' + file.name)
        # Only FRONT/BACK are handed to NM. Docker's ADM address is preserved.
        config = directory / '90-bind9-lab.conf'
        config.write_text('[device-bind9-lab]\n'
                          'match-device=interface-name:eth1;interface-name:eth2\n'
                          'keep-configuration=no\n', encoding='utf-8')
        self.copy('seed-nm-policy-' + node, config, self.nodes[node],
                  '/etc/NetworkManager/conf.d/90-bind9-lab.conf')
        original = directory / 'firewalld.original.conf'
        self.command('read-firewalld-' + node,
                     ['cp', self.nodes[node] + ':/etc/firewalld/firewalld.conf', str(original)])
        data = original.read_bytes()
        assert data.count(b'FirewallBackend=nftables') == 1 and b'IPv6_rpfilter=yes' in data
        corrected = directory / 'firewalld.conf'
        corrected.write_bytes(data.replace(b'FirewallBackend=nftables', b'FirewallBackend=iptables'))
        self.copy('seed-explicit-native-backend-' + node, corrected, self.nodes[node],
                  '/etc/firewalld/firewalld.conf')
        for zone, device in (('public', 'eth0'), ('external', 'eth1'), ('internal', 'eth2')):
            original = directory / (zone + '.original.xml')
            self.command('read-zone-' + node + '-' + zone, ['cp', self.nodes[node] +
                ':/usr/lib/firewalld/zones/' + zone + '.xml', str(original)])
            root = ET.fromstring(original.read_bytes())
            assert not root.findall('interface')
            ET.SubElement(root, 'interface', {'name': device})
            file = directory / (zone + '.xml')
            file.write_bytes(ET.tostring(root, encoding='utf-8', xml_declaration=True))
            self.copy('seed-zone-' + node + '-' + zone, file, self.nodes[node],
                      '/etc/firewalld/zones/' + zone + '.xml')
        self.copy('seed-packet-probe-' + node, self.assets / 'packet_probe.py', self.nodes[node],
                  '/opt/bind9-packet-probe.py')
        for role, prefix in (('adm', '198.18.154'), ('front', '192.0.2'), ('back', '10.154.0')):
            unit = 'bind9-recipe-witness' + ('' if role == 'adm' else '-' + role) + '.service'
            witness = directory / unit
            witness.write_text('[Unit]\nDescription=Private packet-filter witness\n'
                'After=NetworkManager.service\n[Service]\nType=simple\n'
                'ExecStart=/usr/libexec/platform-python /opt/bind9-packet-probe.py serve '
                '--address ' + prefix + '.' + suffix + '\nRestart=no\n'
                '[Install]\nWantedBy=multi-user.target\n', encoding='utf-8')
            self.copy('seed-witness-unit-' + node + '-' + role, witness, self.nodes[node],
                      '/etc/systemd/system/' + unit)

    def setup(self):
        # Fail before provisioning networks when the engine cannot isolate init.
        engine = self.command('node-cgroup-security', [
            'info', '--format', '{{.CgroupVersion}} {{json .SecurityOptions}}']).strip()
        version, security = engine.split(' ', 1)
        policies = json.loads(security)
        if version != '2' or not any(s.startswith('name=seccomp,') for s in policies):
            raise ValueError('The systemd lab requires cgroup v2 and active Docker seccomp')
        if not {'name=selinux', 'name=apparmor'}.intersection(policies):
            raise ValueError('The systemd lab requires a Docker SELinux or AppArmor policy')
        init_label = ['--security-opt', 'label=type:container_init_t'] if 'name=selinux' in policies else []
        for role, subnet in self.SUBNETS.items():
            self.create('network', self.networks[role], ['network', 'create', '--internal',
                '--subnet', subnet, '--label', 'bind9.recipe=' + self.namespace, self.networks[role]])
        for node, suffix in (('primary', '11'), ('secondary', '12')):
            name = self.namespace + '-' + node
            self.nodes[node] = name
            arguments = ['create', '--pull', 'never', '--name', name,
                '--label', 'bind9.recipe=' + self.namespace,
                '--network', 'name=%s,ip=198.18.154.%s,mac-address=02:42:9a:00:00:%s' % (self.networks['adm'], suffix, suffix),
                '--network', 'name=%s,ip=192.0.2.%s,mac-address=02:42:9a:00:01:%s' % (self.networks['front'], suffix, suffix),
                '--network', 'name=%s,ip=10.154.0.%s,mac-address=02:42:9a:00:02:%s' % (self.networks['back'], suffix, suffix),
                '--env', 'container=docker', '--env', 'SYSTEMD_LOG_LEVEL=warning',
                '--env', 'BIND9_RECIPE_NODE_SUFFIX=' + suffix,
                '--cgroupns', 'private', '--cap-add', 'SYS_ADMIN', '--cap-add', 'NET_ADMIN',
                '--security-opt', 'no-new-privileges=true', '--security-opt', 'writable-cgroups=true',
                *init_label, '--tty', '--tmpfs', '/run', '--tmpfs', '/run/lock',
                '--tmpfs', '/tmp', '--cpus', '2', '--memory', '1g',
                '--stop-signal', 'SIGRTMIN+3', '--entrypoint', '/bin/bash', self.node_image,
                '/opt/bind9-recipe-boot.sh']
            self.create('container', name, arguments)
            self.copy('seed-boot-' + node, self.assets / 'boot.sh', name, '/opt/bind9-recipe-boot.sh')
            self.seed_profiles(node, suffix)
            self.command('start-' + node, ['start', name])
            self.command('ready-' + node, ['exec', name, '/bin/bash', '-euc', self.READY])
            self.command('native-baseline-' + node, ['exec', name, '/bin/bash', '-euc',
                'systemd-detect-virt --container; ip -4 route show; '
                'nmcli -t -f NAME,UUID,DEVICE connection show --active; '
                'test -z "$(nmcli -g ipv4.gateway connection show recette-front-$BIND9_RECIPE_NODE_SUFFIX)"; '
                'ip6tables -t mangle -S PREROUTING'])
            self.command('enable-witness-' + node, ['exec', '-e', 'SYSTEMD_LOG_LEVEL=warning', name,
                'systemctl', 'enable', '--now', 'bind9-recipe-witness.service',
                'bind9-recipe-witness-front.service', 'bind9-recipe-witness-back.service'])
        (self.output / 'setup-state.json').write_text(json.dumps(dict(
            namespace=self.namespace, nodes=self.nodes, networks=self.networks,
            node_image=self.node_image, controller_image=self.controller_image,
            sequence=self.sequence), indent=2) + '\n', encoding='utf-8')

    def prepare_controller(self):
        arguments = ['create', '--pull', 'never', '--name', self.controller,
            '--label', 'bind9.recipe=' + self.namespace,
            '--network', 'name=' + self.networks['adm'] + ',ip=198.18.154.101',
            '--network', 'name=' + self.networks['front'] + ',ip=192.0.2.101',
            '--network', 'name=' + self.networks['back'] + ',ip=10.154.0.101',
            '--mount', 'type=bind,src=' + self.source.as_posix() + ',dst=/snapshot,readonly',
            '--mount', 'type=bind,src=' + self.manifest_path.as_posix() + ',dst=/source-manifest.json,readonly',
            '--env', 'PYTHONDONTWRITEBYTECODE=1', '--cpus', '2', '--memory', '2g',
            '--entrypoint', '/bin/bash', self.controller_image, '-euc',
            'install -d -m 0700 /lab/private /lab/results; '
            'ssh-keygen -q -t ed25519 -N "" -f /lab/private/id_ed25519; exec sleep 1800']
        self.create('container', self.controller, arguments)
        self.command('start-controller', ['start', self.controller])
        self.command('controller-ready', ['exec', self.controller, '/bin/bash', '-euc',
            'for attempt in {1..30}; do test ! -f /lab/private/id_ed25519.pub || exit 0; sleep 1; done; exit 1'])
        public = self.command('read-controller-public-key', ['exec', self.controller,
            'cat', '/lab/private/id_ed25519.pub'])
        assert re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+ [^\r\n]+\n', public)
        public_file = self.output / 'controller-key.pub'
        public_file.write_text(public, encoding='utf-8')
        keys = {}
        for node, name in self.nodes.items():
            self.command('ssh-directory-' + node, ['exec', name, 'install', '-d', '-m', '0700', '/root/.ssh'])
            self.copy('authorize-controller-' + node, public_file, name, '/root/.ssh/authorized_keys')
            self.command('protect-authorization-' + node,
                         ['exec', name, 'chmod', '0600', '/root/.ssh/authorized_keys'])
            keys[node] = self.command('read-node-public-key-' + node,
                         ['exec', name, 'cat', '/etc/ssh/ssh_host_ed25519_key.pub']).strip()
        keys_file = self.output / 'node-host-keys.json'
        keys_file.write_text(json.dumps(keys) + '\n', encoding='utf-8')
        self.copy('controller-known-key-inputs', keys_file, self.controller, '/lab/host-keys.json')
        for asset in ('controller_recipe.py', 'packet_probe.py', 'routes.yml', 'rollback.yml', 'components.yml', 'firewall.yml'):
            self.copy('controller-asset-' + asset.replace('.', '-'), self.assets / asset,
                      self.controller, '/lab/' + asset)
        self.controller_ready = True

    def component(self, label, phase=None):
        arguments = ['exec', self.controller, 'python', '-B', '/lab/controller_recipe.py']
        if phase:
            arguments.append(phase)
        return self.command(label, arguments, timeout=1200)

    def firewall_state(self, label):
        code = '''import json,subprocess
def call(arguments):
 return subprocess.check_output(['firewall-cmd']+arguments).decode().strip()
result={'default_zone':call(['--get-default-zone']), 'scopes':{}}
for scope in ('runtime','permanent'):
 prefix=['--permanent'] if scope=='permanent' else []
 result['scopes'][scope]={}
 for zone in sorted(call(prefix+['--get-zones']).split()):
  options=prefix+['--zone='+zone]
  result['scopes'][scope][zone]={name:sorted(call(options+['--list-'+name]).splitlines() if name=='rich-rules' else call(options+['--list-'+name]).split()) for name in ('services','interfaces','ports','rich-rules')}
print(json.dumps(result,sort_keys=True))'''
        states = {}
        for node, name in self.nodes.items():
            states[node] = json.loads(self.command(label + '-' + node,
                ['exec', '-e', 'SYSTEMD_LOG_LEVEL=warning', name, '/usr/libexec/platform-python', '-c', code]))
        (self.output / (label + '.json')).write_text(json.dumps(states, indent=2) + '\n', encoding='utf-8')
        return states

    @staticmethod
    def require_only_dns_added(before, after):
        expected = json.loads(json.dumps(before))
        for node in expected:
            for scope in ('runtime', 'permanent'):
                for zone in ('external', 'internal'):
                    services = expected[node]['scopes'][scope][zone]['services']
                    assert 'dns' not in services
                    services.append('dns')
                    services.sort()
        assert expected == after, 'Only the four requested DNS service authorizations may change per node'

    def packets(self, phase, dns_allowed, all_witnesses=False):
        for node, suffix in (('primary', '11'), ('secondary', '12')):
            for protocol in ('tcp', 'udp'):
                # A local successful echo proves that the forbidden ADM
                # listener actually exists before its remote refusal is used.
                witnesses = [('adm', '198.18.154')]
                if all_witnesses:
                    witnesses += [('front', '192.0.2'), ('back', '10.154.0')]
                for role, prefix in witnesses:
                    self.command(phase + '-witness-' + node + '-' + role + '-' + protocol,
                        ['exec', self.nodes[node], '/usr/libexec/platform-python', '/opt/bind9-packet-probe.py',
                         'probe', '--address', prefix + '.' + suffix, '--protocol', protocol, '--mode', 'echo'])
                for role, prefix in (('front', '192.0.2'), ('back', '10.154.0'), ('adm', '198.18.154')):
                    expected = 'allowed' if role != 'adm' and dns_allowed else 'blocked'
                    self.command(phase + '-packet-' + node + '-' + role + '-' + protocol,
                        ['exec', self.controller, 'python', '-B', '/lab/packet_probe.py', 'probe',
                         '--address', prefix + '.' + suffix, '--protocol', protocol,
                         '--mode', 'echo' if role == 'adm' or all_witnesses else 'dns', '--expected', expected])

    def identities(self, phase):
        states = {}
        for node, name in self.nodes.items():
            raw = json.loads(self.command(phase + '-runtime-' + node, ['inspect', name]))[0]
            assert raw['Config']['Labels']['bind9.recipe'] == self.namespace
            assert raw['HostConfig']['Privileged'] is False and raw['State']['Running'] is True
            assert raw['Image'] == self.node_image
            states[node] = dict(id=raw['Id'], mac_and_ip={key:dict(mac=value['MacAddress'], ip=value['IPAddress'])
                for key,value in raw['NetworkSettings']['Networks'].items()})
            assert set(states[node]['mac_and_ip']) == set(self.networks.values())
        return states

    def export_controller(self):
        # Inspect private values inside the controller; only public evidence
        # can leave it. No private key or backup/config content is exported.
        self.export_attempted = True
        code = '''from pathlib import Path
root=Path('/lab/results')
private=[p.read_bytes().strip() for p in Path('/lab/private').iterdir() if p.is_file() and not p.name.endswith('.pub')]
for p in root.rglob('*'):
 if p.is_file():
  data=p.read_bytes()
  assert b'PRIVATE KEY-----' not in data and all(not secret or secret not in data for secret in private), 'Private data in evidence'
print('private-data-export-check=clean')'''
        self.command('private-data-export-check', ['exec', self.controller, 'python', '-B', '-c', code])
        self.command('export-public-controller-evidence',
            ['cp', self.controller + ':/lab/results', str(self.output / 'component-results')])

    def cleanup(self):
        # Validate immutable IDs and the ownership label immediately before
        # removal. Never enumerate/prune another project's Docker resources.
        for resource in reversed(self.resources):
            kind, identity = resource['kind'], resource['id']
            arguments = ['network', 'inspect', identity] if kind == 'network' else ['inspect', identity]
            actual = json.loads(self.command('cleanup-inspect-' + resource['name'], arguments))[0]
            labels = actual['Labels'] if kind == 'network' else actual['Config']['Labels']
            assert actual['Id'] == identity and labels['bind9.recipe'] == self.namespace
            if kind == 'container':
                self.command('cleanup-stop-' + resource['name'], ['stop', '--timeout', '20', identity])
                self.command('cleanup-remove-' + resource['name'], ['rm', identity])
            else:
                self.command('cleanup-remove-' + resource['name'], ['network', 'rm', identity])
        (self.output / 'cleanup.json').write_text(json.dumps(dict(
            owned_containers_removed=sum(r['kind']=='container' for r in self.resources),
            owned_networks_removed=sum(r['kind']=='network' for r in self.resources),
            unrelated_resources_touched=False, images_and_evidence_preserved=True)) + '\n', encoding='utf-8')

    @classmethod
    def cleanup_saved(cls, output):
        """Recover only saved, immutable resource IDs after an interrupted run."""
        instance = cls.__new__(cls)
        instance.output = Path(output).resolve(strict=True)
        path = instance.output / 'owned-resources.json'
        assert path.is_file() and not path.is_symlink()
        instance.resources = json.loads(path.read_text(encoding='utf-8'))
        assert isinstance(instance.resources, list) and 0 < len(instance.resources) <= 6
        labels = {resource['label'] for resource in instance.resources}
        assert len(labels) == 1
        instance.namespace = labels.pop()
        assert re.fullmatch(r'bind9-recipe-[0-9a-f]{12}', instance.namespace)
        for resource in instance.resources:
            assert set(resource) == {'kind', 'name', 'id', 'label'}
            assert resource['kind'] in ('container', 'network')
            assert re.fullmatch(r'[0-9a-f]{64}', resource['id'])
            assert re.fullmatch(re.escape(instance.namespace) +
                                r'-(primary|secondary|controller|adm|front|back)', resource['name'])
        instance.sequence = max(int(p.name.split('-', 1)[0])
                                for p in instance.output.glob('[0-9][0-9][0-9][0-9]-*.log'))
        instance.cleanup()

    def execute(self):
        try:
            self.setup()
            self.prepare_controller()
            before = self.firewall_state('before-firewall')
            self.packets('before-firewall', False, all_witnesses=True)
            for node, name in self.nodes.items():
                self.command('release-temporary-dns-witnesses-' + node,
                    ['exec', '-e', 'SYSTEMD_LOG_LEVEL=warning', name, 'systemctl', 'disable', '--now',
                     'bind9-recipe-witness-front.service', 'bind9-recipe-witness-back.service'])
            self.component('canonical-components-and-route-transactions')
            after = self.firewall_state('after-firewall')
            self.require_only_dns_added(before, after)
            self.component('canonical-firewall-and-idempotence', '--firewall')
            self.packets('after-firewall', True)
            self.component('save-before-cold-restart', '--before-restart')
            identities = self.identities('before-cold-restart')
            for node, name in self.nodes.items():
                self.command('cold-restart-' + node, ['restart', '--timeout', '20', name])
                self.command('cold-ready-' + node, ['exec', name, '/bin/bash', '-euc', self.READY])
            assert identities == self.identities('after-cold-restart')
            self.component('verify-automatic-persistence', '--after-restart')
            assert self.firewall_state('after-cold-restart-firewall') == after
            self.packets('after-cold-restart', True)
            self.export_controller()
            assert {p.relative_to(self.source).as_posix():self.sha(p)
                    for p in self.source.rglob('*') if p.is_file()} == self.manifest
            (self.output / 'result.json').write_text(json.dumps(dict(
                status='EXÉCUTÉ', source_files=len(self.manifest),
                native_firewall=True, ipv6_reverse_path_filter_preserved=True,
                dns_only_on_existing_front_back_zones=True, adm_dns_refused_with_live_listener=True,
                automatic_front_routes_after_docker_restart=True,
                fixed_macs_and_profile_uuids=True, no_postrestart_route_reapply=True,
                actual_downstream_failure_rollback_before_and_after_restart=True,
                public_rhel_guards_preserved=True, rhel_host_reboot_qualified=False,
                full_rhel_qualification=False, private_keys_exported=False,
                source_manifest_sha256=self.sha(self.manifest_path)), indent=2)+'\n', encoding='utf-8')
        except BaseException as error:
            (self.output / 'failure.json').write_text(json.dumps(dict(
                status='ÉCHOUÉ', error_type=type(error).__name__,
                command_events='docker-events.jsonl', qualification_granted=False))+'\n', encoding='utf-8')
            if self.controller_ready and not self.export_attempted:
                self.export_controller()
            raise
        finally:
            self.cleanup()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--node-image', default=DockerRecipe.NODE_IMAGE)
    parser.add_argument('--controller-image', default=DockerRecipe.CONTROLLER_IMAGE)
    parser.add_argument('--cleanup-only', action='store_true',
                        help='Verify saved ownership and remove only this interrupted run\'s resources')
    options = parser.parse_args()
    if options.cleanup_only:
        DockerRecipe.cleanup_saved(options.output)
    else:
        DockerRecipe(options.output, options.node_image, options.controller_image).execute()

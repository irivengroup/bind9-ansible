"""Exercise targeted CRUD, real backups, native validation/reload and wire DNS.

Run through run_integration_management.sh or in a disposable standard container
with its own root filesystem and no external network or published ports.
SELinux is doubled; no production server may be used as the laboratory.
"""
import argparse
import base64
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time


class NativeManagement:
    """Own the disposable daemon and check externally observable CRUD results."""

    def __init__(self, binaries, native, daemon):
        self.binaries, self.native, self.daemon = binaries, native, daemon
        self.project = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('management', self.project / 'roles/bind9/module_utils/bind9_management.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)
        self.context = dict(primary=True, primary_front='127.0.0.2', secondary_front='127.0.0.4',
                            primary_back='127.0.0.3', secondary_back='127.0.0.5', tsig_key_name='site-transfer', named_gid=0)
        self.transactions = 0
        # Standard containers need no extra loopback address or network capability.
        self.upstream = os.environ.get('BIND_MANAGEMENT_TEST_UPSTREAM', '127.0.0.6')
        if self.upstream not in ('127.0.0.1', '127.0.0.6'):
            raise ValueError('The laboratory upstream must be an approved loopback address')
        self.dnssec = os.environ.get('BIND_MANAGEMENT_DNSSEC', 'no')

    def command(self, argv):
        name = Path(argv[0]).name
        if name == 'restorecon':
            return ''  # No SELinux policy in this namespace: explicit test double.
        if name in ('named-checkconf', 'rndc', 'dig'):
            argv = [str(self.binaries / name)] + argv[1:]
        if name == 'python3' and Path(argv[1]).name == 'bind-private-dnssec':
            argv += ['--rndc', str(self.binaries / 'rndc')]
        return self.module.Manager.command(argv)

    def prepare(self):
        for directory in ('/var/named/zones', '/var/named/slaves', '/var/named/data', '/var/named/dynamic', '/run/named'):
            Path(directory).mkdir(parents=True, exist_ok=True)
            # The isolated lab daemon runs as the harness user. Native RPMs
            # may have already created named-owned runtime directories.
            os.chown(directory, os.geteuid(), os.getegid())
            Path(directory).chmod(0o750)
        for name in ('named.localhost', 'named.loopback', 'named.empty'):
            shutil.copyfile(str(self.native / name), '/var/named/' + name)
        shutil.copyfile(str(self.native / 'named.rfc1912.zones'), '/etc/named.rfc1912.zones')
        for name in ('rndc-key', 'site-transfer'):
            key = base64.b64encode(secrets.token_bytes(32)).decode('ascii')
            Path('/etc/' + name + '.key').write_text('key "' + name + '" { algorithm hmac-sha256; secret "' + key + '"; };\n')
            Path('/etc/' + name + '.key').chmod(0o600)
        Path('/etc/rndc.key').write_bytes(Path('/etc/rndc-key.key').read_bytes())
        Path('/etc/rndc.key').chmod(0o600)
        Path('/var/named/empty-root.zone').write_text('$TTL 60\n@ IN SOA localhost. hostmaster.localhost. (7 3600 900 604800 60)\n@ IN NS localhost.\nlocalhost. IN A 127.0.0.1\n')
        Path('/var/named/zones/example.test.zone').write_text('$TTL 300\n@ IN SOA ns1.example.test. hostmaster.example.test. (41 3600 900 604800 300)\n@ IN NS ns1.example.test.\nns1 IN A 127.0.0.2\nwww IN A 192.0.2.80\n')
        Path('/etc/named.conf').write_text('''// operator annotation must survive
options { directory "/var/named"; listen-on {127.0.0.1;}; listen-on-v6 {none;};
 dnssec-validation no; minimal-responses yes;
 pid-file "/run/named/named.pid"; session-keyfile "/run/named/session.key"; };
include "/etc/rndc.key";
include "/etc/site-transfer.key";
controls { inet 127.0.0.1 port 953 allow {127.0.0.1;} keys {"rndc-key";}; };
acl bind_secondary_back_ip {127.0.0.5;};
acl bind_primary_back_ip {127.0.0.3;};
include "/etc/named.rfc1912.zones";
zone "." {type master; file "empty-root.zone";};
zone "example.test" {type master; file "zones/example.test.zone"; allow-update {none;};};
''')
        Path('/etc/named.conf').chmod(0o640)
        if self.dnssec == 'auto':
            shutil.copyfile(str(self.native / 'named.root.key'), '/etc/named.root.key')
            source = Path('/etc/named.conf').read_text().replace('dnssec-validation no;', 'dnssec-validation auto; bindkeys-file "/etc/named.root.key"; managed-keys-directory "/var/named/dynamic";')
            Path('/etc/named.conf').write_text(source)
            shutil.copyfile(str(self.project / 'roles/bind9/files/bind-private-dnssec.py'), '/usr/local/sbin/bind-private-dnssec')
            Path('/usr/local/sbin/bind-private-dnssec').chmod(0o700)
        shutil.copyfile(str(self.project / 'roles/bind9/files/bind-configtool.sh'), '/usr/local/sbin/bind-configtool')
        Path('/usr/local/sbin/bind-configtool').chmod(0o700)

    def action(self, resource, action, name, **fields):
        manager = self.module.Manager(dict(resource=resource, action='read', name=name, **({key: fields[key] for key in ('zone', 'type') if key in fields})), self.context, runner=self.command)
        revision = manager.run()['checksum']
        manager = self.module.Manager(dict(resource=resource, action=action, name=name, expected_checksum=revision, **fields), self.context, runner=self.command)
        with manager.operation():
            result = manager.run()
        assert result['changed'], result
        assert json.loads(Path(result['backup'], 'manifest.json').read_text())['status'] == 'committed'
        archive = json.loads(Path(result['backup'], 'manifest.json').read_text())['full_archive']
        assert Path(archive).is_file() and Path(archive + '.sha256').is_file()
        self.transactions += 1
        return result

    def query(self, name, kind='A'):
        return self.command(['/usr/bin/dig', '-r', '-4', '@127.0.0.1', '+time=2', '+tries=1', '+short', name, kind]).strip()

    def batch(self, operations):
        """Approve an exact candidate, check it, then publish it once."""
        request = dict(resource='batch', action='read', operations=operations)
        reader = self.module.BatchManager(request, self.context, runner=self.command)
        with reader.operation():
            preview = reader.run()
        request.update(action='apply', expected_checksum=preview['checksum'], expected_plan_checksum=preview['plan_checksum'])
        candidate = self.module.BatchManager(request, self.context, runner=self.command, check=True)
        with candidate.operation():
            assert candidate.run()['check_mode']
        manager = self.module.BatchManager(request, self.context, runner=self.command)
        with manager.operation():
            result = manager.run()
        assert result['status'] == 'committed'
        self.transactions += 1
        return result

    def batches(self):
        """Prove mixed final state and joint rollback using actual wire DNS."""
        create = [dict(resource='option', action='update', name='minimal-responses', value='yes'),
            dict(resource='zone', action='create', name='batch.test', kind='authoritative'),
            dict(resource='record', action='create', zone='batch.test', name='app', type='A', values=['192.0.2.92']),
            dict(resource='record', action='create', zone='batch.test', name='v6', type='AAAA', values=['2001:db8::92']),
            dict(resource='record', action='update', zone='example.test', name='www', type='A', values=['192.0.2.92']),
            dict(resource='zone', action='create', name='forward.test', kind='forward',
                 forwarders=[{'address': self.upstream, 'port': 5353}], dnssec_validate=False)]
        result = self.batch(create)
        assert result['serials']['batch.test.'] == '1'
        assert self.query('app.batch.test') == '192.0.2.92'
        assert self.query('v6.batch.test', 'AAAA') == '2001:db8::92'
        assert self.query('www.example.test') == '192.0.2.92'
        assert self.query('app.forward.test') == '192.0.2.100'
        listings = {}
        for resource in ('zone', 'option', 'record'):
            request = dict(resource=resource, action='list')
            if resource == 'record':
                request['zone'] = 'batch.test'
            reader = self.module.Manager(request, self.context, runner=self.command)
            with reader.operation():
                listings[resource] = reader.run()
        assert len({page['checksum'] for page in listings.values()}) == 1
        assert next(item for item in listings['zone']['items'] if item['name'] == 'batch.test.')['type'] == 'master'
        assert next(item for item in listings['option']['items'] if item['name'] == 'minimal-responses')['value'] == 'yes'
        assert next(item for item in listings['record']['items'] if item['name'] == 'app.batch.test.')['value'] == '192.0.2.92'
        # NTA/cache runtime files legitimately change during reconciliation.
        # The guarded transaction owns the complete static input graph only.
        before = {Path(name): data for name, data in self.module.Configuration(self.module.SafeFiles()).contents.items()}
        unavailable = [dict(resource='option', action='create', name='max-cache-ttl', value='3600'),
            dict(resource='record', action='update', zone='example.test', name='www', type='A', values=['192.0.2.93']),
            dict(resource='record', action='delete', zone='batch.test', name='app', type='A'),
            dict(resource='zone', action='update', name='forward.test', forwarders=[{'address': self.upstream, 'port': 5355}])]
        try:
            self.batch(unavailable)
        except self.module.ManagementError as error:
            assert 'rolled-back' in str(error), str(error)
        else:
            raise AssertionError('A mixed batch with an unavailable forwarder was committed')
        assert all(path.read_bytes() == data for path, data in before.items())
        assert self.query('www.example.test') == '192.0.2.92'
        assert self.query('app.batch.test') == '192.0.2.92'
        assert self.query('app.forward.test') == '192.0.2.100'
        invalid = [create[0], dict(resource='record', action='update', zone='example.test', name='www', type='A', values=['999.0.0.1'])]
        reader = self.module.BatchManager(dict(resource='batch', action='read', operations=invalid), self.context, runner=self.command)
        try:
            with reader.operation():
                reader.run()
        except self.module.ManagementError:
            pass
        else:
            raise AssertionError('The native checker accepted invalid late RDATA')
        assert all(path.read_bytes() == data for path, data in before.items())
        self.batch([dict(resource='zone', action='delete', name='batch.test', purge_data=True),
            dict(resource='zone', action='delete', name='forward.test'),
            dict(resource='option', action='update', name='minimal-responses', value='no'),
            dict(resource='record', action='update', zone='example.test', name='www', type='A', values=['192.0.2.81'])])
        assert not Path('/var/named/zones/batch.test.zone').exists()
        assert self.query('app.batch.test') == '' and self.query('app.forward.test') == ''

    def run(self):
        assert os.environ.get('BIND_MANAGEMENT_ISOLATED') == '1' and os.geteuid() == 0
        self.prepare()
        # A real operator's digrc must never alter machine-parsed health data.
        client_home = Path('/run/named/probe-home')
        client_home.mkdir(mode=0o700, exist_ok=True)
        (client_home / '.digrc').write_text('+multiline\n+short\n')
        os.environ['HOME'] = str(client_home)
        Path('/run/named/upstream.zone').write_text('$TTL 300\n@ IN SOA ns.forward.test. hostmaster.forward.test. (1 3600 900 604800 300)\n@ IN NS ns.forward.test.\nns IN A ' + self.upstream + '\napp IN A 192.0.2.100\n')
        Path('/run/named/upstream.conf').write_text('options {directory "/run/named"; listen-on port 5353 {' + self.upstream + ';}; listen-on port 5354 {' + self.upstream + ';}; listen-on-v6 {none;}; recursion no; dnssec-validation no; pid-file "/run/named/upstream.pid"; session-keyfile "/run/named/upstream-session.key";}; controls {}; zone "forward.test" {type master; file "upstream.zone";};')
        with Path('/run/named/daemon-test.log').open('w') as log:
            upstream = subprocess.Popen([str(self.daemon), '-g', '-c', '/run/named/upstream.conf', '-n', '1', '-U', '1'], stdout=log, stderr=log)
            daemon = subprocess.Popen([str(self.daemon), '-g', '-c', '/etc/named.conf', '-n', '1', '-U', '1'], stdout=log, stderr=log)
            try:
                for attempt in range(50):
                    try:
                        if self.query('localhost') == '127.0.0.1':
                            break
                    except self.module.ManagementError:
                        pass
                    assert daemon.poll() is None, Path('/run/named/daemon-test.log').read_text()
                    time.sleep(0.1)
                else:
                    raise AssertionError('Native BIND did not start')
                self.action('option', 'update', 'minimal-responses', value='no')
                assert '// operator annotation must survive' in Path('/etc/named.conf').read_text()
                self.action('record', 'update', 'www', zone='example.test', type='A', values=['192.0.2.81'])
                assert self.query('www.example.test') == '192.0.2.81'
                self.action('record', 'create', 'v6', zone='example.test', type='AAAA', values=['2001:db8::80'])
                assert self.query('v6.example.test', 'AAAA') == '2001:db8::80'
                self.action('record', 'delete', 'v6', zone='example.test', type='AAAA')
                assert self.query('v6.example.test', 'AAAA') == ''
                self.action('zone', 'create', 'new.test', kind='authoritative')
                assert self.query('new.test', 'SOA').split()[2] == '1'
                self.action('record', 'create', 'app', zone='new.test', type='A', values=['192.0.2.90'])
                assert self.query('app.new.test') == '192.0.2.90'
                self.action('zone', 'delete', 'new.test', purge_data=True)
                assert not Path('/var/named/zones/new.test.zone').exists()
                assert self.query('app.new.test') == ''
                self.action('zone', 'create', 'forward.test', kind='forward', forwarders=[{'address': self.upstream, 'port': 5353}], dnssec_validate=False)
                assert self.query('app.forward.test') == '192.0.2.100'
                before = Path('/etc/named.conf').read_bytes()
                revision = self.module.Configuration(self.module.SafeFiles()).revision()
                unreachable = self.module.Manager(dict(resource='zone', action='update', name='forward.test',
                    forwarders=[{'address': self.upstream, 'port': 5355}], expected_checksum=revision), self.context, runner=self.command)
                try:
                    with unreachable.operation():
                        unreachable.run()
                except self.module.ManagementError as error:
                    assert 'rolled-back' in str(error), str(error)
                else:
                    raise AssertionError('A cached SOA concealed an unavailable replacement forwarder')
                assert Path('/etc/named.conf').read_bytes() == before
                assert self.query('app.forward.test') == '192.0.2.100'
                self.action('zone', 'update', 'forward.test', forwarders=[{'address': self.upstream, 'port': 5354}])
                assert self.query('app.forward.test') == '192.0.2.100'
                self.action('zone', 'delete', 'forward.test')
                assert self.query('app.forward.test') == ''
                self.batches()
                print(json.dumps({'transactions': self.transactions, 'dnssec': self.dnssec, 'native_validation_reload_wire_dns': True, 'real_archives': True, 'unavailable_forwarder_rollback_after_cached_answer': True, 'mixed_batch_transactions': 2, 'joint_batch_rollback': True, 'invalid_late_batch_rdata_refused': True, 'catalogs_native': True, 'selinux_double': True}))
            finally:
                daemon.terminate()
                daemon.wait(timeout=10)
                upstream.terminate()
                upstream.wait(timeout=10)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bin-dir', type=Path, required=True)
    parser.add_argument('--native-dir', type=Path, required=True)
    parser.add_argument('--named', type=Path, required=True)
    args = parser.parse_args()
    NativeManagement(args.bin_dir, args.native_dir, args.named).run()

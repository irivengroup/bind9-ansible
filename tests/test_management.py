"""Behavioral regression tests for targeted static configuration transactions.

The fake runner isolates runtime commands; separate native integration checks
exercise the BIND checker and the Ansible module transport.
"""
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

SPEC = importlib.util.spec_from_file_location('maintenance', Path(__file__).resolve().parents[1] / 'roles/bind9/module_utils/bind9_management.py')
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)
pytestmark = pytest.mark.skipif(os.name != 'posix' or os.geteuid() != 0, reason='Requires an isolated Linux root test environment')

ZONE = '''; manual comment survives
$TTL 300
@ IN SOA ns1.example.test. hostmaster.example.test. ( 41 3600 900 604800 300 )
@ IN NS ns1.example.test.
ns1 IN A 192.0.2.11
www IN A 192.0.2.80
txt IN TXT "a; b" "two strings"
'''
ROOT_ZONE = '''$TTL 60
@ IN SOA localhost. hostmaster.localhost. ( 7 3600 900 604800 60 )
@ IN NS localhost.
localhost. IN A 127.0.0.1
'''
CONF = '''// operator comment before options
options { directory "/var/named"; dnssec-validation no;
  minimal-responses yes; // keep this comment
};
key "site-transfer" { algorithm hmac-sha256; secret "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="; };
acl bind_secondary_back_ip { 10.0.0.12; };
acl bind_primary_back_ip { 10.0.0.11; };
zone "." { type master; file "empty-root.zone"; };
zone "example.test" { type master; file "zones/example.test.zone"; allow-update { none; }; };
'''
CONTEXT = dict(primary=True, primary_front='192.0.2.11', secondary_front='192.0.2.12', primary_back='10.0.0.11', secondary_back='10.0.0.12', tsig_key_name='site-transfer', named_gid=0)


class Lab:
    """Private static deployment and observable native command boundary."""

    def __init__(self, root):
        self.root, self.calls, self.fail = root, [], None
        for name in ('etc', 'var/named/zones', 'var/named/slaves', 'usr/local/sbin'):
            (root / name).mkdir(parents=True, exist_ok=True)
        self.put('/etc/named.conf', CONF)
        self.put('/var/named/empty-root.zone', ROOT_ZONE)
        self.put('/var/named/zones/example.test.zone', ZONE)
        self.put('/usr/local/sbin/bind-configtool', '# private helper\n', 0o700)

    def put(self, name, text, mode=0o640):
        path = self.root / name.lstrip('/')
        path.write_text(text)
        path.chmod(mode)
        return path

    def runner(self, argv):
        self.calls.append(argv)
        if self.fail:
            self.fail(argv)
        if Path(argv[0]).name == 'named-checkconf' and os.environ.get('BIND_MANAGEMENT_CHECKCONF'):
            result = subprocess.run([os.environ['BIND_MANAGEMENT_CHECKCONF']] + argv[1:], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=30)
            if result.returncode:
                raise M.ManagementError('Native checker rejected the test candidate: ' + result.stdout)
        if Path(argv[0]).name == 'bind-configtool':
            return 'BACKUP_CREATED /var/backups/Bind9-Ansible/bind/test.tar.gz\n'
        if Path(argv[0]).name == 'dig':
            name, kind = argv[-2:]
            if kind == 'A':
                return ';; status: NOERROR\nlocalhost. 60 IN A 127.0.0.1\n'
            config = M.Configuration(M.SafeFiles(self.root))
            zone = config.zones.get(name)
            serial = M.ZoneDocument(config.contents[zone['file']].decode(), name).serial().text if zone and zone.get('file') in config.contents else '1'
            return ';; status: NOERROR\n;; flags: qr aa;\n' + name + ' 300 IN SOA ns1.example.test. hostmaster.example.test. ' + serial + ' 3600 900 604800 300\n'
        return ''

    def manager(self, resource='option', action='read', name='minimal-responses', **fields):
        return M.Manager(dict(resource=resource, action=action, name=name, **fields), dict(CONTEXT), root=self.root, runner=self.runner)

    def write(self, resource='option', action='update', name='minimal-responses', **fields):
        revision = M.Configuration(M.SafeFiles(self.root)).revision()
        manager = self.manager(resource, action, name, expected_checksum=revision, **fields)
        with manager.lease():
            result = manager.run()
        return result


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


def test_option_crud_keeps_manual_text_and_private_backup(lab):
    read = lab.manager().run()
    assert read['value'] == 'yes' and not read['changed']
    changed = lab.write(value='no')
    assert changed['changed'] and changed['checksum'] != read['checksum']
    text = (lab.root / 'etc/named.conf').read_text()
    assert text == CONF.replace('minimal-responses yes;', 'minimal-responses no;')
    journal = json.loads(Path(changed['backup'], 'manifest.json').read_text())
    assert journal['status'] == 'committed' and journal['full_archive'].endswith('test.tar.gz')
    assert stat.S_IMODE(Path(changed['backup']).stat().st_mode) == 0o700
    assert lab.write(value='no')['changed'] is False
    lab.write(action='delete')
    assert not lab.manager().run()['found']
    lab.write(action='create', value='yes')
    assert lab.manager().run()['value'] == 'yes'


@pytest.mark.parametrize('checksum', [None, '', '0' * 64, True, 42])
def test_missing_or_stale_checksum_cannot_write(lab, checksum):
    with pytest.raises(M.ManagementError, match='Checksum'):
        lab.manager(action='update', value='no', expected_checksum=checksum).run()
    assert (lab.root / 'etc/named.conf').read_text() == CONF and not lab.calls


def test_manual_change_in_include_or_zone_invalidates_revision(lab):
    checksum = lab.manager().run()['checksum']
    lab.put('/var/named/zones/example.test.zone', ZONE + '; intervening operator change\n')
    with pytest.raises(M.ManagementError, match='Checksum'):
        lab.manager(action='update', value='no', expected_checksum=checksum).run()


@pytest.mark.parametrize('name,value', [('allow-query', '{ any; }'), ('directory', '"/tmp"'), ('dnssec-validation', 'no'), ('minimal-responses', 'no; recursion yes'), ('bad; name', 'yes'), ('minimal-responses', ''), ('minimal-responses', None)])
def test_invalid_or_owned_options_refused_before_commands(lab, name, value):
    with pytest.raises(M.ManagementError):
        lab.write(name=name, value=value)
    assert not lab.calls


def test_record_rrset_crud_preserves_unrelated_records(lab):
    first = lab.write('record', 'update', 'www', zone='example.test', type='A', values=['192.0.2.81', '192.0.2.82'])
    assert first['serials'] == {'example.test.': '42'}
    result = lab.manager('record', 'read', 'www', zone='example.test', type='A').run()
    assert result['values'] == ['192.0.2.81', '192.0.2.82']
    assert 'txt IN TXT "a; b" "two strings"' in (lab.root / 'var/named/zones/example.test.zone').read_text()
    lab.write('record', 'delete', 'www', zone='example.test', type='A')
    assert not lab.manager('record', 'read', 'www', zone='example.test', type='A').run()['found']
    lab.write('record', 'create', 'www', zone='example.test', type='AAAA', values=['2001:db8::80'], ttl=600)
    assert lab.manager('record', 'read', 'www', zone='example.test', type='AAAA').run()['values'] == ['2001:db8::80']


@pytest.mark.parametrize('fields', [dict(name='elsewhere.', type='A', values=['192.0.2.1']), dict(name='www', type='SOA', values=['bad']), dict(name='www', type='A', values=['192.0.2.1; hidden']), dict(name='www', type='A', values=[]), dict(name='www', type='A', values=['192.0.2.1\nfoo']), dict(name='www', type='A', values=['192.0.2.1'], ttl=True)])
def test_invalid_record_request_refused(lab, fields):
    name = fields.pop('name')
    with pytest.raises(M.ManagementError):
        lab.write('record', 'update', name, zone='example.test', **fields)


def test_inherited_owner_change_refused_without_touching_file(lab):
    source = ZONE + 'child IN A 192.0.2.1\n      IN TXT "inherited"\n'
    lab.put('/var/named/zones/example.test.zone', source)
    with pytest.raises(M.ManagementError, match='inherited'):
        lab.write('record', 'delete', 'child', zone='example.test', type='A')
    assert (lab.root / 'var/named/zones/example.test.zone').read_text() == source


def test_multiline_soa_comment_and_serial_wrap():
    source = ZONE.replace('41 3600', '4294967295 ; serial\n 3600')
    doc = M.ZoneDocument(source, 'example.test.')
    assert doc.serial().text == '4294967295'
    assert '1 ; serial' in doc.bump()
    assert '"a; b"' in doc.change('update', 'www.example.test.', 'A', ['192.0.2.99'])


def test_forward_zone_crud_updates_only_required_cuts(lab):
    result = lab.write('zone', 'create', 'forward.test', kind='forward', forwarders=[{'address': '10.10.0.53'}])
    assert result['serials'] == {'.': '8'}
    root = (lab.root / 'var/named/empty-root.zone').read_text()
    assert 'forward.test. IN NS bind-forwarder-1.forward.test.' in root
    assert 'bind-forwarder-1.forward.test. IN A 10.10.0.53' in root
    assert lab.manager('zone', 'read', 'forward.test').run()['forwarders'] == [{'address': '10.10.0.53', 'port': 53}]
    lab.write('zone', 'update', 'forward.test', forwarders=[{'address': '2001:db8::53', 'port': 5353}])
    assert lab.manager('zone', 'read', 'forward.test').run()['forwarders'][0]['port'] == 5353
    root = (lab.root / 'var/named/empty-root.zone').read_text()
    assert ' IN AAAA 2001:db8::53' in root and ' IN A 10.10.0.53' not in root
    lab.write('zone', 'delete', 'forward.test')
    assert not lab.manager('zone', 'read', 'forward.test').run()['found']
    assert 'forward.test.' not in (lab.root / 'var/named/empty-root.zone').read_text()


@pytest.mark.parametrize('address,port', [('127.0.0.1', 53), ('::1', 53), ('::ffff:127.0.0.1', 53), ('192.0.2.12', 53), ('224.0.0.1', 53), ('0.0.0.0', 53), ('fe80::1', 53), ('garbage', 53), ('10.0.0.53', 0), ('10.0.0.53', True)])
def test_forwarder_loops_and_invalid_endpoints_refused(lab, address, port):
    with pytest.raises(M.ManagementError):
        lab.write('zone', 'create', 'forward.test', kind='forward', forwarders=[dict(address=address, port=port)])


def test_forward_delegation_manual_conflict_is_preserved(lab):
    lab.write('zone', 'create', 'forward.test', kind='forward', forwarders=[{'address': '10.10.0.53'}])
    path = lab.root / 'var/named/empty-root.zone'
    path.write_text(path.read_text().replace('10.10.0.53', '10.10.0.54'))
    with pytest.raises(M.ManagementError, match='manually|conflicts'):
        lab.write('zone', 'update', 'forward.test', forwarders=[{'address': '10.10.0.55'}])
    assert '10.10.0.54' in path.read_text()


@pytest.mark.parametrize('name', ['.', 'localhost', 'sub.localhost', '127.in-addr.arpa', 'arpa', '*.test', '_service.test'])
def test_native_zones_and_ambiguous_names_protected(lab, name):
    with pytest.raises(M.ManagementError):
        lab.write('zone', 'create', name, kind='authoritative')


def test_authoritative_create_update_delete_retains_data_by_default(lab):
    result = lab.write('zone', 'create', 'new.test', kind='authoritative')
    assert result['serials']['new.test.'] == '1'
    path = lab.root / 'var/named/zones/new.test.zone'
    assert path.exists() and 'notify explicit' in (lab.root / 'etc/named.conf').read_text()
    lab.write('zone', 'update', 'new.test', settings={'max-zone-ttl': '86400'})
    lab.write('zone', 'delete', 'new.test')
    assert path.exists() and not lab.manager('zone', 'read', 'new.test').run()['found']
    with pytest.raises(M.ManagementError, match='pre-existing'):
        lab.write('zone', 'create', 'new.test', kind='authoritative')


def test_explicit_purge_deletes_only_target_zone_data(lab):
    result = lab.write('zone', 'delete', 'example.test', purge_data=True)
    assert not (lab.root / 'var/named/zones/example.test.zone').exists()
    assert (lab.root / 'var/named/empty-root.zone').exists()
    assert 'var/named/zones/example.test.zone' in ''.join(result['paths'])


def test_check_mode_validates_candidate_without_runtime_or_backup(lab):
    manager = lab.manager(action='update', value='no', expected_checksum=lab.manager().run()['checksum'])
    manager.check = True
    result = manager.run()
    assert result['check_mode'] and (lab.root / 'etc/named.conf').read_text() == CONF
    assert len(lab.calls) == 1 and Path(lab.calls[0][0]).name == 'named-checkconf'
    assert not (lab.root / 'var/backups').exists()


def test_invalid_candidate_never_publishes(lab):
    def reject(argv):
        if Path(argv[0]).name == 'named-checkconf':
            raise M.ManagementError('native checker rejected candidate')
    lab.fail = reject
    with pytest.raises(M.ManagementError, match='checker'):
        lab.write(value='invalid')
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert not (lab.root / 'var/backups').exists()


def test_checksum_rechecked_after_validation(lab):
    def intervene(argv):
        if Path(argv[0]).name == 'named-checkconf':
            lab.put('/etc/named.conf', CONF + '// manual intervening write\n')
    lab.fail = intervene
    with pytest.raises(M.ManagementError, match='changed while'):
        lab.write(value='no')
    assert 'manual intervening' in (lab.root / 'etc/named.conf').read_text()


def test_reload_failure_restores_only_this_transaction(lab):
    failures = [True]
    def reject(argv):
        if Path(argv[0]).name == 'rndc' and failures:
            failures.pop()
            raise M.ManagementError('reload failed')
    lab.fail = reject
    with pytest.raises(M.ManagementError, match='rolled-back'):
        lab.write(value='no')
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert not (lab.root / 'var/lib/Bind9-Ansible/.operation.lock').exists()
    assert json.loads(next((lab.root / 'var/backups').rglob('manifest.json')).read_text())['status'] == 'rolled-back'


def test_error_after_rename_also_restores_file(lab, monkeypatch):
    original = M.Manager.publish
    failures = [True]
    def fail_after_publish(manager, *args, **kwargs):
        original(manager, *args, **kwargs)
        if failures:
            failures.pop()
            raise OSError('directory fsync failed after rename')
    monkeypatch.setattr(M.Manager, 'publish', fail_after_publish)
    with pytest.raises(M.ManagementError, match='rolled-back'):
        lab.write(value='no')
    assert (lab.root / 'etc/named.conf').read_text() == CONF


def test_rollback_conflict_preserves_manual_write_and_lease(lab):
    def conflict(argv):
        if Path(argv[0]).name == 'rndc':
            lab.put('/etc/named.conf', CONF + '// operator changed during reload\n')
            raise M.ManagementError('reload failed')
    lab.fail = conflict
    with pytest.raises(M.ManagementError, match='recovery-required'):
        lab.write(value='no')
    assert 'operator changed' in (lab.root / 'etc/named.conf').read_text()
    assert (lab.root / 'var/lib/Bind9-Ansible/.operation.lock').is_dir()


def test_shared_lease_rejects_concurrent_operations(lab):
    first = lab.manager()
    with first.lease():
        with pytest.raises(M.ManagementError, match='Another operation'):
            with lab.manager().lease():
                pytest.fail('must not enter a second transaction')


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'writable', 'fifo'])
def test_unsafe_configuration_files_refused(lab, kind):
    path = lab.root / 'etc/named.conf'
    if kind == 'symlink':
        path.unlink()
        path.symlink_to(lab.root / 'var/named/empty-root.zone')
    elif kind == 'hardlink':
        os.link(str(path), str(path) + '.linked')
    elif kind == 'writable':
        path.chmod(0o666)
    else:
        path.unlink()
        os.mkfifo(str(path), 0o600)
    with pytest.raises((OSError, M.ManagementError)):
        lab.manager().run()


@pytest.mark.parametrize('source', ['options { x yes;', 'options { x "unterminated; };', 'x yes; }', 'x /* unclosed'])
def test_incomplete_configuration_rejected(source):
    with pytest.raises(M.ManagementError):
        M.Syntax(source)


def test_forward_creation_cannot_hide_existing_manual_authority(lab):
    lab.put('/var/named/empty-root.zone', ROOT_ZONE + 'existing.forward.test. IN A 192.0.2.99\n')
    with pytest.raises(M.ManagementError, match='conflicts'):
        lab.write('zone', 'create', 'forward.test', kind='forward', forwarders=[{'address': '10.10.0.53'}])
    assert not lab.manager('zone', 'read', 'forward.test').run()['found']


def test_record_write_cannot_hide_below_an_existing_forward_cut(lab):
    lab.write('zone', 'create', 'forward.example.test', kind='forward', forwarders=[{'address': '10.10.0.53'}])
    with pytest.raises(M.ManagementError, match='hidden'):
        lab.write('record', 'create', 'www.forward', zone='example.test', type='A', values=['192.0.2.99'])

"""Native zone publication and reconciliation against a real private archive."""
import json
import os
from pathlib import Path
import tarfile

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeUnitBoundary, NativeZoneRun
from test_management import M
from test_native_directories import named_identity
from test_native_preflight import scope
from test_reverse_serial_archive import backup

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')
REVERSE = '2.0.192.in-addr.arpa'


@pytest.fixture
def zones(tmp_path):
    with NativeFilesystemScope(NativeZoneRun):
        _, gid = named_identity()
        Path('/var/named/zones').mkdir(parents=True)
        with NativeUnitBoundary(tmp_path, {'named': {'active': True, 'enabled': True}}) as state:
            yield gid, state


@pytest.mark.parametrize('primary', [False, True])
@pytest.mark.parametrize('populated', [False, True])
@pytest.mark.parametrize('delegations', [False, True])
def test_native_zone_outputs_permissions_retirement_and_serial_helper(zones, tmp_path, primary, populated, delegations):
    gid, state = zones
    hosts, groups = scope()
    values = dict(hosts['localhost'])
    forwarders = [{'address': '198.51.100.53'}, {'address': '2001:db8::53'}]
    forwarded = [{'domain': name, 'forwarders': forwarders} for name in
                 ('corp.example.test', 'outside.test', '0.' + REVERSE)] if delegations else []
    reverse = {'domain': REVERSE, 'serial': 8, 'records': [{'name': '20', 'value': 'www.example.test.'}]}
    values.update(bind_authoritative_zones=values['bind_authoritative_zones'] if populated else [],
                  bind_generated_reverse_zones=[reverse] if populated else [], bind_forward_zones=forwarded,
                  bind_managed_zones=values['bind_authoritative_zones'] + [reverse] if populated else [],
                  group_names=['dns_primary' if primary else 'dns_secondary'])
    archive = backup(tmp_path, [])
    values['bind_recovery_archive'] = str(archive)
    obsolete = Path('/var/named/zones/obsolete.in-addr.arpa.zone')
    obsolete.write_text('synthetic previously inventoried managed reverse file\n', encoding='utf-8')
    retained = Path('/var/named/zones/' + REVERSE + '.zone')
    retained.write_text('synthetic prior record\n', encoding='utf-8')
    values['bind_existing_reverse_files'] = {'files': [{'path': str(path)} for path in (obsolete, retained)]}
    status, result = NativeZoneRun('tasks.d/deploy/zones.yml', values,
        inventory_hosts=hosts, inventory_groups=groups).run()
    assert status == 0 and not result.failed
    assert obsolete.exists() is not primary
    assert retained.exists() is (not primary or populated)
    root = Path('/var/named/empty-root.zone')
    assert root.is_file() and root.stat().st_uid == 0 and root.stat().st_gid == gid and root.stat().st_mode & 0o777 == 0o640
    rendered = root.read_text(encoding='utf-8')
    assert ('outside.test. IN NS' in rendered) is delegations
    if primary and populated:
        for name, owner, kind, data in [('example.test', 'www', 'A', '192.0.2.20'),
                                         (REVERSE, '20', 'PTR', 'www.example.test.')]:
            path = Path('/var/named/zones/' + name + '.zone')
            info = path.stat()
            assert info.st_uid == 0 and info.st_gid == gid and info.st_mode & 0o777 == 0o640
            document = M.ZoneDocument(path.read_text(encoding='utf-8'), name + '.')
            record = document.rrset(owner + '.' + name + '.', kind)
            assert len(record) == 1 and [token.text for token in record[0]['tokens']] == [data]
            if delegations:
                cut = 'corp.example.test.' if name == 'example.test' else '0.' + REVERSE + '.'
                assert len(document.rrset(cut, 'NS')) == 2
                assert document.rrset('bind-forwarder-2.' + cut, 'AAAA')[0]['tokens'][0].text == '2001:db8::53'
    assert Path('/usr/local/sbin/reverse-serial').exists() is primary
    if primary:
        script = Path('/usr/local/sbin/reverse-serial')
        assert script.read_bytes() == (Path(__file__).resolve().parents[1] / 'roles/bind9/files/reverse-serial.py').read_bytes()
        assert script.stat().st_mode & 0o777 == 0o700
    assert {'name': 'named', 'state': 'reloaded'} in json.loads(state.read_text())['operations']


def test_native_reverse_reconciliation_uses_real_predeployment_bytes(zones, tmp_path):
    from test_reverse_serial import TestReverseSerial as ReverseTemplate
    hosts, groups = scope()
    values = dict(hosts['localhost'], group_names=['dns_primary'])
    reverse = {'domain': REVERSE, 'serial': 8, 'records': [{'name': '10', 'value': 'changed.example.test.'}]}
    prior = ReverseTemplate().render(serial=25)
    archive = backup(tmp_path, [('var/named/zones/' + REVERSE + '.zone', prior.encode('utf-8'), tarfile.REGTYPE)])
    values.update(bind_generated_reverse_zones=[reverse], bind_managed_zones=values['bind_authoritative_zones'] + [reverse],
                  bind_existing_reverse_files={'files': []}, bind_recovery_archive=str(archive))
    status, result = NativeZoneRun('tasks.d/deploy/zones.yml', values,
        inventory_hosts=hosts, inventory_groups=groups).run()
    assert status == 0 and not result.failed
    target = Path('/var/named/zones/' + REVERSE + '.zone')
    document = M.ZoneDocument(target.read_text(encoding='utf-8'), REVERSE + '.')
    assert document.serial().text == '26'
    assert document.rrset('10.' + REVERSE + '.', 'PTR')[0]['tokens'][0].text == 'changed.example.test.'

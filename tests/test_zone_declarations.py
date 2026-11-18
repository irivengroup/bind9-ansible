"""The same zone definitions can be used in inventory or in management."""
import hashlib
import json

import pytest

from test_management import CONF, CONTEXT, Lab, M, ZONE, pytestmark as management_pytestmark

pytestmark = management_pytestmark


def manager(lab, action, zone, kind=None, check=False, **fields):
    request = dict(resource='zone', action=action, zone=zone, **fields)
    if kind is not None:
        request['kind'] = kind
    if action in ('create', 'update', 'delete'):
        request['expected_checksum'] = M.Configuration(M.SafeFiles(lab.root)).revision()
    return M.Manager(request, dict(CONTEXT), root=lab.root, runner=lab.runner, check=check)


def run(operation):
    with operation.lease():
        return operation.run()


def test_inventory_forward_object_through_crud_and_port_normalization(tmp_path):
    lab = Lab(tmp_path)
    zone = {'domain': 'corp.test', 'policy': 'only', 'dnssec_validate': True,
            'forwarders': [{'address': '192.0.2.53', 'port': '0053'}]}
    assert run(manager(lab, 'create', zone, 'forward'))['changed']
    assert manager(lab, 'read', {'domain': zone['domain']}).run()['forwarders'] == [
        {'address': '192.0.2.53', 'port': 53}]
    zone['forwarders'] = [{'address': '198.51.100.4', 'port': 5300},
                          {'address': '2001:db8::53', 'port': 53}]
    assert run(manager(lab, 'update', zone, 'forward'))['changed']
    assert manager(lab, 'read', {'domain': zone['domain']}).run()['forwarders'] == zone['forwarders']
    assert run(manager(lab, 'delete', {'domain': zone['domain']}))['changed']
    assert manager(lab, 'read', {'domain': zone['domain']}).run()['found'] is False


def test_authority_initial_records_and_update_preserve_unmentioned_rrsets(tmp_path):
    lab = Lab(tmp_path)
    zone = {'domain': 'app.test', 'serial': '2026111501', 'ttl': '500', 'records': [
        {'name': 'www', 'type': 'A', 'value': '192.0.2.10'},
        {'name': 'www', 'type': 'AAAA', 'value': '2001:db8::10'},
        {'name': 'www', 'type': 'A', 'value': '192.0.2.11'},
        {'name': 'unmentioned', 'type': 'TXT', 'value': '"kept"'},
        {'name': 'ns1', 'type': 'A', 'value': '192.0.2.88'},
    ]}
    assert run(manager(lab, 'create', zone, 'authoritative'))['changed']
    path = lab.root / 'var/named/zones/app.test.zone'
    created = M.ZoneDocument(path.read_text(), 'app.test.')
    assert created.serial().text == '2026111501'
    assert len(created.rrset('www.app.test.', 'A')) == 2
    assert [rr['tokens'][0].text for rr in created.rrset('ns1.app.test.', 'A')] == ['192.0.2.11', '192.0.2.88']
    assert created.rrset('www.app.test.', 'AAAA')[0]['ttl'] == '500'
    update = {'domain': zone['domain'], 'serial': 2026111550, 'ttl': 600,
              'records': [{'name': 'www', 'type': 'A', 'value': '192.0.2.33'}]}
    assert run(manager(lab, 'update', update, 'authoritative'))['changed']
    revised = M.ZoneDocument(path.read_text(), 'app.test.')
    assert revised.serial().text == '2026111550'
    assert revised.rrset('www.app.test.', 'A')[0]['tokens'][0].text == '192.0.2.33'
    assert revised.rrset('www.app.test.', 'AAAA')[0]['ttl'] == '600'
    assert revised.rrset('unmentioned.app.test.', 'TXT')[0]['tokens'][0].text == '"kept"'
    assert not run(manager(lab, 'update', update, 'authoritative'))['changed']
    assert run(manager(lab, 'update', {'domain': zone['domain'], 'ttl': 700}, 'authoritative'))['changed']
    assert not run(manager(lab, 'update', {'domain': zone['domain'], 'ttl': 700}, 'authoritative'))['changed']
    assert M.ZoneDocument(path.read_text(), 'app.test.').rrset('www.app.test.', 'A')[0]['ttl'] == '700'
    assert manager(lab, 'read', {'domain': zone['domain']}).run()['serial'] == '2026111551'
    with pytest.raises(M.ManagementError, match='RFC 1982'):
        run(manager(lab, 'update', dict(update, ttl=701), 'authoritative'))
    before = path.read_bytes()
    calls = len(lab.calls)
    with pytest.raises(M.ManagementError, match='RFC 1982'):
        run(manager(lab, 'update', dict(update, serial=2026111551, ttl=701), 'authoritative'))
    assert path.read_bytes() == before and len(lab.calls) == calls


@pytest.mark.parametrize('ttl,seconds', [('1h30m', 5400), (0, 0)])
def test_authoritative_declaration_accepts_the_same_default_ttl_as_inventory(tmp_path, ttl, seconds):
    lab = Lab(tmp_path)
    zone = {'domain': 'ttl.test', 'serial': 42, 'ttl': ttl}
    assert run(manager(lab, 'create', zone, 'authoritative'))['changed']
    assert '$TTL ' + str(seconds) + '\n' in (lab.root / 'var/named/zones/ttl.test.zone').read_text()


def test_declarative_updates_do_not_adopt_external_primary_files_or_change_zone_type(tmp_path):
    lab = Lab(tmp_path)
    with pytest.raises(M.ManagementError, match='managed primary zone data'):
        run(manager(lab, 'update', {'domain': 'example.test', 'serial': 42}, 'authoritative'))
    with pytest.raises(M.ManagementError, match='cannot change the existing zone kind'):
        run(manager(lab, 'update', {'domain': 'example.test', 'forwarders': [{'address': '198.51.100.53'}]}, 'forward'))
    path = lab.put('/var/named/operator.zone', ZONE)
    lab.put('/etc/named.conf', CONF.replace('zones/example.test.zone', 'operator.zone'))
    with pytest.raises(M.ManagementError, match='require primary data under /var/named/zones'):
        run(manager(lab, 'update', {'domain': 'example.test', 'serial': 42}, 'authoritative'))
    assert path.read_text() == ZONE
    assert not lab.calls


def test_declarative_authority_update_preserves_the_secondary_transfer_file(tmp_path):
    lab = Lab(tmp_path)
    secondary = CONF.replace('type master; file "zones/example.test.zone"',
                             'type slave; file "slaves/example.test.zone"')
    lab.put('/etc/named.conf', secondary)
    path = lab.put('/var/named/slaves/example.test.zone', ZONE)
    operation = manager(lab, 'update', {'domain': 'example.test', 'serial': 42, 'ttl': 600}, 'authoritative')
    assert run(operation)['changed'] is False
    assert path.read_text() == ZONE
    assert (lab.root / 'etc/named.conf').read_text() == secondary
    assert not lab.calls


@pytest.mark.parametrize('owner', ['child', 'host.child'])
def test_declarative_authority_update_preserves_an_existing_forwarding_cut(tmp_path, owner):
    lab = Lab(tmp_path)
    run(manager(lab, 'create', {'domain': 'app.test', 'serial': 42}, 'authoritative'))
    run(manager(lab, 'create', {'domain': 'child.app.test',
                                'forwarders': [{'address': '198.51.100.53'}]}, 'forward'))
    path = lab.root / 'var/named/zones/app.test.zone'
    before = path.read_bytes()
    calls = len(lab.calls)
    update = {'domain': 'app.test', 'records': [{'name': owner, 'type': 'A', 'value': '192.0.2.53'}]}
    with pytest.raises(M.ManagementError, match='below a forwarding cut'):
        run(manager(lab, 'update', update, 'authoritative'))
    assert path.read_bytes() == before
    assert len(lab.calls) == calls


@pytest.mark.parametrize('replacement', ['', '$TTL 300\n$TTL 300\n'])
def test_authoritative_ttl_edit_refuses_ambiguous_existing_defaults(tmp_path, replacement):
    lab = Lab(tmp_path)
    run(manager(lab, 'create', {'domain': 'app.test', 'serial': 42, 'ttl': 300}, 'authoritative'))
    path = lab.root / 'var/named/zones/app.test.zone'
    content = path.read_text()
    assert content.count('$TTL 300\n') == 1
    damaged = content.replace('$TTL 300\n', replacement)
    path.write_text(damaged)
    calls = len(lab.calls)
    with pytest.raises(M.ManagementError, match='one ordinary default TTL'):
        run(manager(lab, 'update', {'domain': 'app.test', 'ttl': 600}, 'authoritative'))
    assert path.read_text() == damaged
    assert len(lab.calls) == calls


def test_inventory_batch_digest_and_candidate_include_the_raw_declaration(tmp_path):
    lab = Lab(tmp_path)
    declaration = {'domain': 'new.test', 'serial': 77, 'records': [
        {'name': 'www', 'type': 'A', 'value': '198.51.100.2'}]}
    operations = [dict(resource='zone', action='create', kind='authoritative', zone=declaration)]
    batch = M.BatchManager(dict(resource='batch', action='read', operations=operations),
                           dict(CONTEXT), root=lab.root, runner=lab.runner)
    result = batch.run()
    assert result['batch_digest'] == hashlib.sha256(json.dumps(operations, sort_keys=True).encode()).hexdigest()
    assert result['operations'][0]['name'] == 'new.test'
    assert result['operations'][0]['before']['found'] is False
    assert result['operations'][0]['after']['serial'] == '77'
    assert b'198.51.100.2' in batch.changes['/var/named/zones/new.test.zone']
    assert not (lab.root / 'var/named/zones/new.test.zone').exists()


@pytest.mark.parametrize('zone,kind,fields,message', [
    ('app.test', 'forward', {}, 'valid domain'),
    ({'domain': None}, 'forward', {}, 'valid domain'),
    ({'domain': 'invalid.'}, 'forward', {}, 'valid domain'),
    ({'domain': 'app.test'}, 'unknown', {}, 'kind forward or authoritative'),
    ({'domain': 'app.test'}, 'forward', {'name': 'compat.test'}, 'inside zone only'),
    ({'domain': 'app.test', 'policy': 'first'}, 'forward', {}, 'policy only'),
    ({'domain': 'app.test', 'serial': 42}, 'forward', {}, 'policy only'),
    ({'domain': 'app.test', 'forwarders': {}}, 'forward', {}, 'nonempty list'),
    ({'domain': 'app.test', 'forwarders': []}, 'forward', {}, 'nonempty list'),
    ({'domain': 'app.test', 'forwarders': []}, 'authoritative', {}, 'uses domain, serial'),
    ({'domain': 'app.test'}, 'authoritative', {}, 'requires serial'),
    ({'domain': 'app.test', 'serial': True}, 'authoritative', {}, 'positive 32-bit integer'),
    ({'domain': 'app.test', 'serial': 'four'}, 'authoritative', {}, 'positive 32-bit integer'),
    ({'domain': 'app.test', 'serial': 0}, 'authoritative', {}, 'positive 32-bit integer'),
    ({'domain': 'app.test', 'serial': 4294967296}, 'authoritative', {}, 'positive 32-bit integer'),
    ({'domain': 'app.test', 'serial': 1, 'records': {}}, 'authoritative', {}, 'inventory-style list'),
    ({'domain': 'app.test', 'serial': 1, 'records': [{'name': 'www'}]}, 'authoritative', {}, 'name, type and value'),
    ({'domain': 'app.test', 'serial': 1, 'records': [{'name': 'www', 'type': 'SOA', 'value': 'a'}]},
     'authoritative', {}, 'record name or type'),
    ({'domain': 'app.test', 'serial': 1, 'records': [{'name': 'www', 'type': 'TXT', 'value': 'x\ny'}]},
     'authoritative', {}, 'printable one-line'),
    ({'domain': 'app.test', 'serial': 1, 'ttl': '10fortnights'}, 'authoritative', {}, 'Invalid default zone TTL'),
    ({'domain': 'app.test', 'serial': 1, 'ttl': True}, 'authoritative', {}, 'Invalid default zone TTL'),
    ({'domain': 'app.test', 'serial': 1, 'ttl': []}, 'authoritative', {}, 'Invalid default zone TTL'),
    ({'domain': 'app.test', 'serial': 1, 'ttl': '1s' * 65}, 'authoritative', {}, 'Invalid default zone TTL'),
    ({'domain': 'app.test', 'serial': 1, 'ttl': 2147483648}, 'authoritative', {}, 'Invalid default zone TTL'),
])
def test_invalid_zone_declarations_are_rejected_before_planning(tmp_path, zone, kind, fields, message):
    lab = Lab(tmp_path)
    with pytest.raises(M.ManagementError, match=message):
        manager(lab, 'create', zone, kind, **fields)


@pytest.mark.parametrize('action,kind,field,message', [
    ('update', 'authoritative', {}, 'with serial, ttl or records'),
    ('read', 'authoritative', {}, 'for a zone declaration write'),
    ('delete', None, {'ttl': 300}, 'for a zone declaration write'),
])
def test_zone_declaration_actions_require_their_exact_fields(tmp_path, action, kind, field, message):
    lab = Lab(tmp_path)
    with pytest.raises(M.ManagementError, match=message):
        manager(lab, action, dict(domain='app.test', **field), kind)


def test_additional_listeners_are_protected_from_forwarding_loops(tmp_path):
    lab = Lab(tmp_path)
    operation = M.Manager(dict(resource='zone', action='read', name='corp.test'),
                          dict(CONTEXT, service_addresses=['198.51.100.2']), root=lab.root)
    assert operation.forwarders([{'address': '198.51.100.2', 'port': 5300}])
    with pytest.raises(M.ManagementError, match='Invalid forwarder'):
        operation.forwarders([{'address': '198.51.100.2'}])
    malformed = M.Manager(dict(resource='zone', action='read', name='corp.test'),
                          dict(CONTEXT, service_addresses='198.51.100.2'), root=lab.root)
    with pytest.raises(M.ManagementError, match='DNS listener context must be a list'):
        malformed.forwarders([{'address': '198.51.100.53'}])


@pytest.mark.parametrize('key,value,message', [
    ('name', 53, 'record name or type'),
    ('name', 'bad/name', 'record name or type'),
    ('type', 1, 'record name or type'),
    ('value', 53, 'printable one-line value'),
    ('value', '', 'printable one-line value'),
    ('value', '192.0.2.53\x7f', 'printable one-line value'),
])
def test_invalid_inventory_record_fields_never_reach_bind(tmp_path, key, value, message):
    lab = Lab(tmp_path)
    record = {'name': 'www', 'type': 'A', 'value': '192.0.2.53'}
    record[key] = value
    with pytest.raises(M.ManagementError, match=message):
        manager(lab, 'create', {'domain': 'app.test', 'serial': 42, 'records': [record]}, 'authoritative')
    assert not lab.calls


def test_inventory_record_list_requires_mapping_entries(tmp_path):
    lab = Lab(tmp_path)
    zone = {'domain': 'app.test', 'serial': 42, 'records': ['www IN A 192.0.2.53']}
    with pytest.raises(M.ManagementError, match='requires name, type and value'):
        manager(lab, 'create', zone, 'authoritative')


@pytest.mark.parametrize('value', [' 192.0.2.53', '192.0.2.53 '])
def test_inventory_zone_rejects_rdata_with_outer_whitespace(tmp_path, value):
    lab = Lab(tmp_path)
    zone = {'domain': 'app.test', 'serial': 42,
            'records': [{'name': 'www', 'type': 'A', 'value': value}]}
    with pytest.raises(M.ManagementError, match='printable one-line value'):
        run(manager(lab, 'create', zone, 'authoritative'))
    assert not (lab.root / 'var/named/zones/app.test.zone').exists()
    assert not lab.calls


@pytest.mark.parametrize('endpoint,error', [
    ({'address': '::ffff:198.51.100.5'}, 'Invalid forwarder'),
    ({'address': '2001:db8::5%eth0'}, 'Invalid forwarder'),
    ({'address': '198.51.100.5', 'port': '53x'}, 'Invalid forwarder'),
    ('198.51.100.5', 'accepts address and optional port only'),
])
def test_declarative_forwarders_share_inventory_address_constraints(tmp_path, endpoint, error):
    lab = Lab(tmp_path)
    with pytest.raises(M.ManagementError, match=error):
        run(manager(lab, 'create', {'domain': 'corp.test', 'forwarders': [endpoint]}, 'forward'))
    assert not lab.calls


def test_declarative_forwarder_refuses_ipv4_alias_under_legacy_parser(tmp_path, monkeypatch, legacy_ipaddress):
    address = '192.000.2.53'
    assert str(legacy_ipaddress.ip_address(address)) == '192.0.2.53'
    monkeypatch.setattr(M, 'ipaddress', legacy_ipaddress)
    lab = Lab(tmp_path)
    with pytest.raises(M.ManagementError, match='Invalid forwarder'):
        run(manager(lab, 'create', {'domain': 'corp.test', 'forwarders': [{'address': address}]}, 'forward'))

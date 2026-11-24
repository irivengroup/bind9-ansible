"""One public resource model for cold deployment and targeted maintenance."""
import copy
import importlib.util
import os
from pathlib import Path

import pytest

from test_management import CONTEXT, Lab, M


def authority(name='service.example.test', **fields):
    return dict(resource='zone', action='create', name=name, kind='authoritative', **fields)


def record(name='app', **fields):
    return dict(resource='record', action='create', zone='service.example.test', name=name,
                type='A', values=['192.0.2.80', '192.0.2.81'], **fields)


def compile_resources(operations, **fields):
    return M.DeploymentResources(dict(resource='batch', action='create', operations=operations, **fields), CONTEXT).compile()


def test_documented_resource_list_keeps_ttls_serial_rdata_and_caller_bytes():
    operations = [authority(ttl=300, serial=1), record(ttl=90),
        dict(resource='record', action='create', zone='service.example.test', name='description',
             type='TXT', ttl=300, values=['"service de demonstration"']),
        dict(resource='zone', action='create', name='private.example.test', kind='forward', policy='only',
             dnssec_validate=False, forwarders=[{'address': '10.11.0.53', 'port': 53}, {'address': '10.11.0.54', 'port': 53}])]
    original = copy.deepcopy(operations)
    result = compile_resources(operations)
    assert operations == original
    zone, = result['authoritative_zones']
    data = M.ZoneDocument(zone['data'], 'service.example.test.')
    assert data.serial().text == '1'
    values = data.rrset('app.service.example.test.', 'A')
    assert [rr['tokens'][0].text for rr in values] == ['192.0.2.80', '192.0.2.81']
    assert [rr['ttl'] for rr in values] == ['90', '90']
    assert data.rrset('description.service.example.test.', 'TXT')[0]['tokens'][0].text == '"service de demonstration"'
    assert len(zone['records']) == 3
    assert result['forward_zones'][0] == dict(domain='private.example.test', policy='only', dnssec_validate=False,
        forwarders=[{'address': '10.11.0.53', 'port': 53}, {'address': '10.11.0.54', 'port': 53}])
    assert result['extra_operations'] == []


def test_single_resource_and_empty_desired_graph_are_explicit():
    assert M.DeploymentResources(authority(), CONTEXT).compile()['authoritative_zones'][0]['serial'] == 1
    assert compile_resources([]) == dict(authoritative_zones=[], forward_zones=[], extra_operations=[])
    request = dict(resource='batch', action='apply', operations=[authority()])
    assert M.DeploymentResources(request, CONTEXT).compile()['authoritative_zones'][0]['ttl'] == 300


def test_inventory_zone_mapping_and_apex_records_share_management_normalization():
    operation = dict(resource='zone', action='create', kind='authoritative', zone=dict(domain='Example.test',
        serial='42', ttl='5m', records=[dict(name='@', type='TXT', value='"apex"'),
                                      dict(name='app', type='A', value='192.0.2.90')]))
    result = compile_resources([operation])['authoritative_zones'][0]
    assert result['serial'] == 42 and result['ttl'] == 300
    assert result['records'] == [dict(name='@', type='TXT', value='"apex"'), dict(name='app', type='A', value='192.0.2.90')]
    forward = dict(resource='zone', action='create', kind='forward', zone=dict(domain='private.test',
        policy='only', forwarders=[{'address': '2001:db8::53', 'port': '5353'}]))
    assert compile_resources([forward])['forward_zones'][0]['forwarders'] == [{'address': '2001:db8::53', 'port': 5353}]


def test_all_customization_resource_types_keep_the_exact_order_and_schema():
    operations = [dict(resource='option', action='update', name='max-cache-size', value='128m'),
        dict(resource='acl', action='create', name='monitoring', value='192.0.2.40;'),
        dict(resource='logging', action='update', name='logging', value='category default { null; };')]
    result = compile_resources(operations)
    assert result['extra_operations'] == operations
    assert result['authoritative_zones'] == result['forward_zones'] == []


def test_repeated_compilation_and_mutable_return_values_cannot_change_the_owned_request():
    request = dict(resource='batch', action='create', operations=[authority(), record(),
        dict(resource='option', action='update', name='max-cache-size', value='128m')])
    compiler = M.DeploymentResources(request, CONTEXT)
    # Freeze the oracle independently: the old implementation returned aliases
    # that could otherwise mutate the expected result along with the actual one.
    expected = copy.deepcopy(compiler.compile())
    actual = compiler.compile()
    assert actual == expected
    actual['extra_operations'][0]['value'] = '1m'
    actual['authoritative_zones'][0]['records'].clear()
    request['operations'].clear()
    assert compiler.compile() == expected


@pytest.mark.parametrize('envelope', [None, [], 'bad', {},
    dict(resource='batch', action='read', operations=[]),
    dict(resource='batch', action='create', operations=[], expected_checksum='a' * 64),
    dict(resource='batch', action='create', operations=None),
    dict(resource='batch', action='create', operations=[authority()] * 65)])
def test_bad_envelopes_fail_before_materialization(envelope):
    with pytest.raises(M.ManagementError):
        M.DeploymentResources(envelope, CONTEXT).compile()


@pytest.mark.parametrize('operations', [
    [authority(), authority('SERVICE.EXAMPLE.TEST')], [record()],
    [dict(authority(), action='update')], [authority(name='localhost')], [authority(name='foo.localhost')],
    [authority(name='arpa')],
    [dict(resource='zone', action='update', name='existing.test', settings={'notify': 'no'})],
    [authority(name='.')], [authority(name='invalid_name.test')],
    [dict(authority(), kind='invalid')], [authority(policy='only')],
    [authority(forwarders=[])], [authority(ttl=True)], [authority(serial=0)],
    [dict(resource='zone', action='create', name='private.test', kind='forward', policy='first', forwarders=[{'address': '10.0.0.53'}])],
    [dict(resource='zone', action='create', name='private.test', kind='forward', ttl=300, forwarders=[{'address': '10.0.0.53'}])],
    [dict(resource='zone', action='create', name='private.test', kind='forward', dnssec_validate='false', forwarders=[{'address': '10.0.0.53'}])],
    [dict(resource='zone', action='create', name='private.test', kind='forward', forwarders=[{'address': '127.0.0.1'}])],
    [authority(), dict(record(), type='SOA')], [authority(), dict(record(), values=['1; include "/etc/passwd"'])],
    [authority(), record(), dict(record(), name='APP')]])
def test_unsafe_or_ambiguous_desired_resources_are_refused(operations):
    with pytest.raises(M.ManagementError):
        compile_resources(operations)


def test_record_update_of_generated_names_preserves_the_exact_requested_state():
    operation = dict(resource='record', action='update', zone='service.example.test', name='ns1',
                     type='A', values=['192.0.2.99'])
    zone, = compile_resources([authority(), operation])['authoritative_zones']
    document = M.ZoneDocument(zone['data'], 'service.example.test.')
    assert [rr['tokens'][0].text for rr in document.rrset('ns1.service.example.test.', 'A')] == ['192.0.2.99']
    assert document.serial().text == '1'


@pytest.mark.parametrize('kind,value,expected_kind,expected_value', [
    ('TYPE1', '192.0.2.80', 'A', '192.0.2.80'),
    ('TYPE28', '2001:db8::80', 'AAAA', '2001:db8::80'),
    ('TYPE1', '\\# 4 C0000250', 'A', '192.0.2.80'),
    ('AAAA', '\\# 16 20010DB8000000000000000000000080', 'AAAA', '2001:db8::80'),
    ('TXT', '\\# 2 0141', 'TYPE16', '\\# 2 0141'),
    ('TYPE65280', '\\# 0', 'TYPE65280', '\\# 0')])
def test_generic_rdata_retains_wire_source_and_derives_address_metadata(kind, value, expected_kind, expected_value):
    operation = dict(record(), type=kind, values=[value])
    zone, = compile_resources([authority(), operation])['authoritative_zones']
    assert zone['records'] == [dict(name='app', type=expected_kind, value=expected_value)]
    assert ' IN ' + kind + ' ' + value in zone['data']


@pytest.mark.parametrize('kind,value', [('A', '\\# 4 ZZZZ'), ('A', '\\# 4 123'),
    ('A', '\\# 3 C0000250'), ('A', '\\# 4 C00002'), ('A', '\\# 100000 C0000250'),
    ('AAAA', '\\# 4 C0000250'), ('TXT', '\\# 65536 ' + 'A' * 131072)],
    ids=['non-hex', 'odd-hex', 'declared-length', 'short-bytes', 'huge-length', 'wrong-family', 'oversize'])
def test_malformed_generic_data_cannot_mask_length_or_address_family(kind, value):
    with pytest.raises(M.ManagementError):
        compile_resources([authority(), dict(record(), type=kind, values=[value])])


def test_filter_loads_the_shared_compiler_from_the_delivered_role():
    path = Path(__file__).resolve().parents[1] / 'roles/bind9/filter_plugins/resources_deploy.py'
    spec = importlib.util.spec_from_file_location('deployment_filter', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.FilterModule().filters()['resources_deploy'](authority(), CONTEXT)
    assert result['authoritative_zones'][0]['domain'] == 'service.example.test'


@pytest.mark.skipif(os.name != 'posix' or os.geteuid() != 0, reason='Static graph observation requires private root-owned files')
def test_explicit_forward_policy_uses_the_same_management_schema(tmp_path):
    lab = Lab(tmp_path)
    request = dict(resource='zone', action='create', name='private.test', kind='forward', policy='only',
                   forwarders=[{'address': '10.0.0.53'}])
    planned = M.Manager(request, CONTEXT, root=tmp_path, runner=lab.runner, check=True)
    planned.config = M.Configuration(planned.files)
    planned.plan()
    assert b'forward only;' in planned.changes['/etc/named.conf']
    assert (tmp_path / 'etc/named.conf').read_bytes() == planned.config.contents['/etc/named.conf']
    with pytest.raises(M.ManagementError, match='policy only'):
        M.Manager(dict(request, policy='first'), CONTEXT, root=tmp_path).plan()
    authority_request = dict(resource='zone', action='create', name='authority.test', kind='authoritative', policy='only')
    managed = M.Manager(authority_request, CONTEXT, root=tmp_path)
    managed.config = planned.config
    with pytest.raises(M.ManagementError, match='Forwarding fields'):
        managed.plan()

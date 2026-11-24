"""Integration namespace rebasing must preserve the declared DNS data oracle."""
from resource_inputs import compiler, edit_zone_data, rebase_generated_nameservers, role_module
from test_management import CONTEXT


def context():
    request = dict(resource='batch', action='create', operations=[
        dict(resource='zone', action='create', name='example.test', kind='authoritative', serial=7),
        dict(resource='record', action='create', zone='example.test', name='app', type='A', ttl=90, values=['192.0.2.80'])])
    zone, = compiler()(request, CONTEXT)['authoritative_zones']
    return dict(_bind_deploy_authoritative_zones=[zone], bind_primary_host='p', bind_secondary_host='s',
                hostvars={'p': {'bind_front_ip': '127.0.0.2'}, 's': {'bind_front_ip': '127.0.0.4'}})


def test_loopback_rebasing_changes_only_generated_nameservers_and_keeps_record_ttl_and_serial():
    values = context()
    rebase_generated_nameservers(values)
    zone, = values['_bind_deploy_authoritative_zones']
    module = role_module('module_utils/bind9_management.py')
    document = module.ZoneDocument(zone['data'], 'example.test.')
    assert document.serial().text == '7'
    for name, address in [('ns1', '127.0.0.2'), ('ns2', '127.0.0.4'), ('app', '192.0.2.80')]:
        rows = document.rrset(name + '.example.test.', 'A')
        assert len(rows) == 1 and rows[0]['tokens'][0].text == address
    assert document.rrset('app.example.test.', 'A')[0]['ttl'] == '90'
    zone['records'][0]['value'] = '192.0.2.99'
    edit_zone_data(zone, zone['records'][0], serial=8)
    updated = module.ZoneDocument(zone['data'], 'example.test.')
    assert updated.serial().text == '8'
    assert updated.rrset('app.example.test.', 'A')[0]['tokens'][0].text == '192.0.2.99'
    assert updated.rrset('app.example.test.', 'A')[0]['ttl'] == '90'


def test_loopback_rebasing_preserves_an_explicit_nameserver_record_override():
    values = context()
    zone, = values['_bind_deploy_authoritative_zones']
    override = dict(name='ns1', type='A', value='192.0.2.99')
    zone['records'].append(override)
    edit_zone_data(zone, override)
    rebase_generated_nameservers(values)
    module = role_module('module_utils/bind9_management.py')
    document = module.ZoneDocument(zone['data'], 'example.test.')
    assert document.rrset('ns1.example.test.', 'A')[0]['tokens'][0].text == '192.0.2.99'
    assert document.rrset('ns2.example.test.', 'A')[0]['tokens'][0].text == '127.0.0.4'

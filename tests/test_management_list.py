"""Discover explicit managed state without writes, silent defaults or leakage."""
import os

import pytest

from test_management import CONF, Lab, M, ZONE

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.geteuid() != 0,
                                reason='Requires isolated Linux root')


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


def listing(lab, resource, **fields):
    manager = M.Manager(dict(resource=resource, action='list', **fields), {}, root=lab.root, runner=lab.runner)
    with manager.lease():
        return manager.run()


def test_zone_list_returns_actual_type_and_all_explicit_options(lab):
    result = listing(lab, 'zone')
    assert result['total'] == 2 and result['count'] == 2 and not result['changed']
    item = next(item for item in result['items'] if item['name'] == 'example.test.')
    assert item['type'] == 'master' and item['source'] == '/etc/named.conf'
    assert dict((option['name'], option['value']) for option in item['options']) == {
        'type': 'master', 'file': '"zones/example.test.zone"', 'allow-update': '{ none; }'}
    assert 'options' not in result['catalog'][1]
    assert set(result['catalog'][1]['option_names']) == {'type', 'file', 'allow-update'}
    assert not lab.calls and not (lab.root / 'var/backups').exists()


def test_options_include_provenance_and_do_not_invent_native_defaults(lab):
    lab.put('/etc/extra.conf', 'max-cache-ttl 3600;\n')
    lab.put('/var/named/relative.conf', 'max-ncache-ttl 300;\n')
    lab.put('/etc/named.conf', CONF.replace('minimal-responses yes;',
        'minimal-responses yes; include "/etc/extra.conf"; include "relative.conf";'))
    result = listing(lab, 'option')
    declarations = {item['name']: item for item in result['items']}
    assert declarations['max-cache-ttl']['value'] == '3600'
    assert declarations['max-cache-ttl']['source'] == '/etc/extra.conf'
    assert declarations['max-ncache-ttl']['source'] == '/var/named/relative.conf'
    assert 'recursion' not in declarations
    assert all('value' not in item for item in result['catalog'])


def test_records_are_individual_rows_with_owner_type_ttl_and_rdata(lab):
    lab.put('/var/named/zones/example.test.zone', ZONE + 'www IN A 192.0.2.81\n')
    result = listing(lab, 'record', zone='example.test')
    records = [item for item in result['items'] if item['name'] == 'www.example.test.']
    assert [item['value'] for item in records] == ['192.0.2.80', '192.0.2.81']
    assert all(item['type'] == 'A' and item['ttl'] == '300' for item in records)
    assert any(item['type'] == 'SOA' for item in result['items'])
    assert all('value' not in item for item in result['catalog'])
    assert not lab.calls


def test_pages_are_stable_and_can_be_pinned_to_the_first_revision(lab):
    first = listing(lab, 'record', zone='example.test', limit=2)
    second = listing(lab, 'record', zone='example.test', limit=2, offset=first['next_offset'], expected_checksum=first['checksum'])
    assert first['checksum'] == second['checksum']
    assert first['items'] != second['items'] and first['total'] == second['total']
    empty = listing(lab, 'record', zone='example.test', offset=999)
    assert empty['count'] == 0 and empty['next_offset'] is None
    lab.put('/var/named/zones/example.test.zone', ZONE + 'added IN A 192.0.2.99\n')
    with pytest.raises(M.ManagementError, match='restart pagination'):
        listing(lab, 'record', zone='example.test', offset=2, expected_checksum=first['checksum'])


@pytest.mark.parametrize('fields', [dict(limit=0), dict(limit=501), dict(limit=True), dict(limit='2'),
    dict(offset=-1), dict(offset=True), dict(offset='0'), dict(name='www'), dict(type='A'), dict(values=['192.0.2.1'])])
def test_invalid_pagination_and_unused_selectors_are_refused(lab, fields):
    with pytest.raises(M.ManagementError):
        listing(lab, 'record', zone='example.test', **fields)
    assert not lab.calls


@pytest.mark.parametrize('zone', ['', 'missing.test'])
def test_missing_record_zone_is_a_controlled_refusal(lab, zone):
    with pytest.raises(M.ManagementError):
        listing(lab, 'record', zone=zone)


def test_slave_runtime_data_is_not_read_as_static_primary_data(lab):
    lab.put('/etc/named.conf', CONF.replace('zone "example.test" { type master;', 'zone "example.test" { type slave;'))
    assert next(item for item in listing(lab, 'zone')['items'] if item['name'] == 'example.test.')['type'] == 'slave'
    with pytest.raises(M.ManagementError, match='primary static'):
        listing(lab, 'record', zone='example.test')


@pytest.mark.parametrize('native', [False, True])
def test_listing_transport_masks_values_by_default_and_exposes_catalog(tmp_path, native):
    from test_management_ansible import Runner
    runner = Runner(tmp_path, native)
    request = dict(resource='option', action='list', limit=2)
    output = runner.run([runner.role_task('tasks.d/manage/main.yml')], {'bind_manage': request})
    assert 'catalog' in output and 'dnssec-validation' in output
    assert '"items"' not in output
    output = runner.run([runner.role_task('tasks.d/manage/main.yml')],
                        {'bind_manage': request, 'bind_manage_show_values': True})
    assert '"items"' in output and '"value"' in output
    assert (runner.lab.root / 'etc/named.conf').read_text() == CONF

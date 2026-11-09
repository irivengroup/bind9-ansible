"""Run canonical DNS preconditions through the genuine inventory and executor."""
import base64
import copy
import importlib.util
import os
from pathlib import Path

import pytest

from native_ansible_runner import NativeTaskRun

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Requires the native Linux Ansible controller')


def scope():
    zone = {'domain': 'example.test', 'serial': 1, 'ttl': 300,
            'records': [{'name': 'www', 'type': 'A', 'value': '192.0.2.20'}]}
    local = dict(bind_front_ip='198.51.100.2', bind_back_ip='192.0.2.2',
                 bind_tsig_key_name='site-transfer', bind_tsig_algorithm='hmac-sha256',
                 bind_tsig_secret=base64.b64encode(b'public-synthetic-test-fixture-0000').decode('ascii'),
                 bind_primary_host='localhost', bind_secondary_host='peer',
                 bind_allowed_clients=['203.0.113.0/24'], ansible_distribution='RedHat',
                 ansible_distribution_major_version='8', bind_authoritative_zones=[copy.deepcopy(zone)],
                 bind_managed_zones=[zone], bind_forward_zones=[{'domain': 'upstream.test',
                    'forwarders': [{'address': '192.0.2.53'}]}])
    hosts = {'localhost': local, 'peer': {'bind_front_ip': '198.51.100.3', 'bind_back_ip': '192.0.2.3'}}
    return hosts, {'dns_primary': ['localhost'], 'dns_secondary': ['peer']}


def run(hosts, groups):
    # Host-specific addresses cannot be extra-vars: those would override every
    # peer's inventory and manufacture an invalid duplicate-address topology.
    return NativeTaskRun('tasks.d/deploy/validate.yml', {}, inventory_hosts=hosts,
                         inventory_groups=groups).run()


@pytest.mark.parametrize('secondary', [False, True])
@pytest.mark.parametrize('populated', [False, True])
def test_native_preflight_accepts_both_dns_roles_and_optional_empty_zone_lists(secondary, populated):
    hosts, groups = scope()
    local = hosts['localhost']
    if secondary:
        groups = {'dns_primary': ['peer'], 'dns_secondary': ['localhost']}
        local.update(bind_primary_host='peer', bind_secondary_host='localhost')
    if not populated:
        local.update(bind_authoritative_zones=[], bind_managed_zones=[], bind_forward_zones=[])
    status, result = run(hosts, groups)
    assert status == 0 and not result.failed
    assert result.ok[-1] == 'Reject explicit reverse masks in authoritative records' if populated else bool(result.skipped)


@pytest.mark.parametrize('key', ['bind_authoritative_zones', 'bind_forward_zones', 'bind_allowed_clients'])
@pytest.mark.parametrize('value', ['invalid scalar', {}, None])
def test_native_preflight_rejects_container_type_confusion(key, value):
    hosts, groups = scope()
    hosts['localhost'][key] = value
    status, result = run(hosts, groups)
    assert status == 2 and result.failed == ['Validate template input types before rendering']


@pytest.mark.parametrize('key,value', [('bind_front_ip', ''), ('bind_back_ip', ''),
    ('bind_tsig_secret', ''), ('bind_tsig_key_name', ''), ('bind_tsig_algorithm', '')])
def test_native_preflight_requires_nonempty_identity_and_secret_inputs(key, value):
    hosts, groups = scope()
    hosts['localhost'][key] = value
    status, result = run(hosts, groups)
    assert status == 2 and result.failed == ['Validate template input types before rendering']


@pytest.mark.parametrize('key,value', [('bind_front_ip', '127.0.0.1'), ('bind_back_ip', '::1'),
    ('bind_tsig_algorithm', 'hmac-md5'), ('bind_tsig_key_name', 'unsafe;key'),
    ('bind_tsig_secret', 'not-base64-but-over-thirty-two-characters'), ('bind_back_ip', '198.51.100.2')])
def test_native_preflight_rejects_unsafe_literal_dns_identity(key, value):
    hosts, groups = scope()
    hosts['localhost'][key] = value
    status, result = run(hosts, groups)
    assert status == 2 and result.failed == ['Validate literal service addresses and TSIG material before interpolation']


@pytest.mark.parametrize('key,value', [('ansible_distribution', 'Debian'),
    ('ansible_distribution_major_version', '7'), ('bind_primary_host', 'unknown')])
def test_native_preflight_rejects_unsupported_platform_or_peer(key, value):
    hosts, groups = scope()
    hosts['localhost'][key] = value
    status, result = run(hosts, groups)
    assert status == 2 and result.failed == ['Validate supported platform and mandatory inputs']


@pytest.mark.parametrize('ttl,accepted', [
    (0, True), ('0', True), (300, True), (2147483647, True), ('1h', True), ('1h30m', True),
    ('1W2D3h4M5s', True), ('1m1h', True), ('1h1h', True),
    ('not-a-number', False), ('300\n@ IN TXT "injected"', False),
    (True, False), (None, False), (-1, False), (1.5, False), (' 300', False), ('300\n', False),
    ('1h30', False), ('2147483648', False), ('3551w', False)])
def test_native_authoritative_ttl_is_a_bounded_dns_duration_not_interpolated_text(ttl, accepted):
    hosts, groups = scope()
    hosts['localhost']['bind_managed_zones'][0]['ttl'] = ttl
    status, result = run(hosts, groups)
    assert status == (0 if accepted else 2)
    assert result.failed == ([] if accepted else ['Validate authoritative zone definitions'])


@pytest.mark.parametrize('endpoint', [
    {'address': '127.0.0.2'}, {'address': '0:0:0:0:0:0:0:1'}, {'address': '198.51.100.3'},
    {'address': '192.0.2.53', 'port': 0}, {'address': '192.0.2.53', 'port': 65536},
    {'address': '192.0.2.53', 'port': True}, {'address': 'not-an-address'}])
def test_native_forwarding_guards_refuse_loops_and_invalid_ports(endpoint):
    hosts, groups = scope()
    hosts['localhost']['bind_forward_zones'][0]['forwarders'] = [endpoint]
    status, result = run(hosts, groups)
    assert status == 2 and result.failed == ['Validate each forwarding destination']


@pytest.mark.parametrize('key,value', [('domain', '.'), ('domain', ''), ('domain', '*.test'),
    ('policy', 'first'), ('forwarders', [])])
def test_native_forward_zone_contract_rejects_unsafe_zone_definitions(key, value):
    hosts, groups = scope()
    hosts['localhost']['bind_forward_zones'][0][key] = value
    status, result = run(hosts, groups)
    assert status == 2 and result.failed == ['Validate each conditional forwarding zone']


@pytest.mark.parametrize('key,value', [('serial', 0), ('serial', 4294967296), ('serial', 'invalid'),
    ('domain', 'bad/zone.test'), ('records', 1)])
def test_native_authoritative_zone_contract_rejects_unsafe_definitions(key, value):
    hosts, groups = scope()
    hosts['localhost']['bind_managed_zones'][0][key] = value
    status, result = run(hosts, groups)
    assert status == 2 and result.failed == ['Validate authoritative zone definitions']


@pytest.mark.parametrize('key,value', [('type', 'SOA'), ('name', 'unsafe;owner'), ('value', ''),
                                     ('value', '192.0.2.20\ninjected')])
def test_native_resource_record_guards_reject_unsafe_rdata(key, value):
    hosts, groups = scope()
    hosts['localhost']['bind_managed_zones'][0]['records'][0][key] = value
    status, result = run(hosts, groups)
    assert status == 2 and result.failed == ['Validate authoritative resource records']


@pytest.mark.parametrize('value,accepted', [
    ('0s', True), ('2147483647s', True), ('2147483648s', False), ('3549w', True),
    ([], False), ({}, False), ('9' * 129, False), ('０', False), ('1\x00h', False),
    ('1h 30m', False), ('', False), ('1h;' , False)])
def test_ttl_filter_has_explicit_types_ascii_grammar_and_resource_bounds(value, accepted):
    path = Path(__file__).resolve().parents[1] / 'roles/bind9/filter_plugins/client_acl.py'
    spec = importlib.util.spec_from_file_location('bind9_ttl_guard', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.valid_dns_ttl(value) is accepted
    assert module.FilterModule().filters()['valid_dns_ttl'] is module.valid_dns_ttl


@pytest.mark.parametrize('length', [32, 64, 65])
@pytest.mark.parametrize('canonical', [False, True])
def test_native_identity_requires_canonical_base64_while_preserving_longer_keys(length, canonical):
    payload = b'p' * length
    secret = base64.b64encode(payload).decode('ascii')
    if not canonical:
        alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
        index = len(secret.rstrip('=')) - 1
        changed = secret[:index] + alphabet[alphabet.index(secret[index]) | 1] + secret[index + 1:]
        assert changed != secret and base64.b64decode(changed, validate=True) == payload
        secret = changed
    hosts, groups = scope()
    hosts['localhost']['bind_tsig_secret'] = secret
    status, result = run(hosts, groups)
    assert status == (0 if canonical else 2)
    assert result.failed == ([] if canonical else ['Validate literal service addresses and TSIG material before interpolation'])

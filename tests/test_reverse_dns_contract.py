"""The automatic reverse contract uses fixed masks and DNS name equality."""
import pytest

from test_automatic_reverse import module as M, zone


@pytest.mark.parametrize('records', [
    [{'name': 'APP', 'type': 'A', 'value': '192.0.2.10'},
     {'name': 'app', 'type': 'A', 'value': '192.0.2.10'}],
    [{'name': '@', 'type': 'A', 'value': '192.0.2.10'},
     {'name': 'CLOUD.EXAMPLE.TEST.', 'type': 'A', 'value': '192.0.2.10'}],
])
def test_dns_case_does_not_create_a_conflicting_ptr(records):
    result = M.automatic_reverse_zones([zone(records)])
    assert len(result) == 1 and len(result[0]['records']) == 1
    assert result[0]['records'][0]['value'].lower() in ('app.cloud.example.test.', 'cloud.example.test.')


def test_non_ascii_label_is_not_folded_into_a_different_ascii_wire_name():
    # RFC 4343 folds only ASCII A-Z. Python lower() also folds Kelvin sign to k.
    records = [dict(name='K', type='A', value='192.0.2.10'),
               dict(name='\u212a', type='A', value='192.0.2.10')]
    with pytest.raises(ValueError, match='Conflicting PTR'):
        M.automatic_reverse_zones([zone(records)])


@pytest.mark.parametrize('value,message', [
    ({}, 'must be a list'), ([None], 'nonempty domain'),
    ([{'domain': '   '}], 'nonempty domain'), ([{'domain': 7}], 'nonempty domain'),
    ([zone([None])], 'mapping'), ([zone([{'type': 1}])], 'must be a string'),
    ([zone([{'name': 'app', 'type': 'A', 'value': '2001:db8::1'}])], 'family'),
    ([zone([{'name': 'bad name', 'type': 'A', 'value': '192.0.2.1'}])], 'PTR target'),
])
def test_invalid_reverse_inputs_are_rejected(value, message):
    with pytest.raises(ValueError, match=message):
        M.automatic_reverse_zones(value)


def test_filter_exports_real_function_and_keeps_empty_zones_empty():
    assert M.FilterModule().filters()['automatic_reverse_zones'] is M.automatic_reverse_zones
    assert M.automatic_reverse_zones([{'domain': 'example.test'}]) == []


def test_distinct_ptrs_in_the_same_network_are_both_preserved():
    records = [dict(name='a', type='A', value='192.0.2.1'), dict(name='b', type='A', value='192.0.2.2')]
    result = M.automatic_reverse_zones([zone(records)])
    assert result[0]['records'] == [dict(name='1', type='PTR', value='a.cloud.example.test.'),
                                    dict(name='2', type='PTR', value='b.cloud.example.test.')]


def test_fixed_prefix_policy_cannot_generate_apex_ptrs_or_nested_zones():
    """Independent ipaddress oracle, boundary addresses and deterministic corpus."""
    import ipaddress
    import random
    rng = random.Random(4343)
    addresses = [ipaddress.ip_address(value) for value in
                 ('0.0.0.0', '255.255.255.255', '::', 'ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff')]
    addresses += [ipaddress.IPv4Address(rng.getrandbits(32)) for _ in range(128)]
    addresses += [ipaddress.IPv6Address(rng.getrandbits(128)) for _ in range(128)]
    records = [dict(name='host' + str(i), type='A' if address.version == 4 else 'AAAA', value=str(address))
               for i, address in enumerate(addresses)]
    result = M.automatic_reverse_zones([zone(records)])
    assert sum(len(entry['records']) for entry in result) == len(addresses)
    domains = [entry['domain'] for entry in result]
    assert all(not child.endswith('.' + parent) for child in domains for parent in domains if child != parent)
    by_target = {record['value']: (entry['domain'], record['name']) for entry in result for record in entry['records']}
    for index, address in enumerate(addresses):
        domain, label = by_target['host' + str(index) + '.cloud.example.test.']
        assert label != '@' and label + '.' + domain == address.reverse_pointer
        assert len(domain.split('.')) == (5 if address.version == 4 else 18)

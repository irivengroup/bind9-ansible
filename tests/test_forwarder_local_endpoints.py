"""Local DNS aliases must not bypass the canonical Ansible forwarding guard."""
import copy

import pytest

from test_ansible_canonical_predicates import CanonicalPredicates
from test_dns_topology import FilterModule


@pytest.mark.parametrize('address', ['0:0:0:0:0:0:0:1', '::0001', '127.0.0.2', '127.255.255.254'])
def test_port_53_loopback_aliases_are_rejected_by_the_real_preflight(address):
    scope = dict(item=[{'domain': 'example.test'}, {'address': address}],
                 bind_front_ip='198.51.100.2', bind_back_ip='192.0.2.2',
                 bind_primary_host='primary', bind_secondary_host='secondary',
                 hostvars={'primary': {'bind_front_ip': '198.51.100.2', 'bind_back_ip': '192.0.2.2'},
                           'secondary': {'bind_front_ip': '198.51.100.3', 'bind_back_ip': '192.0.2.3'}})
    CanonicalPredicates('deploy/validate.yml').check('Validate each forwarding destination', scope, False)


@pytest.mark.parametrize('address,port,expected', [
    ('0:0:0:0:0:0:0:1', 53, False), ('::0001', 53, False), ('127.0.0.2', 53, False),
    ('198.51.100.2', 53, False), ('2001:db8::53', 53, False),
    ('2001:0DB8:0:0:0:0:0:53', 53, False), ('198.51.100.53', 53, True),
    ('2001:db8::54', 53, True), ('::1', 5353, True), ('127.0.0.2', 5353, True),
    ('198.51.100.2', 5353, True), ('2001:db8::53', '5353', True),
])
def test_forwarding_endpoint_identity_compares_addresses_not_text(address, port, expected):
    server = {'address': address, 'port': port}
    protected = ['198.51.100.2', '2001:db8::53']
    before = copy.deepcopy((server, protected))
    assert FilterModule.forwarder(server, protected) is expected
    assert (server, protected) == before


@pytest.mark.parametrize('protected', ['198.51.100.2', {}, 1, [None], ['bad'], [1], ['fe80::1%eth0']])
def test_invalid_protected_endpoint_configuration_is_not_silently_ignored(protected):
    assert FilterModule.forwarder({'address': '192.0.2.53'}, protected) is False

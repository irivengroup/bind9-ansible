"""Exercise role assignment over active IPv4 interfaces of a site pair."""
import importlib.util
from pathlib import Path
import re
import sys

from jinja2 import Environment, StrictUndefined
import pytest


ROLE = Path(__file__).resolve().parents[1] / 'roles/bind9'
SPEC = importlib.util.spec_from_file_location('dns_network', ROLE / 'filter_plugins/dns_network.py')
NETWORK = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(NETWORK)


def link(name, *addresses):
    return {'ifname': name, 'addr_info': [
        {'local': address, 'scope': 'host' if address.startswith('127.') else 'global'}
        for address in addresses]}


def links(suffix='11', names=('eth0', 'eth1', 'eth2', 'eth3')):
    return [link('lo', '127.0.0.1')] + [
        link(name, prefix + suffix) for name, prefix in zip(
            names, ('192.0.2.', '198.51.100.', '203.0.113.', '10.0.0.'))]


def write_ip_probe(folder, observations, existing=False):
    """Expose only the exact read-only command to the native Ansible fixture."""
    binary = folder / 'ip'
    shebang, remaining = binary.read_text().split('\n', 1) if existing else ('#!' + sys.executable, 'sys.exit(91)\n')
    binary.write_text(shebang + '\nimport json, os, sys\n'
                      'if sys.argv[1:] == ["-j", "-4", "address", "show", "up"]:\n'
                      '    print(json.dumps(' + repr(observations) + '[os.environ.get("BIND_TEST_TARGET", "")]))\n'
                      '    sys.exit(0)\n' + remaining)
    binary.chmod(0o755)


def resolve(rows, selection=None, front=None, back=None, admin='192.0.2.11'):
    return NETWORK.FilterModule().filters()['dns_network_resolve'](rows, selection, front, back, admin)


def test_single_interface_shares_the_ssh_address_and_uses_existing_routing():
    profile = resolve([link('lo', '127.0.0.1'), link('ens192', '192.0.2.11', '192.0.2.99')])
    assert profile['front'] == profile['back'] == profile['admin'] == '192.0.2.11'
    assert profile['listeners'] == ['192.0.2.11']
    assert profile['interface_count'] == 1 and profile['front_routes'] is False


def test_multi_interface_can_choose_one_listener_or_separate_front_back_and_admin():
    shared = resolve(links(), ['eth1'])
    assert shared['listeners'] == ['198.51.100.11']
    assert shared['front'] == shared['back'] == '198.51.100.11'
    assert shared['admin_role'] == 'dedicated' and shared['front_routes'] is False
    split = resolve(links(), ['eth0', 'eth1', 'eth3'], '192.0.2.11', '198.51.100.11', '203.0.113.11')
    assert split['listeners'] == ['192.0.2.11', '198.51.100.11', '10.0.0.11']
    assert split['front_routes'] is True and split['admin_role'] == 'dedicated'
    declared = resolve(links(), ['eth1'], '198.51.100.11', '198.51.100.11')
    assert declared == shared


def test_legacy_listener_selection_and_explicit_address_with_ipv4_aliases():
    legacy = resolve(links(), None, '192.0.2.11', '198.51.100.11', '203.0.113.11')
    assert legacy['listeners'] == ['192.0.2.11', '198.51.100.11'] and legacy['front_routes']
    rows = links()
    rows[1] = link('eth0', '192.0.2.11', '192.0.2.99')
    chosen = resolve(rows, {'eth0': '192.0.2.99'})
    assert chosen['listeners'] == ['192.0.2.99'] and chosen['admin_role'] == 'front'
    with pytest.raises(ValueError, match='Specify one local IPv4'):
        resolve(rows, ['eth0'])


@pytest.mark.parametrize('rows,selection,front,back,admin,reason', [
    ({}, None, None, None, '192.0.2.11', 'IPv4 interface list'),
    ([link('lo', '127.0.0.1')], None, None, None, '192.0.2.11', 'No active'),
    ([link('bad/name', '192.0.2.11')], None, None, None, '192.0.2.11', 'Invalid interface name'),
    ([link(123, '192.0.2.11')], None, None, None, '192.0.2.11', 'Invalid interface name'),
    ([link('eth0', '169.254.0.1')], None, None, None, '192.0.2.11', 'No active'),
    ([link('eth0', '192.0.2.11'), link('eth1', '192.0.2.11')], None, None, None,
     '192.0.2.11', 'occurs more than once'),
    (links(), None, None, None, '192.0.2.11', 'Declare bind_listen_interfaces'),
    (links(), None, '198.51.100.11', None, '192.0.2.11', 'Declare bind_listen_interfaces'),
    (links(), None, '192.0.2.11', '192.0.2.11', '192.0.2.11', 'share an interface'),
    (links(), [], None, None, '192.0.2.11', 'nonempty list or mapping'),
    (links(), 'eth0', None, None, '192.0.2.11', 'nonempty list or mapping'),
    (links(), ['eth1', 'eth1'], None, None, '192.0.2.11', 'each listening interface once'),
    (links(), [{}], None, None, '192.0.2.11', 'each listening interface once'),
    (links(), {'missing': '192.0.2.11'}, None, None, '192.0.2.11', 'active local IPv4'),
    (links(), {0: '192.0.2.11'}, None, None, '192.0.2.11', 'active local IPv4'),
    (links(), {'eth1': '192.0.2.11'}, None, None, '192.0.2.11', 'Specify one local IPv4'),
    (links(), ['eth1'], None, None, 'host.example.test', 'SSH endpoint'),
    (links(), ['eth1'], None, None, '192.0.2.99', 'SSH endpoint'),
    (links(), ['eth0'], '192.0.2.99', None, '192.0.2.11', 'FRONT and BACK addresses must be local'),
    (links(), ['eth0'], 'hostname.test', None, '192.0.2.11', 'FRONT and BACK addresses must be local'),
    (links(), ['eth0'], '198.51.100.11', None, '192.0.2.11', 'sole DNS listener'),
    (links(), ['eth1', 'eth2'], '198.51.100.11', '198.51.100.11', '192.0.2.11', 'distinct FRONT and BACK'),
    (links(), ['eth1', 'eth2'], '198.51.100.11', '192.0.2.11', '192.0.2.11', 'distinct FRONT and BACK'),
    (links(), ['eth1', 'eth2'], '192.0.2.11', '198.51.100.11', '192.0.2.11', 'distinct FRONT and BACK'),
])
def test_refuses_ambiguous_or_incorrect_server_inventory(rows, selection, front, back, admin, reason):
    with pytest.raises(ValueError, match=reason):
        resolve(rows, selection, front, back, admin)


def test_pair_compares_network_roles_without_requiring_equal_device_names():
    left = resolve(links(), ['eth0', 'eth1', 'eth3'], '192.0.2.11', '198.51.100.11', '203.0.113.11')
    renamed = links('12', ('ens1', 'ens2', 'ens3', 'ens4'))
    right = resolve(renamed, ['ens1', 'ens2', 'ens4'], '192.0.2.12', '198.51.100.12', '203.0.113.12')
    validate = NETWORK.FilterModule().filters()['dns_network_peer_valid']
    assert validate(left, right)
    assert resolve(renamed, ['ens1', 'ens2'], '192.0.2.12', '198.51.100.12', '198.51.100.12')['admin_role'] == 'back'
    assert resolve(renamed, ['ens1', 'ens2', 'ens4'], '192.0.2.12', '198.51.100.12', '10.0.0.12')['admin_role'] == 'listener'
    for key, value in [('interface_count', 5), ('listener_count', 2), ('admin_role', 'back'), ('front_routes', False)]:
        assert not validate(left, dict(right, **{key: value}))
    assert not validate(left, dict(right, listeners=['192.0.2.11', '198.51.100.12', '10.0.0.12']))


@pytest.mark.parametrize('front,back,listeners,admin,required', [
    ('192.0.2.11', '198.51.100.11', False, False, False),
    ('192.0.2.11', '198.51.100.11', True, False, True),
    ('192.0.2.11', '198.51.100.11', False, True, True),
    (None, '198.51.100.11', False, False, True),
    ('192.0.2.11', None, False, False, True),
    ('192.0.2.11', '192.0.2.11', False, False, True),
])
def test_discovery_is_required_outside_the_explicit_legacy_pair(front, back, listeners, admin, required):
    predicate = NETWORK.FilterModule().filters()['dns_network_required']
    assert predicate(front, back, listeners, admin) is required


@pytest.mark.parametrize('address', [None, '', 'fe80::1', '0.0.0.0', '127.1.1.1',
                                     '224.0.0.2', '169.254.0.1', '255.255.255.255'])
def test_protected_non_service_addresses_are_not_interface_candidates(address):
    assert NETWORK.DnsNetwork.unicast(address) is False


def test_legacy_ipv4_parser_cannot_admit_a_different_spelling(legacy_ipaddress, monkeypatch):
    address = '192.000.2.11'
    assert str(legacy_ipaddress.IPv4Address(address)) == '192.0.2.11'
    monkeypatch.setattr(NETWORK, 'ipaddress', legacy_ipaddress)
    assert NETWORK.DnsNetwork.unicast(address) is False


@pytest.mark.parametrize('shared,cloud,internal', [(True, True, True), (True, False, True),
                                                   (True, True, False), (False, True, True)])
@pytest.mark.parametrize('controller', ['plain', 'ansible'])
def test_listen_and_query_acl_render_the_selected_addresses_once(shared, cloud, internal, controller):
    front = '192.0.2.11'
    back = front if shared else '198.51.100.11'
    extra = [] if shared else ['10.0.0.11']
    hosts = {'p': {'bind_back_ip': back, 'bind_front_ip': front},
             's': {'bind_back_ip': '192.0.2.12', 'bind_front_ip': '192.0.2.12'}}
    cloud_networks = ['192.0.2.0/24'] if cloud else []
    internal_networks = ['198.51.100.0/24'] if internal else []
    context = dict(
        bind_front_ip=front, bind_back_ip=back,
        bind_dns_listen_addresses=list(dict.fromkeys([front, back])) + extra,
        bind_primary_host='p', bind_secondary_host='s', hostvars=hosts,
        group_names=['dns_primary'], bind_tsig_key_name='transfer', bind_tsig_algorithm='hmac-sha256',
        bind_tsig_secret='render-only', bind_dnssec_active=False,
        bind_allowed_clients=cloud_networks + internal_networks, bind_cloud_clients=cloud_networks,
        bind_internal_clients=internal_networks, bind_authoritative_zones=[], bind_forward_zones=[])
    source = (ROLE / 'templates/named.conf.j2').read_text()
    if controller == 'ansible':
        from ansible.parsing.dataloader import DataLoader
        from ansible.template import Templar
        rendered = Templar(loader=DataLoader(), variables=context).template(source, convert_data=False)
    else:
        rendered = Environment(undefined=StrictUndefined).from_string(source).render(**context)
    sources = ['local_loopback;', 'bind_primary_front_ip;']
    if not shared:
        sources.extend(['bind_primary_back_ip;', '10.0.0.11;'])
    for directive in ('listen-on port 53', 'allow-query-on'):
        block = re.search(directive + r'\s*\{([^{}]*)\};', rendered).group(1)
        assert block.split() == sources
    destinations = re.search(r'allow-recursion-on\s*\{([^{}]*)\};', rendered).group(1)
    assert destinations.split() == (sources if shared else sources[:-1])

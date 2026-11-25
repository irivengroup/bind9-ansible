"""Exercise role assignment over active IPv4 interfaces of a site pair."""
import importlib.util
from pathlib import Path
import re
import sys

from jinja2 import Environment, StrictUndefined
import pytest
import yaml


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


def ipv4_text(observations):
    """Model the same ip -o format emitted by EL7 and newer kernels."""
    return '\n'.join('{}: {} inet {}/{} scope {} {}'.format(
        index, row['ifname'], address['local'], address.get('prefixlen', 24),
        address.get('scope', 'global'), row['ifname'])
        for index, row in enumerate(observations, 1) for address in row['addr_info'])


def write_ip_probe(folder, observations, existing=False):
    """Expose only the exact read-only command to the native Ansible fixture."""
    binary = folder / 'ip'
    shebang, remaining = binary.read_text().split('\n', 1) if existing else ('#!' + sys.executable, 'sys.exit(91)\n')
    binary.write_text(shebang + '\nimport json, os, sys\n'
                      'if sys.argv[1:] == ["-o", "-4", "address", "show", "up"]:\n'
                      '    print(' + repr({name: ipv4_text(rows) for name, rows in observations.items()}) + '[os.environ.get("BIND_TEST_TARGET", "")])\n'
                      '    sys.exit(0)\n' + remaining)
    binary.chmod(0o755)


def modeled_management_interfaces(folder, names=('localhost', 'peer')):
    """Supply only modeled IPv4 discovery to local management aliases.

    Native Ansible/modules/files remain real. These aliases share one test
    namespace, so this fixture makes no physical network or RHEL claim.
    """
    assert len(names) == 2
    hosts = {name: dict(bind_admin_ip='192.0.2.' + suffix, bind_listen_interfaces=[
        dict(name='front0', ipaddress='192.0.2.' + suffix, scope='front'),
        dict(name='back0', ipaddress='10.0.0.' + suffix, scope='back')])
        for name,suffix in zip(names,('11','12'))}
    write_ip_probe(folder, {'': [link('front0','192.0.2.11','192.0.2.12'),
                               link('back0','10.0.0.11','10.0.0.12')]})
    return hosts


def resolve(rows, selection=None, admin='192.0.2.11'):
    return NETWORK.FilterModule().filters()['dns_network_resolve'](rows, selection, admin)


def test_native_ipv4_text_preserves_aliases_peer_suffixes_scopes_and_duplicate_addresses():
    rows = [link('lo', '127.0.0.1'), link('eth0', '192.0.2.11', '192.0.2.99'),
            link('eth1', '192.0.2.11')]
    parsed = NETWORK.DnsNetwork.ipv4_links(ipv4_text(rows).replace('eth0 inet', 'eth0@if123 inet'))
    assert [row['ifname'] for row in parsed] == ['lo', 'eth0', 'eth1']
    assert [entry['local'] for row in parsed for entry in row['addr_info']] == [
        '127.0.0.1', '192.0.2.11', '192.0.2.99', '192.0.2.11']
    assert parsed[0]['addr_info'][0] == dict(family='inet', local='127.0.0.1', prefixlen=24, scope='host')
    with pytest.raises(ValueError, match='occurs more than once'):
        NETWORK.DnsNetwork(parsed)
    assert NETWORK.FilterModule().filters()['dns_ipv4_links']('') == []


@pytest.mark.parametrize('output', [None, [], {}, 1, 'not an address', '\x00',
    '1: bad/name inet 192.0.2.1/24 scope global bad/name',
    '1: eth0 inet 192.0.2.1/33 scope global eth0',
    '1: eth0 inet 999.0.2.1/24 scope global eth0',
    '1: eth0 inet 192.0.2.1/024 scope global eth0',
    '1: eth0 inet 192.0.2.1/01 scope global eth0',
    '1: eth0 inet 192.0.2.1/24 scope global eth0\ninvalid'])
def test_malformed_native_ipv4_text_never_becomes_a_partial_inventory(output):
    with pytest.raises(ValueError):
        NETWORK.DnsNetwork.ipv4_links(output)


def test_native_ipv4_text_accepts_real_el7_and_current_iproute_attributes():
    output = ('2: ens192 inet 192.0.2.11/24 brd 192.0.2.255 scope global dynamic ens192\\'
              '       valid_lft 3600sec preferred_lft 3600sec\n'
              '2: ens192 inet 192.0.2.99/32 scope global secondary noprefixroute ens192:1\\'
              '       valid_lft forever preferred_lft forever\n'
              '3: eth1 inet 169.254.0.1/16 scope link eth1')
    parsed = NETWORK.DnsNetwork.ipv4_links(output)
    assert parsed[0]['addr_info'][1]['prefixlen'] == 32
    assert parsed[1]['addr_info'][0]['scope'] == 'link'
    assert NETWORK.DnsNetwork(parsed).resolve([dict(name='ens192', ipaddress='192.0.2.11')], '192.0.2.11')['front'] == '192.0.2.11'


def test_single_interface_shares_the_ssh_address_and_uses_existing_routing():
    profile = resolve([link('lo', '127.0.0.1'), link('ens192', '192.0.2.11', '192.0.2.99')], [dict(name='ens192', ipaddress='192.0.2.11')])
    assert profile['front'] == profile['back'] == profile['admin'] == '192.0.2.11'
    assert profile['listeners'] == ['192.0.2.11']
    assert profile['interface_count'] == 1 and profile['front_routes'] is False


@pytest.mark.parametrize('observed,selection,admin,expected', [
    ([link('ens256', '192.0.2.11'), link('ens224', '192.0.2.12')],
     [dict(name='ens256', ipaddress='192.0.2.11', scope='front'),
      dict(name='ens224', ipaddress='192.0.2.12', scope='back')],
     '192.0.2.11', ('192.0.2.11', '192.0.2.12', 'front', False)),
    ([link('ens192', '10.10.1.11')], [dict(name='ens192')],
     '10.10.1.11', ('10.10.1.11', '10.10.1.11', 'front', False)),
    ([link('admin0', '10.10.1.11'), link('ens256', '192.0.2.11'), link('ens224', '192.0.2.12')],
     [dict(name='ens256', ipaddress='192.0.2.11', scope='front'),
      dict(name='ens224', ipaddress='192.0.2.12', scope='back')],
     '10.10.1.11', ('192.0.2.11', '192.0.2.12', 'dedicated', True)),
])
def test_uniform_listener_objects_resolve_the_three_requested_host_var_shapes(observed, selection, admin, expected):
    # The kernel must confirm every declared name/address; scopes are the
    # single declaration of DNS roles, even when SSH shares a DNS interface.
    profile = resolve(observed, selection, admin=admin)
    assert (profile['front'], profile['back'], profile['admin_role'], profile['front_routes']) == expected
    assert profile['listeners'] == [row.get('ipaddress', admin) for row in selection]


def test_uniform_listener_objects_keep_extra_listeners_and_caller_data_unchanged():
    from copy import deepcopy
    selection = [dict(name='eth1', scope='back'), dict(name='eth3'),
                 dict(name='eth0', ipaddress='192.0.2.11', scope='front')]
    before = deepcopy(selection)
    profile = resolve(links(), selection, admin='203.0.113.11')
    assert selection == before
    assert profile['front'] == '192.0.2.11' and profile['back'] == '198.51.100.11'
    assert profile['listeners'] == ['198.51.100.11', '10.0.0.11', '192.0.2.11']
    assert profile['front_routes'] is True
    selection[0]['ipaddress'] = '192.0.2.99'
    assert profile['back'] == '198.51.100.11'


@pytest.mark.parametrize('selection,reason', [
    ([dict(name='eth1'), 'eth0'], 'allow only'),
    ([dict(name='eth1', address='198.51.100.11')], 'allow only'),
    ([dict(ipaddress='198.51.100.11')], 'valid name'),
    ([dict(name=1)], 'valid name'),
    ([dict(name='bad/name')], 'valid name'),
    ([dict(name='eth1'), dict(name='eth1')], 'interface once'),
    ([dict(name='eth1', ipaddress=None)], 'unicast IPv4'),
    ([dict(name='eth1', ipaddress=1)], 'unicast IPv4'),
    ([dict(name='eth1', ipaddress='198.051.100.11')], 'unicast IPv4'),
    ([dict(name='eth1', ipaddress='224.0.0.1')], 'unicast IPv4'),
    ([dict(name='eth1', ipaddress='198.51.100.99')], 'Specify one local IPv4'),
    ([dict(name='missing')], 'active local IPv4'),
    ([dict(name='eth1', scope=None)], 'scope must'),
    ([dict(name='eth1', scope=[])], 'scope must'),
    ([dict(name='eth1', scope='admin')], 'scope must'),
    ([dict(name='eth1', scope='front'), dict(name='eth0', scope='front')], 'scope once'),
    ([dict(name='eth1', scope='back'), dict(name='eth0', scope='back')], 'scope once'),
    ([dict(name='eth1'), dict(name='eth0')], 'front scope and one back scope'),
])
def test_uniform_listener_objects_refuse_ambiguous_or_unobserved_declarations(selection, reason):
    with pytest.raises(ValueError, match=reason):
        resolve(links(), selection)


def test_uniform_listener_objects_select_secondary_ipv4_and_resolve_idempotently():
    rows = links()
    rows[1] = link('eth0', '192.0.2.11', '192.0.2.99')
    selection = [dict(name='eth0', ipaddress='192.0.2.99', scope='front'), dict(name='eth1', scope='back')]
    first = resolve(rows, selection)
    assert first['front'] == '192.0.2.99'
    assert resolve(rows, selection) == first
    assert resolve(rows, [dict(name='eth1', scope='back')])['front'] == '198.51.100.11'
    with pytest.raises(ValueError, match='Specify one local IPv4'):
        resolve(rows, [dict(name='eth0')])


def test_multi_interface_can_choose_one_listener_or_separate_front_back_and_admin():
    shared = resolve(links(), [dict(name='eth1')])
    assert shared['listeners'] == ['198.51.100.11']
    assert shared['front'] == shared['back'] == '198.51.100.11'
    assert shared['admin_role'] == 'dedicated' and shared['front_routes'] is False
    split = resolve(links(), [dict(name='eth0', scope='front'), dict(name='eth1', scope='back'),
                              dict(name='eth3')], admin='203.0.113.11')
    assert split['listeners'] == ['192.0.2.11', '198.51.100.11', '10.0.0.11']
    assert split['front_routes'] is True and split['admin_role'] == 'dedicated'


@pytest.mark.parametrize('rows,selection,admin,reason', [
    ({}, None, '192.0.2.11', 'IPv4 interface list'),
    ([link('lo', '127.0.0.1')], None, '192.0.2.11', 'No active'),
    ([link('bad/name', '192.0.2.11')], None, '192.0.2.11', 'Invalid interface name'),
    ([link(123, '192.0.2.11')], None, '192.0.2.11', 'Invalid interface name'),
    ([link('eth0', '169.254.0.1')], None, '192.0.2.11', 'No active'),
    ([link('eth0', '192.0.2.11'), link('eth1', '192.0.2.11')], None,
     '192.0.2.11', 'occurs more than once'),
    (links(), None, '192.0.2.11', 'nonempty list of interface objects'),
    (links(), [], '192.0.2.11', 'nonempty list of interface objects'),
    (links(), 'eth0', '192.0.2.11', 'nonempty list of interface objects'),
    (links(), {'eth1': '198.51.100.11'}, '192.0.2.11', 'nonempty list of interface objects'),
    (links(), ['eth1'], '192.0.2.11', 'allow only'),
    (links(), [dict(name='eth1')], 'host.example.test', 'SSH endpoint'),
    (links(), [dict(name='eth1')], '192.0.2.99', 'SSH endpoint'),
])
def test_refuses_ambiguous_or_incorrect_server_inventory(rows, selection, admin, reason):
    with pytest.raises(ValueError, match=reason):
        resolve(rows, selection, admin)


@pytest.mark.parametrize('environment,site', [
    ('dev', 'site1'), ('dev', 'site2'), ('preprod', 'site1'),
    ('preprod', 'site3'), ('prod', 'site1'), ('prod', 'site3')])
def test_active_host_vars_explicitly_select_dns_and_keep_admin_separate(environment, site):
    # These are the documented fictional multi-interface inventories, not a
    # discovery of real servers. Both declared peers must resolve to an ISO pair.
    root = Path(__file__).resolve().parents[1]
    profiles = []
    for number in (1, 2):
        path = root / 'inventory' / environment / 'host_vars' / (site + '-dns0' + str(number) + '.yml')
        values = yaml.safe_load(path.read_text())
        assert 'bind_listen_interfaces' in values, 'Active example inventories must explicitly declare their DNS listeners'
        devices = values['bind_listen_interfaces']
        assert isinstance(devices, list) and len(devices) == 2
        assert {device['scope'] for device in devices} == {'front', 'back'}
        assert len({device['name'] for device in devices}) == 2
        assert all(set(device) == {'name', 'ipaddress', 'scope'} for device in devices)
        assert 'bind_front_ip' not in values and 'bind_back_ip' not in values
        observed = [link('lo', '127.0.0.1'), link('qa-admin', values['ansible_host']),
                    *[link(device['name'], device['ipaddress']) for device in devices]]
        profile = resolve(observed, devices, admin=values['ansible_host'])
        assert set(profile['listeners']) == {device['ipaddress'] for device in devices}
        assert profile['listener_count'] == 2 and profile['admin_role'] == 'dedicated'
        assert values['ansible_host'] not in profile['listeners']
        profiles.append(profile)
    assert NETWORK.DnsNetwork.validate_pair(*profiles)


def test_pair_compares_network_roles_without_requiring_equal_device_names():
    left = resolve(links(), [dict(name='eth0', scope='front'), dict(name='eth1', scope='back'),
                             dict(name='eth3')], admin='203.0.113.11')
    renamed = links('12', ('ens1', 'ens2', 'ens3', 'ens4'))
    selection = [dict(name='ens1', scope='front'), dict(name='ens2', scope='back'), dict(name='ens4')]
    right = resolve(renamed, selection, admin='203.0.113.12')
    validate = NETWORK.FilterModule().filters()['dns_network_peer_valid']
    assert validate(left, right)
    assert resolve(renamed, selection[:2], admin='198.51.100.12')['admin_role'] == 'back'
    assert resolve(renamed, selection, admin='10.0.0.12')['admin_role'] == 'listener'
    for key, value in [('interface_count', 5), ('listener_count', 2), ('admin_role', 'back'), ('front_routes', False)]:
        assert not validate(left, dict(right, **{key: value}))
    assert not validate(left, dict(right, listeners=['192.0.2.11', '198.51.100.12', '10.0.0.12']))


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
        bind_allowed_clients=cloud_networks + internal_networks, bind_trusted_networks=dict(cloud_clients=cloud_networks, internal_clients=internal_networks), _bind_deploy_authoritative_zones=[], _bind_deploy_forward_zones=[])
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

"""Resolve peers with the real inventory and action plugins, including refusals."""
import copy

from ansible import release
import jinja2
import pytest

from native_ansible_runner import NativeTaskRun

pytestmark = pytest.mark.skipif(release.__version__ != '2.16.19' or jinja2.__version__ != '3.1.6',
                               reason='Native observer integration requires the inspected QA controller')


def scope():
    variables = dict(bind_environment='lab', bind_site_name='site',
                     bind_primary_group='primaries', bind_secondary_group='secondaries')
    hosts = {name: dict(bind_environment='lab', bind_site_name='site', bind_back_ip=back, bind_front_ip=front)
             for name, back, front in [('localhost', '10.0.0.2', '198.51.100.2'),
                                       ('peer', '10.0.0.3', '198.51.100.3')]}
    groups = dict(site=['localhost', 'peer'], primaries=['localhost'], secondaries=['peer'])
    return variables, hosts, groups


def run(variables, hosts, groups):
    hosts = copy.deepcopy(hosts)
    local = hosts.setdefault('localhost', {})
    for key in ('bind_environment', 'bind_site_name', 'bind_primary_group', 'bind_secondary_group'):
        local.pop(key, None)
    local.update(variables)
    return NativeTaskRun('tasks.d/site_peers.yml', {},
                         inventory_hosts=hosts, inventory_groups=groups).run()


def test_native_inventory_resolves_exact_site_membership_and_peers():
    variables, hosts, groups = scope()
    status, result = run(variables, hosts, groups)
    assert status == 0 and not result.failed
    assert result.facts == dict(bind_site_primary_hosts=['localhost'], bind_site_secondary_hosts=['peer'],
                                bind_primary_host='localhost', bind_secondary_host='peer')
    assert len(result.ok) == 5


@pytest.mark.parametrize('key,value', [
    ('bind_environment', None), ('bind_environment', ''),
    ('bind_site_name', None), ('bind_site_name', ''), ('bind_site_name', 'absent'),
    ('bind_primary_group', None), ('bind_primary_group', ''), ('bind_primary_group', 'absent'),
    ('bind_secondary_group', None), ('bind_secondary_group', ''), ('bind_secondary_group', 'absent'),
    ('bind_secondary_group', 'primaries')])
def test_invalid_site_declarations_fail_before_peer_resolution(key, value):
    variables, hosts, groups = scope()
    variables[key] = value
    status, result = run(variables, hosts, groups)
    assert status == 2 and result.failed == ['Validate declared DNS site group']
    assert not result.facts


@pytest.mark.parametrize('key', ['bind_environment', 'bind_site_name', 'bind_primary_group', 'bind_secondary_group'])
def test_missing_site_declarations_fail_before_peer_resolution(key):
    variables, hosts, groups = scope()
    del variables[key]
    # Group-name defaults point at dns_primary/dns_secondary, absent from this
    # inventory; environment and site have no project defaults.
    status, result = run(variables, hosts, groups)
    assert status == 2 and result.failed == ['Validate declared DNS site group']
    assert not result.facts


@pytest.mark.parametrize('key', ['bind_primary_group', 'bind_secondary_group'])
def test_native_undef_override_cannot_bypass_required_group_declarations(key):
    variables, hosts, groups = scope()
    # This is the native Ansible undef() configuration input, not a mocked
    # dictionary pretending that a mandatory default was never loaded.
    variables[key] = "{{ undef('Group explicitly removed by an inventory override') }}"
    status, result = run(variables, hosts, groups)
    assert status == 2 and result.failed == ['Validate declared DNS site group']
    assert not result.facts


@pytest.mark.parametrize('group,members', [('primaries', []), ('secondaries', []),
                                        ('primaries', ['localhost', 'peer']), ('secondaries', ['localhost', 'peer']),
                                        ('site', ['peer'])])
def test_peer_cardinality_and_current_host_membership_are_checked(group, members):
    variables, hosts, groups = scope()
    groups[group] = members
    status, result = run(variables, hosts, groups)
    assert status == 2 and len(result.failed) == 1
    assert result.failed == [ 'Validate declared DNS site group' if group == 'site'
                             else 'Validate DNS peers share the environment and site']
    assert 'bind_primary_host' not in result.facts


@pytest.mark.parametrize('host,key', [(host, key) for host in ('localhost', 'peer')
                                    for key in ('bind_environment', 'bind_site_name')])
def test_different_peer_environment_or_site_is_rejected(host, key):
    variables, hosts, groups = scope()
    hosts = copy.deepcopy(hosts)
    hosts[host][key] = 'other'
    # Extra vars necessarily override the current host's own declaration.
    # Exercise that peer through an independent primary host instead.
    if host == 'localhost':
        hosts['primary'] = hosts.pop('localhost')
        groups['primaries'] = ['primary']
        groups['site'].append('primary')
    status, result = run(variables, hosts, groups)
    assert status == 2 and result.failed == ['Validate DNS peers share the environment and site']
    assert 'bind_primary_host' not in result.facts


@pytest.mark.parametrize('host,key', [(host, key) for host in ('localhost', 'peer')
                                    for key in ('bind_back_ip', 'bind_front_ip')])
def test_missing_peer_addresses_fail_explicitly(host, key):
    variables, hosts, groups = scope()
    del hosts[host][key]
    status, result = run(variables, hosts, groups)
    assert status == 2 and result.failed == ['Validate required peer host variables']

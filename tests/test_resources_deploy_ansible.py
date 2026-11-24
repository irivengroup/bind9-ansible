"""Native Ansible input guards and compilation, without target provisioning."""
import copy

import pytest

from native_ansible_runner import NativeTaskRun
from test_resources_deploy import authority


def peers():
    request = dict(resource='batch', action='create', operations=[authority()])
    hosts = {name: dict(bind_resources_deploy=copy.deepcopy(request),
        bind_front_ip='192.0.2.' + suffix, bind_back_ip='10.0.0.' + suffix)
        for name, suffix in [('localhost', '11'), ('peer', '12')]}
    return hosts


def run(hosts, **values):
    return NativeTaskRun('tasks.d/deploy/resources.yml', dict(
        bind_primary_host='localhost', bind_secondary_host='peer', **values),
        inventory_hosts=hosts).run()


def test_native_resource_compilation_uses_both_peer_identities_without_public_output():
    status, result = run(peers())
    assert status == 0 and not result.failed
    assert result.ok == ['Require one unambiguous desired resource declaration',
        'Require identical desired resources on both site peers',
        'Compile deployment resources using the management request schemas',
        'Select only materialized resource data for deployment templates']
    assert '_bind_deploy_resources' not in result.facts


@pytest.mark.parametrize('old', ['bind_authoritative_zones', 'bind_forward_zones'])
def test_native_resource_guard_refuses_obsolete_variables_even_if_new_is_present(old):
    status, result = run(peers(), **{old: []})
    assert status == 2 and result.failed == ['Require one unambiguous desired resource declaration']
    assert 'Compile deployment resources using the management request schemas' not in result.ok


@pytest.mark.parametrize('value', [None, [], 'scalar', False])
def test_native_resource_guard_refuses_non_mapping_desired_state(value):
    hosts = peers()
    hosts['localhost']['bind_resources_deploy'] = value
    status, result = run(hosts)
    assert status == 2 and result.failed == ['Require one unambiguous desired resource declaration']


def test_native_resource_guard_refuses_divergent_peer_declarations_before_compilation():
    hosts = peers()
    hosts['peer']['bind_resources_deploy']['operations'][0]['name'] = 'other.test'
    status, result = run(hosts)
    assert status == 2 and result.failed == ['Require identical desired resources on both site peers']


def test_native_resource_default_is_the_same_explicit_empty_graph_on_both_peers():
    hosts = peers()
    for values in hosts.values():
        del values['bind_resources_deploy']
    status, result = run(hosts, bind_resources_deploy=dict(resource='batch', action='create', operations=[]))
    assert status == 0 and not result.failed

"""Exercise canonical guards through native Ansible task dispatch, not emulation."""
from ansible import release
import jinja2
import pytest

from native_ansible_runner import NativeTaskRun

pytestmark = pytest.mark.skipif(release.__version__ != '2.16.19' or jinja2.__version__ != '3.1.6',
                               reason='Native observer integration uses the inspected QA controller')


@pytest.mark.parametrize('value,accepted', [(True, True), (False, True), ('auto', True),
    ('true', True), ('false', True), ('yes', False), ('no', False), (1, False),
    (0, False), (None, False), ([], False), ({}, False)])
def test_real_dispatch_accepts_only_the_public_dnssec_modes(value, accepted):
    status, result = NativeTaskRun('tasks.d/dnssec_settings.yml', {'bind_dnssec_active': value}).run()
    assert status == (0 if accepted else 2)
    assert result.failed == ([] if accepted else ['Validate the optional DNSSEC mode'])
    assert len(result.ok) == int(accepted)


@pytest.mark.parametrize('cloud,internal,accepted', [
    (['203.0.113.25', '203.0.113.25'], ['10.0.0.0/24'], True),
    ([], ['10.0.0.0/24'], True), (['203.0.113.0/24'], [], True),
    ([], [], False), ('203.0.113.25', [], False), ({'network': '203.0.113.25'}, [], False),
    (None, [], False), ([], None, False), ([], '10.0.0.0/24', False),
    ([], {'network': '10.0.0.0/24'}, False), ([1], [], False), (['bad'], [], False),
    (['0.0.0.0/0'], [], False), (['255.255.255.255'], [], False),
    (['169.254.0.0/16'], [], False), (['224.0.0.0/3'], [], False),
])
def test_real_client_workflow_derives_only_the_verified_union(cloud, internal, accepted):
    status, result = NativeTaskRun('tasks.d/clients.yml',
        {'bind_cloud_clients': cloud, 'bind_internal_clients': internal}).run()
    assert status == (0 if accepted else 2)
    if accepted:
        assert not result.failed
        assert result.facts['bind_allowed_clients'] == list(dict.fromkeys(cloud + internal))
        assert result.ok[-1] == 'Reject an override that bypasses the computed client union'
    else:
        assert len(result.failed) == 1
        assert 'bind_allowed_clients' not in result.facts


def test_a_higher_precedence_client_override_is_rejected_by_native_ansible():
    status, result = NativeTaskRun('tasks.d/clients.yml', {'bind_cloud_clients': ['203.0.113.25'],
        'bind_internal_clients': [], 'bind_allowed_clients': ['0.0.0.0/0']}).run()
    assert status == 2 and result.failed == ['Reject an override that bypasses the computed client union']


def test_readonly_native_harness_refuses_modifying_task_files():
    with pytest.raises(ValueError, match='side effects'):
        NativeTaskRun('tasks.d/deploy/packages.yml', {})

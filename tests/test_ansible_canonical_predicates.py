"""Exercise real YAML guards through Ansible with original source positions.

These are predicate contract tests, not task-dispatch or deployment coverage.
The complete role still requires the separate execution and rollback suites.
"""
import copy
from pathlib import Path

from ansible.parsing.dataloader import DataLoader
from ansible.playbook.conditional import Conditional
from ansible.plugins.loader import filter_loader
from ansible.template import Templar
import pytest
import yaml

from route_task_expressions import TaskExpressions


ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / 'roles/bind9/tasks/tasks.d'


class CanonicalPredicates:
    """Resolve names in the actual role; never copy or rewrite its expressions."""

    def __init__(self, filename):
        self.loader = DataLoader()
        filter_loader.add_directory(str(ROOT / 'roles/bind9/filter_plugins'))
        self.tasks = self.loader.load_from_file(str(TASKS / filename))

    def check(self, name, variables, expected, key='that'):
        task, = [value for value in self.tasks if value.get('name') == name]
        expression = task['ansible.builtin.assert']['that'] if key == 'that' else task[key]
        before = copy.deepcopy(variables)
        condition = Conditional(loader=self.loader)
        condition.when = expression if isinstance(expression, list) else [expression]
        actual = condition.evaluate_conditional(Templar(loader=self.loader, variables=variables), variables)
        assert actual is expected
        assert variables == before, 'Guard evaluation must not alter approved input data'


def test_native_adapter_keeps_all_existing_task_values_and_original_positions():
    for path in sorted(TASKS.rglob('*.yml')):
        native = TaskExpressions.load(path.relative_to(TASKS).as_posix())
        assert native == yaml.safe_load(path.read_text(encoding='utf-8')), path
    expression = TaskExpressions.load('clients.yml')[0]['ansible.builtin.assert']['that'][0]
    assert expression.ansible_pos == (str(TASKS / 'clients.yml'), 7, 9)


@pytest.mark.parametrize('cloud,internal,expected', [
    ([], [], True), (['203.0.113.0/24'], ['10.0.0.0/24'], True),
    ('203.0.113.0/24', [], False), ({'network': '203.0.113.0/24'}, [], False),
    (None, [], False), (1, [], False), ([], '10.0.0.0/24', False),
    ([], {'network': '10.0.0.0/24'}, False), ([], None, False), ([], 1, False),
])
def test_client_list_types(cloud, internal, expected):
    CanonicalPredicates('clients.yml').check('Validate the cloud and internal client list types',
        {'bind_cloud_clients': cloud, 'bind_internal_clients': internal}, expected)


@pytest.mark.parametrize('cloud,internal,expected', [([], [], False), (['203.0.113.25'], [], True),
    ([], ['10.0.0.0/24'], True), (['203.0.113.25'], ['10.0.0.0/24'], True)])
def test_client_union_is_nonempty(cloud, internal, expected):
    CanonicalPredicates('clients.yml').check('Require at least one configured DNS client network',
        {'bind_cloud_clients': cloud, 'bind_internal_clients': internal}, expected)


@pytest.mark.parametrize('item,expected', [('203.0.113.25', True), ('203.0.113.0/24', True),
    # Loopback authorization is distinct from remote FRONT route destinations.
    ('0.0.0.0/0', False), ('127.0.0.1', True), ('169.254.0.1', False),
    ('224.0.0.1', False), ('255.255.255.255', False), ('203.0.113.1/24', False),
    ('2001:db8::1', False), ('bad', False), (None, False), (1, False), ({}, False)])
def test_client_entries_follow_the_real_authorization_contract(item, expected):
    CanonicalPredicates('clients.yml').check('Validate cloud and internal client IPv4 entries', {'item': item}, expected)


@pytest.mark.parametrize('allowed,expected', [(['203.0.113.25', '10.0.0.0/24'], True),
    (['203.0.113.25'], False), (['0.0.0.0/0'], False)])
def test_computed_union_cannot_be_bypassed(allowed, expected):
    CanonicalPredicates('clients.yml').check('Reject an override that bypasses the computed client union',
        {'bind_cloud_clients': ['203.0.113.25', '203.0.113.25'], 'bind_internal_clients': ['10.0.0.0/24'],
         'bind_allowed_clients': allowed}, expected)


@pytest.mark.parametrize('value,expected', [(True, True), (False, True), ('true', True), ('false', True),
    ('auto', True), ('yes', False), ('no', False), ('AUTO', False), ('', False),
    (0, False), (1, False), (None, False), ([], False), ({}, False)])
def test_dnssec_explicit_mode_contract(value, expected):
    CanonicalPredicates('dnssec_settings.yml').check('Validate the optional DNSSEC mode',
                                                  {'bind_dnssec_active': value}, expected)


@pytest.mark.parametrize('changes,expected', [({}, True), ({'exists': False}, True),
    ({'islnk': True}, False), ({'uid': 1}, False), ({'nlink': 2}, False), ({'isreg': False}, False),
    ({'mode': '0664'}, False), ({'mode': '0646'}, False), ({'size': 1048577}, False),
    ({'size': 1048576}, True), ({'mode': '0700'}, True), ({'mode': '0750'}, True)])
def test_runtime_snapshot_rejects_unsafe_objects(changes, expected):
    stat = dict(exists=True, isreg=True, islnk=False, uid=0, nlink=1, mode='0644', size=100)
    stat.update(changes)
    CanonicalPredicates('runtime_snapshot.yml').check('Refuse unsafe managed runtime paths before making changes',
        {'item': {'item': '/usr/local/sbin/bind-private-dnssec', 'stat': stat}}, expected)


@pytest.mark.parametrize('path,changes,expected', [('/etc/named.conf', {}, True),
    ('/etc/named.conf', {'exists': False}, True), ('/etc/named.conf', {'islnk': True}, False),
    ('/etc/named.conf', {'isreg': False, 'isdir': True}, False), ('/etc/named.conf', {'nlink': 2}, False),
    ('/var/named/zones', {'isreg': False, 'isdir': True}, True),
    ('/var/named/zones', {'isreg': True, 'isdir': False}, False)])
def test_managed_paths_require_their_exact_object_kind(path, changes, expected):
    stat = dict(exists=True, isreg=True, isdir=False, islnk=False, nlink=1)
    stat.update(changes)
    CanonicalPredicates('path_safety.yml').check('Refuse symlinks and non-regular managed files',
        {'item': {'item': path, 'stat': stat}, 'bind_managed_file_paths': ['/etc/named.conf']}, expected)


@pytest.mark.parametrize('action,check,metric,expected', [('plan', True, 100, True),
    ('apply', False, 100, True), ('recover', False, 100, True), ('rollback', False, 100, True),
    ('verify', False, 100, True), ('commit', False, 100, True), ('apply', True, 100, False),
    ('unknown', False, 100, False), ('plan', True, True, False), ('apply', False, '100', False)])
def test_front_action_envelope_is_explicit(action, check, metric, expected):
    CanonicalPredicates('deploy/front_return_routes.yml').check('Validate the native FRONT action envelope',
        {'bind_front_action': action, 'ansible_check_mode': check, 'bind_front_route_metric': metric}, expected)


@pytest.mark.parametrize('rc,failed,changed', [(0, False, False), (2, False, True),
    (1, True, False), (5, True, False), (130, True, False)])
def test_front_native_return_codes_do_not_mask_a_refusal(rc, failed, changed):
    case = CanonicalPredicates('deploy/front_return_routes.yml')
    variables = {'bind_front_operation': {'rc': rc}}
    case.check('Run the single native FRONT transaction with bounded observations', variables, failed, 'failed_when')
    case.check('Run the single native FRONT transaction with bounded observations', variables, changed, 'changed_when')

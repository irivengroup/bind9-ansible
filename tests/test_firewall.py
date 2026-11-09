"""Evaluate actual firewalld discovery, refusal and deduplication expressions."""
import copy
import json
from pathlib import Path

import pytest
import yaml

pytest.importorskip('ansible')
from route_task_expressions import TaskExpressions
from test_ansible_canonical_predicates import CanonicalPredicates

ROOT = Path(__file__).resolve().parents[1]
TASKS = TaskExpressions.load('deploy/firewall.yml')


class TestDnsFirewall:
    """Keep ingress restricted to discovered listener zones without network resets."""

    def test_observed_contract_keeps_exact_original_values_and_loader_positions(self):
        path = ROOT / 'roles/bind9/tasks/tasks.d/deploy/firewall.yml'
        assert TASKS == yaml.safe_load(path.read_text())
        assert TASKS[0]['failed_when'].ansible_pos[0] == str(path)

    @staticmethod
    def run_tasks(evaluator, tasks):
        # A real set_fact loop refreshes host facts between iterations. The
        # expression-only adapter needs a fresh scope for each such iteration.
        for task in tasks:
            if 'loop' not in task:
                evaluator.run('', tasks=[task])
                continue
            for item in evaluator.render(task['loop'], evaluator.variables):
                step = copy.deepcopy(task)
                step['loop'] = [item]
                evaluator.run('', tasks=[step])

    @pytest.mark.parametrize('shared', [False, True])
    def test_address_discovery_includes_secondary_ips_and_skips_administration(self, shared):
        interfaces = [
            {'ifname': 'adm0', 'addr_info': [{'local': '10.99.0.10'}]},
            {'ifname': 'front0', 'addr_info': [{'local': '192.0.2.100'}, {'local': '192.0.2.10'}]},
        ]
        if shared:
            interfaces[1]['addr_info'].append({'local': '10.0.0.10'})
        else:
            interfaces.append({'ifname': 'back0', 'addr_info': [{'local': '10.0.0.10'}]})
        scope = {'bind_front_ip': '192.0.2.10', 'bind_back_ip': '10.0.0.10',
                 'bind_dns_firewall_addresses': {'stdout': json.dumps(interfaces)}}
        evaluator = TaskExpressions(scope)
        self.run_tasks(evaluator, TASKS[2:6])
        found = evaluator.variables['bind_dns_firewall_listener_addresses']
        assert found == [{'address': '192.0.2.10', 'device': 'front0'},
                         {'address': '10.0.0.10', 'device': 'front0' if shared else 'back0'}]

    @pytest.mark.parametrize('addresses', [[], ['192.0.2.10'], ['192.0.2.10', '192.0.2.10', '10.0.0.10']])
    def test_missing_or_duplicated_listener_addresses_are_rejected(self, addresses):
        scope = {'bind_front_ip': '192.0.2.10', 'bind_back_ip': '10.0.0.10',
                 'bind_dns_firewall_listener_addresses': [{'address': ip, 'device': 'eth0'} for ip in addresses]}
        with pytest.raises(ValueError):
            TaskExpressions(scope).run('', tasks=[TASKS[5]])

    @pytest.mark.parametrize('rc,stdout,stderr,accepted', [(0, 'public', '', True), (2, '', 'no zone', True),
                                                         (2, 'no zone', '', True), (0, '', '', False),
                                                         (0, 'no zone', '', False), (2, '', 'D-Bus failure', False),
                                                         (3, 'no zone', '', False)])
    def test_zone_output_must_be_valid_or_explicitly_unbound(self, rc, stdout, stderr, accepted):
        observation = {'rc': rc, 'stdout': stdout, 'stderr': stderr, 'item': ['front0', 'runtime']}
        scope = {'bind_dns_firewall_interface_zones': {'results': [observation]}}
        if accepted:
            TaskExpressions(scope).run('', tasks=[TASKS[9]])
        else:
            with pytest.raises(ValueError):
                TaskExpressions(scope).run('', tasks=[TASKS[9]])

    def test_runtime_and_permanent_zones_are_deduplicated_separately(self):
        results = [{'rc': 0, 'stdout': 'public', 'stderr': '', 'item': ['front0', 'runtime']},
                   {'rc': 2, 'stdout': '', 'stderr': 'no zone', 'item': ['front0', 'permanent']},
                   {'rc': 0, 'stdout': 'public', 'stderr': '', 'item': ['back0', 'runtime']},
                   {'rc': 0, 'stdout': 'internal', 'stderr': '', 'item': ['back0', 'permanent']}]
        evaluator = TaskExpressions({'bind_dns_firewall_targets': {},
                                     'bind_dns_firewall_default_zone': {'stdout': 'public'},
                                     'bind_dns_firewall_interface_zones': {'results': results}})
        self.run_tasks(evaluator, [TASKS[10]])
        assert evaluator.variables['bind_dns_firewall_targets'] == {
            'runtime:public': {'zone': 'public', 'scope': 'runtime'},
            'permanent:public': {'zone': 'public', 'scope': 'permanent'},
            'permanent:internal': {'zone': 'internal', 'scope': 'permanent'}}

    @pytest.mark.parametrize('rc,output,failed', [(0, 'yes', False), (1, 'no', False), (0, '', True),
                                              (1, '', True), (0, 'no', True), (1, 'yes', True), (13, 'no', True)])
    def test_query_exit_code_and_output_must_agree(self, rc, output, failed):
        scope = {'bind_dns_firewall_queries': {'rc': rc, 'stdout': output}}
        assert TaskExpressions(scope).condition([TASKS[11]['failed_when']], scope) is failed

    def test_entry_point_and_commands_never_reload_or_reassign_zones(self):
        main = yaml.safe_load((ROOT / 'roles/bind9/tasks/tasks.d/deploy/main.yml').read_text())
        imported = next(t for t in main if t.get('ansible.builtin.import_tasks') == '{{ role_path }}/tasks/tasks.d/deploy/firewall.yml')
        assert imported['tags'] == ['deploy', 'firewall']
        assert imported['environment'] == {'LC_ALL': 'C'}
        # Ansible's source-attributed containers are JSON-compatible but have
        # no SafeDumper representer. Scan the same values without discarding
        # original positions or including unrelated YAML comments.
        source = json.dumps(TASKS, sort_keys=True)
        for forbidden in ('--reload', '--complete-reload', '--runtime-to-permanent', '--add-interface', '--change-interface', '--set-default-zone'):
            assert forbidden not in source


@pytest.mark.parametrize('rc,stdout,failed', [(0, 'running', False), (0, 'inactive', True), (1, 'running', True)])
def test_native_firewall_state_guard_requires_status_and_output_to_agree(rc, stdout, failed):
    CanonicalPredicates('deploy/firewall.yml').check('Require an already running firewalld daemon',
        {'bind_dns_firewall_state': {'rc': rc, 'stdout': stdout}}, failed, 'failed_when')


@pytest.mark.parametrize('output,accepted', [('[]', True), ('null', False), ('{}', False),
                                          ('"scalar"', False), ('false', False), ('', False)])
def test_native_firewall_address_contract_refuses_malformed_observation_types(output, accepted):
    CanonicalPredicates('deploy/firewall.yml').check('Require nonempty IPv4 interface observations',
        {'bind_dns_firewall_addresses': {'stdout': output}}, accepted)


@pytest.mark.parametrize('output,accepted', [('public', True), (' dns_front-1\n', True),
                                          ('', False), ('no zone', False), ('public\ninjected', False)])
def test_native_firewall_default_zone_contract_accepts_only_one_safe_name(output, accepted):
    CanonicalPredicates('deploy/firewall.yml').check('Require a valid existing default zone name',
        {'bind_dns_firewall_default_zone': {'stdout': output}}, accepted)


@pytest.mark.parametrize('rc,stdout,stderr,failed', [(0, 'public', '', False), (2, 'no zone', '', False),
    (2, '', 'no zone', False), (2, '', 'bad', True), (13, 'no zone', '', True)])
def test_native_firewall_discovery_command_refuses_errors_and_accepts_only_explicit_unbound_results(rc, stdout, stderr, failed):
    CanonicalPredicates('deploy/firewall.yml').check('Discover existing runtime and permanent DNS interface zones',
        {'bind_dns_firewall_interface_zones': {'rc': rc, 'stdout': stdout, 'stderr': stderr}}, failed, 'failed_when')


@pytest.mark.parametrize('rc,stdout,failed', [(0, 'yes', False), (1, 'yes', True), (0, 'no', True)])
def test_native_firewall_postcheck_cannot_accept_failure_or_a_negative_answer(rc, stdout, failed):
    CanonicalPredicates('deploy/firewall.yml').check('Verify DNS service is authorized at runtime and permanently',
        {'bind_dns_firewall_verified': {'rc': rc, 'stdout': stdout}}, failed, 'failed_when')


@pytest.mark.parametrize('check_mode', [False, True])
def test_native_firewall_postcheck_follows_the_actual_check_mode_guard(check_mode):
    CanonicalPredicates('deploy/firewall.yml').check('Verify DNS service is authorized at runtime and permanently',
        {'ansible_check_mode': check_mode}, not check_mode, 'when')

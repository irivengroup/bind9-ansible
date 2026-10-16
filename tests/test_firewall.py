"""Evaluate actual firewalld discovery, refusal and deduplication expressions."""
import copy
import json
from pathlib import Path

import pytest
import yaml

pytest.importorskip('ansible')
from route_task_expressions import TaskExpressions

ROOT = Path(__file__).resolve().parents[1]
TASKS = yaml.safe_load((ROOT / 'roles/bind9/tasks/tasks.d/firewall.yml').read_text())


class TestDnsFirewall:
    """Keep ingress restricted to discovered listener zones without network resets."""

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
        main = yaml.safe_load((ROOT / 'roles/bind9/tasks/main.yml').read_text())
        imported = next(t for t in main if t.get('ansible.builtin.import_tasks') == 'tasks.d/firewall.yml')
        assert imported['tags'] == ['deploy', 'firewall']
        assert imported['environment'] == {'LC_ALL': 'C'}
        source = yaml.safe_dump(TASKS)
        for forbidden in ('--reload', '--complete-reload', '--runtime-to-permanent', '--add-interface', '--change-interface', '--set-default-zone'):
            assert forbidden not in source

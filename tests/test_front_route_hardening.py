"""Evaluate exact route identities and policy guards from the production YAML."""
import copy
import json

import pytest
import yaml

from route_task_expressions import TaskExpressions
from test_native_front_routes import BASE, NEW, RULES, plan


class TestStrictFrontRoutes:
    """Reject extra policy selectors and drift before any automated removal."""

    @pytest.mark.parametrize('selector', [{'dst': '192.0.2.0/24'}, {'fwmark': '0x1'}, {'tos': 4}])
    def test_three_rules_are_not_enough_when_a_rule_has_selectors(self, selector):
        rules = json.loads(RULES)
        rules[1].update(selector)
        with pytest.raises(ValueError):
            plan(rules=json.dumps(rules))

    @pytest.mark.parametrize('attributes', [{'nhid': 12}, {'flags': ['onlink']}, {'prefsrc': '198.51.100.9'}])
    def test_route_with_changed_attributes_is_not_owned(self, attributes):
        tasks = yaml.safe_load((TaskExpressions.TASKS / 'deploy/front_routes_rollback.yml').read_text())
        first = next(i for i, task in enumerate(tasks) if task['name'] == 'Normalize active destinations being inspected')
        context = {'bind_front_plan': plan(), 'bind_front_rollback_kernel': {'stdout': json.dumps(BASE + [dict(NEW, **attributes)])}}
        with pytest.raises(ValueError):
            TaskExpressions(context).run('', tasks=tasks[first:first + 3])

    def test_unspecified_kernel_metric_does_not_match_an_arbitrary_metric(self):
        wanted = plan(persistent='203.0.113.0/24 198.51.100.1')
        with pytest.raises(ValueError):
            TaskExpressions({'bind_front_plan': wanted, 'bind_front_compare_profile': '203.0.113.0/24 198.51.100.1',
                             'bind_front_compare_runtime': BASE + [dict(NEW, metric=200)],
                             'bind_front_compare_applied': True}).run('deploy/front_routes_compare.yml')

    def test_duplicate_owned_journal_entries_are_rejected(self):
        journal = copy.deepcopy(plan())
        journal['runtime_additions'] *= 2
        with pytest.raises(ValueError):
            TaskExpressions({'bind_front_plan': journal}).run('deploy/front_routes_validate_journal.yml')

    @pytest.mark.parametrize('serialized', [False, True])
    def test_ipv4_normalizer_accepts_native_or_json_lists(self, serialized):
        values = ['198.51.100.1', '203.0.113.0/24']
        result = TaskExpressions({'bind_front_ipv4_values': json.dumps(values) if serialized else values}).run('deploy/front_routes_ipv4.yml')
        assert result['bind_front_ipv4_data']['203.0.113.0/24']['prefix'] == 24
        assert result['bind_front_ipv4_data']['198.51.100.1']['prefix'] == 32

    @pytest.mark.parametrize('peer', ['2001:db8::ffff:1', '2001:db8::ffff:203.0.113.8', '::ffff:0:203.0.113.8'])
    def test_native_ipv6_ssh_is_not_mistaken_for_ipv4_mapped_ssh(self, peer):
        assert plan(ssh=peer)['runtime_additions'] == ['203.0.113.0/24 198.51.100.1 100']

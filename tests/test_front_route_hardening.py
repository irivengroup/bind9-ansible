"""Exercise ownership and policy guards in the current native route engine."""
import copy
import json

import pytest

from test_native_front_routes import RULES, NativeRouteLab, plan, pytestmark as inherited_pytestmark

pytestmark = inherited_pytestmark


class TestStrictFrontRoutes:
    """Reject extra policy selectors and drift before any automated removal."""

    @pytest.mark.parametrize('padding', ['', ' ', '\t', ' \t'])
    def test_standard_policy_accepts_el7_horizontal_padding_without_duplicate_routes(self, tmp_path, padding):
        lab = NativeRouteLab(tmp_path)
        lab.data['rules_text'] = '\n'.join(line + padding for line in (
            '0:\tfrom all lookup local', '32766:\tfrom all lookup main', '32767:\tfrom all lookup default')) + '\n'
        original = copy.deepcopy(lab.data)
        assert lab.run('plan').returncode == 2
        assert lab.data == original
        assert lab.run().returncode == 2
        added = copy.deepcopy(lab.data)
        assert lab.run().returncode == 0
        assert lab.data == added
        assert all(route in added['runtime'] for route in original['runtime'])
        assert len([route for route in added['runtime'] if route['dst'] == '203.0.113.0/24']) == 1

    @pytest.mark.parametrize('rules', [
        '0: from all lookup local \n32766: from all lookup main fwmark 1 \n32767: from all lookup default ',
        '0: from all lookup local \n32765: from all lookup main \n32767: from all lookup default ',
        '0: from all lookup local \n32766: from all lookup main \n32767: from all lookup default \n40000: from all lookup 100 ',
        '0: from all lookup local \n32766: from all lookup main \n32766: from all lookup main \n32767: from all lookup default ',
        '0: from all lookup local \n32766: from all lookup 100 \n32767: from all lookup default ',
        '0:\nfrom all lookup local \n32766: from all lookup main \n32767: from all lookup default ',
    ])
    def test_padding_support_keeps_custom_duplicate_and_multiline_policies_refused(self, tmp_path, rules):
        lab = NativeRouteLab(tmp_path)
        lab.data['rules_text'] = rules + '\n'
        original = copy.deepcopy(lab.data)
        result = lab.run()
        assert result.returncode == 1 and 'Custom IPv4 policy rules' in result.stdout
        assert lab.data == original
        assert not (lab.state / 'front-routes.pending').exists()

    @pytest.mark.parametrize('selector', [{'dst': '192.0.2.0/24'}, {'fwmark': '0x1'}, {'tos': 4}])
    def test_three_rules_are_not_enough_when_a_rule_has_selectors(self, selector):
        rules = json.loads(RULES)
        rules[1].update(selector)
        with pytest.raises(ValueError):
            plan(rules=json.dumps(rules))

    @pytest.mark.parametrize('attributes', [{'nhid': 12}, {'flags': ['onlink']}, {'prefsrc': '198.51.100.9'}])
    def test_route_with_changed_attributes_is_not_owned(self, tmp_path, attributes):
        lab = NativeRouteLab(tmp_path)
        assert lab.run(more=['--defer-commit']).returncode == 2
        lab.data['runtime'][-1].update(attributes)
        before = copy.deepcopy(lab.data)
        result = lab.run('recover')
        assert result.returncode == 1, result.stdout
        assert lab.data == before and (lab.state / 'front-routes.pending').is_dir()

    def test_unspecified_kernel_metric_does_not_match_an_arbitrary_metric(self, tmp_path):
        lab = NativeRouteLab(tmp_path)
        lab.data['persistent'] = [{'network': '203.0.113.0/24', 'gateway': '198.51.100.1', 'metric': -1}]
        assert lab.run(more=['--defer-commit']).returncode == 2
        lab.data['runtime'][-1]['metric'] = 200
        before = copy.deepcopy(lab.data)
        result = lab.run('recover')
        assert result.returncode == 1, result.stdout
        assert lab.data == before and (lab.state / 'front-routes.pending').is_dir()

    def test_duplicate_completed_journal_claims_are_rejected(self, tmp_path):
        lab = NativeRouteLab(tmp_path)
        assert lab.run(more=['--defer-commit']).returncode == 2
        journal = lab.state / 'front-routes.pending'
        operations = (journal / 'operations').read_text().splitlines()
        lab.rewrite_journal('operations', '\n'.join(operations * 2) + '\n')
        (journal / 'progress').write_text('4 done\n')
        before = copy.deepcopy(lab.data)
        result = lab.run('recover')
        assert result.returncode == 1, result.stdout
        assert lab.data == before and journal.is_dir()

    @pytest.mark.parametrize('peer', ['2001:db8::ffff:1', '2001:db8::ffff:203.0.113.8', '::ffff:0:203.0.113.8', '::ffff:0:0:abcd'])
    def test_native_ipv6_ssh_is_not_mistaken_for_ipv4_mapped_ssh(self, peer):
        assert plan(ssh=peer)['runtime_additions'] == ['203.0.113.0/24 198.51.100.1 100']

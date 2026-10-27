"""Exercise ownership and policy guards in the current native route engine."""
import copy
import json

import pytest

from test_native_front_routes import RULES, NativeRouteLab, plan, pytestmark as inherited_pytestmark

pytestmark = inherited_pytestmark


class TestStrictFrontRoutes:
    """Reject extra policy selectors and drift before any automated removal."""

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

    @pytest.mark.parametrize('peer', ['2001:db8::ffff:1', '2001:db8::ffff:203.0.113.8', '::ffff:0:203.0.113.8'])
    def test_native_ipv6_ssh_is_not_mistaken_for_ipv4_mapped_ssh(self, peer):
        assert plan(ssh=peer)['runtime_additions'] == ['203.0.113.0/24 198.51.100.1 100']

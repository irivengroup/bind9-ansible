"""Safety and idempotence contracts for native FRONT routing."""
import copy
import ast
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('front_routes', ROOT / 'roles/bind9/filter_plugins/front_routes.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
Planner = MODULE.FrontRoutePlanner
UUID = '12345678-1234-4234-8234-123456789abc'
RULES = json.dumps([{'priority': 0, 'src': 'all', 'table': 'local'},
                    {'priority': 32766, 'src': 'all', 'table': 'main'},
                    {'priority': 32767, 'src': 'all', 'table': 'default'}])
ADDRESSES = json.dumps([{'ifname': name, 'operstate': 'UP', 'addr_info': [
    {'family': 'inet', 'local': address, 'prefixlen': 24}]} for name, address in
    [('front0', '198.51.100.2'), ('back0', '10.0.0.2'), ('adm0', '192.0.2.2')]])
BASE = [{'dst': 'default', 'gateway': '10.0.0.1', 'dev': 'back0', 'metric': 100},
        {'dst': '198.51.100.0/24', 'dev': 'front0', 'protocol': 'kernel', 'scope': 'link'}]
NEW = {'dst': '203.0.113.0/24', 'gateway': '198.51.100.1', 'dev': 'front0', 'metric': 100}


def plan(**kwargs):
    """Use a small three-interface main-table topology for each independent case."""
    values = dict(interface=Planner.interface(ADDRESSES, '198.51.100.2', '10.0.0.2', '192.0.2.2'),
                  clients=['203.0.113.0/24'], gateway='198.51.100.1', persistent='',
                  runtime=json.dumps(BASE), rules=RULES, route_table='0', profile=UUID,
                  metric=100, protected_ips=['10.0.0.3', '192.0.2.99'])
    values.update(kwargs)
    return Planner.plan(**values)


class TestNativeFrontRoutes:
    def test_controller_filter_parses_with_python36_grammar(self):
        source = ROOT / 'roles/bind9/filter_plugins/front_routes.py'
        ast.parse(source.read_text(), filename=str(source), feature_version=(3, 6))

    def test_plan_does_not_require_python37_subnet_of_api(self, monkeypatch):
        # Before the correction this call raised TypeError; Python 3.6 has no
        # subnet_of method. Range containment must retain the same route plan.
        monkeypatch.setattr(MODULE.ipaddress.IPv4Network, 'subnet_of', None)
        assert plan()['profile_additions'] == ['203.0.113.0/24 198.51.100.1 100']

    @pytest.mark.parametrize('address,expected', [('', []),
        ('203.0.113.8', ['203.0.113.8']), ('2001:db8::1', []),
        ('::ffff:203.0.113.8', ['203.0.113.8'])])
    def test_actual_ssh_peer_is_normalized_for_protection(self, address, expected):
        assert Planner.ssh_ipv4_guard(address) == expected

    def test_ssh_controller_route_is_protected_and_invalid_identity_fails(self):
        with pytest.raises(ValueError):
            Planner.ssh_ipv4_guard('invalid-controller')
        with pytest.raises(ValueError):
            plan(protected_ips=['203.0.113.8'])

    def test_additive_plan_keeps_back_default_and_normalizes_single_hosts(self):
        result = plan(clients=['203.0.113.25'])
        assert result['profile_additions'] == ['203.0.113.25/32 198.51.100.1 100']
        assert result['runtime_additions'] == result['profile_additions']
        assert result['runtime_before'] == BASE

    def test_second_run_has_no_mutations(self):
        result = plan(persistent='{ ip = 203.0.113.0/24, nh = 198.51.100.1, mt = 100 }',
                      runtime=json.dumps(BASE + [NEW]))
        assert result['profile_additions'] == result['runtime_additions'] == []

    def test_native_host_routes_omit_prefix_without_breaking_verification_or_recovery(self):
        result = plan(clients=['203.0.113.25'])
        host = dict(NEW, dst='203.0.113.25')
        assert Planner.unchanged_routes(BASE, json.dumps(BASE + [host]), result)
        assert Planner.owned_runtime_routes(result, json.dumps(BASE + [host])) == result['runtime_additions']

    def test_existing_metric_is_retained_when_repairing_runtime_only(self):
        result = plan(persistent='{ ip = 203.0.113.0/24, nh = 198.51.100.1, mt = 230 }')
        assert result['profile_additions'] == []
        assert result['runtime_additions'] == ['203.0.113.0/24 198.51.100.1 230']

    def test_connected_front_client_needs_only_path_verification(self):
        result = plan(clients=['198.51.100.25'])
        assert result['profile_additions'] == result['runtime_additions'] == []
        assert result['probes'] == [{'address': '198.51.100.25', 'network': '198.51.100.25/32', 'gateway': None}]

    @pytest.mark.parametrize('client', ['0.0.0.0/0', '127.0.0.0/8', '169.254.0.0/16',
                                       '224.0.0.0/4', '240.0.0.0/4', '10.0.0.0/24',
                                       '192.0.2.0/24', '198.51.0.0/16', '2001:db8::/64'])
    def test_unsafe_or_unsupported_clients_fail_before_mutation(self, client):
        with pytest.raises(ValueError):
            plan(clients=[client])

    @pytest.mark.parametrize('gateway', ['', 'garbage', '198.51.100.2', '192.0.2.1', '198.51.100.0', '198.51.100.255'])
    def test_missing_offlink_or_self_gateway_rejected(self, gateway):
        with pytest.raises(ValueError):
            plan(gateway=gateway)

    @pytest.mark.parametrize('text', ['', 'garbage', '{}', 'null', '[1]'])
    def test_empty_and_invalid_native_json_is_explicit_failure(self, text):
        # Reproduces the old failure cause; no changed_when parses a helper stdout.
        with pytest.raises(ValueError):
            plan(runtime=text)

    def test_unknown_profile_output_is_not_treated_as_empty(self):
        with pytest.raises(ValueError):
            plan(persistent='unexpected output')

    def test_native_terse_profile_format_matches_documented_brace_format(self):
        assert Planner.profile_routes('203.0.113.0/24 198.51.100.1 100') == Planner.profile_routes(
            '{ ip = 203.0.113.0/24, nh = 198.51.100.1, mt = 100 }')
        assert Planner.profile_routes('172.16.0.0/24 198.51.100.1 150, 203.0.113.0/24 198.51.100.1 100')[-1]['metric'] == 100
        assert Planner.profile_routes('10.99.0.0/24')[0]['gateway'] == '0.0.0.0'
        with pytest.raises(ValueError):
            Planner.profile_routes('203.0.113.0/24 198.51.100.1 100 garbage')

    def test_unrelated_attributed_profile_routes_are_preserved(self):
        raw = '{ ip = 172.16.0.0/24, nh = 198.51.100.1, mt = 50, table = 100 }'
        result = plan(persistent=raw)
        assert result['profile_before'][0]['attributes'] == {'table': '100'}
        with pytest.raises(ValueError):
            plan(persistent=raw.replace('172.16.0.0/24', '203.0.113.0/24'))

    @pytest.mark.parametrize('row', [dict(NEW, gateway='10.0.0.1', dev='back0'),
                                   dict(NEW, dst='203.0.113.25/32', dev='back0'),
                                   dict(NEW, nexthops=[{'dev': 'front0'}]),
                                   dict(NEW, type='blackhole')])
    def test_conflicting_equal_or_more_specific_runtime_route_rejected(self, row):
        with pytest.raises(ValueError):
            plan(runtime=json.dumps(BASE + [row]))

    def test_unowned_runtime_only_route_is_not_deleted_on_rollback(self):
        with pytest.raises(ValueError):
            plan(runtime=json.dumps(BASE + [NEW]))

    def test_custom_policy_rules_and_route_tables_require_explicit_design(self):
        with pytest.raises(ValueError):
            plan(route_table='100')
        with pytest.raises(ValueError):
            plan(rules=RULES[:-1] + ', {"priority": 500, "table": "100", "src": "all"}]')

    @pytest.mark.parametrize('metric', [True, '100', 0, -1, 4294967296])
    def test_invalid_metric_rejected(self, metric):
        with pytest.raises(ValueError):
            plan(metric=metric)

    def test_existing_default_deletion_and_unrelated_additions_detected(self):
        result = plan()
        assert Planner.unchanged_routes(BASE, json.dumps(BASE + [NEW]), result)
        with pytest.raises(ValueError):
            Planner.unchanged_routes(BASE, json.dumps([NEW]), result)
        with pytest.raises(ValueError):
            Planner.unchanged_routes(BASE, json.dumps(BASE + [dict(NEW, dst='8.8.8.8/32')]), result)
        with pytest.raises(ValueError):
            Planner.unchanged_routes(BASE, json.dumps(BASE), result)

    def test_selective_rollback_does_not_remove_existing_routes(self):
        result = plan()
        assert Planner.owned_profile_routes(result, '') == []  # First command failed.
        assert Planner.owned_runtime_routes(result, json.dumps(BASE)) == []
        assert Planner.owned_profile_routes(result, '{ ip = 203.0.113.0/24, nh = 198.51.100.1, mt = 100 }') == result['profile_additions']
        assert Planner.owned_runtime_routes(result, json.dumps(BASE + [NEW])) == result['runtime_additions']
        assert Planner.unchanged_routes(BASE, json.dumps(BASE), result, applied=False)

    def test_replacement_by_other_operator_blocks_automatic_removal(self):
        result = plan()
        with pytest.raises(ValueError):
            Planner.owned_runtime_routes(result, json.dumps(BASE + [dict(NEW, metric=200)]))
        with pytest.raises(ValueError):
            Planner.owned_profile_routes(result, '{ ip = 203.0.113.0/24, nh = 198.51.100.1, mt = 200 }')

    def test_journal_cannot_claim_preexisting_or_default_routes(self):
        result = plan()
        assert Planner.validated_journal(result) == result
        corrupted = copy.deepcopy(result)
        corrupted['runtime_additions'] = ['0.0.0.0/0 198.51.100.1 100']
        with pytest.raises(ValueError):
            Planner.validated_journal(corrupted)
        corrupted = copy.deepcopy(result)
        corrupted['runtime_before'].append(NEW)
        with pytest.raises(ValueError):
            Planner.validated_journal(corrupted)

    def test_return_lookup_requires_device_and_gateway(self):
        assert Planner.verify_lookup(json.dumps([NEW]), 'front0', '198.51.100.1')
        with pytest.raises(ValueError):
            Planner.verify_lookup(json.dumps([NEW]), 'back0')
        with pytest.raises(ValueError):
            Planner.verify_lookup(json.dumps([NEW]), 'front0', '10.0.0.1')

    def test_profile_snapshot_must_match_exact_additions(self):
        result = plan()
        raw = '{ ip = 203.0.113.0/24, nh = 198.51.100.1, mt = 100 }'
        assert Planner.unchanged_profile([], raw, result)
        with pytest.raises(ValueError):
            Planner.unchanged_profile([], '', result)
        assert Planner.unchanged_profile([], '', result, applied=False)

    def test_discovery_requires_isolated_up_devices_and_one_uuid(self):
        assert Planner.profile_uuid(UUID) == UUID
        with pytest.raises(ValueError):
            Planner.profile_uuid('')
        with pytest.raises(ValueError):
            Planner.interface(ADDRESSES, '198.51.100.2', '198.51.100.2', '192.0.2.2')
        with pytest.raises(ValueError):
            Planner.interface(ADDRESSES, '198.51.100.2', '10.99.0.2', '192.0.2.2')
        down = json.loads(ADDRESSES)
        down[0]['operstate'] = 'DOWN'
        with pytest.raises(ValueError):
            Planner.interface(json.dumps(down), '198.51.100.2', '10.0.0.2', '192.0.2.2')

    @pytest.mark.parametrize('text', [',', '{ broken }',
        '{ ip = 203.0.113.0/24 }', '{ ip = 203.0.113.0/24, nh = 198.51.100.1, nh = 198.51.100.1 }'])
    def test_truncated_or_duplicate_native_profile_fields_are_rejected(self, text):
        with pytest.raises(ValueError):
            Planner.profile_routes(text)

    def test_attributed_terse_routes_are_observed_without_being_rewritten(self):
        observed = Planner.profile_routes('172.16.0.0/24 198.51.100.1 150 table=100')
        assert observed[0]['attributes'] == {'table': '100'}

    def test_duplicate_and_connected_conflicting_routes_are_not_overridden(self):
        raw = '{ ip = 203.0.113.0/24, nh = 198.51.100.1, mt = 100 }'
        with pytest.raises(ValueError):
            plan(persistent=raw + ', ' + raw)
        with pytest.raises(ValueError):
            plan(clients=['198.51.100.25'], runtime=json.dumps(BASE + [dict(NEW, dst='198.51.100.25', dev='back0')]))

    @pytest.mark.parametrize('field,value', [
        ('device', 'invalid device'), ('profile_additions', 'not-a-list'),
        ('runtime_additions', [42]), ('runtime_additions', ['203.0.113.1/24 198.51.100.1 100']),
        ('runtime_additions', ['203.0.113.0/24 198.51.100.1 0']),
        ('runtime_additions', ['192.0.2.99/32 198.51.100.1 100'])])
    def test_corrupt_recovery_journals_cannot_trigger_a_route_removal(self, field, value):
        corrupted = copy.deepcopy(plan())
        corrupted[field] = value
        with pytest.raises(ValueError):
            Planner.validated_journal(corrupted)

    def test_corrupt_journal_schema_and_noncanonical_uuid_are_rejected(self):
        with pytest.raises(ValueError):
            Planner.validated_journal({})
        with pytest.raises(ValueError):
            Planner.profile_uuid(UUID.replace('-', ''))

    def test_persistent_route_loss_is_an_explicit_failure(self):
        original = Planner.profile_routes('172.16.0.0/24 198.51.100.1 150')
        with pytest.raises(ValueError):
            Planner.unchanged_profile(original, '', plan(), applied=False)

    def test_filter_entry_points_execute_the_public_planning_contract(self):
        filters = MODULE.FilterModule().filters()
        assert filters['front_profile_uuid'](UUID) == UUID
        assert filters['front_interface'](ADDRESSES, '198.51.100.2', '10.0.0.2', '192.0.2.2')['device'] == 'front0'

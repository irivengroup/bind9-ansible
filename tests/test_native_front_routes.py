"""Safety contracts executed from native route YAML, without custom filters."""
import copy
import json
from pathlib import Path

import pytest
import yaml
from route_task_expressions import TaskExpressions

ROOT = Path(__file__).resolve().parents[1]
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
    """Supply CLI observations; actual planning and checks come from role tasks."""
    values = dict(clients=['203.0.113.0/24'], internal=[], gateway='198.51.100.1', persistent='',
                  runtime=json.dumps(BASE), rules=RULES, route_table='0', profile=UUID, metric=100, ssh='192.0.2.99')
    values.update(kwargs)
    context = {'ansible_host':'192.0.2.2', 'bind_front_ip':'198.51.100.2', 'bind_back_ip':'10.0.0.2',
               'bind_primary_host':'p', 'bind_secondary_host':'s',
               'hostvars':{'p':{'bind_back_ip':'10.0.0.2'}, 's':{'bind_back_ip':'10.0.0.3'}},
               'bind_front_interface':{'device':'front0','network':'198.51.100.0/24',
                   'protected_networks':['10.0.0.0/24','192.0.2.0/24']},
               'bind_front_uuid':values['profile'], 'bind_front_ssh_client':values['ssh'],
               'bind_cloud_clients':values['clients'], 'bind_internal_clients':values['internal'], 'bind_front_route_metric':values['metric'],
               'bind_front_profile':{'results':[{'stdout':x} for x in [values['gateway'],values['persistent'],values['route_table']]]},
               'bind_front_kernel':{'results':[{'stdout':values['runtime']},{'stdout':values['rules']}]}}
    return TaskExpressions(context).run('front_routes_plan.yml')['bind_front_plan']


class TestNativeRouteExpressions:
    @pytest.mark.parametrize('clients', [['203.0.112.0/24','203.0.113.0/24'], ['203.0.112.0/23','203.0.113.0/24']])
    def test_existing_cidr_aggregation_contract_is_preserved(self, clients):
        result = plan(clients=clients, persistent='203.0.112.0/23 198.51.100.1 100',
                      runtime=json.dumps(BASE+[dict(NEW,dst='203.0.112.0/23')]))
        assert result['profile_additions'] == result['runtime_additions'] == []

    def test_destination_comparison_does_not_use_substrings(self):
        result = plan(clients=['3.0.113.0/24'], persistent='103.0.113.0/24 198.51.100.1 100')
        assert result['profile_additions'] == ['3.0.113.0/24 198.51.100.1 100']

    def test_gateway_prefix_similarity_does_not_hide_a_conflicting_route(self):
        with pytest.raises(ValueError):
            plan(persistent='203.0.113.0/24 198.51.100.11 100')

    @pytest.mark.parametrize('secondary', [False, True])
    def test_front_discovery_uses_both_primary_and_secondary_addresses(self, secondary):
        addresses = json.loads(ADDRESSES)
        if secondary:
            addresses[0]['addr_info'].insert(0, {'family':'inet','local':'198.51.100.10','prefixlen':24})
            addresses[0]['addr_info'][1]['secondary'] = True
        tasks = yaml.safe_load((TaskExpressions.TASKS/'front_return_routes.yml').read_text())[2:6]
        result = TaskExpressions({'bind_front_addresses':{'stdout':json.dumps(addresses)}, 'bind_front_ip':'198.51.100.2',
                                  'bind_back_ip':'10.0.0.2','ansible_host':'192.0.2.2'}).run('', tasks=tasks)
        assert result['bind_front_interface']['device'] == 'front0'

    @pytest.mark.parametrize('change', ['down','missing-front','duplicate-front','missing-back','shared-back'])
    def test_unsafe_front_discovery_is_rejected(self, change):
        addresses = json.loads(ADDRESSES)
        if change == 'down': addresses[0]['operstate'] = 'DOWN'
        elif change == 'missing-front': addresses[0]['addr_info'] = []
        elif change == 'duplicate-front': addresses.append(copy.deepcopy(addresses[0]))
        elif change == 'missing-back': addresses[1]['addr_info'] = []
        else: addresses[0]['addr_info'].extend(addresses[1]['addr_info'])
        tasks = yaml.safe_load((TaskExpressions.TASKS/'front_return_routes.yml').read_text())[2:6]
        with pytest.raises(ValueError):
            TaskExpressions({'bind_front_addresses':{'stdout':json.dumps(addresses)}, 'bind_front_ip':'198.51.100.2',
                             'bind_back_ip':'10.0.0.2','ansible_host':'192.0.2.2'}).run('', tasks=tasks)

    @pytest.mark.parametrize('typed', [False, True])
    def test_preservation_accepts_json_or_ansible_converted_lists(self, typed):
        routes = BASE + [NEW]
        TaskExpressions({'bind_front_plan':plan(), 'bind_front_compare_profile':'203.0.113.0/24 198.51.100.1 100',
                         'bind_front_compare_runtime':routes if typed else json.dumps(routes),
                         'bind_front_compare_applied':True}).run('front_routes_compare.yml')

    @pytest.mark.parametrize('routes', [[NEW], BASE, BASE+[dict(NEW,dst='8.8.8.8/32')]])
    def test_route_loss_or_unrelated_changes_are_explicit_failures(self, routes):
        with pytest.raises(ValueError):
            TaskExpressions({'bind_front_plan':plan(), 'bind_front_compare_profile':'203.0.113.0/24 198.51.100.1 100',
                             'bind_front_compare_runtime':routes,'bind_front_compare_applied':True}).run('front_routes_compare.yml')

    @pytest.mark.parametrize('metric', [100, 200])
    def test_owned_active_route_identity_is_checked_before_removal(self, metric):
        all_tasks = yaml.safe_load((TaskExpressions.TASKS/'front_routes_rollback.yml').read_text())
        start = next(index for index,task in enumerate(all_tasks) if task['name']=='Normalize active destinations being inspected')
        runner = TaskExpressions({'bind_front_plan':plan(), 'bind_front_rollback_kernel':{'stdout':json.dumps(BASE+[dict(NEW,metric=metric)])}})
        if metric == 200:
            with pytest.raises(ValueError): runner.run('', tasks=all_tasks[start:start+3])
        else:
            result = runner.run('', tasks=all_tasks[start:start+3])['bind_front_runtime_selection']
            result = json.loads(result) if isinstance(result,str) else result
            assert result['routes'] == ['203.0.113.0/24 198.51.100.1 100']

    def test_no_custom_routing_python_or_external_filter_dependency(self):
        assert not (ROOT/'roles/bind9/filter_plugins/front_routes.py').exists()
        for path in (ROOT/'roles/bind9/tasks/tasks.d').glob('front*.yml'):
            text = path.read_text()
            assert '| ipv4' not in text and '| ipaddr' not in text and 'front_route_plan(' not in text
            commands = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
            assert 'connection, up' not in commands and 'reapply' not in commands

    def test_internal_destinations_never_enter_the_front_route_plan(self):
        # An internal subnet may be directly attached to BACK: it must not even
        # be passed to FRONT's protected-range validation or route additions.
        result = plan(internal=['10.0.0.0/24', '10.30.1.20'])
        assert result['profile_additions'] == ['203.0.113.0/24 198.51.100.1 100']
        assert result['runtime_additions'] == ['203.0.113.0/24 198.51.100.1 100']
        internal_only = plan(clients=[], internal=['10.0.0.0/24'])
        assert internal_only['profile_additions'] == internal_only['runtime_additions'] == []

    def test_single_host_idempotence_and_connected_client(self):
        assert plan(clients=['203.0.113.25'])['profile_additions'] == ['203.0.113.25/32 198.51.100.1 100']
        assert plan(persistent='203.0.113.0/24 198.51.100.1 100',runtime=json.dumps(BASE+[NEW]))['runtime_additions'] == []
        assert plan(clients=['198.51.100.25'])['profile_additions'] == []

    @pytest.mark.parametrize('client',['0.0.0.0/0','127.0.0.0/8','169.254.0.0/16','224.0.0.0/4','240.0.0.0/4',
        '10.0.0.0/24','192.0.2.0/24','198.51.0.0/16','2001:db8::/64','203.0.113.1/24','300.0.0.0/24',
        '203.0.113.0/33','0203.0.113.0/24','203.0.113.0/24; unsafe'])
    def test_unsafe_clients_fail_before_mutation(self,client):
        with pytest.raises(ValueError): plan(clients=[client])

    @pytest.mark.parametrize('gateway',['','garbage','198.51.100.2','192.0.2.1','198.51.100.0','198.51.100.255'])
    def test_gateway_must_be_existing_and_usable(self,gateway):
        with pytest.raises(ValueError): plan(gateway=gateway)

    @pytest.mark.parametrize('value',['','garbage','{}','null','[1]'])
    def test_bad_native_json_is_not_empty_success(self,value):
        with pytest.raises(ValueError): plan(runtime=value)

    @pytest.mark.parametrize('metric',[True,'100',0,-1,4294967296])
    def test_invalid_metric_is_refused(self,metric):
        with pytest.raises(ValueError): plan(metric=metric)

    @pytest.mark.parametrize('text',['unexpected output',',','{ broken }','{ ip = 203.0.113.0/24 }',
        '{ ip = 203.0.113.0/24, nh = 198.51.100.1, nh = 198.51.100.1 }',
        '203.0.113.0/24 198.51.100.1 100 garbage'])
    def test_malformed_profile_is_refused(self,text):
        with pytest.raises(ValueError): plan(persistent=text)

    @pytest.mark.parametrize('ssh',['203.0.113.8','::ffff:203.0.113.8','::ffff:cb00:7108','invalid-controller'])
    def test_real_or_mapped_ssh_peer_is_protected(self,ssh):
        with pytest.raises(ValueError): plan(ssh=ssh)

    def test_pure_ipv6_ssh_is_unaffected_by_ipv4_routes(self):
        assert plan(ssh='2001:db8::1')['profile_additions']

    def test_native_terse_brace_and_existing_metric(self):
        for text in ['203.0.113.0/24 198.51.100.1 230','{ ip = 203.0.113.0/24, nh = 198.51.100.1, mt = 230 }']:
            value=plan(persistent=text)
            assert value['profile_additions']==[]
            assert value['runtime_additions']==['203.0.113.0/24 198.51.100.1 230']

    @pytest.mark.parametrize('row',[dict(NEW,gateway='10.0.0.1',dev='back0'),dict(NEW,dst='203.0.113.25/32',dev='back0'),
        dict(NEW,nexthops=[{'dev':'front0'}]),dict(NEW,type='blackhole'),NEW])
    def test_conflicting_or_unowned_runtime_routes_refused(self,row):
        with pytest.raises(ValueError): plan(runtime=json.dumps(BASE+[row]))

    @pytest.mark.parametrize('field,value',[('device','invalid device'),('profile_additions','not-a-list'),
        ('runtime_additions',[42]),('runtime_additions',['203.0.113.1/24 198.51.100.1 100']),
        ('runtime_additions',['0.0.0.0/0 198.51.100.1 100']),('runtime_additions',['203.0.113.0/24 198.51.100.1 0']),
        ('runtime_additions',['192.0.2.99/32 198.51.100.1 100'])])
    def test_corrupted_recovery_cannot_remove_a_route(self,field,value):
        corrupted=copy.deepcopy(plan()); corrupted[field]=value
        with pytest.raises(ValueError): TaskExpressions({'bind_front_plan':corrupted}).run('front_routes_validate_journal.yml')

    def test_policy_routes_and_duplicate_entries_refused(self):
        with pytest.raises(ValueError): plan(route_table='100')
        raw='203.0.113.0/24 198.51.100.1 100'
        with pytest.raises(ValueError): plan(persistent=raw+', '+raw)

    def test_existing_journal_cannot_claim_preexisting_routes(self):
        corrupted=plan(); corrupted['runtime_before'].append(NEW)
        with pytest.raises(ValueError): TaskExpressions({'bind_front_plan':corrupted}).run('front_routes_validate_journal.yml')

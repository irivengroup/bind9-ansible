"""Safety contracts executed from native route YAML, without custom filters."""
import copy
import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from route_task_expressions import TaskExpressions

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.geteuid() != 0, reason='Requires isolated Linux root')
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


class NativeRouteLab:
    """Run the exact Bash engine with isolated ip/nmcli boundaries."""

    def __init__(self, folder):
        self.folder = folder
        self.state = folder / 'route-state'
        self.data_file = folder / 'network.json'
        self.data = dict(uuid=UUID, gateway='198.51.100.1', addresses=json.loads(ADDRESSES),
                         rules=json.loads(RULES), runtime=copy.deepcopy(BASE), persistent=[], operations=[])
        binaries = folder / 'bin'
        binaries.mkdir()
        (binaries / 'python3').symlink_to(sys.executable)
        for name in ('ip', 'nmcli'):
            target = binaries / name
            shutil.copyfile(str(ROOT / 'tests/fixtures/native_network_cli.py'), str(target))
            target.chmod(0o755)
        self.env = dict(os.environ, BIND_NETWORK_DOUBLE_STATE=str(self.data_file))
        self.script = folder / 'routes.sh'
        source = (ROOT / 'roles/bind9/files/bind-front-routes.sh').read_text()
        source = source.replace('STATE=/var/lib/Bind9-Ansible', 'STATE=' + str(self.state))
        source = source.replace('export PATH=/usr/sbin:/usr/bin:/sbin:/bin',
                                'export PATH=' + str(binaries) + ':/usr/sbin:/usr/bin:/sbin:/bin')
        self.script.write_text(source)

    def run(self, action='apply', clients=None, more=None):
        self.data_file.write_text(json.dumps(self.data))
        args = ['/bin/bash', str(self.script), '--action', action, '--front-ip', '198.51.100.2',
                '--back-ip', '10.0.0.2', '--adm-ip', '192.0.2.2', '--peer-back-ip', '10.0.0.3']
        for client in ['203.0.113.0/24'] if clients is None else clients:
            args.extend(['--client', client])
        args.extend(more or [])
        result = subprocess.run(args, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                universal_newlines=True, timeout=60)
        self.data = json.loads(self.data_file.read_text())
        return result

    def rewrite_journal(self, name, content):
        """Tamper with a current journal while keeping its checksum consistent.

        This exercises semantic ownership guards instead of stopping at the
        checksum check, which has its own corruption regression test.
        """
        journal = self.state / 'front-routes.pending'
        (journal / name).write_text(content)
        files = ('meta', 'operations', 'profile', 'kernel', 'addresses', 'rules', 'clients')
        checksums = ''.join(hashlib.sha256((journal / filename).read_bytes()).hexdigest() +
                            '  ' + filename + '\n' for filename in files)
        (journal / 'checksums').write_text(checksums)


def plan(**kwargs):
    """Return the current engine's observed plan without emulating a journal."""
    values = dict(clients=['203.0.113.0/24'], internal=[], gateway='198.51.100.1', persistent='',
                  runtime=json.dumps(BASE), rules=RULES, route_table='0', profile=UUID, metric=100, ssh='192.0.2.99')
    values.update(kwargs)
    envelope = TaskExpressions(dict(bind_front_action='plan', ansible_check_mode=True,
                                    bind_front_route_metric=values['metric']))
    tasks = TaskExpressions.load('deploy/front_return_routes.yml')
    envelope.run('', tasks=[next(task for task in tasks if task.get('name') == 'Validate the native FRONT action envelope')])
    with tempfile.TemporaryDirectory() as temporary:
        lab = NativeRouteLab(Path(temporary))
        lab.data.update(gateway=values['gateway'], runtime=json.loads(values['runtime']),
                        rules=json.loads(values['rules']), route_table=values['route_table'], uuid=values['profile'],
                        profile_text=values['persistent'])
        if 'addresses' in values:
            lab.data['addresses'] = values['addresses']
        result = lab.run('plan', values['clients'], ['--metric', str(values['metric']), '--ssh-peer', values['ssh']])
        if result.returncode not in (0, 2):
            raise ValueError(result.stdout)
        match = re.search(r'FRONT device=(\S+) profile=(\S+) gateway=(\S+) additions=', result.stdout)
        additions = {'P': [], 'R': []}
        for store, net, gateway, metric in re.findall(r'(?m)^  ([PR])[|]([^|]+)[|]([^|]+)[|]([0-9]+)$', result.stdout):
            additions[store].append(net + ' ' + gateway + ' ' + metric)
        return dict(device=match.group(1), uuid=match.group(2), gateway=match.group(3),
                    profile_additions=additions['P'], runtime_additions=additions['R'])


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
        assert plan(addresses=addresses)['device'] == 'front0'

    @pytest.mark.parametrize('change', ['down','missing-front','duplicate-front','missing-back','shared-back'])
    def test_unsafe_front_discovery_is_rejected(self, change):
        addresses = json.loads(ADDRESSES)
        if change == 'down': addresses[0]['operstate'] = 'DOWN'
        elif change == 'missing-front': addresses[0]['addr_info'] = []
        elif change == 'duplicate-front': addresses.append(copy.deepcopy(addresses[0]))
        elif change == 'missing-back': addresses[1]['addr_info'] = []
        else: addresses[0]['addr_info'].extend(addresses[1]['addr_info'])
        with pytest.raises(ValueError):
            plan(addresses=addresses)

    def test_current_journal_verification_preserves_exact_route_stores(self, tmp_path):
        lab = NativeRouteLab(tmp_path)
        assert lab.run(more=['--defer-commit']).returncode == 2
        before = copy.deepcopy(lab.data)
        token = (lab.state / 'front-routes.pending/meta').read_text().split('|', 1)[0]
        result = lab.run('verify', more=['--token', token])
        assert result.returncode == 0, result.stdout
        assert lab.data == before

    @pytest.mark.parametrize('routes', [[NEW], BASE, BASE+[dict(NEW,dst='8.8.8.8/32')]])
    def test_route_loss_or_unrelated_changes_are_explicit_failures(self, tmp_path, routes):
        lab = NativeRouteLab(tmp_path)
        assert lab.run(more=['--defer-commit']).returncode == 2
        token = (lab.state / 'front-routes.pending/meta').read_text().split('|', 1)[0]
        lab.data['runtime'] = copy.deepcopy(routes)
        before = copy.deepcopy(lab.data)
        result = lab.run('verify', more=['--token', token])
        assert result.returncode == 1, result.stdout
        assert lab.data == before
        assert (lab.state / 'front-routes.pending').is_dir()

    @pytest.mark.parametrize('metric', [100, 200])
    def test_owned_active_route_identity_is_checked_before_removal(self, tmp_path, metric):
        lab = NativeRouteLab(tmp_path)
        assert lab.run(more=['--defer-commit']).returncode == 2
        lab.data['runtime'][-1]['metric'] = metric
        before = copy.deepcopy(lab.data)
        result = lab.run('recover')
        assert result.returncode == (1 if metric == 200 else 2), result.stdout
        if metric == 200:
            assert lab.data == before
            assert (lab.state / 'front-routes.pending').is_dir()
        else:
            assert lab.data['runtime'] == BASE and not lab.data['persistent']

    def test_no_custom_routing_python_or_external_filter_dependency(self):
        assert not (ROOT/'roles/bind9/filter_plugins/front_routes.py').exists()
        for path in (ROOT/'roles/bind9/tasks/tasks.d/deploy').glob('front*.yml'):
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

    @pytest.mark.parametrize('field,value', [
        ('meta', 'invalid device'), ('operations', 'not-a-route'), ('operations', '42'),
        ('operations', 'R|203.0.113.1/24|198.51.100.1|100'),
        ('operations', 'R|0.0.0.0/0|198.51.100.1|100'),
        ('operations', 'R|203.0.113.0/24|198.51.100.1|0'),
        ('operations', 'R|192.0.2.99/32|198.51.100.1|100')])
    def test_corrupted_recovery_cannot_remove_a_route(self, tmp_path, field, value):
        lab = NativeRouteLab(tmp_path)
        assert lab.run(more=['--defer-commit']).returncode == 2
        journal = lab.state / 'front-routes.pending'
        if field == 'meta':
            fields = (journal / 'meta').read_text().strip().split('|')
            fields[2] = value
            content = '|'.join(fields) + '\n'
        else:
            content = (journal / 'operations').read_text().splitlines()[0] + '\n' + value + '\n'
        lab.rewrite_journal(field, content)
        before = copy.deepcopy(lab.data)
        result = lab.run('recover')
        assert result.returncode == 1, result.stdout
        assert lab.data == before and journal.is_dir()

    def test_policy_routes_and_duplicate_entries_refused(self):
        with pytest.raises(ValueError): plan(route_table='100')
        raw='203.0.113.0/24 198.51.100.1 100'
        with pytest.raises(ValueError): plan(persistent=raw+', '+raw)

    def test_existing_journal_cannot_claim_preexisting_routes(self, tmp_path):
        lab = NativeRouteLab(tmp_path)
        assert lab.run(more=['--defer-commit']).returncode == 2
        journal = lab.state / 'front-routes.pending'
        lab.rewrite_journal('kernel', (journal / 'kernel').read_text() + '203.0.113.0/24|198.51.100.1|100|front0|\n')
        before = copy.deepcopy(lab.data)
        result = lab.run('recover')
        assert result.returncode == 1, result.stdout
        assert lab.data == before and journal.is_dir()


class TestNativeProcessTransactions:
    """Exercise the replacement engine, not only its planned arithmetic."""

    @pytest.mark.parametrize('action', ['legacy-rollback', 'legacy-cleanup'])
    def test_removed_actions_are_refused_before_state_creation(self, tmp_path, action):
        lab = NativeRouteLab(tmp_path)
        before = copy.deepcopy(lab.data)
        result = lab.run(action)
        assert result.returncode == 1, result.stdout
        assert 'Unsupported route action' in result.stdout
        assert lab.data == before and not lab.state.exists()

    def test_removed_adapter_and_test_aliases_are_absent(self):
        assert not (ROOT / 'roles/bind9/tasks/tasks.d/deploy/front_routes_legacy.yml').exists()
        with pytest.raises(FileNotFoundError):
            TaskExpressions.load('deploy/front_routes_validate_journal.yml')

    def test_a_native_cleanup_error_cannot_report_a_successful_change(self, tmp_path):
        lab = NativeRouteLab(tmp_path)
        wrapper = tmp_path / 'bin/rmdir'
        wrapper.write_text('#!/bin/bash\n'
            'if [[ "${@: -1}" == "$BIND_ROUTE_WORK_PREFIX"* ]]; then exit 2; fi\n'
            'exec /usr/bin/rmdir "$@"\n')
        wrapper.chmod(0o755)
        lab.env['BIND_ROUTE_WORK_PREFIX'] = str(lab.state / '.route-work.')
        result = lab.run()
        assert result.returncode == 1, result.stdout
        assert 'temporary work cleanup failed' in result.stdout
        # The transaction already committed: report cleanup failure accurately,
        # preserve its diagnostic work directory, and leave valid routes intact.
        assert lab.data['runtime'] == BASE + [NEW] and len(lab.data['persistent']) == 1
        assert not (lab.state / 'front-routes.pending').exists()
        assert len(list(lab.state.glob('.route-work.*'))) == 1

    @pytest.mark.parametrize('stage', ['observation', 'clients', 'verification'])
    def test_native_sort_failure_never_becomes_a_successful_or_partial_route_apply(self, tmp_path, stage):
        lab = NativeRouteLab(tmp_path)
        # A native utility can return 2, also used by the engine for a successful
        # change. Inject once so the guarded rollback can use the real utility.
        wrapper = tmp_path / 'bin/sort'
        wrapper.write_text('''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys
stage = os.environ['BIND_SORT_FAILURE_STAGE']
state = json.loads(Path(os.environ['BIND_NETWORK_DOUBLE_STATE']).read_text())
marker = Path(os.environ['BIND_SORT_FAILURE_MARKER'])
applied = any(not item['persistent'] and item['action'] == '+ipv4.routes' for item in state['operations'])
fail = stage == 'observation' or (stage == 'clients' and '-t|' in sys.argv) or (stage == 'verification' and applied)
if fail and not marker.exists():
    marker.touch()
    text = sys.stdin.read()
    if stage == 'clients' and text:
        print(text.splitlines()[0])
    print('Injected native sort failure', file=sys.stderr)
    sys.exit(2)
os.execv(os.environ['BIND_REAL_SORT'], [os.environ['BIND_REAL_SORT']] + sys.argv[1:])
''')
        wrapper.chmod(0o755)
        marker = tmp_path / 'sort-failed'
        lab.env.update(BIND_REAL_SORT=shutil.which('sort'), BIND_SORT_FAILURE_STAGE=stage,
                       BIND_SORT_FAILURE_MARKER=str(marker))
        result = lab.run(clients=['203.0.113.25', '203.0.113.27'])
        assert marker.exists(), 'The native failure boundary was not reached'
        assert result.returncode == 1, result.stdout
        assert 'Injected native sort failure' in result.stdout
        assert lab.data['runtime'] == BASE and not lab.data['persistent']
        assert not (lab.state / 'front-routes.pending').exists()
        if stage == 'verification':
            assert 'FRONT rollback completed' in result.stdout
            assert any(item['action'] == '-ipv4.routes' for item in lab.data['operations'])
        else:
            assert not lab.data['operations']

    @pytest.mark.parametrize('device', ['front0', 'other0'])
    def test_a_gateway_owned_by_the_server_is_refused_before_any_write(self, tmp_path, device):
        lab = NativeRouteLab(tmp_path)
        lab.data['addresses'].append({'ifname': device, 'operstate': 'UP', 'addr_info': [
            {'family': 'inet', 'local': '198.51.100.1', 'prefixlen': 24}]})
        result = lab.run()
        assert result.returncode == 1, result.stdout
        assert 'gateway is a local server address' in result.stdout
        assert not lab.data['operations']
        assert lab.data['runtime'] == BASE and not lab.data['persistent']

    @pytest.mark.parametrize('mode', ['0700', '0777'])
    def test_concurrent_state_initialization_is_revalidated_without_chmod(self, tmp_path, mode):
        lab = NativeRouteLab(tmp_path)
        wrapper = tmp_path / 'bin/mkdir'
        wrapper.write_text('#!/bin/bash\n'
            'if [[ "${@: -1}" == "$BIND_STATE_INIT_RACE" && ! -e "$BIND_STATE_INIT_RACE" ]]; then\n'
            '  /usr/bin/mkdir -m ' + mode + ' -- "$BIND_STATE_INIT_RACE" || exit 2\n'
            '  exit 1\nfi\nexec /usr/bin/mkdir "$@"\n')
        wrapper.chmod(0o755)
        lab.env['BIND_STATE_INIT_RACE'] = str(lab.state)
        result = lab.run()
        assert result.returncode == (2 if mode == '0700' else 1), result.stdout
        assert lab.state.stat().st_mode & 0o777 == int(mode, 8)
        if mode == '0777':
            assert not lab.data['operations']

    def test_gateway_from_active_front_device_when_profile_property_is_empty(self, tmp_path):
        lab = NativeRouteLab(tmp_path)
        lab.data.update(gateway='', active_gateway='198.51.100.1')
        result = lab.run()
        assert result.returncode == 2, result.stdout
        assert lab.data['runtime'] == BASE + [NEW]
        assert len(lab.data['persistent']) == 1

    @pytest.mark.parametrize('source', ['profile', 'runtime', 'both'])
    def test_gateway_from_front_specific_routes_without_a_front_default(self, tmp_path, source):
        """A nondefault FRONT next-hop must not be confused with BACK's default."""
        lab = NativeRouteLab(tmp_path)
        lab.data.update(gateway='', active_gateway='')
        existing = dict(NEW, dst='172.16.0.0/24', metric=150)
        if source in ('profile', 'both'):
            lab.data['persistent'].append({'network': existing['dst'], 'gateway': existing['gateway'], 'metric': 150})
        if source in ('runtime', 'both'):
            lab.data['runtime'].append(dict(existing, protocol='static'))
        before = copy.deepcopy(lab.data)
        planned = lab.run('plan')
        assert planned.returncode == 2, planned.stdout
        assert lab.data == before
        result = lab.run()
        assert result.returncode == 2, result.stdout
        assert lab.data['runtime'] == before['runtime'] + [NEW]
        assert lab.data['persistent'] == before['persistent'] + [
            {'network': NEW['dst'], 'gateway': NEW['gateway'], 'metric': NEW['metric']}]
        assert lab.run().returncode == 0
        assert not any(row.get('dst') == 'default' and row.get('dev') == 'front0'
                       for row in lab.data['runtime'])

    @pytest.mark.parametrize('source', ['profile', 'runtime', 'both'])
    def test_conflicting_front_specific_next_hops_are_refused_before_writes(self, tmp_path, source):
        lab = NativeRouteLab(tmp_path)
        lab.data.update(gateway='', active_gateway='')
        for index, gateway in enumerate(('198.51.100.1', '198.51.100.254')):
            route = dict(NEW, dst='172.16.' + str(index) + '.0/24', gateway=gateway)
            if source == 'profile' or (source == 'both' and index == 0):
                lab.data['persistent'].append({'network': route['dst'], 'gateway': gateway, 'metric': 100})
            else:
                lab.data['runtime'].append(route)
        before = copy.deepcopy(lab.data)
        result = lab.run()
        assert result.returncode == 1, result.stdout
        assert 'Ambiguous gateways in FRONT routes' in result.stdout
        assert lab.data == before
        assert not (lab.state / 'front-routes.pending').exists()

    def test_specific_front_gateway_supports_mixed_ip_and_subnet_destinations(self, tmp_path):
        """Keep host routes and subnets distinct without widening either one."""
        lab = NativeRouteLab(tmp_path)
        lab.data.update(gateway='', persistent=[
            {'network': '172.16.0.0/24', 'gateway': NEW['gateway'], 'metric': 150}])
        before = copy.deepcopy(lab.data)
        clients = ['203.0.113.25', '172.20.0.0/16']
        result = lab.run(clients=clients)
        assert result.returncode == 2, result.stdout
        assert lab.data['runtime'][:len(BASE)] == BASE
        assert len(lab.data['runtime']) == len(BASE) + 2
        assert lab.data['persistent'][0] == before['persistent'][0]
        assert {row['network'] for row in lab.data['persistent'][1:]} == {'203.0.113.25/32', '172.20.0.0/16'}
        assert {row['dst'] for row in lab.data['runtime'][len(BASE):]} == {'203.0.113.25/32', '172.20.0.0/16'}
        assert all(row['gateway'] == NEW['gateway'] for row in lab.data['persistent'])
        repeated = lab.run(clients=clients)
        assert repeated.returncode == 0, repeated.stdout

    @pytest.mark.parametrize('gateway', ['198.51.100.2', '10.0.0.1'])
    def test_discovered_static_next_hop_still_requires_a_usable_front_address(self, tmp_path, gateway):
        lab = NativeRouteLab(tmp_path)
        lab.data.update(gateway='', persistent=[
            {'network': '172.16.0.0/24', 'gateway': gateway, 'metric': 100}])
        before = copy.deepcopy(lab.data)
        result = lab.run()
        assert result.returncode == 1, result.stdout
        assert 'gateway is a local server address' in result.stdout or 'not a usable on-link address' in result.stdout
        assert lab.data == before

    def test_other_interfaces_next_hops_do_not_make_the_front_gateway_ambiguous(self, tmp_path):
        lab = NativeRouteLab(tmp_path)
        lab.data.update(gateway='', persistent=[
            {'network': '172.16.0.0/24', 'gateway': NEW['gateway'], 'metric': 150}])
        lab.data['runtime'].append(dict(NEW, dst='172.17.0.0/24', dev='other0', gateway='192.0.2.1'))
        before = copy.deepcopy(lab.data)
        result = lab.run()
        assert result.returncode == 2, result.stdout
        assert lab.data['runtime'] == before['runtime'] + [NEW]

    def test_a_connected_front_route_does_not_turn_the_back_default_into_a_front_gateway(self, tmp_path):
        lab = NativeRouteLab(tmp_path)
        lab.data['gateway'] = ''
        before = copy.deepcopy(lab.data)
        result = lab.run()
        assert result.returncode == 1, result.stdout
        assert 'No IPv4 gateway' in result.stdout and 'device=front0' in result.stdout
        assert lab.data == before

    def test_direct_front_clients_do_not_require_an_unambiguous_remote_gateway(self, tmp_path):
        lab = NativeRouteLab(tmp_path)
        lab.data.update(gateway='', persistent=[
            {'network': '172.16.0.0/24', 'gateway': '198.51.100.1', 'metric': 100},
            {'network': '172.17.0.0/24', 'gateway': '198.51.100.254', 'metric': 100}])
        before = copy.deepcopy(lab.data)
        result = lab.run(clients=['198.51.100.25'])
        assert result.returncode == 0, result.stdout
        assert lab.data == before

    @pytest.mark.parametrize('source', ['profile', 'runtime'])
    def test_attributed_routes_cannot_supply_an_implicit_front_next_hop(self, tmp_path, source):
        lab = NativeRouteLab(tmp_path)
        lab.data['gateway'] = ''
        if source == 'profile':
            lab.data['profile_text'] = '{ ip = 172.16.0.0/24, nh = 198.51.100.1, mt = 100, table = 123 }'
        else:
            lab.data['runtime'].append(dict(NEW, dst='172.16.0.0/24', onlink=True))
        before = copy.deepcopy(lab.data)
        result = lab.run()
        assert result.returncode == 1, result.stdout
        assert 'route attributes for gateway discovery' in result.stdout
        assert lab.data == before

    @pytest.mark.parametrize('action', ['verify', 'commit', 'recover'])
    def test_static_next_hop_is_preserved_during_deferred_completion(self, tmp_path, action):
        lab = NativeRouteLab(tmp_path)
        lab.data.update(gateway='', persistent=[
            {'network': '172.16.0.0/24', 'gateway': NEW['gateway'], 'metric': 150}])
        before = copy.deepcopy(lab.data)
        applied = lab.run(more=['--defer-commit'])
        assert applied.returncode == 2, applied.stdout
        token = (lab.state / 'front-routes.pending/meta').read_text().split('|', 1)[0]
        completed = lab.run(action, more=[] if action == 'recover' else ['--token', token])
        assert completed.returncode == (2 if action == 'recover' else 0), completed.stdout
        if action == 'recover':
            assert lab.data['persistent'] == before['persistent']
            assert lab.data['runtime'] == before['runtime']
        assert (lab.state / 'front-routes.pending').exists() == (action == 'verify')

    @pytest.mark.parametrize('system_gateway', ['10.0.0.1', '10.0.0.254'])
    @pytest.mark.parametrize('profile_gateway', ['', '198.51.100.1'])
    def test_front_gateway_is_independent_of_system_default(self, tmp_path, system_gateway, profile_gateway):
        lab = NativeRouteLab(tmp_path)
        lab.data['runtime'][0]['gateway'] = system_gateway
        lab.data.update(gateway=profile_gateway, active_gateway='198.51.100.1')
        assert not any(row.get('dst') == 'default' and row.get('dev') == 'front0' for row in lab.data['runtime'])
        result = lab.run()
        assert result.returncode == 2, result.stdout
        assert lab.data['runtime'][0]['gateway'] == system_gateway
        assert lab.data['runtime'][-1]['gateway'] == '198.51.100.1'
        assert all('198.51.100.1' in item['route'] for item in lab.data['operations'])

    @pytest.mark.parametrize('active', [False, True])
    def test_automatic_profile_metric_uses_observed_front_metric(self, tmp_path, active):
        lab = NativeRouteLab(tmp_path)
        lab.data['route_metric'] = '-1'
        lab.data['runtime'][1]['metric'] = 500
        lab.data['persistent'] = [{'network': '203.0.113.0/24', 'gateway': '198.51.100.1', 'metric': -1}]
        if active:
            lab.data['runtime'].append(dict(NEW, metric=500))
        result = lab.run()
        assert result.returncode == (0 if active else 2), result.stdout
        assert lab.data['runtime'][-1] == dict(NEW, metric=500)
        assert lab.data['persistent'][0]['metric'] == -1

    def test_gateway_from_existing_default_route_of_that_profile(self, tmp_path):
        lab = NativeRouteLab(tmp_path)
        lab.data.update(gateway='', persistent=[{'network': '0.0.0.0/0', 'gateway': '198.51.100.1', 'metric': 600}])
        lab.data['runtime'].append(dict(NEW, dst='default', metric=600))
        result = lab.run()
        assert result.returncode == 2, result.stdout
        assert len(lab.data['operations']) == 2
        assert len(lab.data['persistent']) == 2
        assert lab.data['runtime'] == BASE + [dict(NEW, dst='default', metric=600), NEW]

    @pytest.mark.parametrize('field,value', [('runtime_text', ''), ('runtime_text', 'partial route'),
                                           ('rules_text', ''), ('rules_text', '0: from all lookup local'),
                                           ('addresses_text', ''), ('profile_text', '{ broken }'),
                                           ('active_gateway', '198.51.100.254')])
    def test_invalid_or_conflicting_observation_never_becomes_an_empty_plan(self, tmp_path, field, value):
        lab = NativeRouteLab(tmp_path)
        lab.data[field] = value
        result = lab.run()
        assert result.returncode == 1, result.stdout
        assert not lab.data['operations'] and lab.data['runtime'] == BASE
        assert not (lab.state / 'front-routes.pending').exists()

    @pytest.mark.parametrize('failure', ['fail_active_once', 'bad_lookup'])
    def test_native_failure_rolls_back_only_its_additions(self, tmp_path, failure):
        lab = NativeRouteLab(tmp_path)
        lab.data[failure] = True
        result = lab.run()
        assert result.returncode == 1, result.stdout
        assert lab.data['persistent'] == [] and lab.data['runtime'] == BASE
        assert not (lab.state / 'front-routes.pending').exists()

    @pytest.mark.parametrize('action', ['commit', 'rollback', 'recover'])
    def test_deferred_transaction_has_explicit_owned_completion(self, tmp_path, action):
        lab = NativeRouteLab(tmp_path)
        result = lab.run(more=['--defer-commit'])
        assert result.returncode == 2, result.stdout
        token = re.search('transaction: ([0-9a-f-]{36})', result.stdout).group(1)
        assert (lab.state / 'front-routes.pending').is_dir()
        again = lab.run(action, more=[] if action == 'recover' else ['--token', token])
        assert again.returncode == (0 if action == 'commit' else 2), again.stdout
        assert lab.data['runtime'] == (BASE + [NEW] if action == 'commit' else BASE)
        assert not (lab.state / 'front-routes.pending').exists()

    def test_corrupt_or_foreign_journal_is_preserved_without_route_deletion(self, tmp_path):
        lab = NativeRouteLab(tmp_path)
        result = lab.run(more=['--defer-commit'])
        assert result.returncode == 2, result.stdout
        journal = lab.state / 'front-routes.pending'
        (journal / 'operations').write_text('R|0.0.0.0/0|198.51.100.1|100\n')
        before = copy.deepcopy(lab.data)
        result = lab.run('recover')
        assert result.returncode == 1 and 'checksum' in result.stdout
        assert lab.data == before and journal.exists()

    def test_unexpected_journal_entry_is_never_recursively_removed(self, tmp_path):
        lab = NativeRouteLab(tmp_path)
        result = lab.run(more=['--defer-commit'])
        assert result.returncode == 2, result.stdout
        evidence = lab.state / 'front-routes.pending/operator-evidence'
        evidence.write_bytes(b'preserve')
        before = copy.deepcopy(lab.data)
        result = lab.run('recover')
        assert result.returncode == 1 and lab.data == before
        assert evidence.read_bytes() == b'preserve'

    @pytest.mark.parametrize('changes', [{'metric': 200}, {'dev': 'back0'}, {'prefsrc': '198.51.100.9'}, {'protocol': 'static'}])
    def test_operator_route_change_prevents_any_rollback_deletion(self, tmp_path, changes):
        lab = NativeRouteLab(tmp_path)
        result = lab.run(more=['--defer-commit'])
        assert result.returncode == 2, result.stdout
        lab.data['runtime'][-1].update(changes)
        before = copy.deepcopy(lab.data)
        result = lab.run('recover')
        assert result.returncode == 1, result.stdout
        assert lab.data == before and (lab.state / 'front-routes.pending').is_dir()

    @pytest.mark.parametrize('peer', ['203.0.113.8', '::ffff:203.0.113.8', '::ffff:cb00:7108'])
    def test_recovery_preserves_the_current_ssh_return_path(self, tmp_path, peer):
        lab = NativeRouteLab(tmp_path)
        result = lab.run(more=['--defer-commit'])
        assert result.returncode == 2, result.stdout
        before = copy.deepcopy(lab.data)
        result = lab.run('recover', more=['--ssh-peer', peer])
        assert result.returncode == 1 and 'current SSH peer' in result.stdout
        assert lab.data == before and (lab.state / 'front-routes.pending').exists()

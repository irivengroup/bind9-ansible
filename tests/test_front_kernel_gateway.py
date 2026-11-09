"""FRONT's own main-table next hop is authoritative, independent of defaults."""
import copy
import os
from pathlib import Path

import pytest

from test_native_front_routes import NativeRouteLab, BASE, NEW, ROOT


FRONT_REFERENCE = {'dst': '172.31.255.254/32', 'gateway': '198.51.100.1',
                   'dev': 'front0', 'metric': 150, 'protocol': 'dhcp'}


def lab_revision(tmp_path):
    lab = NativeRouteLab(tmp_path)
    baseline = os.environ.get('BIND9_FRONT_BASELINE_SOURCE')
    if baseline:
        # The same two literal resource substitutions as NativeRouteLab. No
        # branch, command, observation or expected result is changed.
        source = Path(baseline).read_text(encoding='utf-8')
        source = source.replace('STATE=/var/lib/Bind9-Ansible', 'STATE=' + str(lab.state))
        source = source.replace('export PATH=/usr/sbin:/usr/bin:/sbin:/bin',
                                'export PATH=' + str(tmp_path / 'bin') + ':/usr/sbin:/usr/bin:/sbin:/bin')
        lab.script.write_text(source, encoding='utf-8')
    return lab


@pytest.mark.parametrize('configured,active', [('', ''), ('198.51.100.254', '198.51.100.254'),
                                             ('192.0.2.1', '198.51.100.254')])
def test_kernel_front_gateway_wins_over_empty_stale_or_conflicting_profile_properties(tmp_path, configured, active):
    lab = lab_revision(tmp_path)
    lab.data.update(gateway=configured, active_gateway=active)
    lab.data['runtime'].append(copy.deepcopy(FRONT_REFERENCE))
    before = copy.deepcopy(lab.data['runtime'])
    result = lab.run()
    assert result.returncode == 2, result.stdout
    assert lab.data['runtime'] == before + [NEW]
    assert lab.data['persistent'] == [{'network': NEW['dst'], 'gateway': NEW['gateway'], 'metric': NEW['metric']}]
    assert not (lab.state / 'front-routes.pending').exists()


@pytest.mark.parametrize('scenario', ['profile_only', 'other_interface_only', 'ambiguous_front'])
def test_missing_or_ambiguous_kernel_gateway_refuses_all_writes(tmp_path, scenario):
    lab = lab_revision(tmp_path)
    lab.data['runtime'] = copy.deepcopy(BASE[:2])
    if scenario == 'other_interface_only':
        lab.data['runtime'].append(dict(FRONT_REFERENCE, dev='other0'))
    elif scenario == 'ambiguous_front':
        lab.data['runtime'].extend([dict(FRONT_REFERENCE, protocol='static'),
                                  dict(FRONT_REFERENCE, dst='172.31.255.253/32', gateway='198.51.100.254')])
    before = copy.deepcopy(lab.data)
    result = lab.run()
    assert result.returncode == 1, result.stdout
    assert lab.data == before
    assert not (lab.state / 'front-routes.pending').exists()


def test_no_profile_gateway_query_remains_in_the_canonical_detector():
    source = (ROOT / 'roles/bind9/files/bind-front-routes.sh').read_text(encoding='utf-8')
    assert 'nmcli -g ipv4.gateway' not in source
    assert 'nmcli -g IP4.GATEWAY' not in source
    assert 'ip -o -4 route show table main' in source


@pytest.mark.parametrize('kind', ['unreachable', 'prohibit', 'throw', 'local', 'broadcast', 'multicast', 'nat'])
def test_unrelated_nonunicast_routes_cannot_supply_a_front_next_hop(tmp_path, kind):
    """Preserve non-unicast records without using them to infer a gateway."""
    lab = lab_revision(tmp_path)
    lab.data['runtime'].append(dict(dst='172.20.0.0/16', type=kind, dev='other0'))
    before = copy.deepcopy(lab.data['runtime'])
    result = lab.run()
    assert result.returncode == 2, result.stdout
    assert lab.data['runtime'] == before + [NEW]
    assert lab.data['persistent'] == [dict(network=NEW['dst'], gateway=NEW['gateway'], metric=NEW['metric'])]
    again = copy.deepcopy(lab.data)
    assert lab.run().returncode == 0
    assert lab.data == again


@pytest.mark.parametrize('gateway', ['198.51.100.3', '198.51.100.2', '198.51.100.1', '198.51.100.4'])
def test_point_to_point_front_accepts_only_the_other_31_address(tmp_path, gateway):
    """RFC 3021 endpoints are usable; the server's own endpoint remains forbidden."""
    lab = lab_revision(tmp_path)
    lab.data['addresses'][0]['addr_info'][0]['prefixlen'] = 31
    lab.data['runtime'][1]['dst'] = '198.51.100.2/31'
    lab.data['runtime'][2]['gateway'] = gateway
    before = copy.deepcopy(lab.data)
    result = lab.run()
    if gateway.endswith('.2'):
        assert result.returncode == 1 and 'FRONT gateway is a local server address' in result.stdout
        assert lab.data == before
    elif gateway in ('198.51.100.1', '198.51.100.4'):
        assert result.returncode == 1 and 'FRONT gateway is not a usable on-link address' in result.stdout
        assert lab.data == before
    else:
        assert result.returncode == 2, result.stdout
        assert lab.data['runtime'] == before['runtime'] + [dict(NEW, gateway=gateway)]
        assert lab.data['persistent'] == [dict(network=NEW['dst'], gateway=gateway, metric=100)]
        again = copy.deepcopy(lab.data)
        assert lab.run().returncode == 0
        assert lab.data == again
    assert not (lab.state / 'front-routes.pending').exists()


@pytest.mark.parametrize('prefix,metric', [('198.0.0.0/8', 1), ('198.51.100.0/24', 200)])
def test_numeric_lpm_and_metric_select_front_from_unordered_matching_routes(tmp_path, prefix, metric):
    lab = lab_revision(tmp_path)
    lab.data['runtime'][1]['metric'] = 100
    lab.data['runtime'].insert(0, {'dst': prefix, 'dev': 'back0', 'gateway': '10.0.0.1', 'metric': metric})
    # A lexical sort selects 198.0.0.0/8 before the actual /24. A /24 tie
    # instead requires the lower metric; table order is deliberately reversed.
    result = lab.run()
    assert result.returncode == 2, result.stdout
    assert lab.data['runtime'][-1] == NEW


def test_a_later_higher_cost_matching_route_preserves_the_selected_front_path(tmp_path):
    """A competing route after FRONT cannot replace the cheaper native path."""
    lab = lab_revision(tmp_path)
    lab.data['runtime'][1]['metric'] = 100
    lab.data['runtime'].append(dict(dst='198.51.100.0/24', dev='back0', gateway='10.0.0.1', metric=200))
    before = copy.deepcopy(lab.data['runtime'])
    result = lab.run()
    assert result.returncode == 2, result.stdout
    assert lab.data['runtime'] == before + [NEW]
    assert lab.data['persistent'] == [dict(network=NEW['dst'], gateway=NEW['gateway'], metric=100)]
    again = copy.deepcopy(lab.data)
    assert lab.run().returncode == 0
    assert lab.data == again


def test_a_typed_kernel_route_without_a_destination_refuses_before_any_write(tmp_path):
    lab = lab_revision(tmp_path)
    lab.data['runtime_text'] = 'blackhole'
    before = copy.deepcopy(lab.data)
    result = lab.run()
    assert result.returncode == 1 and 'Invalid IPv4' in result.stdout, result.stdout
    assert lab.data == before and not (lab.state / 'front-routes.pending').exists()


@pytest.mark.parametrize('scenario', ['wrong_more_specific', 'equal_cost_other_device',
                                    'missing_match', 'contradictory_match', 'equal_cost_opaque', 'equal_cost_via',
                                    'equal_cost_opaque_first', 'equal_cost_via_first'])
def test_lpm_refuses_wrong_ambiguous_or_inconsistent_front_selection(tmp_path, scenario):
    lab = lab_revision(tmp_path)
    if scenario == 'wrong_more_specific':
        lab.data['runtime'].append({'dst': '198.51.100.2/32', 'dev': 'back0', 'gateway': '10.0.0.1'})
    elif scenario == 'equal_cost_other_device':
        lab.data['runtime'].append({'dst': '198.51.100.0/24', 'dev': 'back0'})
    elif scenario == 'missing_match':
        lab.data['matched_text'] = ''
    elif scenario.startswith(('equal_cost_opaque', 'equal_cost_via')):
        route = copy.deepcopy(BASE[1])
        route.update({'mtu': 1400} if scenario.startswith('equal_cost_opaque') else {'gateway': '198.51.100.1'})
        # Equal-cost ambiguity must be refused whichever candidate is seen first.
        if scenario.endswith('_first'):
            lab.data['runtime'].insert(0, route)
        else:
            lab.data['runtime'].append(route)
    else:
        lab.data['matched_text'] = '198.51.100.2 dev front0'
    before = copy.deepcopy(lab.data)
    result = lab.run()
    assert result.returncode == 1, result.stdout
    assert lab.data == before and not (lab.state / 'front-routes.pending').exists()


def test_duplicate_bare_ip_and_host_subnet_are_added_only_once(tmp_path):
    lab = lab_revision(tmp_path)
    clients = ['203.0.113.25', '203.0.113.25/32', '203.0.113.25', '172.20.0.0/16', '172.20.0.0/16']
    result = lab.run(clients=clients)
    assert result.returncode == 2, result.stdout
    assert {row['network'] for row in lab.data['persistent']} == {'203.0.113.25/32', '172.20.0.0/16'}
    assert len(lab.data['persistent']) == 2
    before = copy.deepcopy(lab.data)
    again = lab.run(clients=clients)
    assert again.returncode == 0, again.stdout
    assert lab.data == before, 'A repeated invocation must not publish duplicate routes'


@pytest.mark.parametrize('reverse', [False, True])
def test_connected_front_aliases_with_equal_metric_select_the_same_device(tmp_path, reverse):
    lab = lab_revision(tmp_path)
    connected = copy.deepcopy(lab.data['runtime'][1])
    lab.data['runtime'][1].update(protocol='kernel', scope='link', prefsrc='198.51.100.2')
    connected.update(protocol='kernel', scope='link', prefsrc='198.51.100.10')
    lab.data['runtime'].append(connected)
    lab.data['addresses'][0]['addr_info'].append(dict(family='inet', local='198.51.100.10', prefixlen=24))
    if reverse:
        lab.data['runtime'].reverse()
    before = copy.deepcopy(lab.data['runtime'])
    result = lab.run()
    assert result.returncode == 2, result.stdout
    assert lab.data['runtime'] == before + [NEW]
    assert lab.run().returncode == 0
    assert len(lab.data['persistent']) == 1


def remove_previous_reference_gateway(lab, journal):
    """Reconstruct a predecessor's baseline without a main-table FRONT via."""
    reference = BASE[2]
    lab.data['runtime'].remove(reference)
    record = '{}|{}|{}|{}|proto=static;\n'.format(reference['dst'], reference['gateway'],
                                                reference['metric'], reference['dev'])
    text = (journal / 'kernel').read_text(encoding='utf-8')
    assert text.count(record) == 1
    lab.rewrite_journal('kernel', text.replace(record, ''))


def previous_persistent_only_transaction(tmp_path, progress=1, phase='done', complete=False):
    """Model an acknowledged persistent add before its runtime add completed.

    Start with the genuine eight-field transaction, then retain exactly the
    stores/progress that the predecessor could leave without a baseline via.
    This fixture does not claim to execute an unavailable historical binary.
    """
    lab = lab_revision(tmp_path)
    assert lab.run(more=['--defer-commit']).returncode == 2
    journal = lab.state / 'front-routes.pending'
    remove_previous_reference_gateway(lab, journal)
    lab.data['runtime'].remove(NEW)
    if progress == 0:
        lab.data['persistent'] = []
    lab.data['operations'] = []
    if complete:
        persistent = [line for line in (journal / 'operations').read_text().splitlines() if line.startswith('P|')]
        assert len(persistent) == 1
        lab.rewrite_journal('operations', persistent[0] + '\n')
    lab.rewrite_journal('progress', '{} {}\n'.format(progress, phase))
    return lab, journal


@pytest.mark.parametrize('progress', [0, 1])
@pytest.mark.parametrize('phase', ['done', 'intent'])
def test_previous_persistent_only_journal_can_recover_without_a_kernel_gateway(tmp_path, progress, phase):
    lab, journal = previous_persistent_only_transaction(tmp_path, progress, phase)
    recovered = lab.run('recover')
    assert recovered.returncode == 2, recovered.stdout
    assert lab.data['runtime'] == BASE[:2] and lab.data['persistent'] == []
    assert not journal.exists()
    operations = lab.data['operations']
    assert len(operations) == progress
    assert all(row['persistent'] and row['action'] == '-ipv4.routes' for row in operations)


@pytest.mark.parametrize('action', ['verify', 'commit'])
def test_previous_persistent_only_journal_cannot_commit_without_a_kernel_gateway(tmp_path, action):
    lab, journal = previous_persistent_only_transaction(tmp_path, complete=True)
    token = (journal / 'meta').read_text().split('|')[0]
    before = copy.deepcopy(lab.data)
    contents = {path.name: path.read_bytes() for path in journal.iterdir()}
    result = lab.run(action, more=['--token', token])
    assert result.returncode == 1 and 'FRONT gateway changed during operation' in result.stdout, result.stdout
    assert lab.data == before and {path.name: path.read_bytes() for path in journal.iterdir()} == contents


@pytest.mark.parametrize('store', ['persistent', 'runtime'])
def test_previous_gatewayless_recovery_preserves_foreign_route_store_changes(tmp_path, store):
    lab, journal = previous_persistent_only_transaction(tmp_path, progress=1, phase='intent')
    foreign = (dict(network='172.20.0.0/16', gateway='198.51.100.1', metric=150) if store == 'persistent' else
               dict(dst='172.20.0.0/16', dev='back0', metric=150))
    lab.data[store].append(foreign)
    before = copy.deepcopy(lab.data)
    contents = {path.name: path.read_bytes() for path in journal.iterdir()}
    result = lab.run('recover')
    assert result.returncode == 1, result.stdout
    assert lab.data == before and {path.name: path.read_bytes() for path in journal.iterdir()} == contents


def test_gatewayless_persistent_recovery_exception_cannot_remove_a_runtime_route(tmp_path):
    """A legacy on-link runtime claim is outside the persistent-only exception."""
    lab = lab_revision(tmp_path)
    lab.data['addresses'][0]['addr_info'].append(dict(family='inet', local='203.0.113.2', prefixlen=24))
    lab.data['runtime'].append(dict(dst='203.0.113.0/24', dev='front0', protocol='kernel', scope='link'))
    original_destination = '172.20.0.0/16'
    assert lab.run(clients=[original_destination], more=['--defer-commit']).returncode == 2
    journal = lab.state / 'front-routes.pending'
    remove_previous_reference_gateway(lab, journal)
    lab.data['persistent'] = []
    lab.data['runtime'] = [row for row in lab.data['runtime'] if row['dst'] != original_destination]
    lab.data['runtime'].append(copy.deepcopy(NEW))
    lab.data['operations'] = []
    lab.rewrite_journal('clients', '203.0.113.0/24\n')
    lab.rewrite_journal('operations', 'R|203.0.113.0/24|198.51.100.1|100\n')
    lab.rewrite_journal('progress', '1 done\n')
    before = copy.deepcopy(lab.data)
    contents = {path.name: path.read_bytes() for path in journal.iterdir()}
    result = lab.run('recover')
    assert result.returncode == 1 and 'cannot authorize runtime rollback' in result.stdout, result.stdout
    assert lab.data == before and {path.name: path.read_bytes() for path in journal.iterdir()} == contents


def test_previous_journal_can_finish_rollback_when_its_only_front_via_route_was_owned(tmp_path):
    """The preceding detector could create a transaction without a base via.

    Keep its real eight-field journal format and checksum contract. Removing
    the unrelated fixture reference from the live/baseline tables reconstructs
    precisely that formerly supported state, without changing owned operations.
    """
    lab = lab_revision(tmp_path)
    assert lab.run(more=['--defer-commit']).returncode == 2
    journal = lab.state / 'front-routes.pending'
    remove_previous_reference_gateway(lab, journal)
    result = lab.run('recover')
    assert result.returncode == 2, result.stdout
    assert lab.data['runtime'] == BASE[:2] and not lab.data['persistent']
    assert not journal.exists()

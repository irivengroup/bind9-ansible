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


@pytest.mark.parametrize('scenario', ['wrong_more_specific', 'equal_cost_other_device',
                                    'missing_match', 'contradictory_match'])
def test_lpm_refuses_wrong_ambiguous_or_inconsistent_front_selection(tmp_path, scenario):
    lab = lab_revision(tmp_path)
    if scenario == 'wrong_more_specific':
        lab.data['runtime'].append({'dst': '198.51.100.2/32', 'dev': 'back0', 'gateway': '10.0.0.1'})
    elif scenario == 'equal_cost_other_device':
        lab.data['runtime'].append({'dst': '198.51.100.0/24', 'dev': 'back0'})
    elif scenario == 'missing_match':
        lab.data['matched_text'] = ''
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


def test_previous_journal_can_finish_rollback_when_its_only_front_via_route_was_owned(tmp_path):
    """The preceding detector could create a transaction without a base via.

    Keep its real eight-field journal format and checksum contract. Removing
    the unrelated fixture reference from the live/baseline tables reconstructs
    precisely that formerly supported state, without changing owned operations.
    """
    lab = lab_revision(tmp_path)
    assert lab.run(more=['--defer-commit']).returncode == 2
    reference = BASE[2]
    lab.data['runtime'].remove(reference)
    journal = lab.state / 'front-routes.pending'
    record = '{}|{}|{}|{}|proto=static;\n'.format(reference['dst'], reference['gateway'],
                                                reference['metric'], reference['dev'])
    text = (journal / 'kernel').read_text(encoding='utf-8')
    assert text.count(record) == 1
    lab.rewrite_journal('kernel', text.replace(record, ''))
    result = lab.run('recover')
    assert result.returncode == 2, result.stdout
    assert lab.data['runtime'] == BASE[:2] and not lab.data['persistent']
    assert not journal.exists()

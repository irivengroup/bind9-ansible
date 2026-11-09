"""Exercise exact public FRONT parsing and journal ownership boundaries."""
import copy
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from test_native_front_routes import NativeRouteLab, BASE, NEW, ROOT, pytestmark as native_marks

pytestmark = native_marks


def test_native_plan_respects_the_existing_management_inode_lock(tmp_path):
    """Even a read-only plan shares the real management lock, without replacement."""
    import fcntl
    lab = NativeRouteLab(tmp_path)
    lab.state.mkdir(mode=0o700)
    lock = lab.state / 'operations.flock'
    lock.write_bytes(b'')
    lock.chmod(0o600)
    before = copy.deepcopy(lab.data)
    with lock.open('r+b') as stream:
        identity = (os.fstat(stream.fileno()).st_dev, os.fstat(stream.fileno()).st_ino)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        refused = lab.run('plan')
        assert refused.returncode == 1 and 'Another route or management operation is active' in refused.stdout
        assert lab.data == before and not (lab.state / 'front-routes.pending').exists()
        assert (lock.stat().st_dev, lock.stat().st_ino) == identity and lock.read_bytes() == b''
    # Releasing the holder permits the same plan; it still performs no writes.
    planned = lab.run('plan')
    assert planned.returncode == 2, planned.stdout
    assert lab.data == before and not (lab.state / 'front-routes.pending').exists()


@pytest.mark.skipif(os.environ.get('BIND9_CANONICAL_BASH_LAB') != '1',
                   reason='Requires the canonical disconnected disposable root')
def test_missing_native_prerequisite_refuses_before_creating_state(tmp_path):
    """Hide owned network doubles without deleting or replacing their links."""
    from canonical_shell_lab import CanonicalFrontEnvironment
    CanonicalFrontEnvironment(ROOT)
    replacement = tmp_path / 'private-sbin'
    replacement.mkdir(mode=0o700)
    subprocess.run(['mount', '--bind', str(replacement), '/usr/sbin'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    try:
        assert shutil.which('ip') is None and shutil.which('nmcli') is None
        result = subprocess.run(['/bin/bash', str(ROOT / 'roles/bind9/files/bind-front-routes.sh'),
                                 '--action', 'plan'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
        assert result.returncode == 1 and 'Missing native prerequisite: ip' in result.stderr
        assert not Path('/var/lib/Bind9-Ansible').exists()
        assert not list(replacement.iterdir())
    finally:
        subprocess.run(['umount', '/usr/sbin'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)


@pytest.mark.skipif(os.environ.get('BIND9_CANONICAL_BASH_LAB') != '1',
                   reason='Requires the canonical disconnected disposable root')
@pytest.mark.parametrize('ancestor', ['missing', 'symlink'])
def test_state_ancestor_must_be_a_real_directory_in_the_native_root(tmp_path, ancestor):
    """Hide /var only inside the disposable root and restore it in all outcomes."""
    from canonical_shell_lab import CanonicalFrontEnvironment
    # This independently enforces Docker/root/lo-only boundaries and installs
    # owned prerequisite doubles. Neither network command should be reached.
    CanonicalFrontEnvironment(ROOT)
    assert Path('/.dockerenv').is_file()
    replacement = tmp_path / 'private-var'
    replacement.mkdir(mode=0o700)
    target = tmp_path / 'private-lib'
    target.mkdir(mode=0o700)
    if ancestor == 'symlink':
        (replacement / 'lib').symlink_to(target, target_is_directory=True)
    subprocess.run(['mount', '--bind', str(replacement), '/var'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    try:
        result = subprocess.run(['/bin/bash', str(ROOT / 'roles/bind9/files/bind-front-routes.sh'),
                                 '--action', 'plan'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
        assert result.returncode == 1 and 'Unsafe state ancestor: /var/lib' in result.stderr
        assert not list(target.iterdir()), 'A refused ancestor cannot create state through its target'
        assert sorted(path.name for path in replacement.iterdir()) == (['lib'] if ancestor == 'symlink' else [])
    finally:
        subprocess.run(['umount', '/var'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)


@pytest.mark.parametrize('kind', ['state-file', 'state-link', 'state-dangling', 'lock-link', 'lock-fifo', 'lock-dangling'])
def test_unsafe_native_state_objects_are_preserved_without_opening_a_transaction(tmp_path, kind):
    """Use actual filesystem objects; state existence never proves ownership."""
    lab = NativeRouteLab(tmp_path)
    target = tmp_path / 'operator-object'
    if kind == 'state-file':
        lab.state.write_bytes(b'operator state bytes\n')
    elif kind in ('state-link', 'state-dangling'):
        if kind == 'state-link':
            target.mkdir(mode=0o700)
        lab.state.symlink_to(target, target_is_directory=True)
    else:
        lab.state.mkdir(mode=0o700)
        lock = lab.state / 'operations.flock'
        if kind in ('lock-link', 'lock-dangling'):
            if kind == 'lock-link':
                target.write_bytes(b'operator lock target\n')
                target.chmod(0o600)
            lock.symlink_to(target)
        else:
            os.mkfifo(str(lock), mode=0o600)
    result = lab.run()
    assert result.returncode == 1, result.stdout
    assert ('Unsafe private directory' if kind.startswith('state-') else 'Unsafe journal file') in result.stdout
    assert lab.data['runtime'] == BASE and lab.data['persistent'] == [] and lab.data['operations'] == []
    if kind == 'state-file':
        assert lab.state.read_bytes() == b'operator state bytes\n'
    elif kind in ('state-link', 'state-dangling'):
        assert lab.state.is_symlink() and os.readlink(str(lab.state)) == str(target)
        assert target.exists() is (kind == 'state-link')
    elif kind == 'lock-link':
        assert lock.is_symlink() and target.read_bytes() == b'operator lock target\n'
    elif kind == 'lock-dangling':
        assert lock.is_symlink() and not target.exists()
    else:
        assert lock.is_fifo()
    assert not (lab.state / 'front-routes.pending').exists()
    assert not list(lab.state.glob('.route-work.*'))


@pytest.mark.parametrize('action,target_exists,message', [
    ('apply', False, 'Pending FRONT journal:'),
    ('recover', True, 'No pending FRONT transaction to recover'),
])
def test_untrusted_pending_link_is_preserved_before_any_route_observation(tmp_path, action, target_exists, message):
    """A dangling or live journal link never authorizes cleanup or recovery."""
    lab = NativeRouteLab(tmp_path)
    lab.state.mkdir(mode=0o700)
    target = tmp_path / 'operator-recovery-target'
    if target_exists:
        target.mkdir(mode=0o700)
    pending = lab.state / 'front-routes.pending'
    pending.symlink_to(target, target_is_directory=True)
    before = copy.deepcopy(lab.data)
    result = lab.run(action)
    assert result.returncode == 1 and message in result.stdout, result.stdout
    assert lab.data == before and lab.data['operations'] == []
    assert pending.is_symlink() and os.readlink(str(pending)) == str(target)
    assert target.exists() is target_exists
    if target_exists:
        assert not list(target.iterdir())
    assert not list(lab.state.glob('.route-work.*'))


@pytest.mark.parametrize('changes,message', [
    ({'profile_text': '{ malformed'}, 'Unsupported NetworkManager route representation'),
    ({'profile_text': '203.0.114.0/24 198.51.100.1 -2'}, 'Invalid profile metric'),
    ({'runtime_text': '198.51.100.0/24 dev front0 metric invalid'}, 'Invalid runtime metric'),
    ({'runtime_text': '198.51.100.0/24 dev front0 proto'}, 'Incomplete runtime route'),
    ({'addresses_text': '1: invalid/name inet 198.51.100.2/24 scope global invalid/name'}, 'Unsupported interface name'),
    ({'route_metric': 'invalid', 'persistent': [dict(network='203.0.113.0/24', gateway='198.51.100.1', metric=-1)]}, 'No effective metric could be observed'),
    ({'persistent': [dict(network='203.0.113.0/24', gateway='198.51.100.1', metric=200)], 'runtime': BASE + [NEW]}, 'Persistent and runtime metrics disagree'),
    # Missing and ambiguous active connections must not select another profile.
    ({'uuid': ''}, 'FRONT has no unique active profile UUID'),
    ({'uuid': '12345678-1234-4234-8234-123456789abc\n87654321-4321-4321-8321-cba987654321'}, 'FRONT has no unique active profile UUID'),
    ({'profile_text': '{ = 203.0.114.0/24 }'}, 'Duplicate or empty profile route field'),
    ({'profile_text': '{ ip =  }'}, 'Duplicate or empty profile route field'),
    ({'runtime_text': '198.51.100.0/24 dev front0 via'}, 'Invalid IPv4:'),
    ({'runtime_text': '198.51.100.0/24 dev front0 metric'}, 'Invalid runtime metric'),
    ({'profile_text': '}'}, 'Unsupported NetworkManager route representation'),
    ({'runtime_text': '198.51.100.0/24 dev'}, 'No usable non-loopback routing match'),
])
def test_unusable_observations_refuse_before_any_write(tmp_path, changes, message):
    lab = NativeRouteLab(tmp_path)
    lab.data.update(copy.deepcopy(changes))
    before = copy.deepcopy(lab.data)
    result = lab.run('plan')
    assert result.returncode == 1 and message in result.stdout, result.stdout
    assert lab.data == before and lab.data['operations'] == []
    assert not (lab.state / 'front-routes.pending').exists()


@pytest.mark.parametrize('kind', ['prefix', 'device'])
def test_incoherent_matched_and_complete_front_tables_refuse_before_any_write(tmp_path, kind):
    """Two valid native observations must still identify the same FRONT path."""
    lab = NativeRouteLab(tmp_path)
    lab.data['matched_text'] = '198.51.100.0/24 dev front0 proto kernel scope link'
    if kind == 'prefix':
        lab.data['matched_text'] = '198.51.100.0/25 dev front0 proto kernel scope link'
    else:
        for row in lab.data['runtime']:
            if row.get('dev') == 'front0':
                row['dev'] = 'back0'
    before = copy.deepcopy(lab.data)
    result = lab.run('plan')
    assert result.returncode == 1 and 'FRONT routing match disagrees with the complete main table' in result.stdout
    assert lab.data == before and lab.data['operations'] == []
    assert not (lab.state / 'front-routes.pending').exists()


@pytest.mark.parametrize('field', ['profile', 'kernel', 'uuid', 'addresses', 'rules', 'gateway', 'device'])
def test_context_drift_after_planning_preserves_operator_changes_without_route_writes(tmp_path, field):
    """Reobserve each independently meaningful context before publishing writes."""
    lab = NativeRouteLab(tmp_path)
    if field == 'profile':
        updates = {'profile_text': '172.20.0.0/16 198.51.100.1 100'}
    elif field == 'kernel':
        updates = {'runtime': copy.deepcopy(BASE) + [dict(dst='172.20.0.0/16', dev='back0')]}
    elif field == 'uuid':
        updates = {'uuid': '87654321-4321-4321-8321-cba987654321'}
    elif field == 'gateway':
        # A secondary address added with noprefixroute can make every client
        # directly connected without changing the observed main table/profile.
        # Its changed gateway requirement must invalidate the approved plan.
        addresses = copy.deepcopy(lab.data['addresses'])
        addresses[0]['addr_info'].append(dict(family='inet', local='203.0.113.5', prefixlen=24))
        updates = {'addresses': addresses}
    elif field == 'device':
        addresses = copy.deepcopy(lab.data['addresses'])
        addresses[0]['ifname'] = 'front1'
        routes = copy.deepcopy(lab.data['runtime'])
        for route in routes:
            if route.get('dev') == 'front0':
                route['dev'] = 'front1'
        updates = {'addresses': addresses, 'runtime': routes}
    elif field == 'addresses':
        addresses = copy.deepcopy(lab.data['addresses'])
        addresses[0]['addr_info'].append(dict(family='inet', local='198.51.100.10', prefixlen=24))
        updates = {'addresses': addresses}
    else:
        # Both observations describe the permitted main-table policy. A textual
        # context change still requires a new plan before any native mutation.
        updates = {'rules_text': '0:  from all lookup local\n32766: from all lookup main\n32767: from all lookup default'}
    lab.data['front_observation_update'] = dict(read=2, values=updates)
    result = lab.run()
    assert lab.data['front_observation_reads'] == 2
    assert result.returncode == 1 and 'Routing changed after planning; no native write attempted' in result.stdout
    assert all(lab.data[key] == value for key, value in updates.items())
    assert lab.data['runtime'] == updates.get('runtime', BASE)
    assert lab.data['persistent'] == [] and lab.data['operations'] == []
    assert not (lab.state / 'front-routes.pending').exists()
    assert not list(lab.state.glob('.route-work.*'))


@pytest.mark.parametrize('case', ['plain-attributes', 'loopback-match', 'duplicate-link', 'opaque-link', 'explicit-main-table',
                                 'empty-profile-marker', 'direct-profile', 'metric-only-profile', 'attribute-only-profile',
                                 'boot-global-runtime', 'dotted-profile-attribute'])
def test_unrelated_route_details_are_preserved_by_a_plan(tmp_path, case):
    lab = NativeRouteLab(tmp_path)
    if case == 'explicit-main-table':
        # NetworkManager's 0 (automatic main) and 254 (explicit main) agree.
        lab.data['route_table'] = '254'
    elif case == 'plain-attributes':
        lab.data['profile_text'] = '203.0.114.0/24 198.51.100.1 100 onlink=true'
    elif case == 'loopback-match':
        lab.data['runtime'].append(dict(dst='198.51.100.2/32', dev='lo', protocol='kernel', scope='host'))
    elif case == 'duplicate-link':
        lab.data['runtime'].append(copy.deepcopy(BASE[1]))
    elif case == 'empty-profile-marker':
        lab.data['profile_text'] = '--'
    elif case in ('direct-profile', 'metric-only-profile', 'attribute-only-profile'):
        lab.data['profile_text'] = '172.20.0.0/16' + {
            'direct-profile': '', 'metric-only-profile': ' 120',
            'attribute-only-profile': ' table=254 onlink=true'}[case]
    elif case == 'boot-global-runtime':
        lab.data['runtime'].append(dict(dst='172.20.0.0/16', dev='other0', protocol='boot', scope='global'))
    elif case == 'dotted-profile-attribute':
        lab.data['profile_text'] = '172.20.0.0/16 src=198.51.100.2'
    else:
        lab.data['runtime'][1]['mtu'] = 1400
    before = copy.deepcopy(lab.data)
    result = lab.run('plan')
    assert result.returncode == 2 and 'additions=2' in result.stdout, result.stdout
    assert lab.data == before and lab.data['operations'] == []


@pytest.mark.parametrize('protected_index', [1, 2], ids=['back', 'adm'])
def test_front_cannot_share_a_protected_device_with_distinct_local_addresses(tmp_path, protected_index):
    """Distinct IP addresses do not make a shared FRONT/BACK or FRONT/ADM device safe."""
    lab = NativeRouteLab(tmp_path)
    lab.data['addresses'][protected_index]['ifname'] = 'front0'
    before = copy.deepcopy(lab.data)
    result = lab.run()
    assert result.returncode == 1 and 'FRONT must be uniquely present/up and distinct' in result.stdout
    assert lab.data == before and not (lab.state / 'front-routes.pending').exists()


def test_an_enslaved_front_device_is_refused(tmp_path):
    lab = NativeRouteLab(tmp_path)
    lab.data['addresses'][0]['master'] = 'bridge0'
    before = copy.deepcopy(lab.data)
    result = lab.run()
    assert result.returncode == 1 and 'Enslaved FRONT interface is unsupported' in result.stdout
    assert lab.data == before and lab.data['operations'] == []


@pytest.mark.parametrize('more,clients,message', [
    (['--ssh-peer', '2001:db8::invalid'], None, 'Invalid SSH peer'),
    ([], ['203.0.113.1'] * 513, 'At most 512 client destinations per operation'),
    (['--front-ip', '192.0.2.2'], None, 'FRONT/BACK/ADM addresses must differ'),
    (['--adm-ip', '10.0.0.2'], None, 'FRONT/BACK/ADM addresses must differ'),
    ([], ['203.0.113.1/24'], 'Noncanonical client subnet'),
])
def test_invalid_peer_or_oversized_input_cannot_start_a_transaction(tmp_path, more, clients, message):
    lab = NativeRouteLab(tmp_path)
    result = lab.run(clients=clients, more=more)
    assert result.returncode == 1 and message in result.stdout, result.stdout
    assert lab.data['runtime'] == BASE and lab.data['operations'] == []


@pytest.mark.parametrize('case,message', [
    ('foreign-token', 'Transaction token does not own this pending operation'),
    ('operation-limit', 'Invalid operation journal size'),
    ('operation-gateway', 'Invalid journal operation'),
    ('persistent-zero-metric', 'Invalid persistent journal metric'),
    ('persistent-negative-metric', 'Invalid journal operation fields'),
    ('progress-beyond-operations', 'Malformed journal progress'),
    ('incomplete-commit', 'Incomplete transaction cannot commit'),
    ('live-profile-drift', 'Live FRONT context changed; rollback refused'),
    ('verify-profile-drift', 'FRONT profile, addresses or rules changed during operation'),
    ('verify-persistent-store-drift', 'Routing stores differ from the additive plan'),
    ('live-address-drift', 'Live FRONT context changed; rollback refused'),
    ('verify-address-drift', 'FRONT profile, addresses or rules changed during operation'),
    ('journal-uuid', 'Malformed route journal identity'),
    ('journal-device', 'Malformed route journal identity'),
    ('journal-metric-overflow', 'Invalid journal metric'),
    ('operation-metric-overflow', 'Invalid journal operation'),
    ('progress-overflow', 'Malformed journal progress'),
    ('intent-commit', 'Incomplete transaction cannot commit'),
    ('live-device-drift', 'Live FRONT context changed; rollback refused'),
    ('verify-device-drift', 'FRONT profile, addresses or rules changed during operation'),
    ('live-rules-drift', 'Live FRONT context changed; rollback refused'),
    ('verify-rules-drift', 'FRONT profile, addresses or rules changed during operation'),
    ('live-gateway-drift', 'Live FRONT context changed; rollback refused'),
    ('meta-link', 'Unsafe journal file'),
])
def test_damaged_or_foreign_journal_cannot_authorize_removal(tmp_path, case, message):
    lab = NativeRouteLab(tmp_path)
    assert lab.run(more=['--defer-commit']).returncode == 2
    pending = lab.state / 'front-routes.pending'
    token = (pending / 'meta').read_text().split('|')[0]
    action, more = 'recover', []
    if case == 'foreign-token':
        more = ['--token', '00000000-0000-4000-8000-ffffffffffff']
    elif case == 'operation-limit':
        lab.rewrite_journal('operations', 'P|203.0.113.0/24|198.51.100.1|100\n' * 1025)
    elif case == 'operation-gateway':
        lab.rewrite_journal('operations', 'P|203.0.113.0/24|198.51.100.254|100\n')
    elif case == 'persistent-zero-metric':
        lab.rewrite_journal('operations', 'P|203.0.113.0/24|198.51.100.1|0\n')
    elif case == 'persistent-negative-metric':
        # A former automatic-profile formatter cannot authorize legacy -1
        # operation data: the journal contract requires explicit metrics.
        lab.rewrite_journal('operations', 'P|203.0.113.0/24|198.51.100.1|-1\n')
    elif case == 'progress-beyond-operations':
        lab.rewrite_journal('progress', '3 done\n')
    elif case == 'incomplete-commit':
        lab.rewrite_journal('progress', '1 done\n')
        action, more = 'commit', ['--token', token]
    elif case == 'verify-persistent-store-drift':
        lab.data['persistent'].append(dict(network='172.20.0.0/16', gateway='198.51.100.1', metric=150))
        action, more = 'verify', ['--token', token]
    elif case in ('live-profile-drift', 'verify-profile-drift', 'live-address-drift', 'verify-address-drift'):
        if 'profile' in case:
            lab.data['uuid'] = '87654321-4321-4321-8321-cba987654321'
        else:
            lab.data['addresses'][0]['addr_info'].append(dict(family='inet', local='198.51.100.10', prefixlen=24))
        if case.startswith('verify-'):
            action, more = 'verify', ['--token', token]
    elif case in ('journal-uuid', 'journal-device', 'journal-metric-overflow'):
        fields = (pending / 'meta').read_text().strip().split('|')
        index, value = {'journal-uuid': (1, 'invalid'), 'journal-device': (2, 'invalid/name'),
                        'journal-metric-overflow': (7, '4294967296')}[case]
        fields[index] = value
        lab.rewrite_journal('meta', '|'.join(fields) + '\n')
    elif case == 'operation-metric-overflow':
        lab.rewrite_journal('operations', 'R|203.0.113.0/24|198.51.100.1|4294967296\n')
    elif case == 'progress-overflow':
        lab.rewrite_journal('progress', '4294967296 done\n')
    elif case == 'intent-commit':
        lab.rewrite_journal('progress', '2 intent\n')
        action, more = 'commit', ['--token', token]
    elif case in ('live-device-drift', 'verify-device-drift', 'live-gateway-drift'):
        # A checksummed journal still has to describe the observed native context.
        fields = (pending / 'meta').read_text().strip().split('|')
        fields[3 if case == 'live-gateway-drift' else 2] = (
            '198.51.100.254' if case == 'live-gateway-drift' else 'other0')
        lab.rewrite_journal('meta', '|'.join(fields) + '\n')
        if case == 'live-gateway-drift':
            lab.rewrite_journal('operations', (pending / 'operations').read_text().replace(
                '|198.51.100.1|', '|198.51.100.254|'))
        if case.startswith('verify-'):
            action, more = 'verify', ['--token', token]
    elif case in ('live-rules-drift', 'verify-rules-drift'):
        lab.rewrite_journal('rules', (pending / 'rules').read_text().replace('32766:', '32765:'))
        if case.startswith('verify-'):
            action, more = 'verify', ['--token', token]
    else:
        saved = tmp_path / 'saved-meta'
        saved.write_bytes((pending / 'meta').read_bytes())
        (pending / 'meta').unlink()
        (pending / 'meta').symlink_to(saved)
    before = copy.deepcopy(lab.data)
    journal = {path.name: path.read_bytes() for path in pending.iterdir()}
    result = lab.run(action, more=more)
    assert result.returncode == 1 and message in result.stdout, result.stdout
    assert lab.data == before, 'A rejected ownership claim cannot delete any route'
    assert {path.name: path.read_bytes() for path in pending.iterdir()} == journal
    assert not list(lab.state.glob('.route-work.*'))


@pytest.mark.parametrize('clients,gateway,message,owned', [
    (['203.0.113.0/24'], '198.51.100.254', 'Wrong FRONT gateway', 2),
    (['203.0.113.0/24'], None, 'Wrong FRONT gateway', 2),
    (['198.51.100.55'], '198.51.100.254', 'Unexpected gateway for directly connected client', 0),
])
def test_wrong_native_lookup_cannot_publish_or_keep_an_owned_route(tmp_path, clients, gateway, message, owned):
    lab = NativeRouteLab(tmp_path)
    lab.data['lookup_gateway'] = gateway
    result = lab.run(clients=clients)
    assert result.returncode == 1 and message in result.stdout, result.stdout
    assert lab.data['runtime'] == BASE and lab.data['persistent'] == []
    operations = lab.data['operations']
    assert len(operations) == 2 * owned
    assert all(item['action'] == '+ipv4.routes' for item in operations[:owned])
    assert all(item['action'] == '-ipv4.routes' for item in operations[owned:])
    assert not (lab.state / 'front-routes.pending').exists()
    assert not list(lab.state.glob('.route-work.*'))


@pytest.mark.parametrize('option', ['--action', '--front-ip', '--back-ip', '--adm-ip',
                                   '--peer-back-ip', '--client', '--metric', '--token'])
def test_missing_required_option_value_fails_before_any_network_write(tmp_path, option):
    lab = NativeRouteLab(tmp_path)
    result = lab.run(more=[option])
    assert result.returncode == 1 and 'parameter null or not set' in result.stdout, result.stdout
    assert lab.data['runtime'] == BASE and lab.data['persistent'] == []
    assert lab.data['operations'] == []
    assert not lab.state.exists()


def test_missing_ssh_peer_value_has_an_explicit_refusal_and_keeps_empty_peer_supported(tmp_path):
    """Distinguish an absent CLI value from the supported explicit empty value."""
    lab = NativeRouteLab(tmp_path)
    before = copy.deepcopy(lab.data)
    missing = lab.run('plan', more=['--ssh-peer'])
    assert missing.returncode == 1 and 'Missing value for --ssh-peer' in missing.stdout, missing.stdout
    assert lab.data == before and not lab.state.exists()
    empty = lab.run('plan', more=['--ssh-peer', ''])
    assert empty.returncode == 2, empty.stdout
    assert lab.data == before and not (lab.state / 'front-routes.pending').exists()

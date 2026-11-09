"""Public deployment entry: original role tags, inventory, guards and lease cleanup."""
import base64
import os
from pathlib import Path

import pytest

from native_ansible_runner import NativeFilesystemScope
from native_deployment_runner import NativeDeploymentPlayRun

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')


def run(phase, locked=False, check=False, **values):
    hosts = {name: dict(bind_environment='test', bind_site_name='site', bind_front_ip='198.51.100.' + suffix,
                       bind_back_ip='192.0.2.' + suffix) for name, suffix in [('localhost', '11'), ('peer', '12')]}
    variables = dict(ansible_run_tags=[phase], bind_operation_lock_enabled=locked,
        bind_front_routes_enabled=False, ansible_distribution='RedHat', ansible_distribution_major_version='8',
        bind_tsig_key_name='site-transfer', bind_tsig_algorithm='hmac-sha256',
        bind_tsig_secret=base64.b64encode(b'public-synthetic-test-fixture-0000').decode('ascii'),
        bind_authoritative_zones=[], bind_forward_zones=[], bind_cloud_clients=[], bind_internal_clients=['203.0.113.0/24'])
    variables.update(values)
    return NativeDeploymentPlayRun('main.yml', variables, check_mode=check, inventory_hosts=hosts,
        inventory_groups=dict(site=['localhost', 'peer'], dns_primary=['localhost'], dns_secondary=['peer'],
                              dns_deployment_order=['localhost', 'peer'])).run()


@pytest.fixture(autouse=True)
def scope():
    with NativeFilesystemScope(NativeDeploymentPlayRun):
        yield


@pytest.mark.parametrize('phase', ['preflight', 'routes'])
@pytest.mark.parametrize('locked,check', [(False, False), (True, False), (True, True)])
def test_native_public_deployment_nonprovisioning_tags_preserve_original_dispatch(phase, locked, check):
    status, result = run(phase, locked, check)
    assert status == 0 and not result.failed, (status, result.failed)
    assert result.ok.count('Reset deployment lease ownership for this role invocation') == 2
    assert not Path('/var/lib/Bind9-Ansible/.operation.lock').exists()
    assert not Path('/var/lib/Bind9-Ansible/.deployment.lock').exists()
    if phase == 'routes':
        assert result.ok.count('Report disabled FRONT route management') == 2
        assert ('Remove only our empty operation lease without recursive deletion' in result.ok) is (locked and not check)
    else:
        assert result.ok.count('Validate supported platform and mandatory inputs') == 2
        assert 'Acquire the shared deployment and management lease without stealing it' not in result.ok


def test_native_public_deployment_releases_its_lease_after_a_real_input_guard_failure():
    status, result = run('routes', locked=True, bind_cloud_clients='invalid scalar')
    assert status == 2 and result.failed == ['Validate the cloud and internal client list types',
        'Stop the failed deployment after controlled lease cleanup']
    assert not Path('/var/lib/Bind9-Ansible/.operation.lock').exists()
    assert 'Remove only our empty operation lease without recursive deletion' in result.ok
    assert result.facts['bind_deployment_invocation_failed'] is True


def test_native_public_deployment_retains_lease_for_an_on_disk_front_journal_without_pending_fact():
    state = Path('/var/lib/Bind9-Ansible')
    state.mkdir(mode=0o700)
    journal = state / 'front-routes.pending'
    journal.mkdir(mode=0o700)
    progress = journal / 'progress'
    progress.write_bytes(b'1 intent\n')
    status, result = run('routes', locked=True, bind_cloud_clients='invalid scalar')
    assert status == 2 and result.failed == ['Validate the cloud and internal client list types',
        'Preserve the operation lease while FRONT recovery evidence exists']
    assert (state / '.operation.lock').is_dir()
    assert progress.read_bytes() == b'1 intent\n'
    assert result.facts.get('bind_front_route_transaction_pending', False) is False
    assert 'Remove only our empty operation lease without recursive deletion' not in result.ok


def test_native_public_deployment_refusal_preserves_another_invocations_lease():
    state = Path('/var/lib/Bind9-Ansible')
    state.mkdir(mode=0o700)
    lease = state / '.operation.lock'
    lease.mkdir(mode=0o700)
    evidence = lease / 'operator-evidence'
    evidence.write_bytes(b'owned by a different invocation')
    before = lease.stat().st_ino
    status, result = run('routes', locked=True)
    assert status == 2 and result.failed == [
        'Acquire the shared deployment and management lease without stealing it',
        'Stop the failed deployment after controlled lease cleanup']
    assert lease.stat().st_ino == before and evidence.read_bytes() == b'owned by a different invocation'
    assert 'Remove only our empty operation lease without recursive deletion' not in result.ok


def test_native_public_deployment_does_not_release_owned_lease_with_pending_route_recovery():
    status, result = run('routes', locked=True, bind_cloud_clients='invalid scalar',
                         bind_front_route_transaction_pending=True)
    assert status == 2 and result.failed == ['Validate the cloud and internal client list types',
                                           'Preserve the operation lease if recovery did not complete']
    lease = Path('/var/lib/Bind9-Ansible/.operation.lock')
    assert lease.is_dir() and lease.stat().st_mode & 0o777 == 0o700
    assert result.facts['bind_deployment_lease_acquired'] is True
    assert result.facts['bind_deployment_invocation_failed'] is True
    assert 'Remove only our empty operation lease without recursive deletion' not in result.ok


@pytest.mark.parametrize('check', [False, True])
def test_native_public_deployment_hibernation_keeps_existing_seal_and_skips_provisioning(check):
    state = Path('/var/lib/Bind9-Ansible')
    state.mkdir(mode=0o700)
    seal = state / '.deployment.lock'
    raw = b'existing private deployment barrier\n'
    seal.write_bytes(raw)
    seal.chmod(0o600)
    before = seal.stat().st_ino, seal.stat().st_mtime_ns
    status, result = run('hibernate', check=check, hibernate_deployment=True)
    assert status == 0 and not result.failed
    assert seal.read_bytes() == raw and (seal.stat().st_ino, seal.stat().st_mtime_ns) == before
    assert not (state / '.operation.lock').exists()
    assert result.ok.count('Report the isolated hibernation result without configuration contents') == 2
    assert 'Validate the cloud and internal client list types' not in result.ok


@pytest.mark.parametrize('tags,check,hibernate,expected', [
    (['all'], False, True, True), (['deploy'], False, True, True),
    (['routes'], False, True, False), (['all'], True, True, False), (['all'], False, False, False)])
def test_native_deployment_optional_seal_guard_keeps_full_success_and_check_mode_contract(tags, check, hibernate, expected):
    """Observe the exact native guard; this cannot count a seal publication."""
    from test_ansible_canonical_predicates import CanonicalPredicates
    CanonicalPredicates('../main.yml').check('Optionally seal a successfully completed full deployment',
        dict(ansible_run_tags=tags, ansible_check_mode=check, hibernate_deployment=hibernate), expected, key='when')


def test_native_root_guard_excludes_provisioning_from_a_hibernation_request():
    # The real hibernate entry also has original never/hibernate tags. Evaluate
    # its canonical exclusion guard independently, without counting a skipped
    # deployment action as executed when those tags prune its children first.
    from test_ansible_canonical_predicates import CanonicalPredicates
    CanonicalPredicates('../main.yml').check('Run deployment tasks with their original tags and order',
        dict(ansible_run_tags=['hibernate']), False, key='when')


def test_native_reserved_never_dispatch_preserves_journal_without_acquiring_a_lease():
    """Original public role selection must not turn never into route recovery."""
    state = Path('/var/lib/Bind9-Ansible')
    state.mkdir(mode=0o700)
    journal = state / 'front-routes.pending'
    journal.mkdir(mode=0o700)
    evidence = journal / 'operator-evidence'
    evidence.write_bytes(b'preserve this interrupted operation for explicit recovery\n')
    before = journal.stat().st_ino, evidence.stat().st_ino, evidence.read_bytes()
    status, result = run('never', locked=True)
    assert status == 0 and not result.failed, (status, result.failed)
    assert (journal.stat().st_ino, evidence.stat().st_ino, evidence.read_bytes()) == before
    assert not (state / '.operation.lock').exists()
    assert 'Acquire the shared deployment and management lease without stealing it' not in result.ok
    assert 'Run the single native FRONT transaction with bounded observations' not in result.ok
    assert 'Clear pending FRONT ownership only after successful completion' not in result.ok

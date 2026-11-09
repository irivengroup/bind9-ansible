"""Native module integration for the actual deployment guard and lease release."""
import copy
import os
from pathlib import Path
import shutil

import pytest

from native_ansible_runner import NativeLeaseRun

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires the explicitly enabled disconnected disposable Linux root')
STATE = Path('/var/lib/Bind9-Ansible')
LOCK = STATE / '.operation.lock'
SEAL = STATE / '.deployment.lock'


@pytest.fixture(autouse=True)
def isolated_state():
    NativeLeaseRun('tasks.d/deploy/guard.yml', {})
    assert not STATE.exists() and not STATE.is_symlink()
    try:
        yield
    finally:
        # Only this exact test-owned root may be removed. Never follow a link
        # installed deliberately by a refusal scenario.
        if STATE.is_symlink():
            STATE.unlink()
        elif STATE.exists():
            assert STATE.resolve() == Path('/var/lib/Bind9-Ansible')
            shutil.rmtree(str(STATE))


def guard(**variables):
    return NativeLeaseRun('tasks.d/deploy/guard.yml', variables).run()


def release(values):
    return NativeLeaseRun('tasks.d/deploy/lease_release.yml', values).run()


def make_state():
    STATE.mkdir(mode=0o700)


def test_unlocked_default_checks_seals_without_creating_an_operation_lease():
    status, result = guard()
    assert status == 0 and not result.failed and not STATE.exists()
    assert result.facts['bind_deployment_lease_acquired'] is False


@pytest.mark.parametrize('variables', [dict(bind_operation_lock_enabled=True),
    dict(hibernate_deployment=True), dict(ansible_run_tags=['hibernate'], hibernate_deployment=True)])
def test_native_check_mode_observes_policy_without_acquiring_a_lease(variables):
    status, result = NativeLeaseRun('tasks.d/deploy/guard.yml', variables, check_mode=True).run()
    assert status == 0 and not result.failed and not STATE.exists()
    assert result.facts['bind_deployment_lease_required'] is True
    assert result.facts['bind_deployment_lease_acquired'] is False


def test_targeted_routes_do_not_acquire_a_hibernation_lease_implicitly():
    status, result = guard(ansible_run_tags=['routes'], hibernate_deployment=True)
    assert status == 0 and not result.failed and not STATE.exists()
    assert result.facts['bind_deployment_lease_required'] is False
    assert result.facts['bind_deployment_lease_acquired'] is False


def test_seal_created_during_lease_acquisition_stops_provisioning_and_preserves_evidence(tmp_path, monkeypatch):
    binaries = tmp_path / 'race-bin'
    binaries.mkdir()
    command = binaries / 'mkdir'
    command.write_text('#!/bin/bash\n/usr/bin/mkdir "$@" || exit $?\n'
        'if [[ ${@: -1} == /var/lib/Bind9-Ansible/.operation.lock ]]; then\n'
        '  umask 077\n  printf "concurrent completion seal\\n" > /var/lib/Bind9-Ansible/.deployment.lock\nfi\n', encoding='utf-8')
    command.chmod(0o755)
    monkeypatch.setenv('PATH', str(binaries) + os.pathsep + os.environ['PATH'])
    status, result = guard(bind_operation_lock_enabled=True)
    assert status == 2 and result.failed == ['Reject a deployment sealed between observation and lease acquisition']
    assert LOCK.is_dir() and result.facts['bind_deployment_lease_acquired'] is True
    values = result.facts.copy()
    values['bind_deployment_owned_lease'] = result.registers['bind_deployment_owned_lease']
    status, released = release(values)
    assert status == 0 and not released.failed and not LOCK.exists()
    assert SEAL.read_text(encoding='utf-8') == 'concurrent completion seal\n'


@pytest.mark.parametrize('variables', [dict(bind_operation_lock_enabled=True),
    dict(hibernate_deployment=True), dict(hibernate_deployment='yes'), dict(hibernate_deployment='true'),
    dict(ansible_run_tags=['hibernate'], hibernate_deployment=True),
    dict(ansible_run_tags=['hibernate', 'always'], hibernate_deployment=True)])
def test_requested_lease_is_acquired_and_released_by_its_real_identity(variables):
    status, result = guard(**variables)
    assert status == 0 and not result.failed
    assert LOCK.is_dir() and LOCK.stat().st_mode & 0o777 == 0o700
    assert result.facts['bind_deployment_lease_acquired'] is True
    values = result.facts.copy()
    values['bind_deployment_owned_lease'] = result.registers['bind_deployment_owned_lease']
    status, released = release(values)
    assert status == 0 and not released.failed and not LOCK.exists()
    assert released.facts['bind_deployment_lease_acquired'] is False


@pytest.mark.parametrize('variables', [dict(force=False), dict(deployment_lock=False), dict(deployment_locked=False),
    dict(bind_operation_lock_enabled='true'), dict(hibernate_deployment='YES'), dict(hibernate_deployment=1),
    dict(hibernate_deployment=None), dict(ansible_run_tags=['hibernate'], hibernate_deployment=False),
    dict(ansible_run_tags=['hibernate', 'deploy'], hibernate_deployment=True)])
def test_invalid_lock_and_hibernation_inputs_fail_before_creating_state(variables):
    status, result = guard(**variables)
    assert status == 2 and len(result.failed) == 1
    assert not STATE.exists()


@pytest.mark.parametrize('tags,accepted', [(['all'], False), (['deploy'], False), (['configure'], False),
                                        (['routes'], True), (['backup'], True), (['hibernate'], True)])
def test_existing_seal_blocks_provisioning_and_preserves_targeted_operations(tags, accepted):
    make_state()
    SEAL.write_text('existing operator evidence\n', encoding='utf-8')
    SEAL.chmod(0o600)
    variables = dict(ansible_run_tags=tags, hibernate_deployment=tags == ['hibernate'])
    status, result = guard(**variables)
    assert status == (0 if accepted else 2)
    assert SEAL.read_text(encoding='utf-8') == 'existing operator evidence\n'
    assert LOCK.exists() is (accepted and tags == ['hibernate'])
    if not accepted:
        assert result.failed == ['Refuse another deployment whenever a completion seal exists']


@pytest.mark.parametrize('scenario', ['root_symlink', 'root_mode', 'root_owner', 'seal_symlink',
                                     'seal_directory', 'seal_hardlink', 'seal_mode', 'seal_owner'])
def test_unsafe_state_or_seal_fails_before_a_lease_is_acquired(tmp_path, scenario):
    target = tmp_path / 'foreign'
    target.mkdir()
    if scenario == 'root_symlink':
        STATE.symlink_to(target, target_is_directory=True)
    else:
        make_state()
        if scenario == 'root_mode': STATE.chmod(0o777)
        elif scenario == 'root_owner': os.chown(str(STATE), 12345, 12345)
        elif scenario == 'seal_symlink': SEAL.symlink_to(target, target_is_directory=True)
        elif scenario == 'seal_directory': SEAL.mkdir(mode=0o600)
        else:
            SEAL.write_text('evidence', encoding='utf-8')
            SEAL.chmod(0o600)
            if scenario == 'seal_hardlink': os.link(str(SEAL), str(STATE / 'alias'))
            elif scenario == 'seal_mode': SEAL.chmod(0o666)
            elif scenario == 'seal_owner': os.chown(str(SEAL), 12345, 12345)
    status, result = guard(bind_operation_lock_enabled=True)
    assert status == 2 and result.failed == ['Reject unsafe deployment seal paths regardless of the requested policy']
    assert not LOCK.exists() and target.is_dir() and not list(target.iterdir())


@pytest.mark.parametrize('scenario', ['retained', 'pending', 'missing_identity', 'missing_stat',
    'missing_directory', 'symlink', 'file', 'mode', 'owner', 'device', 'inode', 'nonempty'])
def test_release_keeps_unowned_changed_or_incomplete_evidence(tmp_path, scenario):
    status, result = guard(bind_operation_lock_enabled=True)
    assert status == 0
    values = result.facts.copy()
    owned = copy.deepcopy(result.registers['bind_deployment_owned_lease'])
    values['bind_deployment_owned_lease'] = owned
    target = tmp_path / 'foreign'
    target.mkdir()
    if scenario == 'retained': values['bind_deployment_retain_lease'] = True
    elif scenario == 'pending': values['bind_front_route_transaction_pending'] = True
    elif scenario == 'missing_identity': del values['bind_deployment_owned_lease']
    elif scenario == 'missing_stat': values['bind_deployment_owned_lease'] = {}
    elif scenario == 'missing_directory': LOCK.rmdir()
    elif scenario == 'symlink':
        LOCK.rmdir()
        LOCK.symlink_to(target, target_is_directory=True)
    elif scenario == 'file':
        LOCK.rmdir()
        LOCK.write_text('foreign replacement', encoding='utf-8')
    elif scenario == 'mode': LOCK.chmod(0o777)
    elif scenario == 'owner': os.chown(str(LOCK), 12345, 12345)
    elif scenario == 'device': owned['stat']['dev'] += 1
    elif scenario == 'inode': owned['stat']['inode'] += 1
    else: (LOCK / 'recovery-evidence').write_text('preserve', encoding='utf-8')
    status, released = release(values)
    assert status == 2 and len(released.failed) == 1
    assert 'bind_deployment_lease_acquired' not in released.facts
    assert (LOCK.exists() or LOCK.is_symlink()) is (scenario != 'missing_directory')
    assert target.is_dir() and not list(target.iterdir())
    if scenario == 'nonempty':
        assert (LOCK / 'recovery-evidence').read_text(encoding='utf-8') == 'preserve'


@pytest.mark.parametrize('kind', ['directory', 'file', 'symlink', 'dangling-symlink'])
def test_release_retains_the_lease_when_front_journal_exists_without_pending_facts(tmp_path, kind):
    status, result = guard(bind_operation_lock_enabled=True)
    assert status == 0
    values = dict(result.facts, bind_deployment_owned_lease=result.registers['bind_deployment_owned_lease'],
                  bind_front_route_transaction_pending=False, bind_deployment_retain_lease=False)
    journal = STATE / 'front-routes.pending'
    foreign = tmp_path / 'operator-evidence'
    foreign.write_bytes(b'preserve foreign state')
    if kind == 'directory':
        journal.mkdir(mode=0o700)
        (journal / 'progress').write_bytes(b'1 intent\n')
    elif kind == 'file':
        journal.write_bytes(b'preserve unexpected recovery object')
    else:
        journal.symlink_to(foreign if kind == 'symlink' else tmp_path / 'missing-target')
    before = LOCK.stat().st_ino, journal.lstat().st_ino
    status, released = release(values)
    assert status == 2 and released.failed == ['Preserve the operation lease while FRONT recovery evidence exists']
    assert (LOCK.stat().st_ino, journal.lstat().st_ino) == before
    assert foreign.read_bytes() == b'preserve foreign state'
    assert 'Remove only our empty operation lease without recursive deletion' not in released.ok

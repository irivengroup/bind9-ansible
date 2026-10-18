"""Exercise the real role entry/guard/seal/release with an inert deploy body.

Only the DNS provisioning body is substituted. This isolates tag propagation,
lock transitions and failed cleanup without touching a real DNS/network service.
"""
import hashlib
import json
import os
import shutil

import pytest
import yaml

from test_management_ansible import ROOT, Runner

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.geteuid() != 0 or not shutil.which('ansible-playbook'),
                                reason='Requires isolated Linux root and Ansible')


class DeploymentLab(Runner):
    """Keep the exact production guard and cleanup around a test-only workload."""

    def __init__(self, folder, native=False, body=None):
        super(DeploymentLab, self).__init__(folder, native)
        self.state = self.lab.root / 'var/lib/Bind9-Ansible'
        self.marker = self.lab.root / 'deployed'
        self.body = self.role / 'tasks/tasks.d/deploy/main.yml'
        self.body.write_text(yaml.safe_dump(body if body is not None else [
            {'copy': {'dest': str(self.marker), 'content': 'deployment executed'}},
            {'set_fact': {'bind_deployment_health_completed': True}, 'tags': ['deploy']}]))

    def entry(self, variables=None, check=False, tags=None, expected_error=None):
        tasks = [self.role_task('main.yml')]
        output = self.run(tasks, dict(bind_managed_zones=[], **(variables or {})), check=check, tags=tags, success=expected_error is None)
        if expected_error:
            assert expected_error in output, output
        return output

    def hibernate(self, variables=None, check=False, tags='hibernate', success=True):
        """Select the real opt-in task tags rather than inheriting always."""
        task = self.role_task('main.yml')
        task['tags'] = ['always']
        return self.run([task], dict({'hibernate_deployment': 'yes'}, **(variables or {})),
                        check=check, tags=tags, success=success)


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('value', [None, False, 'false', 'no', True, 'true', 'yes'])
def test_successful_entry_creates_seal_only_when_enabled(tmp_path, native, value):
    lab = DeploymentLab(tmp_path, native)
    lab.entry({} if value is None else {'hibernate_deployment': value})
    seal = lab.state / '.deployment.lock'
    assert seal.exists() is (value in [True, 'true', 'yes'])
    assert lab.marker.is_file()
    assert not (lab.state / '.operation.lock').exists()
    if seal.exists():
        data = json.loads(seal.read_text())
        expected = hashlib.sha256(json.dumps(data['payload'], sort_keys=True).encode()).hexdigest()
        assert data['sha256'] == expected
        assert data['payload']['files'][str(lab.lab.root / 'etc/named.conf')] == hashlib.sha256((lab.lab.root / 'etc/named.conf').read_bytes()).hexdigest()
        assert seal.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('value', [False, True])
def test_persisted_seal_blocks_even_disabled_setting_and_preserves_manual_data(tmp_path, value):
    lab = DeploymentLab(tmp_path)
    lab.entry({'hibernate_deployment': True})
    seal = (lab.state / '.deployment.lock').read_bytes()
    lab.marker.write_text('operator change')
    lab.entry({'hibernate_deployment': value}, expected_error='completed deployment seal')
    assert lab.marker.read_text() == 'operator change'
    assert (lab.state / '.deployment.lock').read_bytes() == seal
    assert not (lab.state / '.operation.lock').exists()
    # Management reads remain available on the sealed host.
    lab.run([lab.role_task('tasks.d/manage/main.yml')],
            {'bind_manage': {'resource': 'option', 'action': 'read', 'name': 'minimal-responses'}})
    assert (lab.state / '.deployment.lock').read_bytes() == seal


@pytest.mark.parametrize('enabled', [False, True])
def test_check_mode_neither_creates_seal_nor_acquires_operation_lease(tmp_path, enabled):
    lab = DeploymentLab(tmp_path)
    lab.entry({'hibernate_deployment': enabled}, check=True)
    assert not lab.state.exists() and not lab.marker.exists()


def test_failed_workload_is_not_sealed_and_releases_only_owned_lease(tmp_path):
    lab = DeploymentLab(tmp_path, body=[{'fail': {'msg': 'simulated provisioning failure'}}])
    lab.entry({'hibernate_deployment': True}, expected_error='simulated provisioning failure')
    assert not (lab.state / '.deployment.lock').exists()
    assert not (lab.state / '.operation.lock').exists()


@pytest.mark.parametrize('tags', ['routes', 'routes-recover', 'backup'])
def test_auxiliary_workflows_refuse_a_busy_shared_lease(tmp_path, tags):
    lab = DeploymentLab(tmp_path)
    lab.state.mkdir(parents=True, mode=0o700)
    lease = lab.state / '.operation.lock'
    lease.mkdir(mode=0o700)
    (lease / 'operator-evidence').write_text('preserve')
    # The dynamic include must carry the selected tags into the actual entry.
    task = lab.role_task('main.yml')
    task['include_role']['apply'] = {'tags': ['always']}
    task['tags'] = ['always']
    output = lab.run([task], tags=tags, success=False)
    assert 'non-zero return code' in output and 'File exists' in output
    assert (lease / 'operator-evidence').read_text() == 'preserve'
    assert not lab.marker.exists()


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('kind', ['directory', 'regular file', 'symlink', 'special file'])
def test_failed_acquisition_reports_existing_path_without_claiming_or_changing_it(tmp_path, native, kind):
    """An occupied lease must stop provisioning, even for non-directory paths."""
    lab = DeploymentLab(tmp_path, native)
    lab.state.mkdir(parents=True, mode=0o700)
    lease = lab.state / '.operation.lock'
    evidence = lab.state / 'operator-evidence'
    evidence.write_bytes(b'preserve operator data')
    if kind == 'directory':
        lease.mkdir(mode=0o700)
        evidence = lease / 'operator-evidence'
        evidence.write_bytes(b'preserve operator data')
    elif kind == 'regular file':
        lease.write_bytes(b'preserve existing file')
        lease.chmod(0o600)
    elif kind == 'symlink':
        lease.symlink_to(evidence)
    else:
        os.mkfifo(str(lease), 0o600)
    original = lease.lstat()
    output = lab.entry(expected_error='Cannot acquire')
    assert 'Path observed after failure: ' + kind + '.' in output
    assert 'rc=1;' in output and 'File exists' in output
    assert 'No lease ownership was granted' in output
    assert 'Consider using the file module' not in output
    current = lease.lstat()
    assert (current.st_dev, current.st_ino, current.st_mode, current.st_mtime_ns) == (
        original.st_dev, original.st_ino, original.st_mode, original.st_mtime_ns)
    assert evidence.read_bytes() == b'preserve operator data'
    if kind == 'regular file':
        assert lease.read_bytes() == b'preserve existing file'
    assert not lab.marker.exists() and not (lab.state / '.deployment.lock').exists()


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('diagnostic', ['Permission denied', 'Read-only file system'])
def test_failed_acquisition_preserves_os_error_instead_of_claiming_a_busy_lease(tmp_path, native, diagnostic):
    """Inject an OS failure without requiring a writable host or real permissions change."""
    lab = DeploymentLab(tmp_path, native)
    command = lab.bin / 'failing-mkdir'
    command.write_text('#!/bin/sh\nprintf "%s\\n" "' + diagnostic + '" >&2\nexit 1\n')
    command.chmod(0o700)
    path = lab.role / 'tasks/tasks.d/deploy/guard.yml'
    tasks = yaml.safe_load(path.read_text())
    acquisition = next(task for task in tasks if task['name'].startswith('Acquire the shared'))
    acquisition['block'][0]['ansible.builtin.command']['argv'][0] = str(command)
    path.write_text(yaml.safe_dump(tasks))
    output = lab.entry(expected_error='Cannot acquire')
    assert 'rc=1;' in output and 'stderr=' + diagnostic + ';' in output
    assert 'Path observed after failure: absent.' in output
    assert not lab.marker.exists() and not (lab.state / '.operation.lock').exists()
    assert not (lab.state / '.deployment.lock').exists()


@pytest.mark.parametrize('native', [False, True])
@pytest.mark.parametrize('scenario', ['existing', 'created-between-observations', 'system-error'])
def test_acquisition_evidence_distinguishes_absence_from_a_competing_creation(tmp_path, native, scenario):
    """Report observations on the Ansible target without asserting a stale owner."""
    lab = DeploymentLab(tmp_path, native)
    lease = lab.state / '.operation.lock'
    path = lab.role / 'tasks/tasks.d/deploy/guard.yml'
    tasks = yaml.safe_load(path.read_text())
    index = next(i for i, task in enumerate(tasks) if task['name'].startswith('Acquire the shared'))
    if scenario == 'existing':
        lab.state.mkdir(parents=True, mode=0o700)
        lease.mkdir(mode=0o700)
        (lease / 'operator-evidence').write_text('preserve')
    elif scenario == 'created-between-observations':
        tasks.insert(index, {'file': {'path': str(lease), 'state': 'directory', 'mode': '0700'}})
    else:
        command = lab.bin / 'failing-mkdir'
        command.write_text('#!/bin/sh\necho "Read-only file system" >&2\nexit 1\n')
        command.chmod(0o700)
        tasks[index]['block'][0]['ansible.builtin.command']['argv'][0] = str(command)
    path.write_text(yaml.safe_dump(tasks))
    output = lab.entry(expected_error='Cannot acquire')
    assert 'Show acquisition evidence from this exact Ansible target and operation' in output
    assert '"inventory_host": "localhost"' in output
    assert '"connection_target": "localhost"' in output
    assert '"command": [' in output and '"stderr": ' in output
    assert '"exists_before": ' + ('true' if scenario == 'existing' else 'false') in output
    assert '"exists_after": ' + ('false' if scenario == 'system-error' else 'true') in output
    assert 'the requested operation has stopped' in output
    assert 'deployment has stopped' not in output
    if scenario == 'system-error':
        assert 'does not establish an abandoned lease' in output and not lease.exists()
    else:
        assert lease.is_dir()
        if scenario == 'existing':
            assert (lease / 'operator-evidence').read_text() == 'preserve'
    assert not lab.marker.exists() and not (lab.state / '.deployment.lock').exists()


@pytest.mark.parametrize('replacement', [False, True])
def test_release_never_recursively_deletes_a_changed_or_nonempty_lease(tmp_path, replacement):
    lab = DeploymentLab(tmp_path)
    lease = lab.state / '.operation.lock'
    if replacement:
        body = [{'command': {'argv': ['mv', str(lease), str(lab.state / 'original-lease')]}},
                {'file': {'path': str(lease), 'state': 'directory', 'mode': '0700'}}]
        error = 'ownership changed'
    else:
        body, error = [], 'non-zero return code'
    body.append({'copy': {'dest': str(lease / 'operator-evidence'), 'content': 'preserve'}})
    lab.body.write_text(yaml.safe_dump(body))
    lab.entry(expected_error=error)
    assert (lease / 'operator-evidence').read_text() == 'preserve'
    if replacement:
        assert (lab.state / 'original-lease').is_dir()


def test_seal_created_just_before_acquisition_is_rechecked_and_lease_released(tmp_path):
    lab = DeploymentLab(tmp_path)
    # Inject a deterministic competing completion after the first stat check.
    path = lab.role / 'tasks/tasks.d/deploy/guard.yml'
    tasks = yaml.safe_load(path.read_text())
    index = next(i for i, task in enumerate(tasks) if task['name'].startswith('Acquire the shared'))
    tasks.insert(index, {'copy': {'dest': str(lab.state / '.deployment.lock'), 'content': 'other completed deployment', 'mode': '0600'}})
    path.write_text(yaml.safe_dump(tasks))
    lab.entry({'hibernate_deployment': True}, expected_error='after operation lease acquisition')
    assert not lab.marker.exists()
    assert (lab.state / '.deployment.lock').read_text() == 'other completed deployment'
    assert not (lab.state / '.operation.lock').exists()


def test_failed_recovery_retains_operation_lease_and_refuses_the_next_invocation(tmp_path):
    lab = DeploymentLab(tmp_path, body=[
        {'set_fact': {'bind_deployment_retain_lease': True}},
        {'fail': {'msg': 'simulated rollback failure'}}])
    lab.entry({'hibernate_deployment': True}, expected_error='recovery is incomplete')
    lease = lab.state / '.operation.lock'
    assert lease.is_dir() and not (lab.state / '.deployment.lock').exists()
    original = lease.stat().st_ino
    lab.entry(expected_error='non-zero return code')
    assert lease.stat().st_ino == original and not lab.marker.exists()


def test_partial_tag_never_generates_a_seal_even_when_enabled(tmp_path):
    lab = DeploymentLab(tmp_path)
    task = lab.role_task('main.yml')
    task['include_role']['apply'] = {'tags': ['always']}
    task['tags'] = ['always']
    lab.run([task], {'hibernate_deployment': True}, tags='configure')
    assert not (lab.state / '.deployment.lock').exists()
    assert not (lab.state / '.operation.lock').exists()


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'public', 'directory', 'fifo'])
def test_unsafe_existing_seal_is_refused_without_repair_or_provisioning(tmp_path, kind):
    lab = DeploymentLab(tmp_path)
    lab.state.mkdir(parents=True, mode=0o700)
    seal = lab.state / '.deployment.lock'
    if kind == 'directory':
        seal.mkdir(mode=0o600)
    elif kind == 'fifo':
        os.mkfifo(str(seal), 0o600)
    else:
        target = lab.state / 'operator-data'
        target.write_bytes(b'preserve operator data')
        target.chmod(0o600)
        if kind == 'symlink':
            seal.symlink_to(target)
        elif kind == 'hardlink':
            os.link(str(target), str(seal))
        else:
            seal.write_text('old seal')
            seal.chmod(0o644)
    identity = seal.lstat()
    lab.entry({'hibernate_deployment': False}, expected_error='Unsafe deployment seal path')
    assert seal.lstat().st_ino == identity.st_ino
    assert seal.lstat().st_mode == identity.st_mode
    assert not lab.marker.exists() and not (lab.state / '.operation.lock').exists()


@pytest.mark.parametrize('native', [False, True])
def test_hibernate_only_uses_live_config_and_is_idempotent_without_clients_or_zones(tmp_path, native):
    lab = DeploymentLab(tmp_path, native)
    # Keep actual provisioning tasks: undefined inventory-derived loops must
    # never be expanded by this path. No client list or generated TSIG is needed.
    shutil.copyfile(ROOT / 'roles/bind9/tasks/tasks.d/deploy/main.yml', lab.body)
    live = lab.lab.root / 'etc/named.conf'
    live.write_text(live.read_text() + '// manual operator data\n')
    before = live.read_bytes()
    first = lab.hibernate()
    seal = lab.state / '.deployment.lock'
    original = seal.read_bytes(), seal.stat().st_mtime_ns
    payload = json.loads(original[0])['payload']
    assert payload['kind'] == 'hibernate'
    assert payload['files'] == {str(live): hashlib.sha256(before).hexdigest()}
    assert 'Deployment seal created from the live configuration' in first
    second = lab.hibernate()
    assert 'Existing deployment seal preserved' in second
    assert (seal.read_bytes(), seal.stat().st_mtime_ns) == original
    assert live.read_bytes() == before and not lab.marker.exists()
    assert not (lab.state / '.operation.lock').exists()


def test_hibernate_check_mode_only_predicts_the_seal(tmp_path):
    lab = DeploymentLab(tmp_path)
    output = lab.hibernate(check=True)
    assert 'a deployment seal would be created' in output
    assert not lab.state.exists() and not lab.marker.exists()


def test_hibernate_refuses_invalid_live_syntax_without_a_seal_or_deployment(tmp_path):
    lab = DeploymentLab(tmp_path)
    (lab.bin / 'checkconf').write_text('#!/bin/sh\necho sensitive-checker-diagnostic\nexit 1\n')
    output = lab.hibernate(success=False)
    assert 'syntax check failed' in output and 'sensitive-checker-diagnostic' not in output
    assert not (lab.state / '.deployment.lock').exists()
    assert not (lab.state / '.operation.lock').exists() and not lab.marker.exists()


@pytest.mark.parametrize('tags,values', [('hibernate,deploy', {}), ('hibernate,routes', {}),
                                       ('hibernate', {'hibernate_deployment': 'no'})])
def test_hibernate_refuses_mixed_actions_or_disabled_setting_before_state_creation(tmp_path, tags, values):
    lab = DeploymentLab(tmp_path)
    output = lab.hibernate(variables=values, tags=tags, success=False)
    assert 'Use --tags hibernate' in output
    assert not lab.state.exists() and not lab.marker.exists()


@pytest.mark.parametrize('kind', ['fifo', 'symlink', 'hardlink', 'writable', 'empty', 'oversized'])
def test_hibernate_refuses_unsafe_canonical_file_before_native_parsing(tmp_path, kind):
    lab = DeploymentLab(tmp_path)
    live = lab.lab.root / 'etc/named.conf'
    if kind in ('fifo', 'symlink', 'hardlink'):
        live.unlink()
        if kind == 'fifo':
            os.mkfifo(str(live), 0o600)
        else:
            outside = lab.lab.root / 'operator-file'
            outside.write_text('preserve')
            if kind == 'symlink':
                live.symlink_to(outside)
            else:
                os.link(str(outside), str(live))
    elif kind == 'writable':
        live.chmod(0o660)
    elif kind == 'empty':
        live.write_bytes(b'')
    else:
        with live.open('wb') as stream:
            stream.truncate(16777217)
    parser_marker = lab.lab.root / 'parser-was-called'
    (lab.bin / 'checkconf').write_text('#!/usr/bin/python3\nfrom pathlib import Path\nPath(' + repr(str(parser_marker)) + ').write_text("called")\n')
    observed = live.lstat()
    output = lab.hibernate(success=False)
    assert 'safety or syntax check failed' in output
    assert not parser_marker.exists() and live.lstat().st_ino == observed.st_ino
    assert not (lab.state / '.deployment.lock').exists() and not (lab.state / '.operation.lock').exists()


def test_hibernate_times_out_a_blocked_native_parser_without_a_seal(tmp_path):
    lab = DeploymentLab(tmp_path)
    (lab.bin / 'checkconf').write_text('#!/bin/sh\nsleep 10\n')
    path = lab.role / 'tasks/tasks.d/deploy/hibernate.yml'
    path.write_text(path.read_text().replace('--kill-after=5s', '--kill-after=0.2s').replace('60s,', '0.2s,'))
    output = lab.hibernate(success=False)
    assert 'exceeded its time limit' in output
    assert not (lab.state / '.deployment.lock').exists() and not (lab.state / '.operation.lock').exists()


def test_partial_configure_failure_runs_actual_recovery_retention_guard(tmp_path):
    lab = DeploymentLab(tmp_path)
    production = yaml.safe_load((ROOT / 'roles/bind9/tasks/tasks.d/deploy/main.yml').read_text())
    retention = next(task['rescue'][0] for task in production if 'rescue' in task)
    lab.body.write_text(yaml.safe_dump([
        {'block': [{'fail': {'msg': 'simulated configuration failure'}, 'tags': ['configure']}],
         'rescue': [retention, {'fail': {'msg': 'partial rollback not qualified'}, 'tags': ['configure']}]}]))
    task = lab.role_task('main.yml')
    task['tags'] = ['always']
    output = lab.run([task], tags='configure', success=False)
    assert 'recovery is incomplete' in output
    assert (lab.state / '.operation.lock').is_dir()
    assert not (lab.state / '.deployment.lock').exists()


def test_standalone_routes_failure_with_pending_journal_retains_the_common_lease(tmp_path):
    lab = DeploymentLab(tmp_path, body=[
        {'set_fact': {'bind_front_route_transaction_pending': True}, 'tags': ['routes']},
        {'fail': {'msg': 'simulated incomplete FRONT rollback'}, 'tags': ['routes']}])
    task = lab.role_task('main.yml')
    task['tags'] = ['always']
    output = lab.run([task], tags='routes', success=False)
    assert 'recovery is incomplete' in output
    assert (lab.state / '.operation.lock').is_dir()
    assert not (lab.state / '.deployment.lock').exists()

"""Test the explicitly unlocked mode without removing another operation's data."""
import pytest

from test_deployment_lock_ansible import DeploymentLab
from test_management_ansible import Runner, pytestmark as inherited_pytestmark

pytestmark = inherited_pytestmark


class ExistingLease:
    """Represent an operator's prior directory and evidence, never test-owned cleanup."""

    def __init__(self, state):
        state.mkdir(parents=True, mode=0o700)
        self.path = state / '.operation.lock'
        self.path.mkdir(mode=0o700)
        self.evidence = self.path / 'operator-evidence'
        self.evidence.write_bytes(b'preserve operator data')
        self.identity = self.path.stat().st_ino

    def assert_preserved(self):
        assert self.path.stat().st_ino == self.identity
        assert self.evidence.read_bytes() == b'preserve operator data'


@pytest.mark.parametrize('tags', ['routes', 'routes-recover', 'backup', 'deploy'])
def test_regular_operations_ignore_existing_shared_lease(tmp_path, tags):
    lab = DeploymentLab(tmp_path)
    lease = ExistingLease(lab.state)
    task = lab.role_task('main.yml')
    task['include_role']['apply'] = {'tags': ['always']}
    task['tags'] = ['always']
    lab.run([task], {'bind_operation_lock_enabled': False, 'bind_managed_zones': [],
                    'hibernate_deployment': tags != 'deploy'}, tags=tags)
    assert lab.marker.exists()
    lease.assert_preserved()
    assert not (lab.state / '.deployment.lock').exists()


@pytest.mark.parametrize('tags', ['hibernate', 'deploy'])
def test_hibernation_still_refuses_an_existing_operation_lease(tmp_path, tags):
    lab = DeploymentLab(tmp_path)
    lease = ExistingLease(lab.state)
    if tags == 'hibernate':
        output = lab.hibernate({'bind_operation_lock_enabled': False}, success=False)
    else:
        # Dynamic includes must propagate the selected tag into the real role;
        # otherwise Ansible selects no tasks and the test never reaches the guard.
        task = lab.role_task('main.yml')
        task['include_role']['apply'] = {'tags': ['always']}
        task['tags'] = ['always']
        output = lab.run([task], {'bind_operation_lock_enabled': False,
                                 'hibernate_deployment': True}, tags='deploy', success=False)
    assert 'File exists' in output
    assert not lab.marker.exists() and not (lab.state / '.deployment.lock').exists()
    lease.assert_preserved()


def test_unlocked_deployment_still_respects_an_existing_completion_seal(tmp_path):
    lab = DeploymentLab(tmp_path)
    lease = ExistingLease(lab.state)
    seal = lab.state / '.deployment.lock'
    seal.write_bytes(b'operator completion seal')
    seal.chmod(0o600)
    lab.entry({'bind_operation_lock_enabled': False}, expected_error='completed deployment seal')
    assert seal.read_bytes() == b'operator completion seal' and not lab.marker.exists()
    lease.assert_preserved()


def test_clean_hibernation_keeps_its_lease_until_the_seal_is_created(tmp_path):
    lab = DeploymentLab(tmp_path)
    lab.hibernate({'bind_operation_lock_enabled': False})
    assert (lab.state / '.deployment.lock').is_file()
    assert not (lab.state / '.operation.lock').exists() and not lab.marker.exists()


@pytest.mark.parametrize('native', [False, True])
def test_unlocked_management_preserves_existing_lease_and_checksum_guards(tmp_path, native):
    runner = Runner(tmp_path, native)
    lease = ExistingLease(runner.lab.root / 'var/lib/Bind9-Ansible')
    request = dict(resource='option', action='read', name='minimal-responses')
    tasks = [runner.role_task('tasks.d/manage/main.yml'),
             {'set_fact': {'bind_manage': dict(request, action='update', value='no',
                                              expected_checksum='{{ bind_management_result.checksum }}')}},
             runner.role_task('tasks.d/manage/main.yml'),
             {'assert': {'that': ['bind_management_result.changed']}},
             {'block': [runner.role_task('tasks.d/manage/main.yml'),
                        {'fail': {'msg': 'stale checksum unexpectedly accepted'}}],
              'rescue': [{'assert': {'that': ["'Checksum missing or changed' in ansible_failed_result.msg"]}}]}]
    runner.run(tasks, {'bind_operation_lock_enabled': False, 'bind_manage': request})
    assert 'minimal-responses no;' in (runner.lab.root / 'etc/named.conf').read_text()
    lease.assert_preserved()

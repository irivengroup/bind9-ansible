"""Keep the optional persistent deployment lock independent of the host lease."""
from pathlib import Path

import pytest
import yaml

from route_task_expressions import TaskExpressions

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / 'roles/bind9/tasks/tasks.d'


@pytest.mark.parametrize('value', [False, True, 'yes', 'no', 'true', 'false'])
def test_legacy_ansible_accepts_only_documented_lock_values(value):
    tasks = yaml.safe_load((TASKS / 'deploy/guard.yml').read_text())[:2]
    result = TaskExpressions({'hibernate_deployment': value}).run('deploy/guard.yml', tasks=tasks)
    assert result['bind_deployment_lease_acquired'] is False
    assert result['bind_deployment_health_completed'] is False


@pytest.mark.parametrize('value', [0, 1, 2, None, '', 'YES', [], {}])
def test_invalid_lock_never_reaches_target_observation_or_writes(value):
    tasks = yaml.safe_load((TASKS / 'deploy/guard.yml').read_text())[:2]
    with pytest.raises(ValueError):
        TaskExpressions({'hibernate_deployment': value}).run('deploy/guard.yml', tasks=tasks)


@pytest.mark.parametrize('name', ['force', 'deployment_lock', 'deployment_locked'])
@pytest.mark.parametrize('value', [False, True, 'yes', 'no'])
def test_removed_names_are_rejected_instead_of_silently_ignored(name, value):
    tasks = yaml.safe_load((TASKS / 'deploy/guard.yml').read_text())[:2]
    with pytest.raises(ValueError):
        TaskExpressions({name: value}).run('deploy/guard.yml', tasks=tasks)


def test_default_does_not_create_or_bypass_a_completion_seal():
    defaults = yaml.safe_load((ROOT / 'roles/bind9/defaults/main.yml').read_text())
    assert defaults['hibernate_deployment'] is False and 'force' not in defaults
    guard = yaml.safe_load((TASKS / 'deploy/guard.yml').read_text())
    checks = [task for task in guard if 'completion seal' in task['name'] and 'ansible.builtin.assert' in task]
    assert len(checks) == 1
    for value in [False, True]:
        scope = {'hibernate_deployment': value, 'bind_deployment_seal_applies': True,
                 'bind_deployment_seal_stat': {'results': [{}, {'stat': {'exists': True}}]}}
        assert not TaskExpressions(scope).condition(checks[0]['ansible.builtin.assert']['that'], scope)


def test_success_marker_follows_every_health_probe_and_seal_requires_it():
    tasks = yaml.safe_load((TASKS / 'deploy/health.yml').read_text())
    assert tasks[-1]['ansible.builtin.set_fact'] == {'bind_deployment_health_completed': True}
    seal = yaml.safe_load((TASKS / 'deploy/seal.yml').read_text())
    assert 'bind_deployment_health_completed' in str(seal[0]['ansible.builtin.assert']['that'])
    entry = yaml.safe_load((ROOT / 'roles/bind9/tasks/main.yml').read_text())
    assert entry[1]['always'][0]['when'] == 'bind_deployment_lease_acquired | default(false) | bool'
    assert entry[1]['block'][-1]['when'] == "('all' in ansible_run_tags or 'deploy' in ansible_run_tags) and not ansible_check_mode and (hibernate_deployment | bool)"
    assert 'routes-recover' in entry[1]['block'][0]['when']
    assert 'backup' in entry[1]['block'][0]['when']
    assert 'routes' in entry[1]['block'][0]['when']


@pytest.mark.parametrize('tags,expected', [(['never'], False), (['all'], False),
    (['preflight'], False), (['routes'], False), (['routes-recover'], True),
    (['never', 'routes-recover'], True)])
def test_recovery_requires_its_explicit_action_even_when_reserved_tags_select_it(tags, expected):
    # Ansible's explicit `never` selector selects every never-tagged task.
    # Evaluate the original task's tags and guard without invoking the device.
    from ansible.parsing.dataloader import DataLoader
    from ansible.playbook.task import Task
    from ansible.template import Templar
    loader = DataLoader()
    tasks = loader.load_from_file(str(TASKS / 'deploy/main.yml'))
    mapping = next(task for task in tasks if task.get('name') ==
                   'Recover an explicitly requested interrupted FRONT operation')
    task = Task.load(mapping, loader=loader)
    scope = {'ansible_run_tags': tags}
    assert task.evaluate_tags(['never'], [], scope) is True
    assert task.evaluate_conditional(Templar(loader=loader, variables=scope), scope) is expected


@pytest.mark.parametrize('tags,deferred', [
    (['all'], True), (['deploy'], True), (['routes'], False), (['configure'], False),
])
def test_deployment_selection_resets_stale_route_ownership_and_defers_only_full_runs(tags, deferred):
    # Evaluate the original set_fact fields with their native source positions.
    # This supplies data-field evidence, never task-dispatch or network credit.
    task, = [item for item in TaskExpressions.load('deploy/main.yml') if item.get('name') ==
             'Determine whether routes belong to a complete DNS deployment']
    result = TaskExpressions(dict(ansible_run_tags=tags, bind_front_route_transaction_pending=True,
        bind_front_route_rollback_attempted=True)).run('', tasks=[task])
    assert result['bind_front_routes_defer_commit'] is deferred
    assert result['bind_front_route_transaction_pending'] is False
    assert result['bind_front_route_rollback_attempted'] is False


@pytest.mark.parametrize('tags,runtime_incomplete,pending,expected', [
    (['all'], False, False, 'named deployment failed; managed files restored. Inspect service and logs.'),
    (['deploy'], True, False, 'named deployment failed; runtime recovery incomplete. DNS backup restoration was attempted; services remain stopped and operation ownership is retained. Inspect the preserved runtime paths and snapshot before manual recovery.'),
    (['configure'], False, False, 'named partial deployment failed; automatic DNS restoration was not selected. Inspect the live configuration and backups before retrying.'),
    (['configure'], True, True, 'named partial deployment failed; automatic DNS restoration was not selected. Inspect the live configuration and backups before retrying. FRONT rollback incomplete: synthetic rollback refusal. Recovery journal retained.'),
    (['deploy'], False, True, 'named deployment failed; managed files restored. Inspect service and logs. FRONT rollback incomplete: synthetic rollback refusal. Recovery journal retained.'),
])
def test_fatal_deployment_diagnostic_distinguishes_partial_runs_and_incomplete_recovery(
        tags, runtime_incomplete, pending, expected):
    from ansible.parsing.dataloader import DataLoader
    from ansible.template import Templar
    block, = [item for item in TaskExpressions.load('deploy/main.yml') if item.get('name') ==
              'Apply DNS changes with rollback on failure']
    failure, = [item for item in block['rescue'] if item.get('name') == 'Stop deployment after rollback']
    values = dict(ansible_run_tags=tags, bind_runtime_restore_incomplete=runtime_incomplete,
        bind_front_route_transaction_pending=pending, bind_front_route_rollback_error='synthetic rollback refusal')
    message = Templar(loader=DataLoader(), variables=values).template(failure['ansible.builtin.fail']['msg'])
    assert message.strip() == expected

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

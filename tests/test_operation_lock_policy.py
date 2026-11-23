"""Keep the temporary unlocked mode separate from persistent hibernation."""
from pathlib import Path

import pytest
import yaml

from route_task_expressions import TaskExpressions

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('enabled,hibernate,tags,required', [
    (False, False, ['all'], False), (False, False, ['deploy'], False),
    (False, False, ['routes'], False), (False, True, ['routes'], False),
    (False, True, ['routes-recover'], False), (False, True, ['backup'], False),
    (False, True, ['all'], True), (False, True, ['deploy'], True),
    (False, True, ['hibernate'], True), (False, 'yes', ['hibernate'], True),
    (True, False, ['routes'], True), (True, False, ['deploy'], True),
    (True, False, ['routes-recover'], True), (True, False, ['backup'], True),
])
def test_role_lease_policy_preserves_every_hibernation_path(enabled, hibernate, tags, required):
    tasks = yaml.safe_load((ROOT / 'roles/bind9/tasks/tasks.d/deploy/guard.yml').read_text())[:3]
    result = TaskExpressions({'bind_operation_lock_enabled': enabled,
                              'hibernate_deployment': hibernate, 'ansible_run_tags': tags}).run(
                                  'deploy/guard.yml', tasks=tasks)
    assert result['bind_deployment_lease_required'] is required


@pytest.mark.parametrize('value', ['false', 'true', 'no', '', None, 0, 1, [], {}])
def test_lock_policy_rejects_untyped_values(value):
    tasks = yaml.safe_load((ROOT / 'roles/bind9/tasks/tasks.d/deploy/guard.yml').read_text())[:3]
    with pytest.raises(ValueError):
        TaskExpressions({'bind_operation_lock_enabled': value}).run('deploy/guard.yml', tasks=tasks)


def test_temporary_role_default_is_disabled_and_does_not_remove_hibernation():
    defaults = yaml.safe_load((ROOT / 'roles/bind9/defaults/main.yml').read_text())
    assert defaults['bind_operation_lock_enabled'] is False
    assert defaults['hibernate_deployment'] is False

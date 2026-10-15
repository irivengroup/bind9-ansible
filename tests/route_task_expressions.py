"""Evaluate real task expressions with Ansible's templar, without network writes.

This test adapter supplies observations and executes only imports, assertions
and set_fact; the separate workflow tests execute the actual Ansible runner.
"""
from pathlib import Path

from ansible.errors import AnsibleError
from ansible.parsing.dataloader import DataLoader
from ansible.playbook.conditional import Conditional
from ansible.template import Templar
import yaml


class TaskExpressions:
    """Execute the read-only expression subset; never duplicate routing logic."""

    TASKS = Path(__file__).resolve().parents[1] / 'roles/bind9/tasks/tasks.d'

    def __init__(self, variables):
        self.variables = dict(variables)
        self.loader = DataLoader()
        # Load the role's real validators for configuration-contract tests too.
        from ansible.plugins.loader import filter_loader
        filter_loader.add_directory(str(self.TASKS.parents[1] / 'filter_plugins'))

    def render(self, value, scope):
        return Templar(loader=self.loader, variables=scope).template(value)

    def condition(self, values, scope):
        condition = Conditional(loader=self.loader)
        condition.when = values
        return condition.evaluate_conditional(Templar(loader=self.loader, variables=scope), scope)

    def run(self, filename, tasks=None, inherited=None):
        filename = filename.replace('{{ role_path }}/tasks/tasks.d/', '')
        try:
            for task in tasks if tasks is not None else yaml.safe_load((self.TASKS / filename).read_text()):
                scope = dict(self.variables, **(inherited or {}), **task.get('vars', {}))
                items = self.render(task['loop'], scope) if 'loop' in task else [None]
                for item in items:
                    local = dict(scope, item=item) if 'loop' in task else scope
                    if not self.condition(task.get('when', []), local):
                        continue
                    if 'ansible.builtin.import_tasks' in task:
                        nested = dict(inherited or {}, **task.get('vars', {}))
                        self.run(task['ansible.builtin.import_tasks'], inherited=nested)
                    elif 'ansible.builtin.set_fact' in task:
                        for name, value in task['ansible.builtin.set_fact'].items():
                            self.variables[name] = self.render(value, local)
                    elif 'ansible.builtin.assert' in task:
                        if not self.condition(task['ansible.builtin.assert']['that'], local):
                            raise ValueError(task['name'])
                    else:
                        raise ValueError('Test adapter refuses side effects: ' + task['name'])
        except (AnsibleError, KeyError, TypeError, AttributeError) as error:
            raise ValueError(str(error)) from error
        return self.variables

"""Protect private file logging, query silence and persistent SELinux rule selection."""
from pathlib import Path

import pytest
import yaml

pytest.importorskip('ansible')
from route_task_expressions import TaskExpressions

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / 'roles/bind9/tasks/tasks.d'


class TestLoggingContract:
    """Test the public operational contract and evaluate the real rule expressions."""

    def test_logs_are_created_privately_without_changing_existing_contents_or_times(self):
        tasks = yaml.safe_load((TASKS / 'deploy/directories.yml').read_text())
        assert {'path': '/var/log/named', 'owner': 'named', 'mode': '0750'} in tasks[0]['loop']
        files = tasks[1]
        assert files['loop'] == ['/var/log/named/default.log', '/var/log/named/security.log', '/var/log/named/xfer.log']
        module = files['ansible.builtin.file']
        assert module['owner'] == module['group'] == 'named'
        assert module['mode'] == '0640' and module['state'] == 'touch'
        assert module['access_time'] == module['modification_time'] == 'preserve'
        assert module['follow'] is False

    @pytest.mark.parametrize('rule,operation,changed', [
        ('', '-a', True),
        ('/var/log/named.* regular file system_u:object_r:named_log_t:s0', '-a', True),
        ('/var/log/named(/.*)? all files system_u:object_r:var_log_t:s0', '-m', True),
        ('/var/log/named(/.*)? all files system_u:object_r:named_log_t:s0', '-m', False),
        ('/var/log/named-extra(/.*)? all files system_u:object_r:named_log_t:s0', '-a', True),
        ('/var/log/named(/.*)? regular file system_u:object_r:named_log_t:s0', '-a', True),
    ])
    def test_exact_all_files_rule_is_added_corrected_or_reused(self, rule, operation, changed):
        task = next(t for t in yaml.safe_load((TASKS / 'deploy/security.yml').read_text())
                    if t['name'].startswith('Persist the SELinux log context'))
        scope = {'ansible_selinux': {'status': 'enabled'},
                 'bind_named_log_contexts': {'stdout_lines': [rule]}}
        evaluator = TaskExpressions(scope)
        scope.update({name: evaluator.render(value, scope) for name, value in task['vars'].items()})
        assert evaluator.condition(task['when'], scope) is changed
        argv = evaluator.render(task['ansible.builtin.command']['argv'], scope)
        assert argv == ['semanage', 'fcontext', operation, '-f', 'a', '-t', 'named_log_t', '/var/log/named(/.*)?']

    @pytest.mark.parametrize('facts', [{}, {'ansible_selinux': {'status': 'disabled'}}])
    def test_disabled_selinux_skips_new_policy_commands_and_label_checks(self, facts):
        tasks = yaml.safe_load((TASKS / 'deploy/security.yml').read_text())
        for task in tasks:
            if 'bind_named_log_' in str(task) and 'when' in task:
                when = task['when'] if isinstance(task['when'], list) else [task['when']]
                assert TaskExpressions(facts).condition(when, facts) is False

    @pytest.mark.parametrize('wrong', [False, True])
    def test_post_restore_check_rejects_incorrect_labels(self, wrong):
        tasks = yaml.safe_load((TASKS / 'deploy/security.yml').read_text())
        names = [task['name'] for task in tasks]
        assert names.index('Restore SELinux contexts') < names.index('Inspect SELinux labels on the BIND log directory and files')
        task = next(t for t in tasks if t['name'] == 'Require named_log_t on all managed log paths')
        labels = ['system_u:object_r:named_log_t:s0 path'] * 4
        if wrong:
            labels[2] = 'system_u:object_r:var_log_t:s0 path'
        scope = {'bind_named_log_labels': {'stdout_lines': labels}}
        assert TaskExpressions(scope).condition(task['ansible.builtin.assert']['that'], scope) is not wrong

    def test_log_paths_are_rejected_if_they_are_symlinks(self):
        paths = yaml.safe_load((TASKS / 'path_safety.yml').read_text())[0]['loop']
        for suffix in ('', '/default.log', '/security.log', '/xfer.log'):
            assert '/var/log/named' + suffix in paths

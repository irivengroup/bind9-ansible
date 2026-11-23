"""Observe native static loading, play scheduling and meta action outcomes."""
import json
import os
from pathlib import Path

import pytest
import yaml

from ansible.executor.play_iterator import PlayIterator
from ansible import context
from ansible.utils.vars import load_options_vars
import native_ansible_runner
from native_ansible_runner import NativeTaskRun, NativePlayRun
from test_ansible_coverage_semantics import (AnsibleCollector, InstrumentationError, fixture_project,
    isolate_fixture_observer, pytestmark as native_profile_marks)

pytestmark = native_profile_marks
isolate_fixture_observer = isolate_fixture_observer


@pytest.mark.skipif(os.name != 'posix', reason='Requires native Ansible workers')
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('mode', ['tasks', 'role', 'declarative_role', 'meta'])
def test_native_import_play_role_and_meta_events_preserve_outcomes(tmp_path, monkeypatch, enabled, mode):
    path = fixture_project(tmp_path)
    role = tmp_path / 'roles/bind9'
    (path.parent / 'child.yml').write_text('- name: Child\n  ansible.builtin.debug:\n    msg: "{{ label }}"\n', encoding='utf-8')
    path.write_text('- name: Static import\n  ansible.builtin.import_tasks: child.yml\n  when: enabled\n', encoding='utf-8')
    play = dict(name='Native entry', hosts='localhost', gather_facts=False)
    if mode == 'declarative_role':
        play['roles'] = [{'role': str(role), 'when': 'enabled'}]
    elif mode == 'role':
        play['tasks'] = [{'name': 'Static role', 'ansible.builtin.import_role': {'name': str(role)}}]
    elif mode == 'meta':
        play['tasks'] = [{'name': 'Native conditional meta', 'ansible.builtin.meta': 'end_play', 'when': 'enabled'},
                         {'name': 'After meta', 'ansible.builtin.debug': {'msg': 'after'}}]
    else:
        play['tasks'] = [{'name': 'Static tasks', 'ansible.builtin.import_tasks': str(path)}]
    (tmp_path / 'playbooks/main.yml').write_text(yaml.safe_dump([play], sort_keys=False), encoding='utf-8')
    variables = dict(enabled=enabled, label='synthetic')
    expected_status, expected = NativePlayRun(tmp_path, variables).run()
    output = tmp_path / 'workers'
    monkeypatch.setenv('BIND9_CONDITION_COVERAGE', str(output))
    with AnsibleCollector(tmp_path).observing() as collector:
        status, callbacks = NativePlayRun(tmp_path, variables).run()
    collector.save(output)
    collector.merge(output)
    data = json.loads((output / 'ansible-merged.json').read_text(encoding='utf-8'))
    assert status == expected_status == 0
    assert (callbacks.ok, callbacks.failed, callbacks.skipped) == (expected.ok, expected.failed, expected.skipped)
    assert len(data['entered_plays']) == 1
    assert len(data['entered_imports']) == (0 if mode == 'meta' else (1 if mode == 'declarative_role' else 2))
    assert len(data['entered_roles']) == int(mode == 'declarative_role')
    if mode == 'meta':
        key = next(key for key in data['attempted_tasks'] if key.startswith('playbooks/'))
        assert (key in data['dispatched_tasks']) is enabled
        assert ('After meta' in callbacks.ok) is not enabled
    else:
        assert ('Child' in callbacks.ok) is enabled
        assert any(key.startswith('roles/bind9/tasks/child.yml|') for key in data['dispatched_tasks']) is enabled


@pytest.mark.skipif(os.name != 'posix', reason='Requires native Ansible workers')
def test_uncommitted_native_iterator_peeks_cannot_credit_blocks(tmp_path, monkeypatch):
    path = fixture_project(tmp_path)
    path.write_text('- name: Block\n  block:\n    - ansible.builtin.debug: {msg: body}\n'
                    '  rescue:\n    - ansible.builtin.debug: {msg: recovery}\n', encoding='utf-8')
    original = PlayIterator.__init__
    observed = []
    with AnsibleCollector(tmp_path).observing() as collector:
        def initialize(instance, *args, **kwargs):
            original(instance, *args, **kwargs)
            host = kwargs['inventory'].get_host('localhost')
            for _ in range(3):
                state, task = instance.get_next_task_for_host(host, peek=True)
                assert task is not None
                observed.append(task.name)
                assert not collector.entered_blocks and not collector.block_rescues
        monkeypatch.setattr(PlayIterator, '__init__', initialize)
        status, result = NativeTaskRun('main.yml', {}, root=tmp_path).run()
    assert status == 0 and not result.failed and len(observed) == 3
    assert len(collector.entered_blocks) == 1
    assert {value for _, value in collector.block_rescues} == {False}
    # The central fixture owns this destination; worker output cannot pollute
    # the outer paired run even when this test does not call save()/merge().
    Path(os.environ['BIND9_CONDITION_COVERAGE']).relative_to(tmp_path)


@pytest.mark.parametrize('field', ['entered_blocks', 'block_rescues', 'entered_imports', 'entered_plays', 'entered_roles'])
def test_worker_control_ids_cannot_impersonate_an_action_dispatch(tmp_path, field):
    fixture_project(tmp_path)
    collector = AnsibleCollector(tmp_path)
    value = collector.payload()
    key = 'roles/bind9/tasks/main.yml|' + next(iter(collector.inventory.components['roles/bind9/tasks/main.yml']['tasks']))
    value[field] = [[key, True]] if field == 'block_rescues' else [key]
    path = tmp_path / 'ansible-1.json'
    path.write_text(json.dumps(value), encoding='utf-8')
    with pytest.raises(InstrumentationError):
        collector.combine([path], collector.inventory)


@pytest.mark.skipif(os.name != 'posix', reason='Requires native Ansible workers')
def test_repeated_native_invocations_do_not_share_tag_or_check_mode_caches(tmp_path):
    path = fixture_project(tmp_path)
    path.write_text('- ansible.builtin.set_fact:\n    observed_tags: "{{ ansible_run_tags }}"\n'
                    '    observed_check: "{{ ansible_check_mode }}"\n', encoding='utf-8')
    previous = context.CLIARGS
    previous_options = getattr(load_options_vars, 'options_vars', None)
    for tags, check in [(['all'], False), (['routes'], True), (['hibernate', 'always'], False), (['all'], True)]:
        status, result = NativeTaskRun('main.yml', {}, root=tmp_path, run_tags=tags, check_mode=check).run()
        assert status == 0 and not result.failed
        assert result.facts == dict(observed_tags=tags, observed_check=check)
        assert context.CLIARGS is previous
        assert load_options_vars.options_vars is previous_options


def test_failed_native_initialization_restores_cli_context_and_cache(tmp_path, monkeypatch):
    fixture_project(tmp_path)
    previous = context.CLIARGS
    previous_options = getattr(load_options_vars, 'options_vars', None)
    def fail(*args, **kwargs):
        raise RuntimeError('Injected native inventory initialization failure')
    monkeypatch.setattr(native_ansible_runner, 'InventoryManager', fail)
    with pytest.raises(RuntimeError, match='initialization failure'):
        NativeTaskRun('main.yml', {}, root=tmp_path).run()
    assert context.CLIARGS is previous
    assert load_options_vars.options_vars is previous_options

"""Native iterator recovery must not be inferred from test input or peeks."""
import json
import os

import pytest
import yaml

from native_ansible_runner import NativeTaskRun
from test_ansible_coverage_semantics import (AnsibleCollector, YamlInventory, fixture_project,
    isolate_fixture_observer, pytestmark as native_profile_marks)

pytestmark = native_profile_marks
isolate_fixture_observer = isolate_fixture_observer


@pytest.mark.skipif(os.name != 'posix', reason='Requires native Ansible workers')
@pytest.mark.parametrize('body_ok,rescue_ok,enabled,nested', [
    (True, True, True, False), (False, True, True, False),
    (False, False, True, False), (False, True, False, False),
    (True, True, True, True), (False, True, True, True),
    (False, False, True, True), (False, True, False, True)])
def test_native_block_entry_recovery_and_always_preserve_results(tmp_path, monkeypatch,
                                                               body_ok, rescue_ok, enabled, nested):
    path = fixture_project(tmp_path)
    block = {'name': 'Guarded block', 'when': 'enabled',
             'block': [{'name': 'Body', 'ansible.builtin.assert': {'that': ['body_ok']}}],
             'rescue': [{'name': 'Recovery', 'ansible.builtin.assert': {'that': ['rescue_ok']}}],
             'always': [{'name': 'Always', 'ansible.builtin.debug': {'msg': 'observed'}}]}
    if nested:
        block = {'name': 'Outer block', 'block': [block]}
    path.write_text(yaml.safe_dump([block], sort_keys=False), encoding='utf-8')
    variables = dict(body_ok=body_ok, rescue_ok=rescue_ok, enabled=enabled)
    expected_status, expected = NativeTaskRun('main.yml', variables, root=tmp_path).run()
    output = tmp_path / 'workers'
    monkeypatch.setenv('BIND9_CONDITION_COVERAGE', str(output))
    with AnsibleCollector(tmp_path).observing() as collector:
        status, callbacks = NativeTaskRun('main.yml', variables, root=tmp_path).run()
    collector.save(output)
    collector.merge(output)
    data = json.loads((output / 'ansible-merged.json').read_text(encoding='utf-8'))
    assert status == expected_status == (2 if enabled and not body_ok and not rescue_ok else 0)
    assert (callbacks.ok, callbacks.failed, callbacks.skipped) == (expected.ok, expected.failed, expected.skipped)
    assert len(data['entered_blocks']) == (2 if nested else 1)
    assert {value for _, value in data['block_rescues']} == {bool(enabled and not body_ok)}
    assert len(data['block_rescues']) == 1
    if enabled:
        assert 'Always' in callbacks.ok
        assert ('Recovery' in callbacks.ok) == (not body_ok and rescue_ok)
    else:
        assert not callbacks.ok and not callbacks.failed


@pytest.mark.parametrize('action', [
    {'ansible.builtin.copy': {'dest': '/etc/profile.d/test.sh', 'content': 'case "$PATH" in *) :;; esac'}},
    {'ansible.builtin.copy': {'dest': '/tmp/program', 'content': '#!/bin/bash\ntrue\n'}},
    {'ansible.builtin.shell': 'true'}, {'ansible.builtin.shell': {'cmd': 'true'}}])
def test_embedded_script_execution_is_not_covered_by_copy_or_shell_dispatch(tmp_path, action):
    path = fixture_project(tmp_path)
    path.write_text(yaml.safe_dump([{'name': 'Embedded source', **action}]), encoding='utf-8')
    inventory = YamlInventory(tmp_path)
    name = 'roles/bind9/tasks/main.yml'
    assert len(inventory.components[name]['embedded_scripts']) == 1
    assert not inventory.task_profile_complete(name)


def test_separate_login_source_keeps_its_own_bash_scope_without_implicit_copy_credit():
    from bash_protocol import NativeLoginPathProfile
    from coverage_gate import CoverageGate
    from test_ansible_coverage_semantics import ROOT
    from test_jinja_coverage_semantics import unobserved_parser
    inventory = YamlInventory(ROOT)
    name = 'roles/bind9/tasks/tasks.d/deploy/security.yml'
    profile = NativeLoginPathProfile(ROOT)
    task = yaml.safe_load((ROOT / name).read_text(encoding='utf-8'))[0]['ansible.builtin.copy']
    assert task['src'] == profile.source.name and 'content' not in task
    assert not inventory.components[name]['embedded_scripts']
    assert inventory.task_profile_complete(name)
    # Both physical sources remain unmeasured until their own native receipts
    # arrive. Parsing the copy action never covers the installed Bash program.
    # Build this independent no-receipt census through the native parser. The
    # outer paired collector must keep refusing unsupported runtime profiles.
    with unobserved_parser():
        report = CoverageGate(ROOT).report()
    for component in (name, profile.COMPONENT):
        assert all(value['status'] == 'NON EXÉCUTÉ' for value in report['components'][component]['metrics'].values())

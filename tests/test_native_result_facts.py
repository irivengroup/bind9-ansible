"""Native callbacks must observe loop facts without bypassing no_log."""
import os
import json

import pytest
import yaml

from native_ansible_runner import NativeTaskRun

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Requires the native Linux Ansible controller')


def test_native_loop_fact_observations_match_actual_ansible_scope_and_keep_no_log_censored(tmp_path):
    tasks = tmp_path / 'roles/bind9/tasks'
    tasks.mkdir(parents=True)
    (tasks / 'main.yml').write_text(yaml.safe_dump([
        {'name': 'Accumulate public loop facts', 'ansible.builtin.set_fact': {
            'fixture_seen': '{{ (fixture_seen | default([])) + [item] }}'}, 'loop': [1, 2]},
        {'name': 'Keep protected loop facts private', 'ansible.builtin.set_fact': {
            'fixture_protected': 'synthetic-redacted-fact'}, 'loop': [1, 2], 'no_log': True}]), encoding='utf-8')
    status, result = NativeTaskRun('main.yml', {}, root=tmp_path, checks=[
        'fixture_seen == [1, 2]', "fixture_protected == 'synthetic-redacted-fact'"]).run()
    assert status == 0 and not result.failed
    assert result.facts.get('fixture_seen') == [1, 2]
    assert 'fixture_protected' not in result.facts


@pytest.mark.parametrize('protected', [False, True])
def test_native_failed_registers_preserve_actual_refusals_and_no_log_censorship(tmp_path, protected):
    tasks = tmp_path / 'roles/bind9/tasks'
    tasks.mkdir(parents=True)
    message = 'synthetic-private-refusal' if protected else 'public native refusal'
    (tasks / 'main.yml').write_text(yaml.safe_dump([{
        'name': 'Refuse the inspected native assertion', 'ansible.builtin.assert': {'that': ['false'],
            'fail_msg': message}, 'register': 'fixture_refusal', 'no_log': protected}]), encoding='utf-8')
    status, result = NativeTaskRun('main.yml', {}, root=tmp_path).run()
    assert status == 2 and result.failed == ['Refuse the inspected native assertion']
    recorded = result.registers['fixture_refusal']
    if protected:
        assert 'censored' in recorded and message not in json.dumps(recorded)
        assert 'invocation' not in recorded
    else:
        # Native TaskResult.clean_copy deliberately removes failed/skipped:
        # failure is carried by the callback event, already asserted above.
        assert recorded['msg'] == message and recorded['evaluated_to'] is False
        assert recorded['assertion'] == 'false' and 'failed' not in recorded

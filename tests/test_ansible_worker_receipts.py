"""Verifier fixtures never stand in for real production coverage observations."""
import copy
import hashlib
import json
import os

import pytest

from test_ansible_coverage_semantics import (AnsibleCollector, InstrumentationError,
    fixture_project, isolate_fixture_observer, pytestmark as native_profile_marks)
from native_ansible_runner import NativeTaskRun

pytestmark = native_profile_marks
isolate_fixture_observer = isolate_fixture_observer


@pytest.fixture
def worker_fixture(tmp_path):
    path = fixture_project(tmp_path, 'a or b')
    text = path.read_text(encoding='utf-8')
    path.write_text(text + '  loop: [1, 2]\n', encoding='utf-8')
    collector = AnsibleCollector(tmp_path)
    entry = next(iter(collector.inventory.predicates.values()))
    atom = next(entry['label'] + '|' + key for key, unit in entry['inventory'].units.items() if unit['kind'] == 'condition')
    task = 'roles/bind9/tasks/main.yml|' + next(iter(collector.inventory.components['roles/bind9/tasks/main.yml']['tasks']))
    value = collector.payload()
    value['conditions'] = {atom: [True]}
    value['attempted_tasks'] = value['dispatched_tasks'] = [task]
    value['task_loops'] = [[task, True], [task, False]]
    output = tmp_path / 'workers'
    output.mkdir()
    return collector, value, atom, task, output


def write_worker(output, pid, value):
    path = output / ('ansible-%s.json' % pid)
    path.write_text(json.dumps(value), encoding='utf-8')
    return path


def test_identical_valid_worker_hits_are_deduplicated(worker_fixture):
    collector, value, atom, task, folder = worker_fixture
    paths = [write_worker(folder, pid, value) for pid in (1, 2)]
    expected = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    result = collector.combine(paths, collector.inventory, expected)
    assert result['conditions'] == {atom: [True]}
    assert result['dispatched_tasks'] == [task]
    assert len(result['task_loops']) == len(result['process_receipts']) == 2
    assert not collector.conditions and not collector.dispatched_tasks


@pytest.mark.parametrize('mutation', ['schema_bool', 'schema_float', 'complete', 'source', 'units',
    'condition_integer', 'unknown_empty_condition', 'statement', 'branch_integer',
    'dispatch', 'loop_integer', 'loop_unknown', 'unit_type', 'unit_collection', 'condition_collection', 'missing_executor'])
def test_worker_union_rejects_malformed_data_before_any_admission(worker_fixture, mutation):
    collector, value, atom, task, folder = worker_fixture
    first = write_worker(folder, 1, value)
    broken = copy.deepcopy(value)
    if mutation == 'schema_bool': broken['schema'] = True
    elif mutation == 'schema_float': broken['schema'] = 1.0
    elif mutation == 'complete': broken['complete'] = True
    elif mutation == 'source': broken['files'] = {}
    elif mutation == 'units': broken['components'] = {}
    elif mutation == 'condition_integer': broken['conditions'][atom] = [1]
    elif mutation == 'unknown_empty_condition': broken['conditions']['unknown'] = []
    elif mutation == 'statement': broken['statements'] = ['unknown']
    elif mutation == 'branch_integer': broken['branches'] = [[atom, 1]]
    elif mutation == 'dispatch': broken['dispatched_tasks'] = ['unknown']
    elif mutation == 'loop_integer': broken['task_loops'] = [[task, 1]]
    elif mutation == 'loop_unknown': broken['task_loops'] = [['unknown', True]]
    elif mutation == 'unit_type': broken['lines'] = [1]
    elif mutation == 'unit_collection': broken['dispatched_tasks'] = task
    elif mutation == 'missing_executor': broken['attempted_tasks'] = []
    else: broken['conditions'] = []
    last = write_worker(folder, 2, broken)
    with pytest.raises(InstrumentationError):
        collector.combine([first, last], collector.inventory)
    assert not collector.conditions and not collector.dispatched_tasks


def test_changed_worker_and_duplicate_process_identity_are_refused(worker_fixture):
    collector, value, _, _, folder = worker_fixture
    path = write_worker(folder, 1, value)
    with pytest.raises(InstrumentationError, match='digest'):
        collector.combine([path], collector.inventory, {path.name: '0' * 64})
    with pytest.raises(InstrumentationError, match='duplicate'):
        collector.combine([path, path], collector.inventory)
    with pytest.raises(InstrumentationError, match='count'):
        collector.combine([], collector.inventory)


def test_merged_payload_preserves_types_across_the_required_json_wire_format(worker_fixture):
    collector, value, _, _, folder = worker_fixture
    path = write_worker(folder, 1, value)
    combined = collector.combine([path], collector.inventory)
    assert combined == json.loads(json.dumps(combined))


@pytest.mark.skipif(os.name != 'posix', reason='Native Ansible worker integration requires POSIX')
@pytest.mark.parametrize('items', [[], [1], [1, 2]])
def test_genuine_worker_loop_edges_and_action_dispatch_are_distinct(tmp_path, monkeypatch, items):
    path = fixture_project(tmp_path)
    path.write_text('- name: Loop through the native executor\n  ansible.builtin.debug:\n'
                    '    msg: "{{ item }}"\n  loop: "{{ items }}"\n', encoding='utf-8')
    folder = tmp_path / 'native-workers'
    monkeypatch.setenv('BIND9_CONDITION_COVERAGE', str(folder))
    with AnsibleCollector(tmp_path).observing() as collector:
        status, callbacks = NativeTaskRun('main.yml', {'items': items}, root=tmp_path).run()
    collector.save(folder)
    collector.merge(folder)
    combined = json.loads((folder / 'ansible-merged.json').read_text(encoding='utf-8'))
    assert status == 0 and not callbacks.failed
    assert len(combined['attempted_tasks']) == 1
    assert len(combined['dispatched_tasks']) == int(bool(items))
    assert {value for _, value in combined['task_loops']} == ({False, True} if items else {False})
    assert len(combined['process_receipts']) == 2

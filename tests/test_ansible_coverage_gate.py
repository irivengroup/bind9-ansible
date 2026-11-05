"""Partial YAML observations can never substitute for complete role coverage."""
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/quality'))
import test_coverage_gate

# Expose the existing fixture to pytest without duplicating its construction.
Q, QUALITY, receipt = test_coverage_gate.Q, test_coverage_gate.QUALITY, test_coverage_gate.receipt


@pytest.fixture
def partial_receipt(receipt):
    previous, evidence, value = receipt
    root = previous.root
    path = root / 'roles/bind9/tasks/main.yml'
    path.parent.mkdir()
    path.write_text('- name: Guard\n  ansible.builtin.debug:\n    msg: "{{ message }}"\n  when: enabled\n', encoding='utf-8')
    tool = root / 'tools/quality/ansible_coverage.py'
    tool.write_bytes((QUALITY / 'ansible_coverage.py').read_bytes())
    gate = Q.CoverageGate(root)
    value['source_sha256'], value['qa_sha256'] = gate.sources, gate.qa_sources
    value['ansible_observer_sha256'] = hashlib.sha256(tool.read_bytes()).hexdigest()
    # Synthetic admission fixture: no claim about product execution. It has
    # every predicate outcome, but deliberately no task/data-field execution.
    entries = gate.yaml.predicates.values()
    units = {entry['label'] + '|' + key: unit for entry in entries for key, unit in entry['inventory'].units.items()}
    observed = {'schema': 1, 'engine': 'ansible-core-2.16.14-native-conditional',
        'files': gate.yaml.files, 'components': gate.yaml.components, 'complete': False, 'unsupported': [],
        'statements': [key for key, unit in units.items() if unit['kind'] == 'statement'],
        'lines': [entry['label'] + '|line:' + str(line) for entry in gate.yaml.predicates.values()
                  for unit in entry['inventory'].units.values() if unit['kind'] == 'statement' for line in unit['lines']],
        'branches': [[key, result] for key, unit in units.items() if unit['kind'] == 'branch' for result in (False, True)],
        'conditions': {key: [False, True] for key, unit in units.items() if unit['kind'] == 'condition'}}
    return gate, evidence, value, observed


def write_receipt(evidence, receipt_value, observed):
    path = evidence / 'ansible.json'
    path.write_text(json.dumps(observed), encoding='utf-8')
    receipt_value['artifacts']['ansible_predicates'] = {'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    (evidence / 'run.json').write_text(json.dumps(receipt_value), encoding='utf-8')


def test_complete_predicates_still_leave_complete_yaml_metrics_unmeasured(partial_receipt):
    gate, evidence, value, observed = partial_receipt
    write_receipt(evidence, value, observed)
    gate.add(evidence / 'run.json')
    report = gate.report()
    assert report['accepted'] is False
    assert all(metric['status'] == 'NON EXÉCUTÉ' for metric in report['components']['roles/bind9/tasks/main.yml']['metrics'].values())
    observation, = report['ansible_predicate_observations']
    assert observation['observed_elementary_outcomes'] == observation['elementary_outcomes_inventory'] == 2
    assert observation['complete'] is False


@pytest.mark.parametrize('mutation', ['source', 'units', 'complete', 'engine', 'schema', 'observer',
    'unknown_empty', 'statement', 'line', 'branch', 'condition_type', 'unsupported'])
def test_forged_or_unknown_predicate_observation_is_refused_atomically(partial_receipt, mutation):
    gate, evidence, value, original = partial_receipt
    observed = copy.deepcopy(original)
    if mutation == 'source':
        observed['files'] = {}
    elif mutation == 'units':
        observed['components'] = {}
    elif mutation == 'complete':
        observed['complete'] = True
    elif mutation == 'engine':
        observed['engine'] = 'unverified-engine'
    elif mutation == 'schema':
        observed['schema'] = True
    elif mutation == 'observer':
        value['ansible_observer_sha256'] = '0' * 64
    elif mutation == 'unknown_empty':
        observed['conditions']['unknown|condition'] = []
    elif mutation == 'statement':
        observed['statements'].append('unknown|statement')
    elif mutation == 'line':
        observed['lines'].append('unknown|line:1')
    elif mutation == 'branch':
        observed['branches'][0][1] = 1
    elif mutation == 'condition_type':
        key = next(iter(observed['conditions']))
        observed['conditions'][key] = [0, 1]
    else:
        observed['unsupported'] = ['unverified truth conversion']
    write_receipt(evidence, value, observed)
    with pytest.raises(ValueError):
        gate.add(evidence / 'run.json')
    assert not gate.runs and not gate.native_units
    assert all(not hits for component in gate.hits.values() for hits in component.values())


def test_only_verified_literal_defaults_and_empty_metadata_have_zero_units(tmp_path):
    for name, text in [('defaults/main.yml', 'bind_front_routes_enabled: false\n'),
                       ('meta/main.yml', 'galaxy_info: {min_ansible_version: "2.9"}\ndependencies: []\n')]:
        path = tmp_path / 'roles/bind9' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    gate = Q.CoverageGate(tmp_path)
    assert not gate.report()['accepted'], 'No test run is not an accepted delivery'
    assert all(metric['status'] == 'NON APPLICABLE' and metric['total'] == 0
               for component in gate.report()['components'].values() for metric in component['metrics'].values())
    default = tmp_path / 'roles/bind9/defaults/main.yml'
    default.write_text('message: "{{ enabled }}"\n', encoding='utf-8')
    report = Q.CoverageGate(tmp_path).report()
    assert all(metric['status'] == 'NON EXÉCUTÉ' for metric in report['components']['roles/bind9/defaults/main.yml']['metrics'].values())

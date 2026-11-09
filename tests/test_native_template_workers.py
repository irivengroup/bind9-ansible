"""A real worker render must contribute source-bound template observations.

This tiny temporary project is a verifier fixture. No parent process renders
the template, so losing forked worker observations cannot appear complete.
"""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml

from test_paired_coverage_suite import project, pytestmark
from coverage_gate import CoverageGate

pytestmark = pytestmark
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def worker_run(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp('native-template-workers')
    root = project(tmp_path, 'complete')
    role = root / 'roles/bind9'
    (role / 'tasks').mkdir()
    (role / 'templates').mkdir()
    (role / 'templates/example.j2').write_text('{% if enabled %}Y{% else %}N{% endif %}', encoding='utf-8')
    (role / 'tasks/main.yml').write_text(yaml.safe_dump([{'name': 'Publish the fixture template',
        'ansible.builtin.template': {'src': 'example.j2', 'dest': str(root / 'rendered'), 'mode': '0600'}}]), encoding='utf-8')
    shutil.copy2(str(ROOT / 'tests/native_ansible_runner.py'), str(root / 'tests/native_ansible_runner.py'))
    (root / 'tests/test_render.py').write_text('''from pathlib import Path
import pytest
from native_ansible_runner import NativeTaskRun

class FileRun(NativeTaskRun):
    SAFE_ACTIONS = NativeTaskRun.SAFE_ACTIONS | {'ansible.builtin.template'}
    ROLE_ASSETS = True

@pytest.mark.parametrize('enabled', [False, True])
def test_actual_render(enabled):
    root = Path(__file__).resolve().parents[1]
    status, result = FileRun('main.yml', {'enabled': enabled}, root=root).run()
    assert status == 0 and not result.failed
    assert (root / 'rendered').read_text() == ('Y' if enabled else 'N')
''', encoding='utf-8')
    output = tmp_path / 'coverage'
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    environment.pop('BIND9_CONDITION_COVERAGE', None)
    result = subprocess.run([sys.executable, str(root / 'tools/quality/run_coverage.py'),
        '--root', str(root), '--output', str(output), 'tests'], cwd=root, env=environment,
        capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    return root, output


def test_actual_native_workers_retain_all_template_units_without_parent_renders(worker_run):
    root, output = worker_run
    data = json.loads((output / 'coverage-gate.json').read_text(encoding='utf-8'))
    assert data['errors'] == [] and data['accepted'] is True
    metrics = data['components']['roles/bind9/templates/example.j2']['metrics']
    assert all(item['percent'] in ('100', None) for item in metrics.values())


@pytest.mark.parametrize('mutation', ['source', 'units', 'schema', 'engine', 'statement', 'line',
    'condition', 'branch', 'unsupported', 'omitted', 'merged', 'digest', 'oversize'])
def test_template_worker_forgery_cannot_be_admitted_even_with_a_rehashed_envelope(worker_run, tmp_path, mutation):
    root, original = worker_run
    output = tmp_path / 'evidence'
    shutil.copytree(str(original), str(output))
    receipt_path = output / 'run.json'
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    merged_path = output / receipt['artifacts']['ansible_predicates']['path']
    merged = json.loads(merged_path.read_text(encoding='utf-8'))
    reference = next(item for item in merged['process_receipts'] if
        json.loads((merged_path.parent / item['path']).read_text(encoding='utf-8'))['template_observations']['statements'])
    worker_path = merged_path.parent / reference['path']
    worker = json.loads(worker_path.read_text(encoding='utf-8'))
    template = worker['template_observations']
    if mutation == 'source': template['files'] = {}
    elif mutation == 'units': template['units'] = {}
    elif mutation == 'schema': template['schema'] = True
    elif mutation == 'engine': template['engine'] = 'unverified'
    elif mutation == 'statement': template['statements'].append('unknown|statement')
    elif mutation == 'line': template['lines'].append('unknown|line:1')
    elif mutation == 'condition':
        key = next(iter(template['conditions']))
        template['conditions'][key] = [1]
    elif mutation == 'branch': template['branches'][0][1] = 1
    elif mutation == 'unsupported': template['unsupported'] = ['Unverified worker grammar']
    elif mutation == 'omitted': del worker['template_observations']
    elif mutation == 'merged': merged['template_observations']['statements'] = []
    elif mutation == 'oversize': worker['oversize'] = 'x' * 16777216
    else: template['statements'] = []
    worker_path.write_text(json.dumps(worker), encoding='utf-8')
    if mutation != 'digest': reference['sha256'] = hashlib.sha256(worker_path.read_bytes()).hexdigest()
    merged_path.write_text(json.dumps(merged), encoding='utf-8')
    receipt['artifacts']['ansible_predicates']['sha256'] = hashlib.sha256(merged_path.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
    gate = CoverageGate(root)
    with pytest.raises(ValueError):
        gate.add(receipt_path)
    assert gate.runs == []
    assert not any(values for component in gate.hits.values() for values in component.values())

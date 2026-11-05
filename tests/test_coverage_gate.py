"""The gate must retain incomplete coverage and original refusal evidence."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason='The QA collector requires Python 3.11')
QUALITY = Path(__file__).resolve().parents[1] / 'tools/quality'
sys.path.insert(0, str(QUALITY))
coverage = pytest.importorskip('coverage', reason='Install the pinned optional QA coverage dependency')
if coverage.__version__ != '7.6.1':
    pytest.skip('The coverage verifier uses the validated 7.6.1 parser', allow_module_level=True)
spec = importlib.util.spec_from_file_location('quality_gate_test', QUALITY / 'coverage_gate.py')
Q = importlib.util.module_from_spec(spec)
spec.loader.exec_module(Q)


@pytest.mark.parametrize('hit,total,expected', [(98, 100, 'EXÉCUTÉ'), (9799999, 10000000, 'ÉCHOUÉ'),
                                              (0, 1, 'ÉCHOUÉ'), (0, 0, 'NON APPLICABLE')])
def test_threshold_uses_integer_counts_before_rounding(hit, total, expected):
    assert Q.CoverageGate.metric(hit, total)['status'] == expected


def test_unknown_shell_yaml_and_template_components_cannot_inherit_python_coverage(tmp_path):
    role = tmp_path / 'roles/bind9'
    role.mkdir(parents=True)
    for name, text in [('script.sh', 'true\n'), ('tasks.yml', '- debug: msg=hello\n'), ('template.j2', '{{ x }}\n')]:
        (role / name).write_text(text)
    (tmp_path / 'playbooks').mkdir()
    (tmp_path / 'playbooks/deploy.yml').write_text('- hosts: all\n  roles: [bind9]\n')
    result = Q.CoverageGate(tmp_path).report()
    assert not result['accepted']
    assert len(result['components']) == 4
    assert all(metric['status'] == 'NON EXÉCUTÉ' for component in result['components'].values()
               for metric in component['metrics'].values())


@pytest.fixture
def receipt(tmp_path):
    root = tmp_path / 'source'
    source = root / 'roles/bind9/sample.py'
    source.parent.mkdir(parents=True)
    source.write_text('def select(value):\n if value:\n  return 1\n return 2\nselect(True)\nselect(False)\n')
    (root / 'tools/quality').mkdir(parents=True)
    (root / 'tools/quality/bind9_coverage.py').write_bytes((QUALITY / 'bind9_coverage.py').read_bytes())
    gate = Q.CoverageGate(root)
    path = tmp_path / 'evidence'
    path.mkdir()
    native = coverage.Coverage(data_file=None, branch=True, include=[str(source)])
    native.start()
    exec(compile(source.read_text(), str(source), 'exec'), {})
    native.stop()
    native.json_report(outfile=str(path / 'native.json'))
    data = json.loads((path / 'native.json').read_text())
    assert data['totals']['num_branches'] > 0
    data['files'] = {'roles/bind9/sample.py': next(iter(data['files'].values()))}
    (path / 'native.json').write_text(json.dumps(data))
    observed = {'schema': 2, 'files': {'roles/bind9/sample.py': hashlib.sha256(source.read_bytes()).hexdigest()},
                'units': gate.inventory.units, 'functions': [], 'statements': [], 'conditions': {}, 'unsupported': []}
    # Synthetic full-coverage fixture tests admission logic, not the application.
    for name, units in gate.inventory.units.items():
        for key, value in units.items():
            full = name + '|' + key
            if value['kind'] == 'condition':
                observed['conditions'][full] = [False, True]
            else:
                observed[value['kind'] + 's'].append(full)
    (path / 'observed.json').write_text(json.dumps(observed))
    for name in ('native.xml', 'instrumented.xml'):
        (path / name).write_text('<testsuites><testsuite tests="1" failures="0" errors="0" skipped="0"><testcase classname="fixture" name="truth"/></testsuite></testsuites>')
    value = {'schema': 1, 'native_instrumented': False, 'source_sha256': gate.sources, 'qa_sha256': gate.qa_sources,
             'observer_sha256': hashlib.sha256((QUALITY / 'bind9_coverage.py').read_bytes()).hexdigest(),
             'exitstatus': {'native': 0, 'instrumented': 0}, 'artifacts': {}}
    for key, name in [('native', 'native.json'), ('observations', 'observed.json'),
                      ('native_junit', 'native.xml'), ('instrumented_junit', 'instrumented.xml')]:
        value['artifacts'][key] = {'path': name, 'sha256': hashlib.sha256((path / name).read_bytes()).hexdigest()}
    (path / 'run.json').write_text(json.dumps(value))
    return gate, path, value


def test_complete_fixture_is_accepted_without_omitting_any_component(receipt):
    gate, path, _ = receipt
    gate.add(path / 'run.json')
    assert gate.report()['accepted']
    assert all(value['hit'] == value['total'] for value in gate.report()['python_totals'].values())


@pytest.mark.parametrize('mutation', ['source', 'observer', 'artifact', 'instrumented', 'failed', 'conditions', 'denominator', 'test_identity'])
def test_stale_altered_failed_or_incomplete_receipt_is_rejected_atomically(receipt, mutation):
    gate, path, value = receipt
    if mutation == 'source':
        value['source_sha256'] = {}
    elif mutation == 'observer':
        value['observer_sha256'] = '0' * 64
    elif mutation == 'artifact':
        (path / 'observed.json').write_text('{}')
    elif mutation == 'instrumented':
        value['native_instrumented'] = True
    elif mutation == 'failed':
        value['exitstatus']['native'] = 1
    else:
        key = 'native' if mutation == 'denominator' else 'observations' if mutation == 'conditions' else 'instrumented_junit'
        target = path / value['artifacts'][key]['path']
        if mutation == 'test_identity':
            target.write_text(target.read_text().replace('truth', 'different'))
        else:
            data = json.loads(target.read_text())
            if mutation == 'conditions':
                data['unsupported'] = ['effectful truth conversion']
            else:
                item = data['files']['roles/bind9/sample.py']
                item['executed_branches'], item['missing_branches'] = [], []
            target.write_text(json.dumps(data))
        value['artifacts'][key]['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
    (path / 'run.json').write_text(json.dumps(value))
    with pytest.raises(ValueError):
        gate.add(path / 'run.json')
    assert not gate.runs
    assert not any(hits for component in gate.hits.values() for hits in component.values())


@pytest.fixture
def template_receipt(receipt):
    import jinja2
    from test_jinja_coverage_semantics import observed as observe_templates
    previous, path, value = receipt
    source = previous.root / 'roles/bind9/template.j2'
    text = '{% if value %}yes{% else %}no{% endif %}'
    source.write_text(text, encoding='utf-8')
    tool = previous.root / 'tools/quality/jinja_coverage.py'
    tool.write_bytes((QUALITY / tool.name).read_bytes())
    gate = Q.CoverageGate(previous.root)
    with observe_templates(previous.root) as collector:
        environment = jinja2.Environment()
        template = environment.from_string(text)
        assert template.render(value=True) == 'yes'
        assert template.render(value=False) == 'no'
    target = path / 'templates.json'
    target.write_text(json.dumps(collector.payload()), encoding='utf-8')
    value.update(source_sha256=gate.sources, qa_sha256=gate.qa_sources,
                 template_observer_sha256=hashlib.sha256(tool.read_bytes()).hexdigest())
    value['artifacts']['templates'] = {'path': target.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    (path / 'run.json').write_text(json.dumps(value), encoding='utf-8')
    return gate, path, value


def test_independently_observed_template_has_its_own_metrics(template_receipt):
    gate, path, _ = template_receipt
    gate.add(path / 'run.json')
    result = gate.report()
    assert result['accepted']
    metrics = result['components']['roles/bind9/template.j2']['metrics']
    assert metrics['functions']['status'] == 'NON APPLICABLE'
    assert metrics['functions']['total'] == 0
    assert all(item['hit'] == item['total'] for item in metrics.values())
    assert result['python_totals']['functions']['total'] == 1


@pytest.mark.parametrize('mutation', ['inventory', 'observer', 'functions', 'unsupported',
                                     'line', 'statement', 'branch', 'condition', 'empty-condition'])
def test_corrupt_template_evidence_is_rejected_before_python_hits_are_admitted(template_receipt, mutation):
    gate, path, receipt_value = template_receipt
    target = path / 'templates.json'
    payload = json.loads(target.read_text(encoding='utf-8'))
    if mutation == 'inventory':
        payload['files'] = {}
    elif mutation == 'observer':
        receipt_value['template_observer_sha256'] = '0' * 64
    elif mutation == 'functions':
        payload['functions'] = ['fabricated macro']
    elif mutation == 'unsupported':
        payload['unsupported'] = ['nonboolean comparison result']
    elif mutation == 'line':
        payload['lines'].append('roles/bind9/template.j2|line:999')
    elif mutation == 'statement':
        payload['statements'].append('roles/bind9/template.j2|unknown')
    elif mutation == 'branch':
        payload['branches'][0][1] = 1
    elif mutation == 'condition':
        payload['conditions'][next(iter(payload['conditions']))] = [0, 1]
    else:
        payload['conditions']['roles/bind9/template.j2|unknown'] = []
    target.write_text(json.dumps(payload), encoding='utf-8')
    receipt_value['artifacts']['templates']['sha256'] = hashlib.sha256(target.read_bytes()).hexdigest()
    (path / 'run.json').write_text(json.dumps(receipt_value), encoding='utf-8')
    with pytest.raises(ValueError):
        gate.add(path / 'run.json')
    assert not gate.runs and not gate.native_units and not gate.template_runs
    assert not any(hits for component in gate.hits.values() for hits in component.values())


def test_omitted_template_measurement_cannot_inherit_python_results(template_receipt):
    gate, path, value = template_receipt
    del value['artifacts']['templates']
    (path / 'run.json').write_text(json.dumps(value), encoding='utf-8')
    gate.add(path / 'run.json')
    result = gate.report()
    assert not result['accepted']
    assert all(metric['status'] == 'NON EXÉCUTÉ' for metric in
               result['components']['roles/bind9/template.j2']['metrics'].values())

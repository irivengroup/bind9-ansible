"""Paired batching must keep every real test and every refusal observable.

The tiny temporary project is a verifier fixture, never production coverage.
Each case runs the unchanged real gate and real native/instrumented processes.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import stat
import time

import pytest
from ansible import release
import jinja2

coverage = pytest.importorskip('coverage', reason='Requires the optional pinned QA coverage dependency')
_QA_UNSUPPORTED = (os.name != 'posix' or sys.version_info < (3, 11) or
                   release.__version__ != '2.16.19' or jinja2.__version__ != '3.1.6' or
                   coverage.__version__ != '7.6.1')
_QA_REASON = 'Requires the pinned Linux QA controller'
pytestmark = pytest.mark.skipif(_QA_UNSUPPORTED, reason=_QA_REASON)
# Dependent QA modules import this file's helpers and marker as well.
if _QA_UNSUPPORTED:
    pytest.skip(_QA_REASON, allow_module_level=True)
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/quality'))
from pytest_selection import TestIdentities
from run_coverage_campaign import CampaignPlan, CampaignSuite, CampaignMerge
from run_coverage_suite import SuiteRun


def project(folder, scenario):
    root = folder / 'project'
    role = root / 'roles/bind9'
    role.mkdir(parents=True)
    (role / 'sample.py').write_text('def choose(value):\n    if value:\n        return 1\n    return 2\n', encoding='utf-8')
    (root / 'playbooks').mkdir()
    tests = root / 'tests'
    tests.mkdir()
    shutil.copy2(str(ROOT / 'tests/conftest.py'), str(tests / 'conftest.py'))
    shutil.copytree(str(ROOT / 'tools/quality'), str(root / 'tools/quality'),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    cases = [(True, 1), (False, 2)] if scenario != 'threshold' else [(True, 1)]
    body = ('import sys\nfrom pathlib import Path\nimport pytest\n'
            'sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n'
            'from roles.bind9.sample import choose\n'
            '@pytest.mark.parametrize("value,expected", ' + repr(cases) +
            (', ids=' + repr(['x' * 70000, 'y' * 70000]) if scenario == 'long_identity' else '') + ')\n'
            'def test_choose(value, expected):\n    assert choose(value) == expected\n')
    if scenario == 'failure': body += '    assert value, "Deliberate verifier refusal fixture"\n'
    elif scenario == 'collection': body += 'invalid Python syntax !!!\n'
    (tests / 'test_sample.py').write_text(body, encoding='utf-8')
    return root



class CampaignExample:
    """Use the existing tiny project to obtain genuine paired merge receipts."""

    def __init__(self, folder):
        self.root = project(folder, 'threshold')
        self.output = folder / 'campaign'
        for name, value, expected in (
                ('test_management_acl_logging.py', False, 2),
                ('test_native_management_playbook.py', True, 1)):
            body = ('import sys\nfrom pathlib import Path\n'
                    'sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n'
                    'from roles.bind9.sample import choose\n'
                    'def test_contract():\n    assert choose(' + repr(value) + ') == ' + str(expected) + '\n')
            (self.root / 'tests' / name).write_text(body, encoding='utf-8')

    def execute(self):
        plan = CampaignPlan.create(self.root, self.output)
        main = CampaignSuite(self.root, self.output / 'main', self.output, 'main', 1)
        # A successful cohort must keep its partial coverage refusal visible.
        assert main.run_cohort() == 0
        partial = json.loads((main.output / 'coverage-gate.json').read_text())
        assert partial['accepted'] is False and partial['errors'] == []
        # These synthetic modules test receipt merging, with no BIND action.
        # The real checker/path requirements have their own native integration.
        for cohort in ('checker', 'public'):
            run = SuiteRun(self.root, self.output / cohort, plan['cohorts'][cohort], 1)
            assert run.run() == 2
            assert all(stage['admitted'] for stage in run.stages)
        return plan


@pytest.mark.parametrize('mutation', ['complete', 'missing', 'duplicate', 'skipped', 'misnamed'])
def test_campaign_merge_requires_every_disjoint_successful_native_receipt(tmp_path, mutation):
    example = CampaignExample(tmp_path)
    plan = example.execute()
    if mutation == 'complete':
        assert CampaignMerge.run(example.root, example.output) == 0
        result = json.loads((example.output / 'coverage-gate.json').read_text())
        assert result['accepted'] is True and result['errors'] == []
        assert result['campaign']['complete'] and result['campaign']['duplicate_count'] == 0
        assert result['campaign']['collected'] == result['campaign']['executed_unique'] == len(plan['nodeids']) == 3
        return
    summary_path = example.output / 'public/coverage-gate.json'
    summary = json.loads(summary_path.read_text())
    if mutation == 'missing':
        summary['paired_suite']['stages'] = []
    elif mutation == 'duplicate':
        summary['paired_suite']['stages'] *= 2
    else:
        stage = summary['paired_suite']['stages'][0]
        receipt_path = summary_path.parent / stage['name'] / 'run.json'
        receipt = json.loads(receipt_path.read_text())
        import xml.etree.ElementTree as ET
        phases = ('instrumented',) if mutation == 'skipped' else ('native', 'instrumented')
        for phase in phases:
            artifact = receipt['artifacts'][phase + '_junit']
            junit = receipt_path.parent / artifact['path']
            tree = ET.parse(junit)
            case = next(tree.iter('testcase'))
            if mutation == 'skipped':
                ET.SubElement(case, 'skipped', {'message': 'Mandatory native dependency absent'})
            else:
                # Matching labels between phases cannot replace a selected case.
                case.set('name', 'unselected_case')
            tree.write(junit, encoding='utf-8')
            artifact['sha256'] = hashlib.sha256(junit.read_bytes()).hexdigest()
        receipt_path.write_text(json.dumps(receipt))
        stage['receipt_sha256'] = hashlib.sha256(receipt_path.read_bytes()).hexdigest()
        if mutation == 'misnamed':
            from coverage_gate import CoverageGate
            gate = CoverageGate(example.root)
            with pytest.raises(ValueError, match='JUnit identities differ'):
                gate.add(receipt_path, selected_tests=stage['tests'])
            assert not gate.runs and not gate.native_units
            assert all(not values for component in gate.hits.values() for values in component.values())
    summary_path.write_text(json.dumps(summary))
    with pytest.raises(ValueError, match='cohort|skipped|JUnit'):
        CampaignMerge.run(example.root, example.output)
    assert not (example.output / 'coverage-gate.json').exists()


def test_campaign_refuses_a_missing_checker_before_creating_cohort_evidence(tmp_path, monkeypatch):
    example = CampaignExample(tmp_path)
    CampaignPlan.create(example.root, example.output)
    monkeypatch.delenv('BIND_MANAGEMENT_CHECKCONF', raising=False)
    with pytest.raises(ValueError, match='genuine native BIND checker'):
        CampaignSuite(example.root, example.output / 'checker', example.output, 'checker', 1)
    assert not (example.output / 'checker').exists()


def test_campaign_rejects_a_changed_test_source_after_collection(tmp_path):
    example = CampaignExample(tmp_path)
    CampaignPlan.create(example.root, example.output)
    source = example.root / 'tests/test_sample.py'
    source.write_text(source.read_text() + '# Source changed after collection\n')
    with pytest.raises(ValueError, match='QA revision changed'):
        CampaignPlan.read(example.root, example.output)


@pytest.mark.parametrize('nodes', [
    ['tests/test_sample.py::one'],
    ['tests/test_sample.py::one', 'tests/test_management_acl_logging.py::one'],
    ['tests/test_sample.py::one', 'tests/test_management_acl_logging.py::one',
     'tests/test_native_management_playbook.py::one', 'tests/test_sample.py::one'],
])
def test_campaign_collection_cannot_omit_a_native_cohort_or_duplicate_a_test(nodes):
    with pytest.raises(ValueError):
        CampaignPlan.partition(nodes)


def test_distinct_selected_nodes_cannot_alias_the_same_native_junit_identity():
    nodes = ['tests/test.a.py::test_one', 'tests/test/a.py::test_one',
             'tests/test_management_acl_logging.py::test_contract',
             'tests/test_native_management_playbook.py::test_contract']
    assert TestIdentities.validate(nodes) == nodes
    with pytest.raises(ValueError, match='ambiguous JUnit'):
        CampaignPlan.partition(nodes)


@pytest.mark.parametrize('nodes,cases', [
    (['tests/test_sample.py::test_one'], [('tests.test_sample', 'test_one')]),
    (['tests/test_sample.py::TestCase::test_one[a::b.é&<>]'],
     [('tests.test_sample.TestCase', 'test_one[a::b.é&<>]')]),
    # Match actual pinned native XML, including legal DEL and escaped C0.
    (['tests/test_sample.py::test_one[\x7f]'], [('tests.test_sample', 'test_one[\x7f]')]),
    (['tests/test_sample.py::test_one[\x01]'], [('tests.test_sample', 'test_one[#x01]')]),
    (['tests/readme.txt'], [('', 'tests.readme.txt')]),
])
def test_selected_identity_digest_uses_the_native_parameter_and_xml_names(nodes, cases):
    expected = hashlib.sha256(json.dumps(sorted(cases), ensure_ascii=True,
        separators=(',', ':')).encode('utf-8')).hexdigest()
    assert TestIdentities.selected_junit_sha256(nodes) == expected


@pytest.mark.parametrize('cases', [None, [], ['case'], [('class',)], [('class', None)],
                                  [('class', 'case'), ('class', 'case')]])
def test_junit_identity_digest_refuses_missing_malformed_or_ambiguous_cases(cases):
    with pytest.raises(ValueError, match='JUnit testcase identities'):
        TestIdentities.junit_identity_sha256(cases)


def test_selected_identity_binding_refuses_an_uninspected_pytest_producer(monkeypatch):
    monkeypatch.setattr(pytest, '__version__', '9.0.2')
    with pytest.raises(ValueError, match='requires pytest 9.0.3'):
        TestIdentities.selected_junit_sha256(['tests/test_sample.py::test_one'])


def test_campaign_resource_partition_preserves_every_identity_and_requested_maximum():
    nodes = ['tests/test_front_routes.py::case' + str(number) for number in range(9)]
    nodes += ['tests/test_python.py::case' + str(number) for number in range(9)]
    groups = list(CampaignSuite.batches(nodes, 8))
    assert list(map(len, groups)) == [1] * 9 + [8, 1]
    assert [node for group in groups for node in group] == nodes
    assert all(len(group) == 1 for group in CampaignSuite.batches(nodes, 1))


@pytest.mark.parametrize('parent_ignores_term', [False, True])
def test_batch_timeout_stops_descendants_even_after_the_leader_exits(tmp_path, parent_ignores_term):
    """A reaped leader must not leave a child ignoring SIGTERM in the owned group."""
    child = tmp_path / 'child.py'
    child.write_text('import os, signal, sys, time\n'
        'signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
        'open(sys.argv[1], "w").write(str(os.getpid()))\n'
        'while True: time.sleep(1)\n', encoding='utf-8')
    parent = tmp_path / 'parent.py'
    parent.write_text('import os, signal, subprocess, sys, time\n'
        'from pathlib import Path\n'
        'if sys.argv[4] == "ignore": signal.signal(signal.SIGTERM, signal.SIG_IGN)\n'
        'subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])\n'
        'while not Path(sys.argv[2]).exists(): time.sleep(.01)\n'
        'open(sys.argv[3], "w").write(str(os.getpid()))\n'
        'while True: time.sleep(1)\n', encoding='utf-8')
    child_pid, parent_pid = tmp_path / 'child.pid', tmp_path / 'parent.pid'
    suite = SuiteRun.__new__(SuiteRun)
    suite.root = tmp_path
    # Force the error path in the isolated test, retaining the production budget.
    suite.COMMAND_TIMEOUT = 3
    try:
        with pytest.raises(ValueError, match='execution budget; owned process group stopped'):
            suite.command([sys.executable, str(parent), str(child), str(child_pid), str(parent_pid),
                'ignore' if parent_ignores_term else 'default'], tmp_path / 'command.log')
        assert parent_pid.exists() and child_pid.exists(), 'The native child never became ready'
        state = Path('/proc/%d/stat' % int(child_pid.read_text()))
        def running():
            try:
                return state.read_text().split()[2] != 'Z'
            except FileNotFoundError:
                return False
        deadline = time.monotonic() + 1
        # Delivery of SIGKILL and reaping by the container init are asynchronous.
        while running() and time.monotonic() < deadline:
            time.sleep(.01)
        assert not running()
    finally:
        if parent_pid.exists():
            try:
                os.killpg(int(parent_pid.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.parametrize('code', [0, 17])
def test_batch_command_preserves_native_exit_status_and_log(tmp_path, code):
    suite = SuiteRun.__new__(SuiteRun)
    suite.root = tmp_path
    log = tmp_path / 'command.log'
    assert suite.command([sys.executable, '-c',
        'import sys; print("bounded command"); sys.exit(' + str(code) + ')'], log) == code
    assert log.read_text(encoding='utf-8') == 'bounded command\n'


def test_nested_suite_collection_cannot_inherit_the_parent_bash_writer(tmp_path):
    """A new suite owns its writers; collection must not open a parent's proof."""
    root = project(tmp_path, 'complete')
    output = tmp_path / 'nested-evidence'
    parent_proof = tmp_path / 'parent-proof.json'
    sentinel = b'private parent proof must remain byte-identical\n'
    parent_proof.write_bytes(sentinel)
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1',
        BIND9_NATIVE_BASH_OUTPUT=str(parent_proof), BIND9_NATIVE_BASH_MODE='observed')
    environment.pop('BIND9_CONDITION_COVERAGE', None)
    result = subprocess.run([sys.executable, str(root / 'tools/quality/run_coverage_suite.py'),
        '--root', str(root), '--output', str(output), '--batch-size', '1', 'tests'],
        env=environment, cwd=root, capture_output=True, timeout=180)
    report = json.loads((output / 'coverage-gate.json').read_text(encoding='utf-8'))
    assert result.returncode == 0, result.stdout + result.stderr
    assert report['errors'] == [] and report['accepted'] is True
    assert len(report['paired_suite']['collection']) == len(report['runs']) == 2
    assert parent_proof.read_bytes() == sentinel


def test_native_yaml_guard_coverage_is_independent_of_batch_partition(tmp_path):
    """Skipped native tasks retain their actual guard outcomes across batches."""
    root = project(tmp_path, 'complete')
    tasks = root / 'roles/bind9/tasks'
    tasks.mkdir()
    (tasks / 'main.yml').write_text('- name: Observe the native choice\n'
        '  ansible.builtin.debug:\n    msg: handled\n  when: enabled\n', encoding='utf-8')
    shutil.copyfile(str(ROOT / 'tests/native_ansible_runner.py'), str(root / 'tests/native_ansible_runner.py'))
    test = root / 'tests/test_sample.py'
    with test.open('a', encoding='utf-8') as stream:
        stream.write('\nfrom native_ansible_runner import NativeTaskRun\n'
            '@pytest.mark.parametrize("enabled", [False, True])\n'
            'def test_native_choice(enabled):\n'
            '    root = Path(__file__).resolve().parents[1]\n'
            '    status, result = NativeTaskRun("main.yml", {"enabled": enabled}, root=root).run()\n'
            '    assert status == 0 and not result.failed\n'
            '    assert ("Observe the native choice" in result.ok) is enabled\n'
            '    assert ("Observe the native choice" in result.skipped) is (not enabled)\n')
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    environment.pop('BIND9_CONDITION_COVERAGE', None)
    reports = []
    for size in (4, 1):
        output = tmp_path / ('batch-size-' + str(size))
        result = subprocess.run([sys.executable, str(root / 'tools/quality/run_coverage_suite.py'),
            '--root', str(root), '--output', str(output), '--batch-size', str(size), 'tests'],
            env=environment, cwd=root, capture_output=True, timeout=180)
        report = json.loads((output / 'coverage-gate.json').read_text(encoding='utf-8'))
        reports.append(report)
        assert report['errors'] == [] and len(report['paired_suite']['collection']) == 4
        assert all(stage['admitted'] for stage in report['paired_suite']['stages'])
        assert result.returncode == 0 and report['accepted'] is True, report['components']
    assert reports[0]['components'] == reports[1]['components']
    # A guard-only batch remains unknown until another admitted receipt proves
    # native task execution. Failed later-language admission cannot leak those
    # pending facts into a subsequent successful dispatch receipt.
    from coverage_gate import CoverageGate
    single = tmp_path / 'batch-size-1'
    false_path, true_path = single / 'batch-0003/run.json', single / 'batch-0004/run.json'
    false_report = json.loads((false_path.parent / 'coverage-gate.json').read_text(encoding='utf-8'))
    name = 'roles/bind9/tasks/main.yml'
    assert all(value['status'] == 'NON EXÉCUTÉ' for value in false_report['components'][name]['metrics'].values())
    forged = json.loads(false_path.read_text(encoding='utf-8'))
    forged['bash_observer_sha256'] = '0' * 64
    bad_path = false_path.with_name('refused-incomplete-bash-run.json')
    bad_path.write_text(json.dumps(forged), encoding='utf-8')
    gate = CoverageGate(root)
    with pytest.raises(ValueError, match='Native Bash requires both complete'):
        gate.add(bad_path)
    assert not gate.runs and not gate.ansible_components
    assert all(not hits for hits in gate.hits[name].values())
    gate.add(true_path)
    assert gate.report()['components'][name]['metrics']['conditions']['percent'] == '50'
    gate.add(false_path)
    assert gate.report()['components'][name] == reports[0]['components'][name]


@pytest.mark.parametrize('scenario,accepted', [('complete', True), ('long_identity', True), ('threshold', False),
                                              ('failure', False), ('collection', False), ('filtered', False)])
def test_real_paired_batches_preserve_completeness_and_fail_closed(tmp_path, scenario, accepted):
    root = project(tmp_path, scenario)
    output = tmp_path / 'evidence'
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    environment.pop('BIND9_CONDITION_COVERAGE', None)
    environment.pop('PYTEST_ADDOPTS', None)
    if scenario == 'filtered': environment['PYTEST_ADDOPTS'] = '-k True'
    result = subprocess.run([sys.executable, str(root / 'tools/quality/run_coverage_suite.py'),
        '--root', str(root), '--output', str(output), '--batch-size', '1', 'tests'],
        env=environment, cwd=root, capture_output=True, timeout=180)
    assert result.returncode == (0 if accepted else 2), result.stdout + result.stderr
    report = json.loads((output / 'coverage-gate.json').read_text(encoding='utf-8'))
    assert report['accepted'] is accepted
    if scenario in ('complete', 'long_identity'):
        assert report['errors'] == []
        assert len(report['paired_suite']['collection']) == len(report['runs']) == 2
        assert all(stage['admitted'] for stage in report['paired_suite']['stages'])
        assert all(metric['percent'] == '100' for metric in report['components']['roles/bind9/sample.py']['metrics'].values())
        for stage in report['paired_suite']['stages']:
            partial = json.loads((output / stage['name'] / 'coverage-gate.json').read_text(encoding='utf-8'))
            assert partial['accepted'] is False and partial['errors'] == []
    elif scenario == 'threshold':
        assert report['errors'] == [] and len(report['runs']) == 1
        assert report['components']['roles/bind9/sample.py']['metrics']['branches']['status'] == 'ÉCHOUÉ'
    else:
        assert report['errors']
        if scenario == 'failure':
            assert any(not stage['admitted'] for stage in report['paired_suite']['stages'])
            assert len(report['runs']) == 1
        else: assert report['runs'] == []


@pytest.mark.parametrize('nodes', [[], ['same', 'same'], ['-k'], ['@another-file'],
    ['test\n--ignore=tests'], ['test\rname'], ['test\0name'], [None], 'tests'])
def test_exact_manifest_refuses_selection_injection_and_duplicate_identities(tmp_path, nodes):
    path = tmp_path / 'selection.json'
    with pytest.raises(ValueError, match='identities'):
        TestIdentities.write(path, nodes)
    assert not path.exists()


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'oversize', 'schema', 'extra-field'])
def test_exact_manifest_refuses_unsafe_files_and_unknown_schema(tmp_path, kind):
    path = tmp_path / 'selection.json'
    if kind == 'symlink':
        foreign = tmp_path / 'foreign'
        foreign.write_text('{"schema":1,"nodeids":["tests"]}', encoding='utf-8')
        path.symlink_to(foreign)
    elif kind == 'fifo':
        os.mkfifo(str(path), stat.S_IRUSR | stat.S_IWUSR)
    elif kind == 'oversize':
        with path.open('wb') as stream:
            stream.truncate(16777217)
    else:
        data = {'schema': True if kind == 'schema' else 1, 'nodeids': ['tests']}
        if kind == 'extra-field': data['ignore'] = 'tests'
        path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises((ValueError, OSError)):
        TestIdentities.read(path)


def test_failed_native_pair_preserves_an_explicit_gate_refusal_with_its_original_receipt(tmp_path):
    root = project(tmp_path, 'failure')
    output = tmp_path / 'evidence'
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    environment.pop('BIND9_CONDITION_COVERAGE', None)
    result = subprocess.run([sys.executable, str(root / 'tools/quality/run_coverage.py'),
        '--root', str(root), '--output', str(output), 'tests'], cwd=root, env=environment,
        capture_output=True, timeout=180)
    assert result.returncode == 2, result.stdout + result.stderr
    receipt = json.loads((output / 'run.json').read_text(encoding='utf-8'))
    report = json.loads((output / 'coverage-gate.json').read_text(encoding='utf-8'))
    assert receipt['exitstatus'] == {'native': 1, 'instrumented': 1}
    assert report['accepted'] is False and report['runs'] == []
    assert report['errors'] == ['A paired test execution did not succeed']
    metrics = report['components']['roles/bind9/sample.py']['metrics']
    assert all(metric['status'] == 'NON EXÉCUTÉ' for metric in metrics.values())
    assert all(not {'hit', 'total', 'percent'}.intersection(metric) for metric in metrics.values())


@pytest.mark.parametrize('mutation', ['changed', 'deleted', 'created'])
def test_actual_paired_receipt_refuses_changed_project_ansible_configuration(tmp_path, mutation):
    """Identical source/tests cannot validate a different controller configuration."""
    from coverage_gate import CoverageGate
    root = project(tmp_path, 'complete')
    configuration = root / 'ansible.cfg'
    if mutation != 'created':
        configuration.write_text('[defaults]\nforks = 1\n', encoding='utf-8')
    output = tmp_path / 'evidence'
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    environment.pop('BIND9_CONDITION_COVERAGE', None)
    result = subprocess.run([sys.executable, str(root / 'tools/quality/run_coverage.py'),
        '--root', str(root), '--output', str(output), 'tests'], cwd=root, env=environment,
        capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    if mutation == 'deleted':
        configuration.unlink()
    else:
        configuration.write_text('[defaults]\nforks = 2\n', encoding='utf-8')
    gate = CoverageGate(root)
    with pytest.raises(ValueError, match='Test/observer sources changed'):
        gate.add(output / 'run.json')
    assert not gate.runs and not gate.native_units


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'oversize'])
def test_project_controller_configuration_refuses_unsafe_source_files(tmp_path, kind):
    from coverage_gate import CoverageGate
    from bind9_coverage import InstrumentationError
    root = project(tmp_path, 'complete')
    configuration = root / 'ansible.cfg'
    if kind == 'symlink':
        foreign = tmp_path / 'foreign.cfg'
        foreign.write_text('[defaults]\n', encoding='utf-8')
        configuration.symlink_to(foreign)
    elif kind == 'fifo':
        os.mkfifo(str(configuration), 0o600)
    else:
        with configuration.open('wb') as stream:
            stream.truncate(4194305)
    with pytest.raises(InstrumentationError, match='Unsafe or excessive project Ansible configuration'):
        CoverageGate(root)


def test_paired_children_use_the_selected_projects_actual_ansible_configuration(tmp_path):
    root = project(tmp_path, 'complete')
    configuration = root / 'ansible.cfg'
    configuration.write_text('[defaults]\nforks = 1\n', encoding='utf-8')
    foreign = tmp_path / 'other-project.cfg'
    foreign.write_text('[defaults]\nforks = 2\n', encoding='utf-8')
    test_file = root / 'tests/test_sample.py'
    with test_file.open('a', encoding='utf-8') as stream:
        stream.write('\ndef test_native_controller_configuration_is_project_local():\n'
                     '    from ansible import constants\n'
                     '    assert Path(constants.CONFIG_FILE).resolve() == '
                     'Path(__file__).resolve().parents[1] / "ansible.cfg"\n')
    output = tmp_path / 'evidence'
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', ANSIBLE_CONFIG=str(foreign))
    environment.pop('BIND9_CONDITION_COVERAGE', None)
    result = subprocess.run([sys.executable, str(root / 'tools/quality/run_coverage.py'),
        '--root', str(root), '--output', str(output), 'tests'], cwd=root, env=environment,
        capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    receipt = json.loads((output / 'run.json').read_text(encoding='utf-8'))
    assert receipt['qa_sha256']['ansible.cfg'] == hashlib.sha256(configuration.read_bytes()).hexdigest()


def test_partial_project_report_retains_each_independently_measured_metric(tmp_path):
    root = project(tmp_path, 'complete')
    (root / 'roles/bind9/unmeasured.sh').write_text('#!/bin/bash\ntrue\n', encoding='utf-8')
    output = tmp_path / 'evidence'
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    environment.pop('BIND9_CONDITION_COVERAGE', None)
    result = subprocess.run([sys.executable, str(root / 'tools/quality/run_coverage.py'),
        '--root', str(root), '--output', str(output), 'tests'], cwd=root, env=environment,
        capture_output=True, timeout=180)
    assert result.returncode == 2, result.stdout + result.stderr
    report = json.loads((output / 'coverage-gate.json').read_text(encoding='utf-8'))
    assert report['errors'] == [] and report['accepted'] is False
    known = report['components']['roles/bind9/sample.py']['metrics']
    for name in ('statements', 'branches', 'functions', 'lines', 'conditions'):
        observed = report['project_totals'][name]
        assert observed['status'] == 'NON EXÉCUTÉ'
        assert (observed['partial_hit'], observed['partial_total']) == (known[name]['hit'], known[name]['total'])
        assert 'percent' not in observed and 'total' not in observed


def test_paired_children_receive_identical_private_paths_without_reusing_native_phase_files(tmp_path):
    root = project(tmp_path, 'complete')
    paths = tmp_path / 'observed-input-paths.txt'
    test_file = root / 'tests/test_sample.py'
    with test_file.open('a', encoding='utf-8') as stream:
        stream.write('\ndef test_input_paths_and_clean_state(tmp_path):\n'
            '    marker = tmp_path / "native-phase-state"\n'
            '    assert not marker.exists(), "A phase reused the other phase\'s private state"\n'
            '    marker.write_bytes(b"owned fixture bytes")\n'
            '    with Path(' + repr(str(paths)) + ').open("a", encoding="utf-8") as observed:\n'
            '        observed.write(str(tmp_path) + "\\n")\n')
    output = tmp_path / 'evidence'
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    environment.pop('BIND9_CONDITION_COVERAGE', None)
    result = subprocess.run([sys.executable, str(root / 'tools/quality/run_coverage.py'),
        '--root', str(root), '--output', str(output), 'tests'], cwd=root, env=environment,
        capture_output=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    observed = paths.read_text(encoding='utf-8').splitlines()
    assert len(observed) == 2 and observed[0] == observed[1]
    assert not Path(observed[0]).exists(), 'The private paired workspace must be cleaned after both phases'

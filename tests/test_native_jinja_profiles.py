"""Distinct real compiler profiles retain all units and independent thresholds."""
import json
import os
from pathlib import Path
import subprocess
import sys

from ansible import release
import jinja2
import pytest
import yaml

from test_paired_coverage_suite import project
from test_jinja_coverage_semantics import observed, unobserved_parser
from test_native_preflight import scope

pytestmark = pytest.mark.skipif(os.name != 'posix' or sys.version_info < (3, 11) or
    release.__version__ != '2.16.19' or jinja2.__version__ != '3.1.6', reason='Requires the inspected QA compiler profile')
ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('native_mode', [False, True])
def test_actual_ansible_environment_copy_preserves_native_outputs_and_observer_ownership(monkeypatch, native_mode):
    from ansible import constants
    from ansible.parsing.dataloader import DataLoader
    from ansible.template import Templar
    monkeypatch.setattr(constants, 'DEFAULT_JINJA2_NATIVE', native_mode)
    source = (ROOT / 'roles/bind9/templates/named.conf.j2').read_text(encoding='utf-8')
    hosts, groups = scope()
    variables = dict(hosts['localhost'], hostvars=hosts, group_names=['dns_primary'])

    def renders():
        parent = Templar(loader=DataLoader(), variables=variables)
        first = parent.template(source, convert_data=False)
        child = parent.copy_with_new_env(available_variables=dict(variables, group_names=['dns_secondary']))
        second = child.template(source, convert_data=False)
        return (first, type(first), second, type(second)), child

    with unobserved_parser():
        golden, unused_child = renders()
    with observed(ROOT) as collector:
        actual, child = renders()
        # A second, uncompiled clone must also behave natively after close.
        late = child.copy_with_new_env()
    assert actual == golden and not collector.unsupported
    before = collector.payload()
    assert late.template(source, convert_data=False) == golden[2]
    assert collector.payload() == before


@pytest.mark.parametrize('native_mode', [False, True])
def test_outer_observer_can_resume_a_known_uncompiled_clone_after_inner_close(monkeypatch, native_mode):
    from ansible import constants
    from ansible.parsing.dataloader import DataLoader
    from ansible.template import Templar
    monkeypatch.setattr(constants, 'DEFAULT_JINJA2_NATIVE', native_mode)
    source = (ROOT / 'roles/bind9/templates/named.conf.j2').read_text(encoding='utf-8')
    hosts, groups = scope()
    values = dict(hosts['localhost'], hostvars=hosts, group_names=['dns_primary'])
    with unobserved_parser():
        golden = Templar(loader=DataLoader(), variables=values).template(source, convert_data=False)
    with observed(ROOT) as outer:
        with observed(ROOT) as inner:
            parent = Templar(loader=DataLoader(), variables=values)
            assert parent.template(source, convert_data=False) == golden
            late = parent.copy_with_new_env()
        closed = inner.payload()
        assert late.template(source, convert_data=False) == golden
        assert inner.payload() == closed
        assert outer.statements and not outer.unsupported


@pytest.mark.parametrize('tamper', ['foreign-probe', 'generator-subclass'])
def test_closed_clone_provenance_still_refuses_foreign_probes_and_compiler_subclasses(tamper):
    from ansible.parsing.dataloader import DataLoader
    from ansible.template import Templar
    from bind9_coverage import InstrumentationError
    from jinja_coverage import JinjaCollector
    from types import MethodType
    source = (ROOT / 'roles/bind9/templates/named.conf.j2').read_text(encoding='utf-8')
    hosts, groups = scope()
    values = dict(hosts['localhost'], hostvars=hosts, group_names=['dns_primary'])
    with observed(ROOT) as outer:
        with observed(ROOT) as inner:
            parent = Templar(loader=DataLoader(), variables=values)
            parent.template(source, convert_data=False)
            late = parent.copy_with_new_env()
        closed = inner.payload()
        if tamper == 'foreign-probe':
            setattr(late.environment, inner.ATTRIBUTE, MethodType(JinjaCollector.probe, object()))
        else:
            late.environment.code_generator_class = type('UnreviewedClosedCompiler',
                (late.environment.code_generator_class,), {})
        with pytest.raises(InstrumentationError, match='compiler'):
            late.environment.parse(source)
        assert inner.payload() == closed and outer.unsupported


@pytest.mark.parametrize('tamper', ['foreign-probe', 'generator-subclass'])
def test_copied_environment_cannot_borrow_observer_ownership(tamper):
    from ansible.parsing.dataloader import DataLoader
    from ansible.template import Templar
    from bind9_coverage import InstrumentationError
    from jinja_coverage import JinjaCollector
    from types import MethodType
    source = (ROOT / 'roles/bind9/templates/named.conf.j2').read_text(encoding='utf-8')
    hosts, groups = scope()
    with observed(ROOT) as collector:
        parent = Templar(loader=DataLoader(), variables=dict(hosts['localhost'], hostvars=hosts, group_names=['dns_primary']))
        parent.template(source, convert_data=False)
        child = parent.copy_with_new_env()
        if tamper == 'foreign-probe':
            setattr(child.environment, collector.ATTRIBUTE, MethodType(JinjaCollector.probe, object()))
        else:
            child.environment.code_generator_class = type('UnknownCompiler', (child.environment.code_generator_class,), {})
        with pytest.raises(InstrumentationError, match='compiler'):
            child.environment.parse(source)
    assert collector.unsupported


@pytest.mark.parametrize('change', ['subclass', 'lstrip_blocks', 'newline_sequence', 'keep_trailing_newline'])
def test_changed_native_compiler_profile_is_still_refused(change):
    from ansible.parsing.dataloader import DataLoader
    from ansible.template import Templar
    from bind9_coverage import InstrumentationError
    source = (ROOT / 'roles/bind9/templates/named.conf.j2').read_text(encoding='utf-8')
    renderer = Templar(loader=DataLoader())
    if change == 'subclass':
        class UnknownEnvironment(type(renderer.environment)):
            pass
        renderer.environment = UnknownEnvironment(trim_blocks=True)
    else:
        setattr(renderer.environment, change, '\r\n' if change == 'newline_sequence' else True)
    with observed(ROOT) as collector:
        with pytest.raises(InstrumentationError, match='parser profile'):
            renderer.environment.parse(source)
    assert collector.unsupported and not collector.statements


@pytest.mark.parametrize('native_mode', [False, True])
def test_both_actual_ansible_compiler_classes_preserve_named_configuration(monkeypatch, native_mode):
    from ansible import constants
    from ansible.parsing.dataloader import DataLoader
    from ansible.plugins.loader import filter_loader
    from ansible.template import Templar
    monkeypatch.setattr(constants, 'DEFAULT_JINJA2_NATIVE', native_mode)
    filter_loader.add_directory(str(ROOT / 'roles/bind9/filter_plugins'))
    hosts, groups = scope()
    variables = dict(hosts['localhost'], hostvars=hosts, group_names=['dns_primary'])
    source = (ROOT / 'roles/bind9/templates/named.conf.j2').read_text(encoding='utf-8')
    with unobserved_parser():
        golden = Templar(loader=DataLoader(), variables=variables).template(source, convert_data=False)
    with observed(ROOT) as collector:
        result = Templar(loader=DataLoader(), variables=variables).template(source, convert_data=False)
    assert result == golden and type(result) is type(golden)
    assert any('|ansible|' in key for key in collector.statements)
    assert not collector.unsupported


def test_native_profile_manifest_matches_every_literal_template_consumer():
    sys.path.insert(0, str(ROOT / 'tools/quality'))
    from jinja_coverage import JinjaCollector
    references = set()

    def walk(value):
        if isinstance(value, list):
            for item in value: walk(item)
        elif isinstance(value, dict):
            if 'ansible.builtin.template' in value:
                args = value['ansible.builtin.template']
                assert isinstance(args, dict) and isinstance(args['src'], str)
                assert '{{' not in args['src'] and '{%' not in args['src']
                references.add('roles/bind9/templates/' + args['src'])
            for item in value.values(): walk(item)

    for path in (ROOT / 'roles/bind9/tasks').rglob('*.yml'):
        walk(yaml.safe_load(path.read_text(encoding='utf-8')))
    files = {path.relative_to(ROOT).as_posix() for path in (ROOT / 'roles/bind9/templates').glob('*.j2')}
    assert references == files == JinjaCollector.ANSIBLE_SOURCES


@pytest.mark.parametrize('complete', [False, True])
def test_real_profile_coverage_cannot_average_away_an_insufficient_native_context(tmp_path, complete):
    # This miniature role is a verifier fixture, never a production observation.
    # At 99.99%, one missing native outcome out of 5,000 must fail even
    # though the two distinct profiles together cover 9,999/10,000 outcomes.
    # Only this isolated verifier fixture is enlarged; production units stay fixed.
    row_count = 2500
    root = project(tmp_path, 'complete')
    template = root / 'roles/bind9/templates/named.conf.j2'
    template.parent.mkdir()
    template.write_text('// verifier fixture\n' + ''.join(
        '{% if c' + str(index) + ' %}Y{% endif %}\n' for index in range(row_count)), encoding='utf-8')
    tests = root / 'tests/test_render.py'
    tests.write_text('''from pathlib import Path
import pytest
import jinja2
from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar

SOURCE = (Path(__file__).resolve().parents[1] / 'roles/bind9/templates/named.conf.j2').read_text()

@pytest.mark.parametrize('value', [False, True])
def test_plain(value):
    result = jinja2.Environment().from_string(SOURCE).render(**{'c'+str(i): value for i in range(ROW_COUNT)})
    assert result.count('Y') == ROW_COUNT * value

@pytest.mark.parametrize('value', [False, True])
def test_ansible(value):
    values = {'c'+str(i): value for i in range(ROW_COUNT)}
    if not COMPLETE:
        values.update({'c'+str(i): False for i in range(1)})
    result = Templar(loader=DataLoader(), variables=values).template(SOURCE, convert_data=False)
    assert result.count('Y') == (ROW_COUNT if COMPLETE else ROW_COUNT - 1) * value
'''.replace('COMPLETE', repr(complete)).replace('ROW_COUNT', str(row_count)), encoding='utf-8')
    output = tmp_path / 'coverage'
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    environment.pop('BIND9_CONDITION_COVERAGE', None)
    result = subprocess.run([sys.executable, str(root / 'tools/quality/run_coverage.py'),
        '--root', str(root), '--output', str(output), 'tests'], cwd=root, env=environment,
        capture_output=True, timeout=180)
    assert result.returncode == (0 if complete else 2), result.stdout + result.stderr
    data = json.loads((output / 'coverage-gate.json').read_text(encoding='utf-8'))
    assert data['threshold_basis_points'] == 9999
    assert data['errors'] == [] and data['accepted'] is complete
    item = data['components']['roles/bind9/templates/named.conf.j2']
    assert all(value['status'] in ('EXÉCUTÉ', 'NON APPLICABLE') for value in item['metrics'].values())
    assert item['profiles']['plain']['branches']['percent'] == '100'
    assert item['profiles']['ansible']['branches']['percent'] == ('100' if complete else '99.98')
    for kind in ('branches', 'conditions'):
        aggregate = item['metrics'][kind]
        native = item['profiles']['ansible'][kind]
        assert (aggregate['hit'], aggregate['total']) == (10000 if complete else 9999, 10000)
        assert (native['hit'], native['total']) == (5000 if complete else 4999, 5000)

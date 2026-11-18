"""Native Ansible task fields retain values, types and truth-conversion order."""
import itertools

from ansible.parsing.dataloader import DataLoader
from ansible.template import Templar
import pytest
import yaml

from test_ansible_coverage_semantics import (AnsibleCollector, TruthValue, fixture_project,
                                           isolate_fixture_observer, pytestmark as native_profile_marks)
from test_jinja_coverage_semantics import observed, unobserved_parser
from native_ansible_runner import NativeTaskRun

# Reuse the enclosing-run isolation fixture; no canonical task executes while
# the outer production collector is suspended for these tiny grammar fixtures.
isolate_fixture_observer = isolate_fixture_observer
pytestmark = native_profile_marks


class RenderedTruthValue(TruthValue):
    """Stable output makes formatting changes observable alongside truth calls."""

    def __str__(self):
        self.events.append(('str', self.name))
        return '<' + self.name + '>'

    def __repr__(self):
        self.events.append(('repr', self.name))
        return '<' + self.name + '>'


def data_field(root, source):
    path = fixture_project(root)
    path.write_text(yaml.safe_dump([{'name': 'Read a canonical data field',
        'ansible.builtin.debug': {'msg': source}}]), encoding='utf-8')
    return path


def render(path, variables, **kwargs):
    loader = DataLoader()
    field = loader.load_from_file(str(path))[0]['ansible.builtin.debug']['msg']
    return Templar(loader=loader, variables=variables).template(field, **kwargs)


def test_one_native_environment_can_alternate_yaml_fields_and_template_sources(tmp_path):
    path = data_field(tmp_path, "{{ 'Y' if enabled else 'N' }}")
    source = "{{ 'T' if enabled else 'F' }}"
    (tmp_path / 'roles/bind9/example.j2').write_text(source, encoding='utf-8')
    field = DataLoader().load_from_file(str(path))[0]['ansible.builtin.debug']['msg']

    def renders():
        renderer = Templar(loader=DataLoader())
        output = []
        for value in (True, False, True):
            renderer.available_variables = {'enabled': value}
            output.extend((renderer.template(field), renderer.template(source)))
        return output

    with unobserved_parser():
        golden = renders()
    with observed(tmp_path) as templates:
        with AnsibleCollector(tmp_path).observing() as tasks:
            assert tasks.template_collector is templates
            actual = renders()
    assert actual == golden == ['Y', 'T', 'N', 'F', 'Y', 'T']
    assert not tasks.unsupported and not templates.unsupported
    assert tasks.statements and templates.statements
    assert all('#template:' in key for key in tasks.statements)
    assert all('#template:' not in key for key in templates.statements)


def test_static_import_path_records_only_its_actual_source_bound_native_compilation(tmp_path):
    tasks = tmp_path / 'roles/bind9/tasks'
    tasks.mkdir(parents=True)
    (tmp_path / 'playbooks').mkdir()
    (tasks / 'main.yml').write_text('- name: Import the inspected child\n  ansible.builtin.import_tasks: "{{ role_path }}/tasks/child.yml"\n', encoding='utf-8')
    (tasks / 'child.yml').write_text('- name: Read the actual child\n  ansible.builtin.debug:\n    msg: child\n', encoding='utf-8')
    with AnsibleCollector(tmp_path).observing() as collector:
        status, result = NativeTaskRun('main.yml', {}, root=tmp_path).run()
    assert status == 0 and not result.failed and result.ok == ['Read the actual child']
    entries = [entry for entry in collector.inventory.templates.values() if entry['file'].endswith('/tasks/main.yml')]
    assert len(entries) == 1
    expected = {entry['label'] + '|' + key for entry in entries for key, unit in entry['inventory'].units.items()
                if unit['kind'] == 'statement'}
    assert expected and expected.issubset(collector.statements)
    assert not collector.unsupported


@pytest.mark.parametrize('value', [True, False, None, 0, 42, 1.5, '', 'value', [], [1, 2], {'x': 1}])
def test_native_single_variable_fast_path_preserves_type_and_value(tmp_path, value):
    file = data_field(tmp_path, '{{ value }}')
    expected = render(file, {'value': value})
    with AnsibleCollector(tmp_path).observing() as collector:
        actual = render(file, {'value': value})
    assert actual == expected and type(actual) is type(expected)
    assert collector.statements and not collector.conditions
    assert all('#template:' in key for key in collector.statements)


@pytest.mark.parametrize('source', [
    '{% if a %}A{% elif b %}B{% elif c %}C{% else %}D{% endif %}',
    '{{ a and (b or c) }}', '{{ (a or b) and c }}',
    '{{ "yes" if a and b else "no" }}',
    '{% set ns = namespace(args=[]) %}{% if a %}{% set ns.args = ns.args + ["A"] %}'
    '{% elif b %}{% set ns.args = ns.args + ["B"] %}{% endif %}{{ ns.args }}',
])
@pytest.mark.parametrize('values', list(itertools.product([False, True], repeat=3)))
def test_task_field_codegen_preserves_native_short_circuiting(tmp_path, source, values):
    file = data_field(tmp_path, source)
    before, after = [], []
    left = {name: RenderedTruthValue(name, value, before) for name, value in zip('abc', values)}
    right = {name: RenderedTruthValue(name, value, after) for name, value in zip('abc', values)}
    # Use stable strings while still making every extra truth conversion visible.
    expected = render(file, left)
    with AnsibleCollector(tmp_path).observing() as collector:
        actual = render(file, right)
    if any(expected is value for value in left.values()):
        index = next(name for name, value in left.items() if expected is value)
        assert actual is right[index]
    else:
        assert actual == expected and type(actual) is type(expected)
    assert before == after and collector.conditions


@pytest.mark.parametrize('source', [
    '{{ {"a": [1, 2], "b": (3, 4)} }}', '{{ values[1:3] }}',
    '{{ (2 + 3) * 4 // 2 }}', '{{ ("x" ~ value) | upper }}',
    '{% for value in values %}{{ value }}{% else %}empty{% endfor %}',
])
@pytest.mark.parametrize('values', [[], [1], [1, 2, 3, 4]])
def test_extended_native_data_grammar_and_loop_results_are_unchanged(tmp_path, source, values):
    file = data_field(tmp_path, source)
    variables = dict(values=values, value='example')
    expected = render(file, variables)
    with AnsibleCollector(tmp_path).observing() as collector:
        actual = render(file, variables)
    assert actual == expected and type(actual) is type(expected)
    assert collector.statements


def test_anonymous_identical_data_does_not_claim_a_canonical_field(tmp_path):
    data_field(tmp_path, '{{ value }}')
    with AnsibleCollector(tmp_path).observing() as collector:
        assert Templar(loader=DataLoader(), variables={'value': 42}).template('{{ value }}') == 42
    assert not collector.statements


def test_data_receipt_does_not_contain_the_rendered_secret(tmp_path):
    file = data_field(tmp_path, '{{ private_value }}')
    with AnsibleCollector(tmp_path).observing() as collector:
        assert render(file, {'private_value': 'synthetic-private-field'}) == 'synthetic-private-field'
    assert 'synthetic-private-field' not in str(collector.payload())
    assert collector.payload()['complete'] is False

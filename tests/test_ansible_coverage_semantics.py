"""Compare source-attributed observation with unmodified native Ansible."""
import itertools
import json
import os
from pathlib import Path
import sys

from ansible.parsing.dataloader import DataLoader
from ansible.errors import AnsibleError
from ansible.playbook.conditional import Conditional
from ansible.template import Templar
from ansible import release
import jinja2
import pytest
import yaml

_QA_UNSUPPORTED = release.__version__ != '2.16.19' or jinja2.__version__ != '3.1.6'
_QA_REASON = 'QA observer requires inspected core 2.16.19 / Jinja 3.1.6'
pytestmark = pytest.mark.skipif(_QA_UNSUPPORTED, reason=_QA_REASON)
# Importing the native observer already requires the inspected Ansible APIs.
# Apply the existing compatibility contract before loading those APIs.
if _QA_UNSUPPORTED:
    pytest.skip(_QA_REASON, allow_module_level=True)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/quality'))
from ansible_coverage import AnsibleCollector, YamlInventory
from bind9_coverage import InstrumentationError


class TruthValue:
    """Make extra truth conversions or altered short circuits observable."""

    def __init__(self, name, value, events):
        self.name, self.value, self.events = name, value, events

    def __bool__(self):
        self.events.append(self.name)
        return self.value


@pytest.fixture(autouse=True)
def isolate_fixture_observer(tmp_path, monkeypatch):
    """Fixture predicates must never be attributed to the enclosing role run."""
    outer = getattr(Conditional._check_conditional, '_bind9_observer', None)
    if outer is not None:
        outer.close()
    # Native workers inherit the output environment. Their fixture source IDs
    # must never enter the enclosing production receipt directory, even when
    # a test only inspects scheduling events and does not explicitly save.
    monkeypatch.setenv('BIND9_CONDITION_COVERAGE', str(tmp_path / 'fixture-observations'))
    try:
        yield
    finally:
        if outer is not None:
            outer.install()


def fixture_project(tmp_path, expression='a and b'):
    folder = tmp_path / 'roles/bind9/tasks'
    folder.mkdir(parents=True)
    (tmp_path / 'playbooks').mkdir()
    file = folder / 'main.yml'
    file.write_text('- name: Verify\n  ansible.builtin.debug:\n    msg: example\n  when: ' + expression + '\n', encoding='utf-8')
    return file


def native(file, variables):
    loader = DataLoader()
    expression = loader.load_from_file(str(file))[0]['when']
    condition = Conditional(loader=loader)
    condition.when = [expression]
    return condition.evaluate_conditional(Templar(loader=loader, variables=variables), variables)


@pytest.mark.parametrize('expression', ['a and b', 'a or b', 'not (a and b) or c',
                                        'a and (b or c)', '(a or b) and c',
                                        'not (a or (b and c))', 'a is defined and a',
                                        '(a if b else c)'])
@pytest.mark.parametrize('values', list(itertools.product([False, True], repeat=3)))
def test_native_truth_order_and_results_are_preserved(tmp_path, expression, values):
    file = fixture_project(tmp_path, expression)
    expected_events = []
    expected_scope = {name: TruthValue(name, value, expected_events) for name, value in zip('abc', values)}
    expected = native(file, expected_scope)
    actual_events = []
    actual_scope = {name: TruthValue(name, value, actual_events) for name, value in zip('abc', values)}
    with AnsibleCollector(tmp_path).observing() as collector:
        actual = native(file, actual_scope)
    assert actual is expected
    assert actual_events == expected_events
    assert collector.conditions
    assert collector.statements
    assert not collector.unsupported
    assert collector.payload()['complete'] is False


def test_real_role_inventory_retains_all_yaml_and_unmeasured_surfaces():
    inventory = YamlInventory(ROOT)
    assert len(inventory.files) == 38
    modules = {path.stem for path in (ROOT / 'roles/bind9/library').glob('*.py')}

    def scalar_conditions(node):
        if isinstance(node, yaml.SequenceNode):
            return sum(scalar_conditions(child) for child in node.value)
        return int(isinstance(node, yaml.ScalarNode) and node.tag == 'tag:yaml.org,2002:str')

    def census(node):
        tasks = conditions = 0
        if isinstance(node, yaml.MappingNode):
            keys = {key.value for key, _ in node.value}
            tasks += int('hosts' in keys or 'block' in keys or any(
                key.startswith('ansible.builtin.') or key in modules for key in keys))
            for key, child in node.value:
                if key.value in ('when', 'until', 'failed_when', 'changed_when', 'that'):
                    conditions += scalar_conditions(child)
                if key.value == 'roles' and isinstance(child, yaml.SequenceNode):
                    tasks += len(child.value)
                nested_tasks, nested_conditions = census(child)
                tasks += nested_tasks
                conditions += nested_conditions
        elif isinstance(node, yaml.SequenceNode):
            for child in node.value:
                nested_tasks, nested_conditions = census(child)
                tasks += nested_tasks
                conditions += nested_conditions
        return tasks, conditions

    raw = {path.relative_to(ROOT).as_posix(): census(yaml.compose(path.read_text(encoding='utf-8')))
        for folder in ('roles/bind9', 'playbooks') for path in (ROOT / folder).rglob('*.yml')}
    # The service-state snapshot adds four tasks and four explicit predicates.
    # Independent raw YAML still includes custom modules, plays and roles.
    assert sum(row[0] for row in raw.values()) == 259
    assert sum(row[1] for row in raw.values()) == 327
    assert {name: len(component['tasks']) for name, component in inventory.components.items()} == {
        name: row[0] for name, row in raw.items()}
    assert len(inventory.predicates) == sum(row[1] for row in raw.values())
    data_files = {name for name, value in inventory.components.items() if value['data_only']}
    assert data_files == {'roles/bind9/defaults/main.yml', 'roles/bind9/meta/main.yml'}
    assert inventory.components['roles/bind9/tasks/tasks.d/deploy/front_return_routes.yml']['templates']


def test_anonymous_same_text_does_not_claim_canonical_execution(tmp_path):
    fixture_project(tmp_path)
    loader = DataLoader()
    condition = Conditional(loader=loader)
    condition.when = ['a and b']
    scope = {'a': True, 'b': True}
    with AnsibleCollector(tmp_path).observing() as collector:
        assert condition.evaluate_conditional(Templar(loader=loader, variables=scope), scope) is True
    assert not collector.conditions and not collector.statements


def test_reusing_a_native_templar_across_collectors_restores_its_environment(tmp_path):
    file = fixture_project(tmp_path)
    loader = DataLoader()
    scope = {'a': True, 'b': True}
    expression = loader.load_from_file(str(file))[0]['when']
    condition = Conditional(loader=loader)
    condition.when = [expression]
    templar = Templar(loader=loader, variables=scope)
    original = templar.environment.code_generator_class
    with AnsibleCollector(tmp_path).observing() as first:
        assert condition.evaluate_conditional(templar, scope) is True
    assert templar.environment.code_generator_class is original
    first_hits = first.payload()
    with AnsibleCollector(tmp_path).observing() as second:
        assert condition.evaluate_conditional(templar, scope) is True
    assert second.conditions and first.payload() == first_hits


def test_retained_compiled_template_does_not_record_after_collector_close(tmp_path):
    file = fixture_project(tmp_path)
    loader = DataLoader()
    scope = {'a': True, 'b': True}
    expression = loader.load_from_file(str(file))[0]['when']
    condition = Conditional(loader=loader)
    condition.when = [expression]
    templar = Templar(loader=loader, variables=scope)
    with AnsibleCollector(tmp_path).observing() as collector:
        assert condition.evaluate_conditional(templar, scope) is True
        # A compiled object may outlive the scope; the observer must not turn
        # that into an exception or silently extend the measured run.
        collector.active.append(next(iter(collector.inventory.predicates.values())))
        try:
            template = templar.environment.from_string(collector.active[-1]['source'])
        finally:
            collector.active.pop()
    before = collector.payload()
    assert template.render(a=False, b=True) == [False, 'bool']
    assert collector.payload() == before


@pytest.mark.parametrize('values', list(itertools.product([False, True], repeat=3)))
def test_new_native_assignment_wrapper_accepts_ternary_without_repairing_its_source(tmp_path, values):
    file = fixture_project(tmp_path, 'a if b else c')
    scope = dict(zip('abc', values))
    expected = native(file, scope)
    with AnsibleCollector(tmp_path).observing() as collector:
        assert native(file, scope) is expected
    units = next(iter(collector.inventory.predicates.values()))['inventory'].units
    assert sum(unit['kind'] == 'condition' for unit in units.values()) == 3
    # Both candidate value arms stay in the denominator; only the selected
    # arm and the selector are actually evaluated in this invocation.
    assert len(collector.conditions) == 2


@pytest.mark.parametrize('allow_non_boolean', [False, True])
def test_native_type_inspection_receives_the_original_class_and_preserves_its_refusal(
        tmp_path, monkeypatch, allow_non_boolean):
    from ansible import constants
    original = constants.config.get_config_value
    monkeypatch.setattr(constants.config, 'get_config_value', lambda name, *args, **kwargs:
        allow_non_boolean if name == 'ALLOW_BROKEN_CONDITIONALS' else original(name, *args, **kwargs))

    class InspectedValue(TruthValue):
        def __getattribute__(self, name):
            if name == '__class__':
                object.__getattribute__(self, 'events').append('native-class-inspection')
            return object.__getattribute__(self, name)

    file = fixture_project(tmp_path, 'a')

    def evaluate(events):
        try:
            return ('result', native(file, {'a': InspectedValue('truth', True, events)}))
        except AnsibleError as error:
            return ('refusal', type(error).__name__, str(error))

    expected_events, observed_events = [], []
    expected = evaluate(expected_events)
    with AnsibleCollector(tmp_path).observing() as collector:
        observed = evaluate(observed_events)
    assert observed == expected
    assert observed_events == expected_events and 'native-class-inspection' in observed_events
    assert observed[0] == ('result' if allow_non_boolean else 'refusal')
    assert collector.conditions


def test_observer_refuses_the_native_security_inspection_escape_hatch(tmp_path, monkeypatch):
    from ansible import constants
    fixture_project(tmp_path)
    original = constants.config.get_config_value
    monkeypatch.setattr(constants.config, 'get_config_value', lambda name, *args, **kwargs:
        True if name == '_DISABLE_BACKPORTED_INSPECTIONS' else original(name, *args, **kwargs))
    with pytest.raises(InstrumentationError, match='security inspections must remain enabled'):
        AnsibleCollector(tmp_path)


def test_changed_canonical_bytes_are_refused(tmp_path):
    file = fixture_project(tmp_path)
    collector = AnsibleCollector(tmp_path)
    file.write_text(file.read_text(encoding='utf-8') + '# changed\n', encoding='utf-8')
    with collector.observing(), pytest.raises(InstrumentationError, match='source changed'):
        native(file, {'a': True, 'b': True})


def test_duplicate_yaml_keys_are_refused(tmp_path):
    file = fixture_project(tmp_path)
    file.write_text(file.read_text(encoding='utf-8') + '  when: c\n', encoding='utf-8')
    with pytest.raises(InstrumentationError, match='duplicate YAML key'):
        YamlInventory(tmp_path)


@pytest.mark.parametrize('budget', ['bytes', 'nodes', 'depth'])
def test_yaml_inventory_work_is_bounded(tmp_path, monkeypatch, budget):
    file = fixture_project(tmp_path)
    if budget == 'bytes':
        monkeypatch.setattr(YamlInventory, 'MAX_BYTES', 8)
    elif budget == 'nodes':
        monkeypatch.setattr(YamlInventory, 'MAX_NODES', 2)
    else:
        # SafeLoader composes aliases without executing them; traversal must
        # still refuse a recursive graph instead of recursing indefinitely.
        file.write_text('recursive: &cycle {child: *cycle}\n', encoding='utf-8')
    with pytest.raises(InstrumentationError, match='budget'):
        YamlInventory(tmp_path)


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='FIFO boundary requires a POSIX filesystem')
def test_special_yaml_input_is_refused_without_waiting_for_a_writer(tmp_path):
    file = fixture_project(tmp_path)
    file.unlink()
    os.mkfifo(file)
    with pytest.raises(InstrumentationError, match='regular file'):
        YamlInventory(tmp_path)


def test_receipt_contains_only_source_units_and_boolean_outcomes(tmp_path):
    file = fixture_project(tmp_path, 'secret == "expected"')
    with AnsibleCollector(tmp_path).observing() as collector:
        assert native(file, {'secret': 'synthetic-private-marker'}) is False
    output = tmp_path / 'proof'
    collector.save(output)
    path, = output.glob('ansible-*.json')
    payload = json.loads(path.read_text(encoding='utf-8'))
    assert 'synthetic-private-marker' not in path.read_text(encoding='utf-8')
    assert payload['complete'] is False and payload['conditions']
    assert payload['components']['roles/bind9/tasks/main.yml']['tasks']

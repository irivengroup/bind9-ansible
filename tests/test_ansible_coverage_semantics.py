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

pytestmark = pytest.mark.skipif(release.__version__ != '2.16.14' or jinja2.__version__ != '3.1.6',
                                reason='QA observer requires inspected core 2.16.14 / Jinja 3.1.6')

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
def isolate_fixture_observer():
    """Fixture predicates must never be attributed to the enclosing role run."""
    outer = getattr(Conditional._check_conditional, '_bind9_observer', None)
    if outer is not None:
        outer.close()
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
    # Includes 240 explicit tasks/blocks, three plays and one role invocation.
    assert sum(len(value['tasks']) for value in inventory.components.values()) == 244
    assert len(inventory.predicates) == 309
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


def test_unparenthesized_ternary_is_refused_by_native_ansible_too(tmp_path):
    file = fixture_project(tmp_path, 'a if b else c')
    with pytest.raises(AnsibleError, match='expected token'):
        native(file, {'a': True, 'b': True, 'c': False})
    # Do not repair the source grammar merely to observe an invalid condition.
    with pytest.raises(jinja2.TemplateSyntaxError):
        YamlInventory(tmp_path)


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

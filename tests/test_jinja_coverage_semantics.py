"""Compare observed compilation against an independent native Jinja execution."""
from contextlib import contextmanager
import itertools
import json
from pathlib import Path
import sys

import jinja2
from markupsafe import Markup
import pytest

pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason='QA observer requires Python 3.11')
if jinja2.__version__ != '3.1.6':
    pytest.skip('Observer contract requires the inspected Jinja 3.1.6 compiler', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/quality'))
from bind9_coverage import InstrumentationError
from jinja_coverage import JinjaCollector


class Value:
    def __init__(self, name, truth, events):
        self.name, self.truth, self.events = name, truth, events

    def __bool__(self):
        self.events.append(('bool', self.name))
        return self.truth

    def __str__(self):
        self.events.append(('str', self.name))
        return '<' + self.name + '>'


@contextmanager
def observed(root):
    previous = jinja2.Environment._parse
    parent = getattr(previous, '_bind9_observer', None)
    if parent:
        jinja2.Environment._parse = parent.original_parse
    collector = JinjaCollector(root)
    collector.install()
    try:
        yield collector
    finally:
        collector.close()
        jinja2.Environment._parse = previous


def evaluate(source, values, escape=False, partial=False):
    events = []
    def finalize(value):
        events.append(('finalize', type(value).__name__))
        return value
    environment = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=escape, finalize=finalize)
    objects = {name: Value(name, truth, events) for name, truth in zip(('x', 'y', 'z'), values)}
    def identity(value):
        assert any(value is original for original in objects.values())
        events.append(('identity', value.name))
        return value
    def tag(value):
        events.append(('filter', value))
        return Markup('<safe>' + value + '</safe>')
    environment.filters['tag'] = tag
    template = environment.from_string(source)
    stream = template.generate(identity=identity, items=[1, 2], **objects)
    if partial:
        output = [next(stream)]
        stream.close()
    else:
        output = list(stream)
    return output, events


EXPRESSIONS = ['x and y', 'x or y', '(x and y) or z', 'x and (y or z)',
               '(x or y) and (z or x)', 'not (x and y) or z']


@pytest.mark.parametrize('expression', EXPRESSIONS)
@pytest.mark.parametrize('values', itertools.product((False, True), repeat=3))
@pytest.mark.parametrize('context', ['if', 'value'])
def test_native_truth_calls_values_and_output_chunks_are_preserved(tmp_path, expression, values, context):
    if context == 'if':
        source = '{% if ' + expression + ' %}yes{% else %}no{% endif %}'
    else:
        source = '{% set chosen = ' + expression + ' %}{{ chosen }}'
    path = tmp_path / 'roles/bind9/example.j2'
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding='utf-8')
    native = evaluate(source, values)
    with observed(tmp_path) as collector:
        actual = evaluate(source, values)
    assert actual == native
    assert not collector.unsupported


@pytest.mark.parametrize('escape,partial', itertools.product((False, True), repeat=2))
def test_native_finalization_filter_timing_and_safe_markup_are_preserved(tmp_path, escape, partial):
    source = 'literal\n{{ "text" | tag }}\n{{ 7 }}{{ x }}\n{{ y }}end'
    path = tmp_path / 'roles/bind9/example.j2'
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding='utf-8')
    native = evaluate(source, [True, False, True], escape, partial)
    with observed(tmp_path) as collector:
        actual = evaluate(source, [True, False, True], escape, partial)
    assert actual == native
    encoded = json.dumps(collector.payload())
    assert '<safe>' not in encoded and 'literal' not in encoded


@pytest.mark.parametrize('source', [
    '{% macro f() %}value{% endmacro %}{{f()}}',
    '{% if x %}yes{% elif y %}maybe{% endif %}',
    '{{ x < y < z }}', '{% for x in items recursive %}{{x}}{% endfor %}',
    '{{ x if y }}', '{% with x=1 %}{{x}}{% endwith %}',
])
def test_unverified_grammar_is_refused_instead_of_removed_from_inventory(tmp_path, source):
    path = tmp_path / 'roles/bind9/example.j2'
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding='utf-8')
    with pytest.raises(InstrumentationError):
        JinjaCollector(tmp_path)


def test_loop_exit_and_each_conditional_arm_have_actual_events(tmp_path):
    source = '{% for item in items %}{{item}}{% endfor %}{% set a = "yes" if x else "no" %}{{a}}'
    path = tmp_path / 'roles/bind9/example.j2'
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding='utf-8')
    with observed(tmp_path) as collector:
        assert ''.join(evaluate(source, [True, False, True])[0]) == '12yes'
        assert ''.join(evaluate(source, [False, False, True])[0]) == '12no'
    assert len(collector.branches) == 4
    assert all(set(values) == {False, True} for values in collector.conditions.values())


def test_double_installation_is_refused(tmp_path):
    (tmp_path / 'roles/bind9').mkdir(parents=True)
    with observed(tmp_path) as collector:
        with pytest.raises(InstrumentationError, match='already installed'):
            collector.install()


def test_nonboolean_comparison_is_not_coerced_and_blocks_admission(tmp_path):
    source = '{{ left == right }}'
    path = tmp_path / 'roles/bind9/example.j2'
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding='utf-8')
    class Result:
        def __bool__(self):
            raise AssertionError('Data result must not acquire a truth conversion')
        def __str__(self):
            return 'comparison data'
    class Comparable:
        def __eq__(self, other):
            return Result()
    with observed(tmp_path) as collector:
        assert jinja2.Environment().from_string(source).render(left=Comparable(), right=Comparable()) == 'comparison data'
    assert collector.unsupported and not collector.conditions


def test_exception_during_predicate_evaluation_does_not_invent_an_outcome(tmp_path):
    source = '{% if explode() %}yes{% else %}no{% endif %}'
    path = tmp_path / 'roles/bind9/example.j2'
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding='utf-8')
    calls = []
    def explode():
        calls.append(True)
        raise RuntimeError('Controlled predicate failure')
    with observed(tmp_path) as collector:
        with pytest.raises(RuntimeError, match='Controlled predicate failure'):
            jinja2.Environment().from_string(source).render(explode=explode)
    assert calls == [True]
    assert not collector.conditions and not collector.branches


def test_changed_canonical_file_is_refused_before_compilation(tmp_path):
    path = tmp_path / 'roles/bind9/example.j2'
    path.parent.mkdir(parents=True)
    path.write_text('{{ value }}', encoding='utf-8')
    with observed(tmp_path):
        path.write_text('changed {{ value }}', encoding='utf-8')
        with pytest.raises(InstrumentationError, match='source changed'):
            jinja2.Environment(loader=jinja2.FileSystemLoader(str(path.parent))).get_template(path.name)


def test_changed_parser_profile_cannot_reuse_another_executable_inventory(tmp_path):
    source = '{% if value %}\nline\n{% endif %}\nnext\n'
    path = tmp_path / 'roles/bind9/example.j2'
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding='utf-8')
    with observed(tmp_path):
        with pytest.raises(InstrumentationError, match='parser profile'):
            jinja2.Environment(trim_blocks=True).from_string(source)

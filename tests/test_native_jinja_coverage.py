"""Preserve native output types and refuse unsupported compiler profiles."""
import itertools
import json
from pathlib import Path
from types import SimpleNamespace

import jinja2
from jinja2.nativetypes import NativeCodeGenerator, NativeEnvironment
import pytest

from test_jinja_coverage_semantics import EXPRESSIONS, Value, observed, pytestmark, unobserved_parser
from bind9_coverage import InstrumentationError

pytestmark = pytestmark


def template_file(root, source):
    path = root / 'roles/bind9/example.j2'
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding='utf-8')
    return path


def evaluate_native(source, values):
    events = []
    objects = {name: Value(name, truth, events) for name, truth in zip(('x', 'y', 'z'), values)}
    def finalize(value):
        events.append(('finalize', type(value).__name__))
        return value
    environment = NativeEnvironment(undefined=jinja2.StrictUndefined, finalize=finalize)
    result = environment.from_string(source).render(**objects)
    if isinstance(result, Value):
        assert any(result is value for value in objects.values())
        result = ('original native object', result.name)
    return result, events


@pytest.mark.parametrize('expression', EXPRESSIONS)
@pytest.mark.parametrize('values', itertools.product((False, True), repeat=3))
@pytest.mark.parametrize('context', ['if', 'value'])
def test_native_boolean_calls_finalizer_order_and_operand_identity_are_preserved(tmp_path, expression, values, context):
    source = ('{% if ' + expression + ' %}yes{% else %}no{% endif %}' if context == 'if' else
              '{% set chosen = ' + expression + ' %}{{ chosen }}')
    template_file(tmp_path, source)
    with unobserved_parser():
        native = evaluate_native(source, values)
    with observed(tmp_path) as collector:
        actual = evaluate_native(source, values)
    assert actual == native
    assert not collector.unsupported


@pytest.mark.parametrize('value', [None, False, True, 0, 17, 'text', ['x', 2], {'a': 1}])
def test_native_data_types_are_not_coerced_into_text(tmp_path, value):
    source = '{{ value }}'
    template_file(tmp_path, source)
    with unobserved_parser():
        native = NativeEnvironment().from_string(source).render(value=value)
    with observed(tmp_path) as collector:
        actual = NativeEnvironment().from_string(source).render(value=value)
    assert actual == native and type(actual) is type(native)
    assert not collector.unsupported and collector.statements


def test_native_ansible_render_keeps_service_directives_and_never_reports_data(tmp_path):
    from ansible.parsing.dataloader import DataLoader
    from ansible.template import Templar
    path = Path(__file__).resolve().parents[1] / 'roles/bind9/templates/named-healthcheck.service.j2'
    source = path.read_text(encoding='utf-8')
    template_file(tmp_path, source)
    values = {'bind_back_ip': '192.0.2.11', 'bind_managed_zones': [{'domain': 'example.test'}]}
    with unobserved_parser():
        native = Templar(loader=DataLoader(), variables=values).template(source, convert_data=False)
    with observed(tmp_path) as collector:
        actual = Templar(loader=DataLoader(), variables=values).template(source, convert_data=False)
    assert actual == native
    assert 'User=root' in actual.splitlines()
    assert 'ExecStart=/usr/local/sbin/named-healthcheck 192.0.2.11 example.test' in actual.splitlines()
    assert not collector.unsupported
    encoded = json.dumps(collector.payload())
    assert '192.0.2.11' not in encoded and 'example.test' not in encoded


def test_native_parser_refusal_cannot_be_swallowed_as_an_unrendered_success(tmp_path):
    from ansible.errors import AnsibleError
    from ansible.parsing.dataloader import DataLoader
    from ansible.template import Templar
    source = '{% if value %}\nline\n{% endif %}\nnext\n'
    template_file(tmp_path, source)
    with observed(tmp_path) as collector:
        with pytest.raises(AnsibleError, match='parser profile'):
            Templar(loader=DataLoader(), variables={'value': True}).template(source)
    assert collector.unsupported and not collector.statements


def test_an_unknown_native_compiler_subclass_stays_refused(tmp_path):
    source = '{{ value }}'
    template_file(tmp_path, source)
    class UnverifiedCompiler(NativeCodeGenerator):
        pass
    environment = NativeEnvironment()
    environment.code_generator_class = UnverifiedCompiler
    with observed(tmp_path) as collector:
        with pytest.raises(InstrumentationError, match='compiler'):
            environment.from_string(source)
    assert collector.unsupported and not collector.statements
    assert environment.code_generator_class is UnverifiedCompiler


def test_nested_fixture_scope_does_not_borrow_outer_canonical_template_hits(tmp_path):
    source = '{% if value %}yes{% else %}no{% endif %}'
    first, second = tmp_path / 'first', tmp_path / 'second'
    template_file(first, source)
    template_file(second, source)
    with observed(first) as outer:
        previous = jinja2.Environment._parse
        def extra_native_layer(*args, **kwargs):
            return previous(*args, **kwargs)
        extra_native_layer._bind9_observer = SimpleNamespace(original_parse=previous)
        jinja2.Environment._parse = extra_native_layer
        before = outer.payload()
        try:
            with observed(second) as inner:
                assert jinja2.Environment().from_string(source).render(value=True) == 'yes'
            assert inner.statements and outer.payload() == before
        finally:
            jinja2.Environment._parse = previous


@pytest.mark.parametrize('failure', ['inventory', 'installation'])
def test_fixture_initialization_failure_restores_the_entire_outer_chain(tmp_path, monkeypatch, failure):
    from jinja_coverage import JinjaCollector
    first, second = tmp_path / 'first', tmp_path / 'second'
    template_file(first, '{{ value }}')
    template_file(second, '{% macro f() %}no{% endmacro %}' if failure == 'inventory' else '{{ value }}')
    with observed(first):
        previous = jinja2.Environment._parse
        if failure == 'installation':
            def refuse_installation(self):
                raise InstrumentationError('Controlled fixture installation refusal')
            monkeypatch.setattr(JinjaCollector, 'install', refuse_installation)
        with pytest.raises(InstrumentationError):
            with observed(second):
                raise AssertionError('A refused fixture must never enter its body')
        assert jinja2.Environment._parse is previous

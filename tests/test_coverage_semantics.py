"""Compare instrumented execution to Python itself, including side effects.

These tests validate the observer independently of application expectations.
They never attribute their own observations to production coverage.
"""
import ast
import builtins
import dis
import importlib.util
from pathlib import Path
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason='The QA collector requires Python 3.11')

SPEC = importlib.util.spec_from_file_location('quality_coverage', Path(__file__).resolve().parents[1] /
                                            'tools/quality/bind9_coverage.py')
Q = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(Q)


def run(source, instrumented):
    """Use separate namespaces and return observable values and call history."""
    collector = object.__new__(Q.PythonCollector)
    collector.conditions, collector.functions, collector.unsupported = {}, set(), set()
    collector.statements = set()
    namespace = {}
    if instrumented:
        tree = Q.Probes('semantic-corpus', source, {}).visit(ast.parse(source))
        ast.fix_missing_locations(tree)
        code = compile(tree, '<semantic-corpus>', 'exec')
        namespace.update({Q.Probes.NAMES['condition']: collector.truth,
                          Q.Probes.NAMES['value']: collector.value,
                          Q.Probes.NAMES['function']: collector.function,
                          Q.Probes.NAMES['comparand']: collector.comparand,
                          Q.Probes.NAMES['statement']: collector.statement,
                          Q.Probes.NAMES['operand']: collector.operand,
                          Q.Probes.NAMES['unwrap']: collector.unwrap})
    else:
        code = compile(source, '<semantic-corpus>', 'exec')
    exec(code, namespace)
    return namespace['result'], namespace['events'], collector


@pytest.mark.parametrize('expression', [
    'x and y', 'x or y', 'not x', 'not (x and y)', 'x and (y or x)',
    '(1 if x or y else 2)', '(1 if x and y else 2)',
    '[i for i in range(4) if i and i < 3]', '0 < mark(2) < 3',
    '3 < mark(2) < mark(7)', '(lambda z: z and 7)(x)',
])
@pytest.mark.parametrize('left,right', [(False, False), (False, True), (True, False), (True, True)])
def test_builtin_short_circuit_order_and_results_match_native_python(expression, left, right):
    source = ('events = []\nx, y = ' + repr((left, right)) + '\n'
              'def mark(value):\n events.append(value)\n return value\nresult = ' + expression)
    plain, events, _ = run(source, False)
    observed, actual, _ = run(source, True)
    assert observed == plain
    assert type(observed) is type(plain)
    assert actual == events


@pytest.mark.parametrize('expression', ['bool(x and y)', 'bool(x or y)', 'not x',
                                       '1 if x and y else 2', '1 if x or y else 2'])
def test_custom_truth_conversion_is_never_repeated(expression):
    source = '''events = []
class Truth:
 def __init__(self, name): self.name = name
 def __bool__(self):
  events.append(self.name)
  return True
x, y = Truth('x'), Truth('y')
result = ''' + expression
    plain, expected, _ = run(source, False)
    actual, observed, _ = run(source, True)
    assert actual == plain
    assert observed == expected


def test_actually_tested_custom_truth_is_observed_without_extra_coercion():
    source = '''events = []
class Truth:
 def __bool__(self):
  events.append('unexpected conversion')
  return True
x = Truth()
answer = x or False
result = answer is x
'''
    expected, events, _ = run(source, False)
    actual, observed, collector = run(source, True)
    assert actual is expected is True
    assert observed == events == ['unexpected conversion']
    assert not collector.unsupported
    assert any(outcomes == {True} for outcomes in collector.conditions.values())


def test_nonboolean_comparison_result_is_retained_without_coercion_and_refused():
    source = '''events = []
class Result:
 def __bool__(self):
  raise AssertionError('comparison result is only returned as data')
class Compare:
 def __eq__(self, other): return value
value = Result()
answer = Compare() == Compare()
result = answer is value
'''
    expected, events, _ = run(source, False)
    actual, observed, collector = run(source, True)
    assert actual is expected is True and observed == events == []
    assert collector.unsupported


@pytest.mark.parametrize('expression', ['(x and y) or z', 'x and (y or z)',
    '(x or y) and (z or x)', 'x and y and z', 'x or y or z', 'not (x and y) or z'])
@pytest.mark.parametrize('flags', [(a, b, c) for a in (False, True) for b in (False, True) for c in (False, True)])
def test_nested_data_boolean_trees_preserve_identity_and_each_truth_call(expression, flags):
    source = '''events = []
class Truth:
 def __init__(self, name, value): self.name, self.value = name, value
 def __bool__(self):
  events.append(self.name)
  return self.value
x, y, z = [Truth(name, value) for name, value in zip('xyz', ''' + repr(flags) + ''')]
answer = ''' + expression + '''
result = (answer is x, answer is y, answer is z, type(answer).__name__)
'''
    expected, events, _ = run(source, False)
    actual, observed, collector = run(source, True)
    assert actual == expected and observed == events
    assert not collector.unsupported


@pytest.mark.parametrize('expression', ['a < b < c', 'not (a < b < c)',
    '(a < b < c) or fallback', '(a < b < c) and fallback',
    '1 if a < b < c else 0', 'True if not (a < b < c) else False'])
@pytest.mark.parametrize('flags', [(False, False), (False, True), (True, False), (True, True)])
def test_chained_custom_comparisons_preserve_native_truth_retests(expression, flags):
    source = '''events = []
class Verdict:
 def __init__(self, name, truth): self.name, self.truth = name, truth
 def __bool__(self):
  events.append(('truth', self.name))
  return self.truth
class Item:
 def __init__(self, name, truth): self.name, self.truth = name, truth
 def __lt__(self, other):
  events.append(('compare', self.name, other.name))
  return Verdict(self.name, self.truth)
a, b, c = Item('a', ''' + repr(flags[0]) + '''), Item('b', ''' + repr(flags[1]) + '''), Item('c', True)
fallback = object()
answer = ''' + expression + '''
result = 'fallback' if answer is fallback else answer.name if isinstance(answer, Verdict) else answer
'''
    expected, events, _ = run(source, False)
    actual, observed, _ = run(source, True)
    assert actual == expected and observed == events


@pytest.mark.parametrize('expression', ['0 < (x := 1) < 2', '0 < locals().get("x", 1) < 2'])
def test_frame_sensitive_chain_is_refused_instead_of_changing_scope(expression):
    with pytest.raises(Q.InstrumentationError, match='Frame-sensitive'):
        Q.Probes('scope', expression, {}).visit(ast.parse(expression))


def test_collector_refuses_source_replaced_after_inventory(tmp_path):
    source = tmp_path / 'roles/bind9/files/test.py'
    source.parent.mkdir(parents=True)
    source.write_text('def f():\n return True\n')
    collector = Q.PythonCollector(tmp_path, tmp_path / 'observations')
    with pytest.raises(Q.InstrumentationError, match='source changed'):
        collector.compile('pass\n', source, 'exec')
    with pytest.raises(Q.InstrumentationError, match='Unverified AST'):
        collector.compile(ast.parse(source.read_text()), source, 'exec')
    assert callable(builtins.compile)


def test_unloaded_functions_and_conditions_remain_in_the_denominator(tmp_path):
    source = tmp_path / 'roles/bind9/files/unused.py'
    source.parent.mkdir(parents=True)
    source.write_text('def unused(x):\n return 1 if x else 2\n')
    collector = Q.PythonCollector(tmp_path, tmp_path / 'observations')
    units = collector.units['roles/bind9/files/unused.py']
    assert {unit['kind'] for unit in units.values()} == {'function', 'condition', 'statement'}
    assert not collector.functions and not collector.conditions


def test_docstrings_future_imports_and_statement_granularity_are_preserved():
    source = '''"module documentation"
from __future__ import annotations
events = []
class Example:
 "class documentation"
 def f(self, value: int):
  "function documentation"
  first = 1; second = 2
  return first + second
result = (__doc__, Example.__doc__, Example.f.__doc__, Example.f.__annotations__, Example().f(1))
'''
    expected, events, _ = run(source, False)
    actual, observed, collector = run(source, True)
    assert actual == expected and observed == events
    # import, events, class, def, two assignments, return, result = eight;
    # both assignments share a source line and must remain separate units.
    assert len(collector.statements) == 8
    assert sum(':8:' in key for key in collector.statements) == 2


def test_compile_inherits_the_original_callers_future_flags(tmp_path):
    collector = Q.PythonCollector(tmp_path, tmp_path / 'observations')
    source = '''from __future__ import annotations
namespace = {}
exec(compile('def f(value: Missing): pass', '<nested>', 'exec'), namespace)
result = namespace['f'].__annotations__
'''
    namespace = {'compile': collector.compile}
    exec(compile(source, '<future-caller>', 'exec'), namespace)
    assert namespace['result'] == {'value': 'Missing'}


def test_data_fallback_that_is_never_truth_tested_is_not_a_condition():
    source = '''events = []
class Fallback:
 def __bool__(self):
  raise AssertionError('fallback truth must never be requested')
fallback = Fallback()
value = None
result = (value or fallback) is fallback
'''
    expected, events, _ = run(source, False)
    actual, observed, collector = run(source, True)
    assert actual is expected is True and observed == events == []
    assert not collector.unsupported
    units = {}
    Q.Probes('fallback-inventory', source, units).visit(ast.parse(source))
    fallback_column = source.splitlines()[6].index('fallback)')
    assert not any(unit['kind'] == 'condition' and unit['line'] == 7 and
                   unit['column'] == fallback_column for unit in units.values())


def test_constant_loop_has_no_runtime_boolean_decision_in_native_bytecode():
    source = 'events = []\nwhile True:\n events.append(1)\n break\nresult = events\n'
    native = compile(source, '<native-constant-loop>', 'exec')
    assert not any('JUMP_IF' in instruction.opname or 'POP_JUMP' in instruction.opname
                   for instruction in dis.get_instructions(native))
    units = {}
    Q.Probes('constant-loop', source, units).visit(ast.parse(source))
    assert not any(unit['kind'] == 'condition' for unit in units.values())
    assert any(unit['kind'] == 'constant_decision' for unit in units.values())

"""Supplement coverage.py with observed function/elementary-condition coverage.

Only canonical, hash-checked production Python is compiled with probes. Native
coverage.py remains responsible for executable lines and branches. AST statement
entries are counted independently of lines; docstrings are metadata, not entries.
The collector records IDs and boolean outcomes, never configuration values.
Requires Python 3.11 on the QA controller; it is not deployed by the role.
"""
import ast
import __future__
import builtins
import hashlib
import json
import operator
import os
import sys
from pathlib import Path
import tempfile


class InstrumentationError(RuntimeError):
    """An unsupported observation must fail rather than silently lose a site."""


class Probes(ast.NodeTransformer):
    """Insert probes without evaluating a short-circuited operand."""

    NAMES = {'condition': '__bind9_cov_condition__', 'value': '__bind9_cov_value__',
             'function': '__bind9_cov_function__', 'comparand': '__bind9_cov_comparand__',
             'statement': '__bind9_cov_statement__', 'operand': '__bind9_cov_operand__',
             'unwrap': '__bind9_cov_unwrap__'}

    def __init__(self, name, source, units):
        self.name, self.source, self.units = name, source, units

    def unit(self, node, kind, slot=0):
        key = '{}:{}:{}:{}:{}:{}'.format(kind, node.lineno, node.col_offset,
                                        node.end_lineno, node.end_col_offset, slot)
        self.units[key] = {'kind': kind, 'line': node.lineno, 'column': node.col_offset,
                           'end_line': node.end_lineno, 'end_column': node.end_col_offset}
        return self.name + '|' + key

    def call(self, kind, node, *arguments):
        return ast.copy_location(ast.Call(func=ast.Name(id=self.NAMES[kind], ctx=ast.Load()),
                                         args=list(arguments), keywords=[]), node)

    def generic_visit(self, node):
        """Probe statement entry; preserve docstrings and future-import ordering."""
        for field, old_value in ast.iter_fields(node):
            if isinstance(old_value, list):
                values, futures = [], []
                for index, value in enumerate(old_value):
                    if not isinstance(value, ast.AST):
                        values.append(value)
                        continue
                    original = value
                    value = self.visit(value)
                    docstring = (field == 'body' and index == 0 and
                        isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and
                        isinstance(original, ast.Expr) and isinstance(original.value, ast.Constant) and
                        isinstance(original.value.value, str))
                    if isinstance(original, ast.stmt) and not docstring:
                        key = self.unit(original, 'statement')
                        probe = ast.copy_location(ast.Expr(value=self.call('statement', original, ast.Constant(key))), original)
                        if isinstance(original, ast.ImportFrom) and original.module == '__future__':
                            futures.append(probe)
                        else:
                            values.extend(futures)
                            futures = []
                            values.append(probe)
                    values.append(value)
                values.extend(futures)
                setattr(node, field, values)
            elif isinstance(old_value, ast.AST):
                setattr(node, field, self.visit(old_value))
        return node

    def leaf(self, node, truth):
        if truth and isinstance(node, ast.Constant):
            # CPython folds literal tests at compile time. Keep their inventory
            # as metadata instead of inventing a runtime False/True decision.
            self.unit(node, 'constant_decision')
            return node
        if isinstance(node, ast.BoolOp):
            if not truth:
                return self.call('unwrap', node, self.boolean_tree(node, False))
            node.values = [self.leaf(value, truth) for value in node.values]
            return node
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            node.operand = self.boolean_tree(node.operand, True)
            return node
        if isinstance(node, ast.Compare) and len(node.ops) > 1:
            node = self.comparison_tree(node)
            return node if truth else self.call('unwrap', node, node)
        key = self.unit(node, 'condition')
        # Visit children, not this node again: a comparison/test is one atom.
        node = self.generic_visit(node)
        return self.call('operand' if truth else 'value', node, ast.Constant(key), node)

    def comparison_tree(self, node):
        """Retain native chain evaluation and cleanup, including truth retests."""
        if any(type(op).__name__ not in PythonCollector.OPERATORS for op in node.ops):
            raise InstrumentationError('Identity/membership comparison chains are unsupported')
        for value in [node.left] + node.comparators:
            if any(isinstance(child, (ast.NamedExpr, ast.Yield, ast.YieldFrom, ast.Await)) or
                   (isinstance(child, ast.Call) and isinstance(child.func, ast.Name) and
                    child.func.id in ('super', 'locals', 'vars', 'eval', 'exec'))
                   for child in ast.walk(value)):
                raise InstrumentationError('Frame-sensitive comparison chain is unsupported')
        keys = [self.unit(node, 'condition', index) for index in range(len(node.ops))] + [None]
        values = [self.visit(value) for value in [node.left] + node.comparators]
        wrapped = [self.call('comparand', value, ast.Constant(key), value) for key, value in zip(keys, values)]
        node.left, node.comparators = wrapped[0], wrapped[1:]
        return node

    def boolean_tree(self, node, tested):
        """Preserve the compiler's short-circuit tree and return value identity.

        A data-valued and/or does not truth-test its final operand. Wrapping the
        whole native tree only once also preserves the compiler's elimination
        of repeated tests across nested and/or expressions.
        """
        if isinstance(node, ast.BoolOp):
            node.values = [self.boolean_tree(value, tested or index < len(node.values) - 1)
                           for index, value in enumerate(node.values)]
            return node
        if not tested:
            return self.visit(node)
        if isinstance(node, ast.Constant):
            self.unit(node, 'constant_decision')
            return node
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return self.visit_UnaryOp(node)
        if isinstance(node, ast.Compare) and len(node.ops) > 1:
            return self.comparison_tree(node)
        key = self.unit(node, 'condition')
        node = self.generic_visit(node)
        return self.call('operand', node, ast.Constant(key), node)

    def with_test(self, node):
        original = node.test
        node.test = ast.Constant(True)
        node = self.generic_visit(node)
        node.test = self.leaf(original, True)
        return node

    visit_If = visit_While = visit_IfExp = visit_Assert = with_test

    def visit_comprehension(self, node):
        node.iter = self.visit(node.iter)
        node.ifs = [self.leaf(value, True) for value in node.ifs]
        return node

    def visit_BoolOp(self, node):
        return self.leaf(node, False)

    def visit_Compare(self, node):
        return self.leaf(node, False)

    def visit_UnaryOp(self, node):
        if isinstance(node.op, ast.Not):
            node.operand = self.boolean_tree(node.operand, True)
            return node
        return self.generic_visit(node)

    def visit_FunctionDef(self, node):
        key = self.unit(node, 'function')
        node = self.generic_visit(node)
        index = int(bool(node.body and isinstance(node.body[0], ast.Expr) and
                         isinstance(node.body[0].value, ast.Constant) and
                         isinstance(node.body[0].value.value, str)))
        location = node.body[index] if index < len(node.body) else node
        probe = ast.copy_location(ast.Expr(value=self.call('function', location, ast.Constant(key))), location)
        node.body.insert(index, probe)
        return node

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Lambda(self, node):
        key = self.unit(node, 'function')
        node.args = self.visit(node.args)
        node.body = self.visit(node.body)
        node.body = ast.copy_location(ast.Subscript(
            value=ast.Tuple(elts=[self.call('function', node, ast.Constant(key)), node.body], ctx=ast.Load()),
            slice=ast.Constant(1), ctx=ast.Load()), node.body)
        return node


class BooleanOperand:
    """Record the one truth conversion requested by Python, retaining its value."""

    def __init__(self, collector, key, value, comparison=False):
        self.collector, self.key, self.value = collector, key, value
        self.comparison, self.tested = comparison, False

    def __bool__(self):
        self.tested = True
        return self.collector.truth(self.key, self.value)


class ComparisonOperand:
    """Delegate rich comparisons to the original operands and operator dispatch."""

    def __init__(self, collector, key, value):
        self.collector, self.key, self.value = collector, key, value

    def compare(self, operation, other):
        right = other.value if type(other) is ComparisonOperand else other
        value = self.collector.OPERATORS[operation](self.value, right)
        if type(value) is bool:
            self.collector.record(self.key, value)
        return BooleanOperand(self.collector, self.key, value, comparison=True)

    def __eq__(self, other):
        return self.compare('Eq', other)

    def __ne__(self, other):
        return self.compare('NotEq', other)

    def __lt__(self, other):
        return self.compare('Lt', other)

    def __le__(self, other):
        return self.compare('LtE', other)

    def __gt__(self, other):
        return self.compare('Gt', other)

    def __ge__(self, other):
        return self.compare('GtE', other)


class PythonCollector:
    """Own a static inventory and observations for exactly one source revision."""

    OPERATORS = {'Eq': operator.eq, 'NotEq': operator.ne, 'Lt': operator.lt,
                 'LtE': operator.le, 'Gt': operator.gt, 'GtE': operator.ge}

    def __init__(self, root, output):
        self.root, self.output = Path(root).resolve(), Path(output)
        self.original_compile = builtins.compile
        self.files, self.units, self.conditions, self.functions = {}, {}, {}, set()
        self.statements = set()
        self.unsupported = set()
        self.installed = False
        for path in sorted((self.root / 'roles/bind9').rglob('*.py')):
            name = path.relative_to(self.root).as_posix()
            raw = path.read_bytes()
            self.files[str(path.resolve())] = (name, hashlib.sha256(raw).hexdigest())
            units = self.units[name] = {}
            tree = ast.parse(raw, filename=str(path))
            Probes(name, raw.decode('utf-8'), units).visit(tree)

    def record(self, key, value):
        self.conditions.setdefault(key, set()).add(bool(value))

    def truth(self, key, value):
        """An actual truth context performs exactly one original truth conversion."""
        result = bool(value)
        self.record(key, result)
        return result

    def value(self, key, value):
        """Observe a computed Boolean comparison without a new truth conversion."""
        if type(value) is bool:
            self.record(key, value)
        else:
            self.unsupported.add(key)
        return value

    def operand(self, key, value):
        return BooleanOperand(self, key, value)

    @staticmethod
    def unwrap(value):
        if type(value) is BooleanOperand:
            if value.comparison and not value.tested and type(value.value) is not bool:
                value.collector.unsupported.add(value.key)
            return value.value
        return value

    def comparand(self, key, value):
        return ComparisonOperand(self, key, value)

    def function(self, key):
        self.functions.add(key)

    def statement(self, key):
        self.statements.add(key)

    def compile(self, source, filename, mode, flags=0, dont_inherit=False, optimize=-1, **kwargs):
        if not dont_inherit:
            # Replacing compile adds a Python frame; inherit from the original
            # caller rather than accidentally from this observer's module.
            future_mask = sum(getattr(__future__, name).compiler_flag for name in __future__.all_feature_names)
            flags |= sys._getframe(1).f_code.co_flags & future_mask
            dont_inherit = True
        filename = os.fspath(filename)
        entry = self.files.get(str(Path(filename).resolve())) if filename and not filename.startswith('<') else None
        if entry is None or mode != 'exec' or flags & ast.PyCF_ONLY_AST:
            return self.original_compile(source, filename, mode, flags, dont_inherit, optimize, **kwargs)
        if optimize > 0 or (optimize == -1 and sys.flags.optimize):
            raise InstrumentationError('Optimized Python would remove assertion instructions')
        if isinstance(source, ast.AST):
            raise InstrumentationError('Unverified AST supplied for a canonical production file')
        raw = source.encode('utf-8') if isinstance(source, str) else source
        name, expected = entry
        if hashlib.sha256(raw).hexdigest() != expected:
            raise InstrumentationError('Production source changed during condition instrumentation: ' + name)
        tree = ast.parse(raw, filename=filename)
        transformed = Probes(name, raw.decode('utf-8'), {}).visit(tree)
        ast.fix_missing_locations(transformed)
        return self.original_compile(transformed, filename, mode, flags, dont_inherit, optimize, **kwargs)

    def install(self):
        if any(hasattr(builtins, name) for name in Probes.NAMES.values()):
            raise InstrumentationError('A condition collector is already installed')
        for kind, name in Probes.NAMES.items():
            setattr(builtins, name, {'condition': self.truth, 'value': self.value,
                                   'function': self.function, 'comparand': self.comparand,
                                   'statement': self.statement, 'operand': self.operand,
                                   'unwrap': self.unwrap}[kind])
        builtins.compile = self.compile
        self.installed = True

    def close(self):
        if not self.installed:
            return
        builtins.compile = self.original_compile
        for name in Probes.NAMES.values():
            delattr(builtins, name)
        self.installed = False

    def save(self):
        """Persist one process's raw observations; merge only matching inventories."""
        payload = {'schema': 2, 'files': {v[0]: v[1] for v in self.files.values()},
                   'units': self.units, 'functions': sorted(self.functions),
                   'conditions': {key: sorted(value) for key, value in self.conditions.items()},
                   'statements': sorted(self.statements), 'unsupported': sorted(self.unsupported)}
        self.output.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.coverage-', dir=str(self.output))
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(payload, stream, sort_keys=True)
        os.replace(temporary, str(self.output / ('conditions-' + str(os.getpid()) + '.json')))

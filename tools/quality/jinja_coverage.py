"""Observe source-level Jinja execution; never relabel generated Python lines.

This QA-only observer supports the grammar used by the six shipped templates.
Native CodeGenerator output/finalization is retained. Unsupported grammar is a
refusal, not an empty denominator. Reports contain IDs/outcomes only.
"""
import hashlib
import json
import os
from pathlib import Path
import tempfile

import jinja2
from jinja2 import nodes
from jinja2.compiler import CodeGenerator

from bind9_coverage import InstrumentationError, PythonCollector


class TemplateInventory:
    """Map authored operations and predicates to stable source locations."""

    FORBIDDEN = (nodes.Macro, nodes.CallBlock, nodes.Block, nodes.Extends,
                 nodes.Include, nodes.Import, nodes.FromImport)
    SUPPORTED = {'Template', 'Output', 'TemplateData', 'Assign', 'If', 'CondExpr',
                 'For', 'Name', 'Const', 'Getattr', 'Getitem', 'Compare', 'Operand',
                 'Filter', 'List', 'And', 'Or', 'Not', 'Call', 'Keyword', 'Test'}

    def __init__(self, name, tree):
        self.name, self.units = name, {}
        self.entries, self.atoms, self.unwraps, self.arms = {}, {}, set(), {}
        self.decisions, self.loop_bodies = {}, {}
        self.references = []
        self.index = 0
        self.walk(tree)

    def unit(self, node, kind, lines=None):
        key = '{}:{}:{}:{}'.format(kind, type(node).__name__, node.lineno, self.index)
        self.index += 1
        full = self.name + '|' + key
        self.units[key] = {'kind': kind, 'line': node.lineno, 'lines': lines or [node.lineno]}
        return full

    def walk(self, node, truth=False, boolean_child=False):
        # Optimizer replaces literal children. Retain original nodes so their
        # Python object IDs cannot be recycled for different generated nodes.
        self.references.append(node)
        if isinstance(node, self.FORBIDDEN):
            raise InstrumentationError('Unsupported Jinja scope: ' + type(node).__name__)
        if type(node).__name__ not in self.SUPPORTED:
            raise InstrumentationError('Unsupported Jinja node: ' + type(node).__name__)
        if isinstance(node, (nodes.Assign, nodes.If, nodes.For)):
            self.entries[id(node)] = self.unit(node, 'statement')
        if isinstance(node, (nodes.If, nodes.CondExpr, nodes.For)):
            self.decisions[id(node)] = self.unit(node, 'branch')
        if isinstance(node, nodes.Output):
            for child in node.nodes:
                lines = [child.lineno]
                if isinstance(child, nodes.TemplateData):
                    count = child.data.count('\n') + int(not child.data.endswith('\n'))
                    lines = list(range(child.lineno, child.lineno + max(count, 1)))
                self.entries[id(child)] = self.unit(child, 'statement', lines)
        if isinstance(node, nodes.For):
            if node.recursive:
                raise InstrumentationError('Recursive Jinja loops are unsupported')
            self.loop_bodies[id(node.body)] = self.decisions[id(node)]
        if isinstance(node, nodes.If):
            if node.elif_:
                raise InstrumentationError('Jinja elif requires a separately verified observer')
            self.walk(node.test, True)
            for child in node.body + node.else_:
                self.walk(child)
            return
        if isinstance(node, nodes.CondExpr):
            if node.expr2 is None:
                raise InstrumentationError('Jinja conditional expressions require an explicit else')
            self.arms[id(node.expr1)] = self.decisions[id(node)], True
            self.arms[id(node.expr2)] = self.decisions[id(node)], False
            self.walk(node.test, True)
            self.walk(node.expr1)
            self.walk(node.expr2)
            return
        if isinstance(node, (nodes.And, nodes.Or)):
            if not truth and not boolean_child:
                self.unwraps.add(id(node))
            self.walk(node.left, True, True)
            self.walk(node.right, truth, True)
            return
        if isinstance(node, nodes.Not):
            self.walk(node.node, True, True)
            return
        if truth:
            if isinstance(node, nodes.Const):
                self.unit(node, 'constant_decision')
            else:
                self.atoms[id(node)] = self.unit(node, 'condition'), 'operand'
        elif isinstance(node, nodes.Compare):
            self.atoms[id(node)] = self.unit(node, 'condition'), 'value'
        if isinstance(node, nodes.Compare) and len(node.ops) != 1:
            raise InstrumentationError('Jinja comparison chains require a separately verified observer')
        for field, value in node.iter_fields():
            children = value if isinstance(value, list) else [value]
            for child in children:
                if isinstance(child, nodes.Node):
                    self.walk(child, isinstance(node, nodes.For) and field == 'test')


class JinjaCollector:
    """Observe canonical template bytes through native Jinja compilation."""

    ATTRIBUTE = '_bind9_template_probe'

    def __init__(self, root):
        if jinja2.__version__ != '3.1.6':
            raise InstrumentationError('Jinja observer requires the inspected 3.1.6 compiler')
        self.root = Path(root).resolve()
        self.files, self.units, self.by_digest = {}, {}, {}
        self.statements, self.lines, self.branches = set(), set(), set()
        self.conditions, self.unsupported = {}, set()
        self.environments = {}
        self.original_parse = jinja2.Environment._parse
        self.installed = False
        for path in sorted((self.root / 'roles/bind9').rglob('*.j2')):
            name = path.relative_to(self.root).as_posix()
            raw = path.read_bytes()
            source = raw.decode('utf-8')
            tree = self.original_parse(jinja2.Environment(), source, name, str(path))
            inventory = TemplateInventory(name, tree)
            digest = hashlib.sha256(raw).hexdigest()
            if digest in self.by_digest:
                raise InstrumentationError('Ambiguous identical Jinja sources')
            self.files[name], self.units[name] = digest, inventory.units
            self.by_digest[digest] = name

    def probe(self, method, key, value=None):
        if method == 'entry':
            self.statements.add(key)
            name, unit = key.split('|', 1)
            self.lines.update(name + '|line:' + str(line) for line in self.units[name][unit]['lines'])
        elif method == 'branch':
            self.branches.add((key, value))
        elif method == 'operand':
            return PythonCollector.operand(self, key, value)
        elif method == 'value':
            return PythonCollector.value(self, key, value)
        elif method == 'unwrap':
            return PythonCollector.unwrap(value)
        else:
            raise InstrumentationError('Unknown Jinja observation method')

    def record(self, key, value):
        self.conditions.setdefault(key, set()).add(value)

    def truth(self, key, value):
        return PythonCollector.truth(self, key, value)

    @classmethod
    def generator(cls):
        """Extend native emission; do not turn template data into finalized values."""
        class ObservingGenerator(CodeGenerator):
            inventory = None

            def probe(self, method, key, value=None):
                suffix = '' if value is None else ', ' + value
                return 'environment.' + cls.ATTRIBUTE + '(' + repr(method) + ', ' + repr(key) + suffix + ')'

            def visit_Template(self, node, frame=None):
                self.inventory = getattr(node, '_bind9_inventory', None)
                self.output_constants = []
                return super().visit_Template(node, frame)

            def visit(self, node, frame=None):
                inv = self.inventory
                if inv is None:
                    return super().visit(node, frame)
                identity = id(node)
                atom, arm = inv.atoms.get(identity), inv.arms.get(identity)
                if arm:
                    self.write('(' + self.probe('branch', arm[0], repr(arm[1])) + ', ')
                if identity in inv.unwraps:
                    self.write('environment.' + cls.ATTRIBUTE + '("unwrap", "", ')
                if atom:
                    self.write('environment.' + cls.ATTRIBUTE + '(' + repr(atom[1]) + ', ' + repr(atom[0]) + ', ')
                result = super().visit(node, frame)
                if atom:
                    self.write(')')
                if identity in inv.unwraps:
                    self.write(')')
                if arm:
                    self.write(')[1]')
                return result

            def visit_Assign(self, node, frame):
                if self.inventory is not None:
                    self.writeline(self.probe('entry', self.inventory.entries[id(node)]), node)
                return super().visit_Assign(node, frame)

            def visit_If(self, node, frame):
                if self.inventory is None:
                    return super().visit_If(node, frame)
                # The supported grammar has no elif/scoped blocks. Keep the
                # native truth context and soft frame, adding only branch entries.
                inv, branch, soft = self.inventory, self.inventory.decisions[id(node)], frame.soft()
                self.writeline(self.probe('entry', inv.entries[id(node)]), node)
                self.writeline('if ', node)
                self.visit(node.test, soft)
                self.write(':')
                self.indent()
                self.writeline(self.probe('branch', branch, 'True'), node)
                self.blockvisit(node.body, soft)
                self.outdent()
                self.writeline('else:')
                self.indent()
                self.writeline(self.probe('branch', branch, 'False'), node)
                self.blockvisit(node.else_, soft)
                self.outdent()

            def visit_For(self, node, frame):
                if self.inventory is None:
                    return super().visit_For(node, frame)
                inv = self.inventory
                self.writeline(self.probe('entry', inv.entries[id(node)]), node)
                result = super().visit_For(node, frame)
                # Native for-loop exit is distinct from entering its body.
                self.writeline(self.probe('branch', inv.decisions[id(node)], 'False'), node)
                return result

            def visit_CondExpr(self, node, frame):
                if self.inventory is None:
                    return super().visit_CondExpr(node, frame)
                # Follow optimizeconst exactly once, then bind its current arm
                # nodes. Generic optimization may replace both literal arms
                # while retaining the conditional expression itself.
                if self.optimizer is not None and not frame.eval_ctx.volatile:
                    optimized = self.optimizer.visit(node, frame.eval_ctx)
                    if optimized is not node:
                        return self.visit(optimized, frame)
                branch = self.inventory.decisions[id(node)]
                self.inventory.references.extend((node.expr1, node.expr2))
                self.inventory.arms[id(node.expr1)] = branch, True
                self.inventory.arms[id(node.expr2)] = branch, False
                return CodeGenerator.visit_CondExpr.__wrapped__(self, node, frame)

            def blockvisit(self, body, frame):
                if self.inventory is not None and id(body) in self.inventory.loop_bodies:
                    self.writeline(self.probe('branch', self.inventory.loop_bodies[id(body)], 'True'))
                return super().blockvisit(body, frame)

            def _output_child_to_const(self, node, frame, finalize):
                value = super()._output_child_to_const(node, frame, finalize)
                if self.inventory is not None:
                    self.output_constants.append(node)
                return value

            def _output_const_repr(self, group):
                value = super()._output_const_repr(group)
                if self.inventory is None:
                    return value
                children, self.output_constants = self.output_constants[:len(group)], self.output_constants[len(group):]
                probes = [self.probe('entry', self.inventory.entries[id(node)]) for node in children]
                return '(' + ', '.join(probes + [value]) + ')[-1]'

            def _output_child_pre(self, node, frame, finalize):
                super()._output_child_pre(node, frame, finalize)
                if self.inventory is not None:
                    self.write('(' + self.probe('entry', self.inventory.entries[id(node)]) + ', ')

            def _output_child_post(self, node, frame, finalize):
                if self.inventory is not None:
                    self.write(')[1]')
                super()._output_child_post(node, frame, finalize)

        return ObservingGenerator

    def install(self):
        if self.installed or getattr(jinja2.Environment._parse, '_bind9_observer', False):
            raise InstrumentationError('A Jinja collector is already installed')
        collector, generator = self, self.generator()
        def parse(environment, source, name, filename):
            tree = collector.original_parse(environment, source, name, filename)
            digest = hashlib.sha256(source.encode('utf-8')).hexdigest()
            canonical = collector.by_digest.get(digest)
            if canonical is None:
                if filename and str(Path(filename).resolve()) in {str(collector.root / key) for key in collector.files}:
                    raise InstrumentationError('Canonical Jinja source changed')
                return tree
            if environment.is_async or environment.sandboxed or environment.extensions:
                raise InstrumentationError('Unsupported Jinja runtime mode')
            if environment not in collector.environments:
                if environment.code_generator_class is not CodeGenerator or hasattr(environment, cls_attribute):
                    raise InstrumentationError('Unsupported or occupied Jinja compiler')
                collector.environments[environment] = environment.code_generator_class
                environment.code_generator_class = generator
                setattr(environment, cls_attribute, collector.probe)
            inventory = TemplateInventory(canonical, tree)
            if inventory.units != collector.units[canonical]:
                raise InstrumentationError('Jinja parser profile changes the executable inventory')
            tree._bind9_inventory = inventory
            return tree
        cls_attribute = self.ATTRIBUTE
        parse._bind9_observer = self
        jinja2.Environment._parse = parse
        self.installed = True

    def close(self):
        if not self.installed:
            return
        jinja2.Environment._parse = self.original_parse
        for environment, generator in self.environments.items():
            environment.code_generator_class = generator
        self.installed = False

    def payload(self):
        return {'schema': 1, 'engine': 'jinja2-3.1.6-native-codegen', 'files': self.files, 'functions': [],
                'units': self.units, 'statements': sorted(self.statements), 'lines': sorted(self.lines),
                'branches': sorted(self.branches), 'conditions': {key: sorted(values) for key, values in self.conditions.items()},
                'unsupported': sorted(self.unsupported)}

    def save(self, output):
        """Persist source IDs and outcomes privately, without rendered values."""
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.templates-', dir=str(output))
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(self.payload(), stream, sort_keys=True)
        os.replace(temporary, str(output / ('templates-' + str(os.getpid()) + '.json')))

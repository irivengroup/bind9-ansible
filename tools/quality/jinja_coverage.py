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
from types import MethodType
import weakref

import jinja2
from jinja2 import nodes
from jinja2.compiler import CodeGenerator
from jinja2.nativetypes import NativeCodeGenerator

from bind9_coverage import InstrumentationError, PythonCollector


class JinjaObservationError(InstrumentationError, jinja2.TemplateSyntaxError):
    """Make an observer refusal visible to Ansible's compilation boundary.

    Templar returns its input on generic compiler exceptions. A syntax-family
    refusal propagates instead; the unsupported receipt also blocks admission.
    """

    def __init__(self, message):
        jinja2.TemplateSyntaxError.__init__(self, message, 0)


class InactiveProbe:
    """Keep retained compiled templates usable after their collector closes.

    This stateless sentinel releases the closed collector. Operand/value probes
    become identities; no new truth conversion or measurement occurs. Only our
    own sentinel may be replaced by a subsequent collector on the environment.
    """

    def __call__(self, method, key, value=None):
        if method in ('operand', 'value', 'unwrap', 'conditional_value', 'native_result'):
            return value
        if method in ('entry', 'branch'):
            return None
        raise InstrumentationError('Unknown inactive observation method')


class CompilerOwnership:
    """Switch between inspected sibling observers without borrowing ownership.

    Ansible reuses and copies environments across YAML fields and file renders.
    Only exact registered generators with their own bound owner probe may cross
    that boundary. A known closed owner stays inert. Weak registrations retain
    provenance until the last copied compiler disappears, without retaining its
    owner. An inherited class name or MRO proves nothing.
    """

    OWNERS = weakref.WeakKeyDictionary()

    @classmethod
    def register(cls, owner, generators):
        for compiler, family in generators.items():
            if compiler in cls.OWNERS or family not in (CodeGenerator, NativeCodeGenerator):
                raise InstrumentationError('Invalid or duplicate native compiler ownership')
            cls.OWNERS[compiler] = (weakref.ref(owner), owner.ATTRIBUTE, family)

    @staticmethod
    def bound(probe, owner):
        return (type(probe) is MethodType and probe.__self__ is owner and
                probe.__func__ is type(owner).probe)

    @classmethod
    def activate(cls, owner, environment, generators):
        compiler = environment.code_generator_class
        if compiler in (CodeGenerator, NativeCodeGenerator):
            probe = getattr(environment, owner.ATTRIBUTE, None)
            if (hasattr(environment, owner.ATTRIBUTE) and not isinstance(probe, InactiveProbe) and
                    not (environment in owner.environments and cls.bound(probe, owner))):
                raise InstrumentationError('Unsupported or occupied Jinja compiler')
            family = compiler
        else:
            registration = cls.OWNERS.get(compiler)
            sibling = registration[0]() if registration else None
            if (sibling is None or
                    not cls.bound(getattr(environment, registration[1], None), sibling)):
                raise InstrumentationError('Unsupported or occupied Jinja compiler')
            family = registration[2]
        if environment in owner.environments and owner.environments[environment] is not family:
            raise InstrumentationError('Native compiler family changed during observation')
        owner.environments[environment] = family
        environment.code_generator_class = generators[family]
        setattr(environment, owner.ATTRIBUTE, owner.probe)

class TemplateInventory:
    """Map authored operations and predicates to stable source locations."""

    FORBIDDEN = (nodes.Macro, nodes.CallBlock, nodes.Block, nodes.Extends,
                 nodes.Include, nodes.Import, nodes.FromImport)
    SUPPORT_ELIF = False
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
            if node.elif_ and not self.SUPPORT_ELIF:
                raise InstrumentationError('Jinja elif requires a separately verified observer')
            self.walk(node.test, True)
            for child in node.elif_:
                self.walk(child)
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
    # These are the six literal template sources consumed by the shipped role.
    # Native Ansible trims block newlines, changing four authored inventories.
    # Keep both complete inventories; never substitute the smaller native one.
    ANSIBLE_SOURCES = frozenset('roles/bind9/templates/' + name for name in (
        'authoritative.zone.j2', 'empty-root.zone.j2', 'named-healthcheck.service.j2',
        'named-runtime-override.j2', 'named.conf.j2', 'reverse.zone.j2'))
    ANSIBLE_PROFILE = dict(block_start_string='{%', block_end_string='%}',
        variable_start_string='{{', variable_end_string='}}', comment_start_string='{#', comment_end_string='#}',
        line_statement_prefix=None, line_comment_prefix=None, trim_blocks=True, lstrip_blocks=False,
        newline_sequence='\n', keep_trailing_newline=False)

    def __init__(self, root):
        if jinja2.__version__ != '3.1.6':
            raise InstrumentationError('Jinja observer requires the inspected 3.1.6 compiler')
        self.root = Path(root).resolve()
        self.files, self.units, self.by_digest = {}, {}, {}
        self.plain_units, self.native_units, self.profile_units = {}, {}, {}
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
            self.files[name], self.units[name] = digest, inventory.units.copy()
            self.plain_units[name] = inventory.units
            if name in self.ANSIBLE_SOURCES:
                native_tree = self.original_parse(jinja2.Environment(**self.ANSIBLE_PROFILE), source, name, str(path))
                native = TemplateInventory(name + '|ansible', native_tree)
                if native.units != inventory.units:
                    self.native_units[name] = native.units
                    prefixed = {'ansible|' + key: value for key, value in native.units.items()}
                    self.units[name].update(prefixed)
                    self.profile_units[name] = {'plain': inventory.units, 'ansible': prefixed}
            self.by_digest[digest] = name

    @classmethod
    def known_ansible_profile(cls, environment):
        """Admit only the inspected Ansible classes and exact lexer settings."""
        if type(environment).__module__ != 'ansible.template':
            return False
        from ansible import release
        if release.__version__ != '2.16.19':
            return False
        from ansible.template import AnsibleEnvironment, AnsibleNativeEnvironment
        return (type(environment) in (AnsibleEnvironment, AnsibleNativeEnvironment) and
                all(getattr(environment, key) == value for key, value in cls.ANSIBLE_PROFILE.items()))

    def probe(self, method, key, value=None):
        if not self.installed:
            # Ansible copies environment dictionaries, including our bound
            # probe. A clone not parsed before close is outside the registry;
            # retained compiled generators must never mutate a closed receipt.
            return InactiveProbe()(method, key, value)
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
        if self.installed:
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
                conditional = getattr(inv, 'conditional_values', {}).get(identity)
                if conditional is not None:
                    self.write('environment.' + cls.ATTRIBUTE + '("conditional_value", ' + repr(conditional) + ', ')
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
                if conditional is not None:
                    self.write(')')
                return result

            def visit_Assign(self, node, frame):
                if self.inventory is not None:
                    self.writeline(self.probe('entry', self.inventory.entries[id(node)]), node)
                return super().visit_Assign(node, frame)

            def visit_If(self, node, frame):
                if self.inventory is None:
                    return super().visit_If(node, frame)
                # Enter each elif only after the preceding test was false.
                # Inventory policies still refuse this grammar unless their
                # native runtime profile has separate equivalence tests.
                inv, branch, soft = self.inventory, self.inventory.decisions[id(node)], frame.soft()
                for current in [node] + node.elif_:
                    branch = inv.decisions[id(current)]
                    self.writeline(self.probe('entry', inv.entries[id(current)]), current)
                    self.writeline('if ', current)
                    self.visit(current.test, soft)
                    self.write(':')
                    self.indent()
                    self.writeline(self.probe('branch', branch, 'True'), current)
                    self.blockvisit(current.body, soft)
                    self.outdent()
                    self.writeline('else:')
                    self.indent()
                    self.writeline(self.probe('branch', branch, 'False'), current)
                self.blockvisit(node.else_, soft)
                for current in [node] + node.elif_:
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
        # Ansible uses NativeCodeGenerator even for textual template files.
        # MRO keeps native constant/output/finalizer methods beneath our probes.
        native_generator = type('ObservingNativeJinjaGenerator', (generator, NativeCodeGenerator), {})
        generators = {CodeGenerator: generator, NativeCodeGenerator: native_generator}
        CompilerOwnership.register(self, {value: key for key, value in generators.items()})
        def refuse(message):
            collector.unsupported.add(message)
            raise JinjaObservationError(message)
        def parse(environment, source, name, filename):
            digest = hashlib.sha256(source.encode('utf-8')).hexdigest()
            canonical = collector.by_digest.get(digest)
            if canonical is None:
                if filename and str(Path(filename).resolve()) in {str(collector.root / key) for key in collector.files}:
                    refuse('Canonical Jinja source changed')
                return collector.original_parse(environment, source, name, filename)
            if environment.is_async or environment.sandboxed or environment.extensions:
                refuse('Unsupported Jinja runtime mode')
            try:
                CompilerOwnership.activate(collector, environment, generators)
            except InstrumentationError as error:
                refuse(str(error))
            try:
                tree = collector.original_parse(environment, source, name, filename)
                inventory = TemplateInventory(canonical, tree)
            except (InstrumentationError, jinja2.TemplateSyntaxError):
                refuse('Canonical Jinja source cannot use this parser profile')
            if inventory.units != collector.plain_units[canonical]:
                if (not collector.known_ansible_profile(environment) or
                        inventory.units != collector.native_units.get(canonical)):
                    refuse('Jinja parser profile changes the executable inventory')
                inventory = TemplateInventory(canonical + '|ansible', tree)
            tree._bind9_inventory = inventory
            return tree
        parse._bind9_observer = self
        jinja2.Environment._parse = parse
        self.installed = True

    def close(self):
        if not self.installed:
            return
        jinja2.Environment._parse = self.original_parse
        for environment, generator in self.environments.items():
            environment.code_generator_class = generator
            setattr(environment, self.ATTRIBUTE, InactiveProbe())
        self.environments.clear()
        self.installed = False

    def payload(self):
        return {'schema': 1, 'engine': 'jinja2-3.1.6-native-codegen', 'files': self.files, 'functions': [],
                'units': self.units, 'statements': sorted(self.statements), 'lines': sorted(self.lines),
                'branches': sorted(self.branches), 'conditions': {key: sorted(values) for key, values in self.conditions.items()},
                'unsupported': sorted(self.unsupported)}

    def checked_payload(self, data, allow_unsupported=False):
        """Validate worker and parent facts against this same full inventory."""
        if (not isinstance(data, dict) or type(data.get('schema')) is not int or data['schema'] != 1 or
                data.get('engine') != 'jinja2-3.1.6-native-codegen' or data.get('files') != self.files or
                data.get('units') != self.units):
            raise InstrumentationError('Stale or incomplete Jinja source/unit inventory')
        if (data.get('functions') != [] or not isinstance(data.get('unsupported'), list) or
                any(not isinstance(value, str) for value in data['unsupported']) or
                (data['unsupported'] and not allow_unsupported)):
            raise InstrumentationError('Unsupported Jinja observation; authored functions are not supported by this grammar')
        known = {name + '|' + key: unit['kind'] for name, units in self.units.items() for key, unit in units.items()}
        lines = {name + '|line:' + str(line) for name, units in self.units.items()
                 for unit in units.values() if unit['kind'] == 'statement' for line in unit['lines']}
        for field, expected in (('statements', 'statement'), ('lines', None)):
            values = data.get(field)
            if not isinstance(values, list) or any(not isinstance(key, str) or
                    (key not in lines if field == 'lines' else known.get(key) != expected) for key in values):
                raise InstrumentationError('Unknown Jinja ' + ('executable line' if field == 'lines' else 'statement'))
        conditions = data.get('conditions')
        if (not isinstance(conditions, dict) or any(known.get(key) != 'condition' or not isinstance(values, list) or
                any(type(value) is not bool for value in values) for key, values in conditions.items())):
            raise InstrumentationError('Unknown Jinja condition inventory or nonboolean outcomes')
        branches = data.get('branches')
        if (not isinstance(branches, list) or any(not isinstance(pair, list) or len(pair) != 2 or
                not isinstance(pair[0], str) or known.get(pair[0]) != 'branch' or type(pair[1]) is not bool for pair in branches)):
            raise InstrumentationError('Unknown Jinja predicate/branch or nonboolean observation')
        return data

    def combine_payloads(self, payloads):
        """Union only validated facts; caller retains bounded worker identities."""
        if not payloads or len(payloads) > 4096:
            raise InstrumentationError('Invalid Jinja process receipt count')
        sets = {key: set() for key in ('statements', 'lines', 'branches', 'unsupported')}
        conditions = {}
        for payload in payloads:
            self.checked_payload(payload, allow_unsupported=True)
            for key in sets:
                sets[key].update(tuple(value) if key == 'branches' else value for value in payload[key])
            for key, values in payload['conditions'].items():
                conditions.setdefault(key, set()).update(values)
        combined = self.payload()
        combined.update({key: [list(value) if isinstance(value, tuple) else value for value in sorted(values)]
                         for key, values in sets.items()})
        combined['conditions'] = {key: sorted(values) for key, values in conditions.items()}
        return combined

    def save(self, output):
        """Persist source IDs and outcomes privately, without rendered values."""
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.templates-', dir=str(output))
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(self.payload(), stream, sort_keys=True)
        os.replace(temporary, str(output / ('templates-' + str(os.getpid()) + '.json')))

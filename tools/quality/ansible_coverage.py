"""Observe canonical YAML fields and native Ansible task/conditional execution.

Native task dispatch, committed block recovery, static loading and templated
data fields are distinct from predicate evaluation. Embedded scripts need their
own language observer. Source
positions must come from Ansible's loader; matching text alone is insufficient
to attribute an execution to a component. No inventory or secret values are
written to the receipt. Requires the QA controller's inspected ansible-core
2.16.19 and Jinja 3.1.6; never installed on a managed host.
"""
from contextlib import contextmanager
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import weakref

from ansible import constants, release
from ansible.parsing.dataloader import DataLoader
from ansible.playbook.conditional import Conditional
from ansible.executor.task_executor import TaskExecutor
from ansible.executor.play_iterator import PlayIterator, IteratingStates
from ansible.executor.task_queue_manager import TaskQueueManager
from ansible.playbook.task_include import TaskInclude
from ansible.playbook.role_include import IncludeRole
from ansible.playbook.role import Role
from ansible.playbook.helpers import load_list_of_tasks
from ansible.plugins.action import ActionBase
from ansible.plugins.strategy import StrategyBase
from ansible.template import Templar, _escape_backslashes
import jinja2
from jinja2 import nodes
from jinja2.compiler import CodeGenerator
from jinja2.nativetypes import NativeCodeGenerator
import yaml

from bind9_coverage import BooleanOperand, InstrumentationError, PythonCollector
from jinja_coverage import CompilerOwnership, InactiveProbe, JinjaCollector, TemplateInventory


class AnsibleExpressionInventory(TemplateInventory):
    """Inventory authored task fields with Ansible's native template grammar."""

    SUPPORTED = TemplateInventory.SUPPORTED | {'Dict', 'Pair', 'Tuple', 'Concat',
        'Add', 'Sub', 'Mul', 'Div', 'FloorDiv', 'Mod', 'Pow', 'Neg', 'Pos', 'Slice', 'NSRef'}
    SUPPORT_ELIF = True

    def __init__(self, name, tree, line, column):
        self.origin_line, self.origin_column = line, column
        super().__init__(name, tree)

    def unit(self, node, kind, lines=None):
        key = super().unit(node, kind, lines)
        unit = self.units[key.split('|', 1)[1]]
        unit['line'] += self.origin_line - 1
        unit['lines'] = [value + self.origin_line - 1 for value in unit['lines']]
        unit['column'] = self.origin_column
        return key


class PredicateInventory(AnsibleExpressionInventory):
    """Measure the authored expression inside the inspected native wrapper.

    Core 2.16.19 preserves the result for a native type inspection. Generated
    list/type nodes are metadata, never extra authored statements/conditions.
    """

    def __init__(self, name, tree, line, column):
        if (len(tree.body) != 2 or type(tree.body[0]) is not nodes.Assign or
                type(tree.body[0].target) is not nodes.Name or tree.body[0].target.name != '__cres' or
                type(tree.body[1]) is not nodes.Output or len(tree.body[1].nodes) != 1):
            raise InstrumentationError('Unknown native conditional inspection wrapper')
        assignment, output = tree.body
        sequence = output.nodes[0]
        if type(sequence) is not nodes.List or len(sequence.items) != 2:
            raise InstrumentationError('Unknown native conditional result sequence')
        decision, type_name = sequence.items
        if (type(decision) is not nodes.CondExpr or type(decision.test) is not nodes.Name or
                decision.test.name != '__cres' or type(decision.expr1) is not nodes.Const or
                decision.expr1.value is not True or type(decision.expr2) is not nodes.Const or
                decision.expr2.value is not False or type(type_name) is not nodes.Getattr or
                type_name.attr != '__name__' or type(type_name.node) is not nodes.Getattr or
                type_name.node.attr != '__class__' or type(type_name.node.node) is not nodes.Name or
                type_name.node.node.name != '__cres'):
            raise InstrumentationError('Unknown native conditional type inspection')
        self.expression = assignment.node
        super().__init__(name, self.expression, line, column)
        # Retain every original node even if the native optimizer replaces it.
        self.references.extend([tree] + list(tree.find_all(nodes.Node)))
        self.entries[id(assignment)] = self.unit(assignment, 'statement')
        self.entries[id(sequence)] = self.unit(sequence, 'native_wrapper')
        branch = self.unit(decision, 'branch')
        self.decisions[id(decision)] = branch
        self.arms[id(decision.expr1)], self.arms[id(decision.expr2)] = (branch, True), (branch, False)
        self.conditional_values = {id(self.expression): self.name}
        self.atoms[id(decision.test)] = self.name, 'native_result'

    def walk(self, node, truth=False, boolean_child=False):
        if node is self.expression:
            truth = True
        if isinstance(node, nodes.CondExpr) and truth:
            # A guard tests the value of its selected arm too. Neither arm's
            # truth may be inferred from the branch selecting it.
            self.references.append(node)
            self.decisions[id(node)] = self.unit(node, 'branch')
            self.arms[id(node.expr1)] = self.decisions[id(node)], True
            self.arms[id(node.expr2)] = self.decisions[id(node)], False
            self.walk(node.test, True)
            self.walk(node.expr1, True)
            if node.expr2 is None:
                raise InstrumentationError('Native guard ternaries require an explicit else')
            self.walk(node.expr2, True)
            return
        super().walk(node, truth, boolean_child)

class YamlInventory:
    """Retain every YAML file and classify data, task and predicate surfaces."""

    CONDITIONAL_KEYS = {'when', 'until', 'failed_when', 'changed_when'}
    MAX_BYTES = 4 * 1024 * 1024
    MAX_NODES = 100000
    MAX_DEPTH = 128

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.files, self.components, self.predicates, self.positions = {}, {}, {}, {}
        self.templates, self.template_positions = {}, {}
        self.by_label = {}
        self.environment = Templar(loader=DataLoader()).environment
        for folder in ('roles/bind9', 'playbooks'):
            for path in sorted((self.root / folder).rglob('*.yml')):
                name = path.relative_to(self.root).as_posix()
                raw = self.source_bytes(path)
                self.files[name] = hashlib.sha256(raw).hexdigest()
                self.components[name] = {'tasks': {}, 'templates': {}, 'predicates': {},
                    'embedded_scripts': {},
                    'data_only': name in ('roles/bind9/defaults/main.yml', 'roles/bind9/meta/main.yml')}
                tree = yaml.compose(raw.decode('utf-8'), Loader=yaml.SafeLoader)
                self.visits = 0
                if tree is not None:
                    self.walk(name, tree, ())

    @classmethod
    def source_bytes(cls, path):
        """Bound the read itself and refuse special files before any read."""
        descriptor = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
        with os.fdopen(descriptor, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise InstrumentationError('YAML source must be a regular file')
            raw = stream.read(cls.MAX_BYTES + 1)
        if len(raw) > cls.MAX_BYTES:
            raise InstrumentationError('YAML source exceeds the inventory byte budget')
        return raw

    @staticmethod
    def location(node):
        return node.start_mark.line + 1, node.start_mark.column + 1

    def step(self, path):
        """Bound alias expansion and deeply nested source structures."""
        self.visits += 1
        if self.visits > self.MAX_NODES or len(path) > self.MAX_DEPTH:
            raise InstrumentationError('YAML source exceeds the inventory node/depth budget')

    def predicate(self, name, node, path):
        self.step(path)
        if isinstance(node, yaml.SequenceNode):
            for index, child in enumerate(node.value):
                self.predicate(name, child, path + (index,))
            return
        if not isinstance(node, yaml.ScalarNode):
            raise InstrumentationError('Ansible condition must be a scalar or scalar list: ' + name)
        line, column = self.location(node)
        key = 'predicate:{}:{}'.format(line, column)
        if node.tag == 'tag:yaml.org,2002:bool':
            self.components[name]['predicates'][key] = {'kind': 'constant', 'line': line, 'column': column}
            return
        if node.tag != 'tag:yaml.org,2002:str':
            raise InstrumentationError('Unverified conditional scalar type: ' + name)
        expression = node.value
        if '{{' in expression or '{%' in expression:
            raise InstrumentationError('Templated conditions need a separately verified two-pass observer: ' + name)
        wrapper = ('{% set __cres = ' + expression +
            ' %}{{ [true if __cres else false, __cres.__class__.__name__] }}')
        source = _escape_backslashes(wrapper, self.environment)
        tree = jinja2.Environment._parse(self.environment, source, None, None)
        label = name + '#' + key
        inventory = PredicateInventory(label, tree, line, column)
        full = name + '|' + key
        self.predicates[full] = {'file': name, 'label': label, 'source': source, 'inventory': inventory,
                                 'expression': expression, 'line': line, 'column': column}
        self.by_label[label] = self.predicates[full]
        self.components[name]['predicates'][key] = {'kind': 'runtime', 'line': line, 'column': column,
                                                   'units': inventory.units}
        position = name, line, column
        if position in self.positions:
            raise InstrumentationError('Ambiguous Ansible source position')
        self.positions[position] = full

    def template(self, name, node):
        """Bind each data field to its native loader position, not matching text."""
        line, column = self.location(node)
        key = 'template:{}:{}'.format(line, column)
        label = name + '#' + key
        source = _escape_backslashes(node.value, self.environment)
        tree = jinja2.Environment._parse(self.environment, source, None, None)
        inventory = AnsibleExpressionInventory(label, tree, line, column)
        entry = {'file': name, 'label': label, 'source': source, 'inventory': inventory,
                 'expression': node.value, 'line': line, 'column': column, 'kind': 'template'}
        position = name, line, column
        if position in self.template_positions:
            raise InstrumentationError('Ambiguous Ansible template source position')
        self.templates[name + '|' + key] = entry
        self.by_label[label] = entry
        self.template_positions[position] = name + '|' + key
        self.components[name]['templates'][key] = {'line': line, 'column': column,
                                                   'units': inventory.units}

    def walk(self, name, node, path):
        self.step(path)
        component = self.components[name]
        if isinstance(node, yaml.MappingNode):
            pairs = {}
            for key, child in node.value:
                if not isinstance(key, yaml.ScalarNode) or key.value in pairs:
                    raise InstrumentationError('Non-scalar or duplicate YAML key: ' + name)
                pairs[key.value] = (key, child)
            # A task has a module action or a structural block. Literal defaults
            # and Galaxy metadata remain in scope with zero executable tasks.
            actions = [key for key in pairs if key.startswith('ansible.builtin.') or key == 'bind9_manage']
            structural = 'block' in pairs
            play = 'hosts' in pairs
            if actions or structural or play:
                component['data_only'] = False
                start = self.location(node)
                identifier = 'task:{}:{}'.format(*start)
                component['tasks'][identifier] = {'line': start[0], 'column': start[1],
                    'action': actions[0] if actions else ('play' if play else 'block'),
                    'recovery': 'rescue' in pairs, 'always': 'always' in pairs, 'loop': 'loop' in pairs}
            for key, (_, child) in pairs.items():
                # A copied script is executable source too. Recording the copy
                # action or rendering its text cannot cover that script's body.
                if key in ('content', 'ansible.builtin.shell'):
                    destination = pairs.get('dest', (None, None))[1]
                    script_destination = (isinstance(destination, yaml.ScalarNode) and
                                          (destination.value.endswith('.sh') or destination.value.startswith('/etc/profile.d/')))
                    if (key == 'ansible.builtin.shell' or script_destination or
                            (isinstance(child, yaml.ScalarNode) and child.value.lstrip().startswith('#!'))):
                        line, column = self.location(child)
                        component['embedded_scripts']['script:{}:{}'.format(line, column)] = {
                            'line': line, 'column': column,
                            'sha256': hashlib.sha256(yaml.serialize(child).encode('utf-8')).hexdigest()}
                if key == 'roles' or (key == 'dependencies' and name == 'roles/bind9/meta/main.yml' and child.value):
                    if not isinstance(child, yaml.SequenceNode):
                        raise InstrumentationError('Role invocations must be a sequence: ' + name)
                    for role in child.value:
                        line, column = self.location(role)
                        component['tasks']['role:{}:{}'.format(line, column)] = {
                            'line': line, 'column': column, 'action': 'role',
                            'recovery': False, 'always': False, 'loop': False}
                    component['data_only'] = False
                if key in self.CONDITIONAL_KEYS or (key == 'that' and path and path[-1] == 'ansible.builtin.assert'):
                    self.predicate(name, child, path + (key,))
                else:
                    self.walk(name, child, path + (key,))
        elif isinstance(node, yaml.SequenceNode):
            for index, child in enumerate(node.value):
                self.walk(name, child, path + (index,))
        elif isinstance(node, yaml.ScalarNode) and node.tag == 'tag:yaml.org,2002:str':
            if '{{' in node.value or '{%' in node.value or '{#' in node.value:
                component['data_only'] = False
                self.template(name, node)

    def task_profile_complete(self, name):
        """Recognize only the whole grammar supported by native dispatch hooks."""
        component = self.components[name]
        return bool(component['tasks']) and not component['embedded_scripts']

    def executable_units(self, name):
        """Keep every authored unit in an admitted task file's denominator."""
        result = {}
        for entry in list(self.predicates.values()) + list(self.templates.values()):
            if entry['file'] == name:
                result.update({entry['label'] + '|' + key: unit for key, unit in entry['inventory'].units.items()
                               if unit['kind'] != 'native_wrapper'})
        for key, task in self.components[name]['tasks'].items():
            result[name + '|' + key] = {'kind': 'statement', 'lines': [task['line']]}
            if task['loop']:
                result[name + '|' + key + ':loop'] = {'kind': 'branch', 'lines': [task['line']]}
            if task['recovery']:
                result[name + '|' + key + ':rescue'] = {'kind': 'branch', 'lines': [task['line']]}
        return result


class AnsibleCollector:
    """Observe actual native conditional calls with loader-proven provenance."""

    ATTRIBUTE = '_bind9_ansible_predicate_probe'
    STATIC_LOADER_CODE = load_list_of_tasks.__code__
    SET_FIELDS = ('statements', 'lines', 'branches', 'attempted_tasks', 'dispatched_tasks',
                  'task_loops', 'entered_blocks', 'block_rescues', 'entered_imports',
                  'entered_plays', 'entered_roles', 'unsupported')

    def __init__(self, root):
        self.controller = self.controller_profile()
        self.inventory = YamlInventory(root)
        self.root = self.inventory.root
        self.statements, self.lines, self.branches = set(), set(), set()
        self.conditions, self.unsupported = {}, set()
        self.environments, self.active = {}, []
        self.attempted_tasks, self.dispatched_tasks, self.task_loops = set(), set(), set()
        self.entered_blocks, self.block_rescues = set(), set()
        self.iterator_context, self.rescued_instances = [], set()
        # Native HostState defines equality without hashing. Index weak
        # references by identity and verify `is` at commit, never by equality.
        self.pending_iterator = {}
        self.entered_imports, self.entered_plays, self.entered_roles = set(), set(), set()
        self.role_invocations = {}
        self.owner_pid = os.getpid()
        self.entry_count = 0
        self.parse_count = 0
        self.original_conditional = Conditional._check_conditional
        self.original_parse = jinja2.Environment._parse
        self.template_collector = None
        self.original_template = Templar.template
        self.installed = False

    @staticmethod
    def controller_profile():
        """Bind native security inspections; never enable their escape hatch."""
        if release.__version__ != '2.16.19' or jinja2.__version__ != '3.1.6':
            raise InstrumentationError('Ansible observer requires inspected core 2.16.19 / Jinja 3.1.6')
        disabled = constants.config.get_config_value('_DISABLE_BACKPORTED_INSPECTIONS')
        broken = constants.config.get_config_value('ALLOW_BROKEN_CONDITIONALS')
        if disabled is not False or type(broken) is not bool:
            raise InstrumentationError('Native conditional security inspections must remain enabled')
        return {'core': release.__version__, 'jinja': jinja2.__version__,
                'backported_inspections': True, 'allow_broken_conditionals': broken}

    @classmethod
    def controller_matches(cls, value):
        """Boolean settings are typed evidence; integer equality is insufficient."""
        expected = cls.controller_profile()
        return (type(value) is dict and set(value) == set(expected) and
            type(value.get('core')) is str and type(value.get('jinja')) is str and
            type(value.get('backported_inspections')) is bool and
            type(value.get('allow_broken_conditionals')) is bool and value == expected)

    def refresh_template_collector(self):
        """Resolve the live same-root sibling when installation actually starts."""
        self.template_collector = None
        parser, seen = self.original_parse, set()
        while getattr(parser, '_bind9_observer', None):
            if id(parser) in seen or len(seen) >= 16:
                raise InstrumentationError('Cyclic or excessive parser ownership chain')
            seen.add(id(parser))
            owner = parser._bind9_observer
            if type(owner) is JinjaCollector and owner.root == self.root and owner.installed:
                self.template_collector = owner
                break
            parser = owner.original_parse

    def resolve(self, expression, surface='predicate'):
        position = getattr(expression, 'ansible_pos', None)
        if position is None:
            return None
        filename, line, column = position
        path = Path(filename).resolve()
        try:
            name = path.relative_to(self.root).as_posix()
        except ValueError:
            return None
        if name not in self.inventory.files:
            return None
        if hashlib.sha256(self.inventory.source_bytes(path)).hexdigest() != self.inventory.files[name]:
            raise InstrumentationError('Canonical YAML source changed during observation')
        positions = self.inventory.positions if surface == 'predicate' else self.inventory.template_positions
        entries = self.inventory.predicates if surface == 'predicate' else self.inventory.templates
        identity = positions.get((name, line, column))
        if identity is None:
            return None
        entry = entries[identity]
        if str(expression) != entry['expression']:
            raise InstrumentationError('Conditional source position has different content')
        return entry

    def resolve_static_import(self, expression, caller):
        """Recover the source lost by native ModuleArgsParser normalization.

        Matching text alone is insufficient. The exact inspected loader frame,
        its real TaskInclude object, argument object identity and unchanged
        DataLoader mapping must all identify the same original authored field.
        No template is re-evaluated or synthetic source position manufactured.
        """
        if caller.f_code is not self.STATIC_LOADER_CODE or not isinstance(expression, str):
            return None
        matches = {}
        for name in ('t', 'parent_include'):
            task = caller.f_locals.get(name)
            if not isinstance(task, TaskInclude) or expression is not task.args.get('_raw_params'):
                continue
            source = self.source_task(task)
            if source is None or source[1]['action'].split('.')[-1] != 'import_tasks':
                continue
            field = task._ds.get(source[1]['action'])
            if isinstance(field, dict):
                field = field.get('file', field.get('_raw_params'))
            entry = self.resolve(field, 'template')
            if entry is not None and expression == entry['expression']:
                matches[entry['label']] = entry
        if len(matches) > 1:
            raise InstrumentationError('Ambiguous native static import field')
        return next(iter(matches.values())) if matches else None

    def record(self, key, value):
        if self.installed:
            self.conditions.setdefault(key, set()).add(value)

    def source_task(self, task, prefix='task'):
        """Resolve a native object's unchanged original YAML task mapping."""
        data = getattr(task, '_ds', None)
        position = getattr(data, 'ansible_pos', None)
        if position is None:
            return None
        filename, line, column = position
        path = Path(filename).resolve()
        try:
            name = path.relative_to(self.root).as_posix()
        except ValueError:
            return None
        if name not in self.inventory.files:
            return None
        if hashlib.sha256(self.inventory.source_bytes(path)).hexdigest() != self.inventory.files[name]:
            raise InstrumentationError('Canonical task source changed during observation')
        key = '{}:{}:{}'.format(prefix, line, column)
        unit = self.inventory.components[name]['tasks'].get(key)
        return (name + '|' + key, unit) if unit is not None else None

    def task_entry(self, task):
        """Resolve a native Task's original mapping and unchanged action."""
        source = self.source_task(task)
        if source is None:
            return None
        key, unit = source
        if unit['action'] in ('block', 'play', 'role'):
            return None
        if task.action.split('.')[-1] != unit['action'].split('.')[-1]:
            raise InstrumentationError('Native task action differs from its canonical source')
        return key

    def block_entry(self, block):
        """Implicit native blocks must not impersonate their task mapping."""
        source = self.source_task(block)
        if source is not None and source[1]['action'] == 'block':
            return source[0]
        return None

    def iterator_block(self, state):
        if state.cur_block < len(state._blocks):
            block = state._blocks[state.cur_block]
            key = self.block_entry(block)
            if key is not None:
                return key, block._uuid
        return None

    def truth(self, key, value):
        return PythonCollector.truth(self, key, value)

    def probe(self, method, key, value=None):
        if not self.installed:
            return InactiveProbe()(method, key, value)
        if method in ('conditional_value', 'native_result'):
            if not self.active or self.active[-1]['label'] != key:
                raise InstrumentationError('Native conditional result lost its authored context')
            entry = self.active[-1]
            if method == 'conditional_value':
                operand = value if type(value) is BooleanOperand else None
                if operand is not None and (operand.collector is not self or
                        operand.key.split('|', 1)[0] != key or
                        operand.key.split('|', 1)[1] not in entry['inventory'].units):
                    raise InstrumentationError('Native conditional result has a foreign operand')
                entry['result_operand'] = operand
                return PythonCollector.unwrap(value)
            operand = entry.get('result_operand')
            if operand is not None:
                if operand.value is not value:
                    raise InstrumentationError('Native conditional result identity changed before inspection')
                return operand
            return value
        if method == 'entry':
            label, unit = key.split('|', 1)
            entry = self.inventory.by_label[label]
            descriptor = entry['inventory'].units[unit]
            if descriptor['kind'] == 'native_wrapper':
                return None
            self.entry_count += 1
            self.statements.add(key)
            self.lines.update(label + '|line:' + str(line) for line in descriptor['lines'])
        elif method == 'branch':
            self.branches.add((key, value))
        elif method == 'operand':
            return PythonCollector.operand(self, key, value)
        elif method == 'value':
            return PythonCollector.value(self, key, value)
        elif method == 'unwrap':
            return PythonCollector.unwrap(value)
        else:
            raise InstrumentationError('Unknown Ansible observation method')

    def install(self):
        if self.installed or getattr(Conditional._check_conditional, '_bind9_observer', False):
            raise InstrumentationError('Ansible conditional collector already installed')
        # Capture the live chain at installation, including the Jinja collector
        # when present; constructor-time hooks may belong to a closed scope.
        self.original_conditional = Conditional._check_conditional
        self.original_parse = jinja2.Environment._parse
        self.refresh_template_collector()
        self.original_template = Templar.template
        self.original_executor = TaskExecutor.run
        self.original_loop = TaskExecutor._run_loop
        self.original_action_init = ActionBase.__init__
        self.original_next_host = PlayIterator.get_next_task_for_host
        self.original_next_state = PlayIterator._get_next_task_from_state
        self.original_mark_failed = PlayIterator.mark_host_failed
        self.original_fail_state = PlayIterator._set_failed_state
        self.original_set_state = PlayIterator.set_state_for_host
        self.original_task_include_load = TaskInclude.load
        self.original_role_include_load = IncludeRole.load
        self.original_role_load = Role.load
        self.original_queue_run = TaskQueueManager.run
        self.original_meta = StrategyBase._execute_meta
        collector = self
        generator_base = type('AnsibleGeneratorBase', (JinjaCollector,), {'ATTRIBUTE': self.ATTRIBUTE})
        generator = generator_base.generator()
        # AnsibleEnvironment uses NativeCodeGenerator even with jinja2_native
        # disabled. Keep its output/finalization methods in super()'s MRO.
        native_generator = type('ObservingNativeAnsibleGenerator', (generator, NativeCodeGenerator), {})
        generators = {CodeGenerator: generator, NativeCodeGenerator: native_generator}
        CompilerOwnership.register(self, {value: key for key, value in generators.items()})

        @wraps(self.original_next_host)
        def next_host(instance, host, peek=False):
            events = {'host': host.name, 'entries': set(), 'rescues': set(), 'instances': set(), 'roles': set()}
            collector.iterator_context.append(events)
            try:
                state, task = collector.original_next_host(instance, host, peek=peek)
                if peek:
                    # The linear strategy peeks, then commits that very state
                    # only when scheduling the task. Uncommitted peeks expire.
                    identity = id(state)
                    def expired(reference):
                        collector.pending_iterator.pop(identity, None)
                    collector.pending_iterator[identity] = (weakref.ref(state, expired), events)
                else:
                    commit_events(events)
                return state, task
            finally:
                collector.iterator_context.pop()

        def commit_events(events):
            collector.entered_blocks.update(events['entries'])
            collector.block_rescues.update(events['rescues'])
            collector.rescued_instances.update(events['instances'])
            collector.entered_roles.update(events['roles'])

        @wraps(self.original_set_state)
        def set_state(instance, host_name, state):
            result = collector.original_set_state(instance, host_name, state)
            pending = collector.pending_iterator.pop(id(state), None)
            if pending is not None and pending[0]() is state:
                events = pending[1]
                if events['host'] != host_name:
                    raise InstrumentationError('Iterator state committed for a different host')
                commit_events(events)
            return result

        @wraps(self.original_mark_failed)
        def mark_failed(instance, host):
            events = {'host': host.name, 'entries': set(), 'rescues': set(), 'instances': set(), 'roles': set()}
            collector.iterator_context.append(events)
            try:
                result = collector.original_mark_failed(instance, host)
                commit_events(events)
                return result
            finally:
                collector.iterator_context.pop()

        def observe_recovery(before, state, prior_state, prior_index):
            if before is None or not collector.iterator_context:
                return
            key, identity = before
            events = collector.iterator_context[-1]
            token = events['host'], identity
            if prior_state == IteratingStates.TASKS:
                if state.run_state == IteratingStates.RESCUE and state.cur_block == prior_index:
                    events['instances'].add(token)
                    events['rescues'].add((key, True))
                elif (state.cur_block != prior_index or state.run_state in (IteratingStates.ALWAYS, IteratingStates.COMPLETE)):
                    task = collector.inventory.components[key.split('|', 1)[0]]['tasks'][key.split('|', 1)[1]]
                    if task['recovery'] and token not in collector.rescued_instances and token not in events['instances']:
                        events['rescues'].add((key, False))

        @wraps(self.original_fail_state)
        def fail_state(instance, state):
            before, prior_state, prior_index = collector.iterator_block(state), state.run_state, state.cur_block
            result = collector.original_fail_state(instance, state)
            observe_recovery(before, result, prior_state, prior_index)
            return result

        @wraps(self.original_next_state)
        def next_state(instance, state, host):
            before, prior_state, prior_index = collector.iterator_block(state), state.run_state, state.cur_block
            observed = bool(collector.iterator_context)
            if observed and before is not None:
                collector.iterator_context[-1]['entries'].add(before[0])
            result, task = collector.original_next_state(instance, state, host=host)
            if observed:
                # A call can advance across an implicit block into an explicit
                # one. The returned task's native ancestry proves that entry.
                parent = getattr(task, '_parent', None)
                role = getattr(task, '_role', None)
                invocation = collector.role_invocations.get(id(role))
                if invocation is not None and invocation[0]() is role:
                    collector.iterator_context[-1]['roles'].add(invocation[1])
                while parent is not None:
                    key = collector.block_entry(parent)
                    if key is not None:
                        collector.iterator_context[-1]['entries'].add(key)
                    parent = getattr(parent, '_parent', None)
                observe_recovery(before, result, prior_state, prior_index)
            return result, task

        def import_entry(task):
            key = collector.task_entry(task)
            if key is not None and task.action.split('.')[-1] in ('import_tasks', 'import_role'):
                # Static imports execute in the native loading phase even
                # when a condition will later skip all expanded child tasks.
                collector.entered_imports.add(key)

        @wraps(self.original_task_include_load)
        def task_include_load(*args, **kwargs):
            task = collector.original_task_include_load(*args, **kwargs)
            import_entry(task)
            return task

        @wraps(self.original_role_include_load)
        def role_include_load(*args, **kwargs):
            task = collector.original_role_include_load(*args, **kwargs)
            import_entry(task)
            return task

        @wraps(self.original_role_load)
        def role_load(role_include, *args, **kwargs):
            role = collector.original_role_load(role_include, *args, **kwargs)
            source = collector.source_task(role_include, 'role')
            if source is not None and source[1]['action'] == 'role':
                identity = id(role)
                def expired(reference):
                    collector.role_invocations.pop(identity, None)
                collector.role_invocations[identity] = (weakref.ref(role, expired), source[0])
            return role

        @wraps(self.original_queue_run)
        def queue_run(instance, play):
            source = collector.source_task(play)
            if source is not None and source[1]['action'] == 'play':
                collector.entered_plays.add(source[0])
            return collector.original_queue_run(instance, play)

        @wraps(self.original_meta)
        def meta(instance, task, *args, **kwargs):
            key = collector.task_entry(task)
            if key is not None:
                collector.attempted_tasks.add(key)
            results = collector.original_meta(instance, task, *args, **kwargs)
            if key is not None and any(not result._result.get('skipped') for result in results):
                collector.dispatched_tasks.add(key)
            return results

        def parse(environment, source, name, filename):
            tree = collector.original_parse(environment, source, name, filename)
            if not collector.active or source != collector.active[-1]['source']:
                return tree
            entry = collector.active[-1]
            if environment.is_async or environment.sandboxed or environment.extensions:
                raise InstrumentationError('Unverified Ansible template runtime profile')
            CompilerOwnership.activate(collector, environment, generators)
            inventory_type = AnsibleExpressionInventory if entry.get('kind') == 'template' else PredicateInventory
            inventory = inventory_type(entry['label'], tree, entry['line'], entry['column'])
            if inventory.units != entry['inventory'].units:
                raise InstrumentationError('Ansible parser profile changed the predicate inventory')
            tree._bind9_inventory = inventory
            collector.parse_count += 1
            return tree

        def template(instance, variable, *args, **kwargs):
            entry = collector.resolve(variable, 'template')
            if entry is None:
                entry = collector.resolve_static_import(variable, sys._getframe(1))
            if entry is None:
                return collector.original_template(instance, variable, *args, **kwargs)
            collector.active.append(entry)
            try:
                before = collector.parse_count
                result = collector.original_template(instance, variable, *args, **kwargs)
                if collector.parse_count == before:
                    # Templar returns numeric/bool/null single references before
                    # invoking Jinja. This is one output operation, no predicate.
                    units = entry['inventory'].units
                    if (len(units) != 1 or not instance.SINGLE_VAR.match(str(variable)) or
                            next(iter(units.values()))['kind'] != 'statement'):
                        raise InstrumentationError('Native Ansible data compilation was not observed')
                    collector.probe('entry', entry['label'] + '|' + next(iter(units)))
                return result
            finally:
                collector.active.pop()

        def check(instance, conditional, templar, all_vars):
            entry = collector.resolve(conditional)
            if entry is None:
                return collector.original_conditional(instance, conditional, templar, all_vars)
            collector.active.append(entry)
            try:
                before = collector.entry_count
                result = collector.original_conditional(instance, conditional, templar, all_vars)
                if collector.entry_count == before:
                    raise InstrumentationError('Native Ansible conditional compilation was not observed')
                return result
            finally:
                collector.active.pop()

        def action_init(instance, *args, **kwargs):
            collector.original_action_init(instance, *args, **kwargs)
            original_run = instance.run
            reference = weakref.ref(collector)

            @wraps(original_run)
            def run(*run_args, **run_kwargs):
                observer = reference()
                if observer is not None and observer.installed:
                    key = observer.task_entry(instance._task)
                    if key is not None:
                        observer.dispatched_tasks.add(key)
                return original_run(*run_args, **run_kwargs)

            instance.run = run

        @wraps(self.original_loop)
        def loop(instance, items):
            key = collector.task_entry(instance._task)
            if key is not None:
                # Native run() calls _run_loop only for a nonempty list.
                collector.task_loops.add((key, True))
            result = collector.original_loop(instance, items)
            if key is not None:
                # This version's native loop has no break: a normal return
                # follows iterator exhaustion, unlike an exception/interrupt.
                collector.task_loops.add((key, False))
            return result

        @wraps(self.original_executor)
        def execute(instance):
            key = collector.task_entry(instance._task)
            if key is not None:
                collector.attempted_tasks.add(key)
            try:
                result = collector.original_executor(instance)
                if key is not None:
                    if result.get('skipped') is True and result.get('skipped_reason') == 'No items in the list':
                        collector.task_loops.add((key, False))
                    if instance._task.action.split('.')[-1] in ('include_tasks', 'include_role') and not result.get('skipped'):
                        collector.dispatched_tasks.add(key)
                return result
            finally:
                # Ansible workers exit without Python atexit handlers. Persist
                # only source IDs/outcomes before returning to that worker.
                output = os.environ.get('BIND9_CONDITION_COVERAGE')
                if output and os.getpid() != collector.owner_pid:
                    collector.save(output)

        check._bind9_observer = self
        # Nested QA scopes must see both owned parser layers, not only the
        # underlying Jinja hook; otherwise an outer collector can borrow hits.
        parse._bind9_observer = self
        jinja2.Environment._parse = parse
        Conditional._check_conditional = check
        Templar.template = template
        ActionBase.__init__ = action_init
        TaskExecutor.run = execute
        TaskExecutor._run_loop = loop
        PlayIterator.get_next_task_for_host = next_host
        PlayIterator._get_next_task_from_state = next_state
        PlayIterator.mark_host_failed = mark_failed
        PlayIterator._set_failed_state = fail_state
        PlayIterator.set_state_for_host = set_state
        TaskInclude.load = staticmethod(task_include_load)
        IncludeRole.load = staticmethod(role_include_load)
        Role.load = staticmethod(role_load)
        TaskQueueManager.run = queue_run
        StrategyBase._execute_meta = meta
        self.installed = True

    def close(self):
        if self.installed:
            Conditional._check_conditional = self.original_conditional
            jinja2.Environment._parse = self.original_parse
            Templar.template = self.original_template
            ActionBase.__init__ = self.original_action_init
            TaskExecutor.run = self.original_executor
            TaskExecutor._run_loop = self.original_loop
            PlayIterator.get_next_task_for_host = self.original_next_host
            PlayIterator._get_next_task_from_state = self.original_next_state
            PlayIterator.mark_host_failed = self.original_mark_failed
            PlayIterator._set_failed_state = self.original_fail_state
            PlayIterator.set_state_for_host = self.original_set_state
            TaskInclude.load = staticmethod(self.original_task_include_load)
            IncludeRole.load = staticmethod(self.original_role_include_load)
            Role.load = staticmethod(self.original_role_load)
            TaskQueueManager.run = self.original_queue_run
            StrategyBase._execute_meta = self.original_meta
            self.pending_iterator.clear()
            self.role_invocations.clear()
            for environment, generator in self.environments.items():
                environment.code_generator_class = generator
                setattr(environment, self.ATTRIBUTE, InactiveProbe())
            self.environments.clear()
            self.installed = False

    @contextmanager
    def observing(self):
        self.install()
        try:
            yield self
        finally:
            self.close()

    def payload(self):
        data = {'schema': 1, 'engine': 'ansible-core-2.16.19-native-conditional', 'controller_profile': self.controller,
                'files': self.inventory.files, 'components': self.inventory.components,
                'statements': sorted(self.statements), 'lines': sorted(self.lines),
                'branches': sorted(self.branches),
                'conditions': {key: sorted(values) for key, values in self.conditions.items()},
                'attempted_tasks': sorted(self.attempted_tasks), 'dispatched_tasks': sorted(self.dispatched_tasks),
                'task_loops': sorted(self.task_loops),
                'entered_blocks': sorted(self.entered_blocks), 'block_rescues': sorted(self.block_rescues),
                'entered_imports': sorted(self.entered_imports), 'entered_plays': sorted(self.entered_plays),
                'entered_roles': sorted(self.entered_roles),
                'unsupported': sorted(self.unsupported),
                'complete': False,
                'limits': ['Embedded script bodies remain unmeasured; every observation requires native loader positions']}
        if self.template_collector is not None:
            # Nested inside the existing worker envelope: its unchanged byte,
            # process-count and aggregate budgets cover both languages jointly.
            data['template_observations'] = self.template_collector.payload()
        return data

    def save(self, output):
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix='.ansible-', dir=str(output))
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(self.payload(), stream, sort_keys=True)
        os.replace(temporary, str(output / ('ansible-' + str(os.getpid()) + '.json')))

    @classmethod
    def combine(cls, paths, inventory, expected=None, template_inventory=None):
        """Validate every worker before union; bool/int equality cannot hide data."""
        if not paths or len(paths) > 4096:
            raise InstrumentationError('Invalid Ansible process receipt count')
        entries = list(inventory.predicates.values()) + list(inventory.templates.values())
        known = {entry['label'] + '|' + key: unit for entry in entries for key, unit in entry['inventory'].units.items()}
        lines = {entry['label'] + '|line:' + str(line) for entry in entries
                 for unit in entry['inventory'].units.values() if unit['kind'] == 'statement' for line in unit['lines']}
        tasks = {name + '|' + key: unit for name, component in inventory.components.items()
                 for key, unit in component['tasks'].items()}
        controls = {'block', 'play', 'role', 'ansible.builtin.import_tasks', 'ansible.builtin.import_role'}
        sets = {key: set() for key in cls.SET_FIELDS}
        conditions, receipts, size, seen = {}, [], 0, set()
        template_payloads = []
        for path in paths:
            path = Path(path)
            if not re.fullmatch(r'ansible-[0-9]+[.]json', path.name) or path.name in seen:
                raise InstrumentationError('Unsafe or duplicate Ansible worker identity')
            seen.add(path.name)
            descriptor = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            with os.fdopen(descriptor, 'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise InstrumentationError('Ansible worker receipt must be a regular file')
                raw = stream.read(16777217)
            size += len(raw)
            if len(raw) > 16777216 or size > 536870912:
                raise InstrumentationError('Excessive Ansible worker observations')
            digest = hashlib.sha256(raw).hexdigest()
            if expected is not None and expected.get(path.name) != digest:
                raise InstrumentationError('Ansible worker receipt digest differs')
            data = json.loads(raw)
            if (not isinstance(data, dict) or type(data.get('schema')) is not int or data['schema'] != 1 or
                    data.get('engine') != 'ansible-core-2.16.19-native-conditional' or data.get('complete') is not False or
                    not cls.controller_matches(data.get('controller_profile')) or
                    data.get('files') != inventory.files or data.get('components') != inventory.components):
                raise InstrumentationError('Ansible worker source or engine differs')
            if template_inventory is not None and template_inventory.files and 'template_observations' not in data:
                raise InstrumentationError('Ansible worker omitted required template observations')
            if 'template_observations' in data:
                if template_inventory is None:
                    raise InstrumentationError('Jinja inventory required for template worker observations')
                template_inventory.checked_payload(data['template_observations'], allow_unsupported=True)
                template_payloads.append(data['template_observations'])
            for field in cls.SET_FIELDS:
                values = data.get(field)
                if not isinstance(values, list):
                    raise InstrumentationError('Malformed Ansible worker observations')
                for value in values:
                    if field in ('branches', 'task_loops', 'block_rescues'):
                        if not isinstance(value, list) or len(value) != 2 or type(value[1]) is not bool or not isinstance(value[0], str):
                            raise InstrumentationError('Malformed Ansible worker branch')
                        key, _ = value
                        if ((field == 'branches' and known.get(key, {}).get('kind') != 'branch') or
                                (field == 'task_loops' and not tasks.get(key, {}).get('loop')) or
                                (field == 'block_rescues' and (tasks.get(key, {}).get('action') != 'block' or not tasks[key]['recovery']))):
                            raise InstrumentationError('Unknown Ansible worker branch')
                        sets[field].add(tuple(value))
                    else:
                        if not isinstance(value, str):
                            raise InstrumentationError('Malformed Ansible worker unit')
                        if ((field == 'statements' and known.get(value, {}).get('kind') != 'statement') or
                                (field == 'lines' and value not in lines) or
                                (field == 'entered_blocks' and tasks.get(value, {}).get('action') != 'block') or
                                (field == 'entered_imports' and tasks.get(value, {}).get('action') not in ('ansible.builtin.import_tasks', 'ansible.builtin.import_role')) or
                                (field == 'entered_plays' and tasks.get(value, {}).get('action') != 'play') or
                                (field == 'entered_roles' and tasks.get(value, {}).get('action') != 'role') or
                                (field in ('attempted_tasks', 'dispatched_tasks') and
                                 (value not in tasks or tasks[value]['action'] in controls))):
                            raise InstrumentationError('Unknown Ansible worker unit')
                        sets[field].add(value)
            if not set(data['dispatched_tasks']).issubset(data['attempted_tasks']) or any(
                    key not in data['attempted_tasks'] for key, _ in data['task_loops']):
                raise InstrumentationError('Native action or loop lacks its executor entry')
            if not isinstance(data.get('conditions'), dict):
                raise InstrumentationError('Malformed Ansible worker conditions')
            for key, values in data['conditions'].items():
                if known.get(key, {}).get('kind') != 'condition' or not isinstance(values, list) or any(type(value) is not bool for value in values):
                    raise InstrumentationError('Unknown or nonboolean Ansible worker condition')
                conditions.setdefault(key, set()).update(values)
            receipts.append({'path': path.name, 'sha256': digest})
        # JSON arrays are lists. Emit wire types here so a verified re-union
        # compares identically after serialization, without relaxing validation.
        combined = {key: [list(value) if isinstance(value, tuple) else value for value in sorted(values)]
                    for key, values in sets.items()}
        combined['conditions'] = {key: sorted(values) for key, values in conditions.items()}
        combined['process_receipts'] = receipts
        if template_payloads:
            if len(template_payloads) != len(paths):
                raise InstrumentationError('Inconsistent template observers across Ansible workers')
            combined['template_observations'] = template_inventory.combine_payloads(template_payloads)
        return combined

    def merge(self, output):
        """Join the current run's worker receipts, retaining their exact identities."""
        output = Path(output)
        paths = sorted(path for path in output.glob('ansible-*.json') if re.fullmatch(r'ansible-[0-9]+[.]json', path.name))
        combined = self.payload()
        combined.update(self.combine(paths, self.inventory, template_inventory=self.template_collector))
        descriptor, temporary = tempfile.mkstemp(prefix='.merged-', dir=str(output))
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(combined, stream, sort_keys=True)
        os.replace(temporary, str(output / 'ansible-merged.json'))

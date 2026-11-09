"""Refuse incomplete, stale or insufficient five-metric production coverage.

Python AST entries, authored functions and elementary boolean outcomes come
from bind9_coverage; native coverage.py supplies lines and branches. Native
observers retain independent Jinja, Ansible and reviewed Bash metrics.
Unsupported executable paths stay explicitly unmeasured.
No native line percentage is relabelled as statement/function/condition coverage.
"""
import argparse
import coverage
from decimal import Decimal, localcontext
import hashlib
import json
import os
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from bind9_coverage import InstrumentationError, PythonCollector
from jinja_coverage import JinjaCollector
from ansible_coverage import AnsibleCollector, YamlInventory
from pytest_selection import TestIdentities


class CoverageGate:
    """Compare integer counts to 99.99% for each metric and component."""

    # Basis points preserve the strict boundary without floating-point rounding.
    THRESHOLD_BASIS_POINTS = 9999
    METRICS = ('statements', 'branches', 'functions', 'lines', 'conditions')

    def __init__(self, root):
        if coverage.__version__ != '7.6.1':
            raise ValueError('This gate requires the validated coverage.py 7.6.1 parser')
        self.root = Path(root).resolve()
        self.inventory = PythonCollector(self.root, self.root)
        self.templates = JinjaCollector(self.root)
        self.template_runs = set()
        self.yaml = YamlInventory(self.root)
        self.ansible_runs = []
        self.ansible_components = set()
        self.sources = {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                        for folder in ('roles/bind9', 'playbooks') for path in sorted((self.root / folder).rglob('*'))
                        if path.is_file() and path.suffix in ('.py', '.sh', '.yml', '.j2')}
        self.qa_sources = {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                           for folder in ('tests', 'tools/quality') for path in sorted((self.root / folder).rglob('*'))
                           if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}
        # Native parser/action behavior also depends on the project's controller
        # configuration. Bind its presence and bytes, even when tests are equal.
        configuration = self.root / 'ansible.cfg'
        if os.path.lexists(str(configuration)):
            try:
                raw = YamlInventory.source_bytes(configuration)
            except (OSError, InstrumentationError) as error:
                raise InstrumentationError('Unsafe or excessive project Ansible configuration') from error
            self.qa_sources['ansible.cfg'] = hashlib.sha256(raw).hexdigest()
        self.hits = {name: {kind: set() for kind in self.METRICS} for name in self.sources}
        self.native_units = {}
        self.bash_units, self.bash_runs = {}, []
        self.native = coverage.Coverage(data_file=None, branch=True)
        self.runs, self.errors = [], []

    @staticmethod
    def metric(hit, total):
        # Validate types and bounds before recognizing a genuinely empty scope.
        # bool/float zero and positive hits over zero cannot become NA evidence.
        if type(hit) is not int or type(total) is not int or not 0 <= hit <= total:
            raise ValueError('Invalid coverage numerator/denominator')
        if total == 0:
            return {'hit': 0, 'total': 0, 'percent': None, 'status': 'NON APPLICABLE',
                    'reason': 'No such executable unit in this parsed component'}
        with localcontext() as context:
            context.prec = 40
            percentage = str(Decimal(hit) * 100 / total)
        return {'hit': hit, 'total': total, 'percent': percentage,
                'status': 'EXÉCUTÉ' if hit * 10000 >= total * CoverageGate.THRESHOLD_BASIS_POINTS else 'ÉCHOUÉ'}

    @staticmethod
    def junit_results(path):
        """Read actual case outcomes; summaries cannot hide a skip or failure."""
        root = ET.parse(path).getroot()
        if root.tag not in ('testsuite', 'testsuites'):
            raise ValueError('Unknown JUnit receipt root')
        suites = [root] if root.tag == 'testsuite' else list(root.findall('testsuite'))
        if not suites:
            raise ValueError('The test receipt is absent or contains unresolved failures')
        results, totals = [], dict(tests=0, failures=0, errors=0, skipped=0)
        labels = {'failure': 'failures', 'error': 'errors', 'skipped': 'skipped'}
        for suite in suites:
            counts = dict(tests=0, failures=0, errors=0, skipped=0)
            for case in suite.iter('testcase'):
                statuses = [child.tag for child in case if child.tag in labels]
                if len(statuses) > 1:
                    raise ValueError('JUnit testcase has conflicting or repeated outcomes')
                outcome = statuses[0] if statuses else 'passed'
                counts['tests'] += 1
                if statuses:
                    counts[labels[outcome]] += 1
                results.append((case.get('classname'), case.get('name'), outcome))
            for name, count in counts.items():
                if name in suite.attrib and suite.get(name) != str(count):
                    raise ValueError('JUnit summary differs from actual test outcomes')
                totals[name] += count
        if root.tag != 'testsuite' and any(name in root.attrib and root.get(name) != str(count)
                for name, count in totals.items()):
            raise ValueError('JUnit aggregate differs from actual test outcomes')
        if not results:
            raise ValueError('The test receipt is absent or contains unresolved failures')
        if len(results) != len({(name, case) for name, case, _ in results}):
            raise ValueError('Duplicate JUnit testcase identities')
        return sorted(results), totals['skipped']

    def add(self, receipt_path, selected_tests=None):
        """Accept checked facts; optionally require exact collected node IDs.

        Batched callers supply their inspected collection. Generic standalone
        selectors remain supported without pretending they are node IDs.
        Selection failures occur before any language's facts are committed.
        """
        receipt_path = Path(receipt_path).resolve()
        receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
        if receipt.get('schema') != 1 or receipt.get('native_instrumented') is not False:
            raise ValueError('A separate uninstrumented native run is mandatory')
        if receipt.get('source_sha256') != self.sources:
            raise ValueError('Run receipt does not describe the current complete role')
        if receipt.get('qa_sha256') != self.qa_sources:
            raise ValueError('Test/observer sources changed between paired executions')
        expected_tool = hashlib.sha256((self.root / 'tools/quality/bind9_coverage.py').read_bytes()).hexdigest()
        if receipt.get('observer_sha256') != expected_tool:
            raise ValueError('Observer revision differs from the run receipt')
        paths = {}
        for key, item in receipt['artifacts'].items():
            path = (receipt_path.parent / item['path']).resolve()
            if receipt_path.parent not in path.parents:
                raise ValueError('Unsafe path or changed coverage artifact')
            if key in ('bash_baseline', 'bash_observed', 'bash_toolchain') and path.stat().st_size > 16777216:
                raise ValueError('Excessive native Bash artifact')
            if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
                raise ValueError('Unsafe path or changed coverage artifact')
            paths[key] = path
        native_path, observation_path, junit_path = paths['native'], paths['observations'], paths['instrumented_junit']
        native = json.loads(Path(native_path).read_text(encoding='utf-8'))
        observed = json.loads(Path(observation_path).read_text(encoding='utf-8'))
        expected = {entry[0]: entry[1] for entry in self.inventory.files.values()}
        if observed.get('schema') != 2 or observed.get('files') != expected or observed.get('units') != self.inventory.units:
            raise ValueError('Stale or incomplete source/unit inventory')
        if observed.get('unsupported'):
            raise ValueError('Unsupported boolean observation; coverage cannot be admitted')
        if any(receipt.get('exitstatus', {}).get(key) != 0 for key in ('native', 'instrumented')):
            raise ValueError('A paired test execution did not succeed')
        native_results, native_skipped = self.junit_results(paths['native_junit'])
        observed_results, skipped = self.junit_results(junit_path)
        if native_results != observed_results or native_skipped != skipped:
            raise ValueError('Native/instrumented test identities or outcomes differ')
        if any(outcome in ('failure', 'error') for _, _, outcome in observed_results):
            raise ValueError('The test receipt is absent or contains unresolved failures')
        # Validate identity structure before committing any language's hits.
        junit_identity = TestIdentities.junit_identity_sha256(
            [(name, case) for name, case, _ in observed_results])
        if selected_tests is not None:
            expected_identity = TestIdentities.selected_junit_sha256(selected_tests)
            if (receipt.get('tests') != selected_tests or len(observed_results) != len(selected_tests) or
                    junit_identity != expected_identity):
                raise ValueError('JUnit identities differ from the exact selected tests')
            if skipped:
                raise ValueError('A required selected test was skipped')
        if native.get('meta', {}).get('branch_coverage') is not True:
            raise ValueError('Native branch coverage is required')
        if native['meta'].get('version') != coverage.__version__:
            raise ValueError('Native parser revision differs from this verifier')
        known = {name + '|' + key: value['kind'] for name, units in self.inventory.units.items() for key, value in units.items()}
        for kind, entries in [('function', observed.get('functions', [])), ('statement', observed.get('statements', [])),
                              ('condition', observed.get('conditions', {}))]:
            if any(known.get(key) != kind for key in entries):
                raise ValueError('Unknown or misclassified observed unit')
        staged = {}
        staged_units = {}
        for name, units in self.inventory.units.items():
            data = native.get('files', {}).get(name)
            if data is None:
                raise ValueError('Missing native component: ' + name)
            if data.get('excluded_lines'):
                raise ValueError('Source exclusions are not permitted by this gate')
            absolute = str(self.root / name)
            self.native.get_data().add_arcs({absolute: []})
            analysis = self.native._analyze(absolute)  # Pinned coverage.py 7.6.1 API.
            lines = set(analysis.statements)
            # The pinned parser derives this complete map from the same analysis.
            # Rebuilding it for every arc makes admission quadratic in graph size.
            branch_starts = analysis.branch_stats()
            branches = {(start, end) for start, end in analysis.arc_possibilities if start in branch_starts}
            reported_lines = set(data['executed_lines']) | set(data['missing_lines'])
            reported_branches = {tuple(value) for key in ('executed_branches', 'missing_branches') for value in data[key]}
            if lines - reported_lines or branches - reported_branches:
                raise ValueError('Truncated native denominator: ' + name)
            if self.native_units.get(name, (lines, branches)) != (lines, branches):
                raise ValueError('Inconsistent native unit inventory: ' + name)
            staged_units[name] = lines, branches
            staged[name] = {kind: set() for kind in self.METRICS}
            staged[name]['lines'].update(set(data['executed_lines']) & lines)
            # coverage.py JSON can contain nonpossible runtime arcs (including
            # context-manager exits). Its own summary uses the static inventory.
            staged[name]['branches'].update(set(map(tuple, data['executed_branches'])) & branches)
            for singular, plural in (('statement', 'statements'), ('function', 'functions')):
                expected_keys = {name + '|' + key for key, value in units.items() if value['kind'] == singular}
                staged[name][plural].update(set(observed.get(plural, [])) & expected_keys)
            for key, outcomes in observed.get('conditions', {}).items():
                if key.startswith(name + '|'):
                    if key.split('|', 1)[1] not in units or any(type(value) is not bool for value in outcomes):
                        raise ValueError('Unknown condition or nonboolean observation')
                    staged[name]['conditions'].update((key, value) for value in outcomes)
        template_hits = self.admit_templates(paths['templates'], receipt) if 'templates' in paths else {}
        ansible, ansible_hits, worker_templates = self.admit_ansible_predicates(paths['ansible_predicates'], receipt) if 'ansible_predicates' in paths else (None, {}, {})
        bash_profiles, bash_diagnostics = self.admit_bash(paths, receipt)
        for name, metrics in worker_templates.items():
            target = template_hits.setdefault(name, {kind: set() for kind in self.METRICS})
            for kind in self.METRICS:
                target[kind].update(metrics[kind])
        # Admit all languages atomically only after every artifact was checked.
        for name in staged:
            self.native_units[name] = staged_units[name]
            for kind in self.METRICS:
                self.hits[name][kind].update(staged[name][kind])
        for name, metrics in template_hits.items():
            self.template_runs.add(name)
            for kind in self.METRICS:
                self.hits[name][kind].update(metrics[kind])
        if ansible is not None:
            self.ansible_runs.append(ansible)
            # Guard-only batches retain their verified facts, but cannot
            # activate complete task-file metrics without native execution.
            self.ansible_components.update(ansible['native_task_files'])
        for name, metrics in ansible_hits.items():
            for kind in self.METRICS:
                self.hits[name][kind].update(metrics[kind])
        for name, (profile, bash_hits) in bash_profiles.items():
            self.bash_units[name] = profile.units
            for kind in self.METRICS:
                self.hits[name][kind].update(bash_hits[kind])
        self.bash_runs.extend(bash_diagnostics)
        self.runs.append({'receipt': str(receipt_path), 'native': str(native_path), 'observations': str(observation_path),
                          'junit': str(junit_path), 'skipped': skipped, 'executed_tests': len(observed_results),
                          'junit_identity_sha256': junit_identity})

    def admit_bash(self, paths, receipt):
        """Validate the entire paired native Bash proof before committing hits."""
        fields = {'bash_baseline', 'bash_observed', 'bash_toolchain'}
        if not fields.intersection(paths) and 'bash_observer_sha256' not in receipt:
            return {}, []
        if not fields.issubset(paths) or os.name != 'posix':
            raise ValueError('Native Bash requires both complete POSIX executions and its toolchain')
        expected = hashlib.sha256((self.root / 'tools/quality/bash_coverage.py').read_bytes()).hexdigest()
        if receipt.get('bash_observer_sha256') != expected:
            raise ValueError('Native Bash observer revision differs from the run receipt')
        manifest = os.environ.get('BIND9_NATIVE_BASH_MANIFEST')
        if not manifest:
            raise ValueError('Replaying native Bash facts requires the verified private toolchain')
        actual = hashlib.sha256(Path(manifest).read_bytes()).hexdigest()
        if (actual != receipt.get('bash_toolchain_sha256') or
                hashlib.sha256(paths['bash_toolchain'].read_bytes()).hexdigest() != actual):
            raise ValueError('Native Bash toolchain changed or differs from its paired receipt')
        from bash_coverage import NativeBashCollector, unique_object
        try:
            baseline = json.loads(paths['bash_baseline'].read_text(encoding='ascii'), object_pairs_hook=unique_object)
            observed = json.loads(paths['bash_observed'].read_text(encoding='ascii'), object_pairs_hook=unique_object)
            profiles = NativeBashCollector.checked_pair(self.root, manifest, baseline, observed)
        except InstrumentationError as error:
            raise ValueError(str(error)) from error
        diagnostics = []
        for name, (profile, hits) in profiles.items():
            if self.sources.get(name) != profile.SOURCE_SHA256:
                raise ValueError('Native Bash source differs from the complete project inventory')
            if self.bash_units.get(name, profile.units) != profile.units:
                raise ValueError('Native Bash complete unit inventory changed between runs')
            diagnostics.append({'component': name, 'baseline': str(paths['bash_baseline']),
                'observed': str(paths['bash_observed']),
                'paired_processes': len(observed['profiles'][name]['processes']), 'toolchain_sha256': actual})
        return profiles, diagnostics

    def admit_templates(self, path, receipt):
        """Check independent Jinja inventory, raw outcomes and observer identity."""
        observed = json.loads(Path(path).read_text(encoding='utf-8'))
        return self.template_facts(observed, receipt)

    def template_facts(self, observed, receipt):
        """Use the same validator for the parent and SHA-bound native workers."""
        expected_tool = hashlib.sha256((self.root / 'tools/quality/jinja_coverage.py').read_bytes()).hexdigest()
        if receipt.get('template_observer_sha256') != expected_tool:
            raise ValueError('Jinja observer revision differs from the run receipt')
        try:
            self.templates.checked_payload(observed)
        except InstrumentationError as error:
            raise ValueError(str(error)) from error
        staged = {name: {kind: set() for kind in self.METRICS} for name in self.templates.files}
        for plural in ('statements', 'lines'):
            for key in observed.get(plural, []):
                staged[key.split('|', 1)[0]][plural].add(key)
        for key, value in observed.get('branches', []):
            staged[key.split('|', 1)[0]]['branches'].add((key, value))
        for key, values in observed.get('conditions', {}).items():
            staged[key.split('|', 1)[0]]['conditions'].update((key, value) for value in values)
        return staged

    def admit_ansible_predicates(self, path, receipt):
        """Retain partial observations without admitting complete YAML coverage."""
        expected = hashlib.sha256((self.root / 'tools/quality/ansible_coverage.py').read_bytes()).hexdigest()
        if receipt.get('ansible_observer_sha256') != expected:
            raise ValueError('Ansible observer revision differs from the run receipt')
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if 'process_receipts' in data:
            references = data['process_receipts']
            if (not isinstance(references, list) or not references or len(references) > 4096 or
                    any(not isinstance(item, dict) or set(item) != {'path', 'sha256'} or
                        not isinstance(item['path'], str) or not isinstance(item['sha256'], str) for item in references)):
                raise ValueError('Malformed Ansible worker receipt references')
            expected = {item['path']: item['sha256'] for item in references}
            if len(expected) != len(references) or any(Path(name).name != name or '/' in name or '\\' in name for name in expected):
                raise ValueError('Unsafe or duplicate Ansible worker receipt references')
            try:
                combined = AnsibleCollector.combine([Path(path).parent / name for name in expected], self.yaml, expected,
                                                    template_inventory=self.templates)
            except InstrumentationError as error:
                raise ValueError(str(error)) from error
            if any(data.get(key) != value for key, value in combined.items()):
                raise ValueError('Merged Ansible observations differ from their workers')
        if (type(data.get('schema')) is not int or data['schema'] != 1 or
                data.get('engine') != 'ansible-core-2.16.19-native-conditional' or
                not AnsibleCollector.controller_matches(data.get('controller_profile')) or
                data.get('files') != self.yaml.files or data.get('components') != self.yaml.components or
                data.get('complete') is not False or data.get('unsupported')):
            raise ValueError('Stale, unsupported or misrepresented Ansible predicate observations')
        expressions = list(self.yaml.predicates.values()) + list(self.yaml.templates.values())
        known = {entry['label'] + '|' + key: unit for entry in expressions
                 for key, unit in entry['inventory'].units.items()}
        lines = {entry['label'] + '|line:' + str(line) for entry in expressions
                 for unit in entry['inventory'].units.values() if unit['kind'] == 'statement' for line in unit['lines']}
        statements = data.get('statements')
        if not isinstance(statements, list) or any(known.get(key, {}).get('kind') != 'statement' for key in statements):
            raise ValueError('Unknown Ansible predicate statement')
        if not isinstance(data.get('lines'), list) or any(key not in lines for key in data['lines']):
            raise ValueError('Unknown Ansible predicate line')
        conditions = data.get('conditions')
        if not isinstance(conditions, dict) or any(known.get(key, {}).get('kind') != 'condition' or not isinstance(values, list)
                                                  for key, values in conditions.items()):
            raise ValueError('Unknown Ansible elementary condition')
        branches = data.get('branches')
        if not isinstance(branches, list) or any(not isinstance(pair, list) or len(pair) != 2 or
                known.get(pair[0], {}).get('kind') != 'branch' or type(pair[1]) is not bool for pair in branches):
            raise ValueError('Unknown Ansible predicate branch')
        if any(type(value) is not bool for values in conditions.values() for value in values):
            raise ValueError('Ansible condition outcome must be exactly boolean')
        tasks = {name + '|' + key: unit for name, component in self.yaml.components.items()
                 for key, unit in component['tasks'].items()}
        controls = {'entered_blocks': ('block',), 'entered_imports': ('ansible.builtin.import_tasks', 'ansible.builtin.import_role'),
                    'entered_plays': ('play',), 'entered_roles': ('role',)}
        control_actions = set().union(*controls.values())
        for field in ('attempted_tasks', 'dispatched_tasks') + tuple(controls):
            values = data.get(field, [])
            if not isinstance(values, list) or any(not isinstance(key, str) or key not in tasks or
                    (field in controls and tasks[key]['action'] not in controls[field]) or
                    (field not in controls and tasks[key]['action'] in control_actions) for key in values):
                raise ValueError('Unknown or malformed native Ansible task observation')
        loops = data.get('task_loops', [])
        if not isinstance(loops, list) or any(not isinstance(pair, list) or len(pair) != 2 or
                not isinstance(pair[0], str) or not tasks.get(pair[0], {}).get('loop') or type(pair[1]) is not bool for pair in loops):
            raise ValueError('Unknown or malformed native Ansible task loop')
        rescues = data.get('block_rescues', [])
        if not isinstance(rescues, list) or any(not isinstance(pair, list) or len(pair) != 2 or
                not isinstance(pair[0], str) or tasks.get(pair[0], {}).get('action') != 'block' or
                not tasks[pair[0]]['recovery'] or type(pair[1]) is not bool for pair in rescues):
            raise ValueError('Unknown or malformed native Ansible block recovery')
        if not set(data.get('dispatched_tasks', [])).issubset(data.get('attempted_tasks', [])) or any(
                key not in data.get('attempted_tasks', []) for key, _ in loops):
            raise ValueError('Native action or loop lacks its executor entry')
        diagnostic = {'status': 'NON EXÉCUTÉ', 'complete': False, 'artifact': str(path),
                'observed_predicate_statements': len({key for key in statements if '#predicate:' in key}),
                'observed_template_statements': len({key for key in statements if '#template:' in key}),
                'attempted_native_tasks': len(set(data.get('attempted_tasks', []))),
                'dispatched_native_tasks': len(set(data.get('dispatched_tasks', []))),
                'observed_native_task_loop_outcomes': len({tuple(value) for value in data.get('task_loops', [])}),
                'entered_native_blocks': len(set(data.get('entered_blocks', []))),
                'observed_native_recovery_outcomes': len({tuple(value) for value in rescues}),
                'observed_elementary_outcomes': sum(len(set(values)) for values in conditions.values()),
                'elementary_outcomes_inventory': 2 * sum(unit['kind'] == 'condition' for unit in known.values()),
                'reason': 'Embedded script bodies remain unmeasured; native task-file metrics are admitted separately'}
        native_files, task_hits = self.admit_ansible_task_files(data)
        diagnostic['native_task_files'] = sorted(native_files)
        worker_templates = self.template_facts(data['template_observations'], receipt) if 'template_observations' in data else {}
        return diagnostic, task_hits, worker_templates

    def admit_ansible_task_files(self, data):
        """Keep source-bound facts separate from native execution witnesses.

        The same observations must survive any valid batch partition/order.
        Pending facts do not qualify a file, and are committed only after the
        whole receipt (including other languages) passes admission.
        """
        if not data.get('process_receipts'):
            return set(), {}  # Legacy payloads are never native dispatch proof.
        dispatched = set(data.get('dispatched_tasks', []))
        controls = set().union(*(data.get(field, []) for field in
                                 ('entered_blocks', 'entered_imports', 'entered_plays', 'entered_roles')))
        eligible = {name for name in self.yaml.files if self.yaml.task_profile_complete(name)}
        measured = {name for name in eligible if
                    any(key.startswith(name + '|') for key in dispatched | controls)}
        staged = {name: {kind: set() for kind in self.METRICS} for name in eligible}
        for name in eligible:
            units = self.yaml.executable_units(name)
            hits = staged[name]
            hits['statements'].update(key for key in data['statements'] if key in units and units[key]['kind'] == 'statement')
            hits['statements'].update(key for key in dispatched if key in units)
            hits['statements'].update(key for key in controls if key in units)
            hits['lines'].update(line for key in hits['statements'] for line in units[key]['lines'])
            hits['branches'].update((key, value) for key, value in data['branches'] if key in units)
            hits['branches'].update((key + ':loop', value) for key, value in data.get('task_loops', []) if key + ':loop' in units)
            hits['branches'].update((key + ':rescue', value) for key, value in data.get('block_rescues', []) if key + ':rescue' in units)
            hits['conditions'].update((key, value) for key, values in data['conditions'].items() if key in units for value in values)
        return measured, staged

    def data_only(self, name):
        """Zero units require a verified data-only source, never a file suffix."""
        component = self.yaml.components.get(name)
        return (component is not None and component['data_only'] and not component['tasks'] and
                not component['templates'] and not component['predicates'])

    def template_profiles(self, name):
        """Require every distinct declared compiler inventory to meet its threshold.

        Profile lines follow the existing contract: an observed authored
        statement covers its inventoried source lines. Never borrow a line hit
        from another compiler profile or average away its missing outcomes.
        """
        result = {}
        for profile, units in self.templates.profile_units.get(name, {}).items():
            statements = {name + '|' + key for key, unit in units.items() if unit['kind'] == 'statement'}
            branches = {(name + '|' + key, value) for key, unit in units.items() if unit['kind'] == 'branch' for value in (False, True)}
            conditions = {(name + '|' + key, value) for key, unit in units.items() if unit['kind'] == 'condition' for value in (False, True)}
            lines = {line for unit in units.values() if unit['kind'] == 'statement' for line in unit['lines']}
            hit_lines = {line for key, unit in units.items() if name + '|' + key in self.hits[name]['statements'] for line in unit['lines']}
            result[profile] = {'statements': self.metric(len(statements & self.hits[name]['statements']), len(statements)),
                'branches': self.metric(len(branches & self.hits[name]['branches']), len(branches)),
                'functions': self.metric(0, 0), 'lines': self.metric(len(lines & hit_lines), len(lines)),
                'conditions': self.metric(len(conditions & self.hits[name]['conditions']), len(conditions))}
        return result

    def report(self):
        components, totals = {}, {kind: [0, 0] for kind in self.METRICS}
        python_totals = {kind: [0, 0] for kind in self.METRICS}
        for name, digest in self.sources.items():
            values = {}
            if name in self.native_units:
                units = self.inventory.units[name]
                counts = {'statements': sum(v['kind'] == 'statement' for v in units.values()),
                          'functions': sum(v['kind'] == 'function' for v in units.values()),
                          'conditions': 2 * sum(v['kind'] == 'condition' for v in units.values()),
                          'lines': len(self.native_units[name][0]), 'branches': len(self.native_units[name][1])}
                for kind in self.METRICS:
                    hit = len(self.hits[name][kind])
                    values[kind] = self.metric(hit, counts[kind])
                    totals[kind][0] += hit
                    totals[kind][1] += counts[kind]
                    python_totals[kind][0] += hit
                    python_totals[kind][1] += counts[kind]
            elif name in self.template_runs:
                units = self.templates.units[name]
                counts = {'statements': sum(unit['kind'] == 'statement' for unit in units.values()),
                          'branches': 2 * sum(unit['kind'] == 'branch' for unit in units.values()), 'functions': 0,
                          'conditions': 2 * sum(unit['kind'] == 'condition' for unit in units.values()),
                          'lines': len({line for unit in units.values() if unit['kind'] == 'statement' for line in unit['lines']})}
                for kind in self.METRICS:
                    hit = len(self.hits[name][kind])
                    values[kind] = self.metric(hit, counts[kind])
                    totals[kind][0] += hit
                    totals[kind][1] += counts[kind]
            elif name in self.ansible_components:
                units = self.yaml.executable_units(name)
                counts = {'statements': sum(unit['kind'] == 'statement' for unit in units.values()),
                          'branches': 2 * sum(unit['kind'] == 'branch' for unit in units.values()), 'functions': 0,
                          'conditions': 2 * sum(unit['kind'] == 'condition' for unit in units.values()),
                          'lines': len({line for unit in units.values() if unit['kind'] == 'statement' for line in unit['lines']})}
                for kind in self.METRICS:
                    values[kind] = self.metric(len(self.hits[name][kind]), counts[kind])
                    totals[kind][0] += len(self.hits[name][kind])
                    totals[kind][1] += counts[kind]
            elif name in self.bash_units:
                for kind in self.METRICS:
                    if self.bash_units[name][kind] is None:
                        values[kind] = {'status': 'NON EXÉCUTÉ', 'reason': 'Native Bash profile does not measure this metric'}
                        continue
                    hit, total = len(self.hits[name][kind]), len(self.bash_units[name][kind])
                    values[kind] = self.metric(hit, total)
                    totals[kind][0] += hit
                    totals[kind][1] += total
            elif self.data_only(name):
                values = {kind: dict(self.metric(0, 0), reason='Parsed literal role defaults or Galaxy metadata; no task, template or predicate')
                          for kind in self.METRICS}
            else:
                values = {kind: {'status': 'NON EXÉCUTÉ', 'reason': 'No source-matched executable observer/result for this component'}
                          for kind in self.METRICS}
            components[name] = {'sha256': digest, 'metrics': values}
            profiles = self.template_profiles(name)
            if profiles:
                components[name]['profiles'] = profiles
        accepted = bool(components) and not self.errors and bool(self.runs) and not any(run['skipped'] for run in self.runs) and all(
            value['status'] in ('EXÉCUTÉ', 'NON APPLICABLE') for component in components.values()
            for value in component['metrics'].values())
        accepted = accepted and all(value['status'] in ('EXÉCUTÉ', 'NON APPLICABLE')
            for component in components.values() for profile in component.get('profiles', {}).values() for value in profile.values())
        return {'schema': 1, 'threshold': self.THRESHOLD_BASIS_POINTS / 100,
                'threshold_basis_points': self.THRESHOLD_BASIS_POINTS, 'accepted': accepted, 'components': components,
                'python_totals': {kind: self.metric(*count) for kind, count in python_totals.items()}
                                 if all(name in self.native_units for name in self.inventory.units) else
                                 {kind: {'status': 'NON EXÉCUTÉ', 'partial_hit': count[0], 'partial_total': count[1],
                                         'reason': 'At least one Python component is unmeasured'} for kind, count in python_totals.items()},
                # Keep each measured subset without inventing a missing
                # denominator or a complete project percentage.
                'project_totals': {kind: (
                    {'status': 'NON EXÉCUTÉ', 'partial_hit': count[0], 'partial_total': count[1],
                     'reason': 'At least one role component is unmeasured for this metric'}
                    if any(component['metrics'][kind]['status'] == 'NON EXÉCUTÉ' for component in components.values())
                    else self.metric(*count)) for kind, count in totals.items()},
                'ansible_predicate_observations': self.ansible_runs, 'bash_observations': self.bash_runs,
                'runs': self.runs, 'errors': self.errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--run', type=Path, action='append', default=[], help='Paired run receipt from run_coverage.py')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    gate = CoverageGate(args.root)
    for run in args.run:
        try:
            gate.add(run)
        except (ValueError, OSError, KeyError, TypeError, ET.ParseError) as error:
            gate.errors.append(str(error))
    report = gate.report()
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print('Coverage accepted' if report['accepted'] else 'Coverage refused; see ' + str(args.output))
    return 0 if report['accepted'] else 2


if __name__ == '__main__':
    sys.exit(main())

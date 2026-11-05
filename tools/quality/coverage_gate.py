"""Refuse incomplete, stale or insufficient five-metric production coverage.

Python AST entries, authored functions and elementary boolean outcomes come
from bind9_coverage; native coverage.py supplies lines and branches. Other role
languages remain explicitly unmeasured until an equivalent observer exists.
No native line percentage is relabelled as statement/function/condition coverage.
"""
import argparse
import coverage
from decimal import Decimal, localcontext
import hashlib
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

from bind9_coverage import PythonCollector
from jinja_coverage import JinjaCollector
from ansible_coverage import YamlInventory


class CoverageGate:
    """Compare integer counts to 98% independently, including each component."""

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
        self.sources = {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                        for folder in ('roles/bind9', 'playbooks') for path in sorted((self.root / folder).rglob('*'))
                        if path.is_file() and path.suffix in ('.py', '.sh', '.yml', '.j2')}
        self.qa_sources = {path.relative_to(self.root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                           for folder in ('tests', 'tools/quality') for path in sorted((self.root / folder).rglob('*'))
                           if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'}
        self.hits = {name: {kind: set() for kind in self.METRICS} for name in self.sources}
        self.native_units = {}
        self.native = coverage.Coverage(data_file=None, branch=True)
        self.runs, self.errors = [], []

    @staticmethod
    def metric(hit, total):
        if total == 0:
            return {'hit': 0, 'total': 0, 'percent': None, 'status': 'NON APPLICABLE',
                    'reason': 'No such executable unit in this parsed component'}
        if type(hit) is not int or type(total) is not int or not 0 <= hit <= total:
            raise ValueError('Invalid coverage numerator/denominator')
        with localcontext() as context:
            context.prec = 40
            percentage = str(Decimal(hit) * 100 / total)
        return {'hit': hit, 'total': total, 'percent': percentage,
                'status': 'EXÉCUTÉ' if hit * 100 >= total * 98 else 'ÉCHOUÉ'}

    def add(self, receipt_path):
        """Accept a complete source-matched run; never repair a failed receipt."""
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
            if receipt_path.parent not in path.parents or hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
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
        def identities(path):
            return sorted((case.get('classname'), case.get('name')) for case in ET.parse(path).getroot().iter('testcase'))
        if not identities(paths['native_junit']) or identities(paths['native_junit']) != identities(junit_path):
            raise ValueError('Native/instrumented test identities differ')
        suites = ET.parse(junit_path).getroot()
        if suites.tag == 'testsuite':
            suites = [suites]
        else:
            suites = list(suites.findall('testsuite'))
        if not suites or any(int(suite.get(key, '0')) for suite in suites for key in ('errors', 'failures')):
            raise ValueError('The test receipt is absent or contains unresolved failures')
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
            branches = {(start, end) for start, end in analysis.arc_possibilities if start in analysis.branch_stats()}
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
        ansible = self.admit_ansible_predicates(paths['ansible_predicates'], receipt) if 'ansible_predicates' in paths else None
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
        self.runs.append({'receipt': str(receipt_path), 'native': str(native_path), 'observations': str(observation_path),
                          'junit': str(junit_path), 'skipped': sum(int(s.get('skipped', '0')) for s in suites)})

    def admit_templates(self, path, receipt):
        """Check independent Jinja inventory, raw outcomes and observer identity."""
        expected_tool = hashlib.sha256((self.root / 'tools/quality/jinja_coverage.py').read_bytes()).hexdigest()
        if receipt.get('template_observer_sha256') != expected_tool:
            raise ValueError('Jinja observer revision differs from the run receipt')
        observed = json.loads(Path(path).read_text(encoding='utf-8'))
        if (observed.get('schema') != 1 or observed.get('engine') != 'jinja2-3.1.6-native-codegen' or
                observed.get('files') != self.templates.files or observed.get('units') != self.templates.units):
            raise ValueError('Stale or incomplete Jinja source/unit inventory')
        if observed.get('unsupported') or observed.get('functions') != []:
            raise ValueError('Unsupported Jinja observation; authored functions are not supported by this grammar')
        known = {name + '|' + key: unit['kind'] for name, units in self.templates.units.items() for key, unit in units.items()}
        lines = {name + '|line:' + str(line) for name, units in self.templates.units.items()
                 for unit in units.values() if unit['kind'] == 'statement' for line in unit['lines']}
        if any(known.get(key) != 'statement' for key in observed.get('statements', [])):
            raise ValueError('Unknown Jinja statement')
        if any(key not in lines for key in observed.get('lines', [])):
            raise ValueError('Unknown Jinja executable line')
        conditions = observed.get('conditions')
        if not isinstance(conditions, dict) or any(known.get(key) != 'condition' or not isinstance(values, list)
                                                  for key, values in conditions.items()):
            raise ValueError('Unknown Jinja condition inventory or malformed outcomes')
        for kind, pairs in (('condition', [(key, value) for key, values in observed.get('conditions', {}).items() for value in values]),
                            ('branch', observed.get('branches', []))):
            if any(known.get(key) != kind or type(value) is not bool for key, value in pairs):
                raise ValueError('Unknown Jinja predicate/branch or nonboolean observation')
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
        if (type(data.get('schema')) is not int or data['schema'] != 1 or
                data.get('engine') != 'ansible-core-2.16.14-native-conditional' or
                data.get('files') != self.yaml.files or data.get('components') != self.yaml.components or
                data.get('complete') is not False or data.get('unsupported')):
            raise ValueError('Stale, unsupported or misrepresented Ansible predicate observations')
        known = {entry['label'] + '|' + key: unit for entry in self.yaml.predicates.values()
                 for key, unit in entry['inventory'].units.items()}
        lines = {entry['label'] + '|line:' + str(line) for entry in self.yaml.predicates.values()
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
        return {'status': 'NON EXÉCUTÉ', 'complete': False, 'artifact': str(path),
                'observed_predicate_statements': len(set(statements)),
                'observed_elementary_outcomes': sum(len(set(values)) for values in conditions.values()),
                'elementary_outcomes_inventory': 2 * sum(unit['kind'] == 'condition' for unit in known.values()),
                'reason': 'Predicate observations do not measure task dispatch, block recovery, loops or templated data fields'}

    def data_only(self, name):
        """Zero units require a verified data-only source, never a file suffix."""
        component = self.yaml.components.get(name)
        return (component is not None and component['data_only'] and not component['tasks'] and
                not component['templates'] and not component['predicates'])

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
            elif self.data_only(name):
                values = {kind: dict(self.metric(0, 0), reason='Parsed literal role defaults or Galaxy metadata; no task, template or predicate')
                          for kind in self.METRICS}
            else:
                values = {kind: {'status': 'NON EXÉCUTÉ', 'reason': 'No source-matched executable observer/result for this component'}
                          for kind in self.METRICS}
            components[name] = {'sha256': digest, 'metrics': values}
        accepted = bool(components) and not self.errors and bool(self.runs) and not any(run['skipped'] for run in self.runs) and all(
            value['status'] in ('EXÉCUTÉ', 'NON APPLICABLE') for component in components.values()
            for value in component['metrics'].values())
        return {'schema': 1, 'threshold': 98, 'accepted': accepted, 'components': components,
                'python_totals': {kind: self.metric(*count) for kind, count in python_totals.items()}
                                 if all(name in self.native_units for name in self.inventory.units) else
                                 {kind: {'status': 'NON EXÉCUTÉ', 'partial_hit': count[0], 'partial_total': count[1],
                                         'reason': 'At least one Python component is unmeasured'} for kind, count in python_totals.items()},
                'project_totals': {kind: {'status': 'NON EXÉCUTÉ', 'reason': 'At least one role component is unmeasured'}
                                   for kind in self.METRICS} if any(name not in self.native_units and name not in self.template_runs and not self.data_only(name) for name in self.sources)
                                  else {kind: self.metric(*count) for kind, count in totals.items()},
                'ansible_predicate_observations': self.ansible_runs, 'runs': self.runs, 'errors': self.errors}


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

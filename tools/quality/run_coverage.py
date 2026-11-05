"""Run the same tests natively and with separate AST probes, then enforce the gate.

Use a fresh output directory to retain initial refusals. Runs are bounded to one
hour each; all artifacts and complete role sources are bound by SHA-256. The
runner is a QA-controller tool for Python 3.11, never a target-host dependency.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from coverage_gate import CoverageGate


class PairedRun:
    def __init__(self, root, output, tests):
        self.root, self.output, self.tests = Path(root).resolve(), Path(output).resolve(), tests
        self.output.mkdir(parents=True, exist_ok=False)
        self.gate = CoverageGate(self.root)

    def command(self, command, environment, name):
        with (self.output / (name + '.log')).open('w', encoding='utf-8') as stream:
            result = subprocess.run(command, cwd=str(self.root), env=environment, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=3600)
        return result.returncode

    def run(self):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', COVERAGE_FILE=str(self.output / 'native.coverage'))
        environment.pop('BIND9_CONDITION_COVERAGE', None)
        environment.pop('COVERAGE_PROCESS_START', None)
        # Test ownership/rename contracts require a native private filesystem,
        # not a Windows-backed evidence mount that advertises permissive modes.
        temporary = tempfile.TemporaryDirectory(prefix='bind9-coverage-')
        test_root = Path(temporary.name)
        native_pytest = ['-m', 'pytest', '-p', 'no:cacheprovider', '-q',
                         '--basetemp=' + str(test_root / 'native'),
                         '--junitxml=' + str(self.output / 'native.xml')] + self.tests
        native = self.command([sys.executable, '-m', 'coverage', 'run', '--branch', '--source=roles/bind9'] + native_pytest,
                              environment, 'native')
        report = self.command([sys.executable, '-m', 'coverage', 'json', '-o', str(self.output / 'native.json')],
                              environment, 'native-report')
        environment['BIND9_CONDITION_COVERAGE'] = str(self.output / 'observations')
        instrumented = self.command([sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-q',
                         '--basetemp=' + str(test_root / 'instrumented'),
                         '--junitxml=' + str(self.output / 'instrumented.xml')] + self.tests,
                              environment, 'instrumented')
        observations = list((self.output / 'observations').glob('conditions-*.json'))
        templates = list((self.output / 'observations').glob('templates-*.json'))
        ansible = list((self.output / 'observations').glob('ansible-*.json'))
        temporary.cleanup()
        if len(observations) != 1 or len(templates) != 1 or len(ansible) != 1:
            raise ValueError('Expected exactly one Python, Jinja and Ansible instrumented pytest process receipt')
        artifacts = {'native': self.output / 'native.json', 'native_junit': self.output / 'native.xml',
                     'observations': observations[0], 'templates': templates[0], 'ansible_predicates': ansible[0],
                     'instrumented_junit': self.output / 'instrumented.xml'}
        receipt = {'schema': 1, 'source_sha256': self.gate.sources, 'qa_sha256': self.gate.qa_sources, 'native_instrumented': False,
                   'observer_sha256': hashlib.sha256((self.root / 'tools/quality/bind9_coverage.py').read_bytes()).hexdigest(),
                   'template_observer_sha256': hashlib.sha256((self.root / 'tools/quality/jinja_coverage.py').read_bytes()).hexdigest(),
                   'ansible_observer_sha256': hashlib.sha256((self.root / 'tools/quality/ansible_coverage.py').read_bytes()).hexdigest(),
                   'tests': self.tests, 'exitstatus': {'native': native or report, 'instrumented': instrumented},
                   'artifacts': {key: {'path': path.relative_to(self.output).as_posix(),
                                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for key, path in artifacts.items()}}
        final_inventory = CoverageGate(self.root)
        if final_inventory.sources != self.gate.sources or final_inventory.qa_sources != self.gate.qa_sources:
            raise ValueError('Production/test/observer source changed while the tests were running')
        receipt_path = self.output / 'run.json'
        receipt_path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        self.gate.add(receipt_path)
        quality = self.gate.report()
        (self.output / 'coverage-gate.json').write_text(json.dumps(quality, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'native_exitstatus': native, 'instrumented_exitstatus': instrumented,
                          'coverage_accepted': quality['accepted'], 'receipt': str(receipt_path)}))
        return 0 if quality['accepted'] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('tests', nargs='*', default=['tests'])
    args = parser.parse_args()
    return PairedRun(args.root, args.output, args.tests or ['tests']).run()


if __name__ == '__main__':
    sys.exit(main())

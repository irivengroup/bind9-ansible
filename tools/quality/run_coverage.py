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
from pytest_selection import TestIdentities


class PairedRun:
    def __init__(self, root, output, tests):
        self.root, self.output, self.tests = Path(root).resolve(), Path(output).resolve(), tests
        self.output.mkdir(parents=True, exist_ok=False)
        self.gate = CoverageGate(self.root)

    def native_source_scope(self):
        """Retain unimported modules in non-package role directories.

        The role deliberately has no Python __init__ files. coverage.py does
        not recursively discover every such directory from the role root.
        Supply each inventoried parent as well, so untouched files contribute
        their real missing lines/branches and never fabricated execution hits.
        """
        return ','.join(sorted({'roles/bind9'} | {
            Path(name).parent.as_posix() for name in self.gate.inventory.units}))

    def command(self, command, environment, name):
        with (self.output / (name + '.log')).open('w', encoding='utf-8') as stream:
            result = subprocess.run(command, cwd=str(self.root), env=environment, stdout=stream,
                                    stderr=subprocess.STDOUT, timeout=3600)
        return result.returncode

    def run(self):
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', COVERAGE_FILE=str(self.output / 'native.coverage'))
        environment.pop('BIND9_CONDITION_COVERAGE', None)
        environment.pop('COVERAGE_PROCESS_START', None)
        environment.pop('BIND9_NATIVE_BASH_OUTPUT', None)
        environment.pop('BIND9_NATIVE_BASH_MODE', None)
        if 'ansible.cfg' in self.gate.qa_sources:
            # A mounted workspace may look world-writable to Ansible. Select
            # the fingerprinted project file explicitly in both fresh phases.
            environment['ANSIBLE_CONFIG'] = str(self.root / 'ansible.cfg')
        bash_manifest = environment.get('BIND9_NATIVE_BASH_MANIFEST')
        bash_enabled = bool(bash_manifest and (self.root / 'roles/bind9/files/named-healthcheck.sh').is_file())
        if bash_enabled:
            environment.update(BIND9_NATIVE_BASH_MODE='baseline',
                               BIND9_NATIVE_BASH_OUTPUT=str(self.output / 'bash-baseline.json'))
        # Test ownership/rename contracts require a native private filesystem,
        # not a Windows-backed evidence mount that advertises permissive modes.
        temporary = tempfile.TemporaryDirectory(prefix='bind9-coverage-')
        test_root = Path(temporary.name)
        # Each fresh pytest process recreates this owned base directory. The
        # pathname is identical, while native-phase files are removed before
        # the observed phase. Different paths would change real CLI arguments
        # and defeat the deliberately strict paired invocation comparison.
        test_workspace = test_root / 'work'
        selection = TestIdentities.pytest_arguments(test_root / 'pytest-arguments.txt', self.tests)
        native_pytest = ['-m', 'pytest', '-p', 'no:cacheprovider', '-q',
                         '--basetemp=' + str(test_workspace),
                         '--junitxml=' + str(self.output / 'native.xml')] + selection
        native = self.command([sys.executable, '-m', 'coverage', 'run', '--branch',
                               '--source=' + self.native_source_scope()] + native_pytest,
                              environment, 'native')
        report = self.command([sys.executable, '-m', 'coverage', 'json', '-o', str(self.output / 'native.json')],
                              environment, 'native-report')
        environment['BIND9_CONDITION_COVERAGE'] = str(self.output / 'observations')
        if bash_enabled:
            environment.update(BIND9_NATIVE_BASH_MODE='observed',
                               BIND9_NATIVE_BASH_OUTPUT=str(self.output / 'bash-observed.json'))
        instrumented = self.command([sys.executable, '-m', 'pytest', '-p', 'no:cacheprovider', '-q',
                         '--basetemp=' + str(test_workspace),
                         '--junitxml=' + str(self.output / 'instrumented.xml')] + selection,
                              environment, 'instrumented')
        observations = list((self.output / 'observations').glob('conditions-*.json'))
        templates = list((self.output / 'observations').glob('templates-*.json'))
        ansible = list((self.output / 'observations').glob('ansible-merged.json'))
        temporary.cleanup()
        if len(observations) != 1 or len(templates) != 1 or len(ansible) != 1:
            raise ValueError('Expected exactly one Python, Jinja and Ansible instrumented pytest process receipt')
        artifacts = {'native': self.output / 'native.json', 'native_junit': self.output / 'native.xml',
                     'observations': observations[0], 'templates': templates[0], 'ansible_predicates': ansible[0],
                     'instrumented_junit': self.output / 'instrumented.xml'}
        if bash_enabled:
            # Keep the toolchain identity beside this run. Executables remain
            # in the controller's private build, never in a deployment archive.
            manifest_copy = self.output / 'bash-toolchain.json'
            manifest_copy.write_bytes(Path(bash_manifest).read_bytes())
            artifacts.update(bash_baseline=self.output / 'bash-baseline.json',
                             bash_observed=self.output / 'bash-observed.json', bash_toolchain=manifest_copy)
        receipt = {'schema': 1, 'source_sha256': self.gate.sources, 'qa_sha256': self.gate.qa_sources, 'native_instrumented': False,
                   'observer_sha256': hashlib.sha256((self.root / 'tools/quality/bind9_coverage.py').read_bytes()).hexdigest(),
                   'template_observer_sha256': hashlib.sha256((self.root / 'tools/quality/jinja_coverage.py').read_bytes()).hexdigest(),
                   'ansible_observer_sha256': hashlib.sha256((self.root / 'tools/quality/ansible_coverage.py').read_bytes()).hexdigest(),
                   'tests': self.tests, 'exitstatus': {'native': native or report, 'instrumented': instrumented},
                   'artifacts': {key: {'path': path.relative_to(self.output).as_posix(),
                                      'sha256': hashlib.sha256(path.read_bytes()).hexdigest()} for key, path in artifacts.items()}}
        if bash_enabled:
            receipt.update(bash_observer_sha256=hashlib.sha256((self.root / 'tools/quality/bash_coverage.py').read_bytes()).hexdigest(),
                           bash_toolchain_sha256=hashlib.sha256(manifest_copy.read_bytes()).hexdigest())
        final_inventory = CoverageGate(self.root)
        if final_inventory.sources != self.gate.sources or final_inventory.qa_sources != self.gate.qa_sources:
            raise ValueError('Production/test/observer source changed while the tests were running')
        receipt_path = self.output / 'run.json'
        receipt_path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        try:
            self.gate.add(receipt_path)
        except (ValueError, OSError, KeyError, TypeError) as error:
            # A refusal is evidence too. Keep the original paired receipt and
            # an explicit unsuccessful gate result instead of losing the latter
            # behind an uncaught exception. Nothing from the pair is admitted.
            self.gate.errors.append(str(error))
        quality = self.gate.report()
        (self.output / 'coverage-gate.json').write_text(json.dumps(quality, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'native_exitstatus': native, 'instrumented_exitstatus': instrumented,
                          'coverage_accepted': quality['accepted'], 'receipt': str(receipt_path)}))
        return 0 if quality['accepted'] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--test-manifest', type=Path, help='Bounded JSON manifest of exact pytest identities')
    parser.add_argument('tests', nargs='*', default=[])
    args = parser.parse_args()
    if args.test_manifest and args.tests:
        parser.error('An exact selection manifest cannot be combined with positional selectors')
    return PairedRun(args.root, args.output, TestIdentities.read(args.test_manifest) if args.test_manifest else args.tests or ['tests']).run()


if __name__ == '__main__':
    sys.exit(main())

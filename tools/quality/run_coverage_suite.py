"""Run every selected test in bounded paired batches, then apply the same gate.

Batching avoids repeating the complete YAML inventory beyond a single run's
unchanged worker budget. No test is sampled, no failed receipt is admitted and
no partial threshold refusal becomes project acceptance. Requires Linux/Python
3.11 and the same pinned QA dependencies as run_coverage.py.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET

from coverage_gate import CoverageGate
from pytest_selection import TestIdentities


class CollectionManifest:
    """Capture actual pytest identities, including collection refusals/skips."""

    def __init__(self):
        self.nodeids, self.failures, self.skipped, self.deselected = [], [], [], []
        self.omissions = {}

    def pytest_configure(self, config):
        for option in ('keyword', 'markexpr', 'ignore', 'ignore_glob', 'deselect'):
            value = getattr(config.option, option, None)
            if value:
                self.omissions[option] = value

    def pytest_deselected(self, items):
        self.deselected.extend(item.nodeid for item in items)

    def pytest_collection_finish(self, session):
        self.nodeids = [item.nodeid for item in session.items]

    def pytest_collectreport(self, report):
        if report.failed:
            self.failures.append(report.nodeid)
        elif report.skipped:
            self.skipped.append(report.nodeid)

    def collect(self, root, selectors, output):
        import pytest
        os.chdir(str(root))
        observer = self
        status = pytest.main(['--collect-only', '-q', '-p', 'no:cacheprovider'] + selectors, plugins=[observer])
        output.write_text(json.dumps(dict(schema=1, exitstatus=int(status), selectors=selectors,
            nodeids=self.nodeids, failures=self.failures, skipped=self.skipped,
            deselected=self.deselected, omissions=self.omissions), indent=2) + '\n', encoding='utf-8')
        return int(status)


class SuiteRun:
    """Keep exact test membership and source identity across sequential batches."""

    MAX_ARGUMENT_BYTES = 65536
    COMMAND_TIMEOUT = 7260

    def __init__(self, root, output, selectors, batch_size=32):
        if os.name != 'posix' or type(batch_size) is not int or not 1 <= batch_size <= 256:
            raise ValueError('Paired batching requires Linux and a batch size from 1 to 256')
        self.root, self.output = Path(root).resolve(), Path(output).resolve()
        for prefix in ('roles/bind9', 'playbooks', 'tests', 'tools/quality'):
            if self.output.is_relative_to(self.root / prefix):
                raise ValueError('Coverage outputs must stay outside source and QA inventory directories')
        self.output.mkdir(parents=True, exist_ok=False)
        self.selectors, self.batch_size = list(selectors), batch_size
        self.gate = CoverageGate(self.root)
        self.initial_sources, self.initial_qa = self.gate.sources.copy(), self.gate.qa_sources.copy()
        self.stages, self.nodes = [], []

    @classmethod
    def batches(cls, nodeids, size):
        """Partition once by count; identities travel in bounded manifest files."""
        batch = []
        for node in nodeids:
            if len(batch) >= size:
                yield batch
                batch = []
            batch.append(node)
        if batch:
            yield batch

    def command(self, argv, log):
        """Terminate the entire owned child group if a batch exceeds its budget."""
        if sum(len(argument.encode('utf-8')) + 1 for argument in argv) > self.MAX_ARGUMENT_BYTES:
            raise ValueError('Command exceeds the supported argv budget')
        environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
        environment.pop('BIND9_CONDITION_COVERAGE', None)
        environment.pop('COVERAGE_PROCESS_START', None)
        # Collection owns no writer. A nested suite must not initialize a
        # parent's Bash collector or occupy/overwrite its evidence pathname.
        # PairedRun explicitly selects fresh writers for its own two phases.
        environment.pop('BIND9_NATIVE_BASH_OUTPUT', None)
        environment.pop('BIND9_NATIVE_BASH_MODE', None)
        with log.open('x', encoding='utf-8') as stream:
            process = subprocess.Popen(argv, cwd=str(self.root), env=environment,
                stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                return process.wait(timeout=self.COMMAND_TIMEOUT)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass  # The owned group exited at the deadline boundary.
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass  # The final group-wide kill also covers a surviving leader.
                # A terminated leader does not prove that its children stopped.
                # Always finish the owned group: descendants can ignore SIGTERM.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
                raise ValueError('Paired batch exceeded its execution budget; owned process group stopped')

    @staticmethod
    def read_manifest(path):
        data = TestIdentities.read_json(path)
        if (not isinstance(data, dict) or type(data.get('schema')) is not int or data['schema'] != 1 or
                type(data.get('exitstatus')) is not int):
            raise ValueError('Malformed, empty or duplicate pytest collection identities')
        TestIdentities.validate(data.get('nodeids'))
        return data

    def collect(self):
        manifest = self.output / 'collection.json'
        code = self.command([sys.executable, str(self.root / 'tools/quality/run_coverage_suite.py'),
            '--root', str(self.root), '--output', str(self.output), '--collect-manifest', str(manifest)] + self.selectors,
            self.output / 'collection.log')
        data = self.read_manifest(manifest)
        if (code != 0 or data['exitstatus'] != 0 or data.get('failures') != [] or data.get('skipped') != [] or
                data.get('deselected') != [] or data.get('omissions') != {} or data.get('selectors') != self.selectors):
            raise ValueError('Requested test collection failed, skipped a module or changed its selectors')
        self.nodes = data['nodeids']

    def check_sources(self):
        current = CoverageGate(self.root)
        if current.sources != self.initial_sources or current.qa_sources != self.initial_qa:
            raise ValueError('Production, test or observer sources changed during the paired suite')

    def run(self):
        try:
            self.collect()
            for index, nodes in enumerate(self.batches(self.nodes, self.batch_size), 1):
                name = 'batch-%04d' % index
                stage = dict(name=name, tests=nodes, admitted=False)
                self.stages.append(stage)
                try:
                    self.check_sources()
                    selection = self.output / (name + '-selection.json')
                    TestIdentities.write(selection, nodes)
                    code = self.command([sys.executable, str(self.root / 'tools/quality/run_coverage.py'),
                        '--root', str(self.root), '--output', str(self.output / name), '--test-manifest', str(selection)],
                        self.output / (name + '.log'))
                    receipt = self.output / name / 'run.json'
                    data = json.loads(receipt.read_text(encoding='utf-8'))
                    if code not in (0, 2) or data.get('tests') != nodes:
                        raise ValueError('Paired batch failed or executed different requested tests')
                    for filename in ('native.xml', 'instrumented.xml'):
                        if len(list(ET.parse(self.output / name / filename).getroot().iter('testcase'))) != len(nodes):
                            raise ValueError('A batch omitted or duplicated requested tests')
                    self.gate.add(receipt, selected_tests=nodes)
                    if self.gate.runs[-1]['skipped']:
                        raise ValueError('A required paired batch skipped tests')
                    stage.update(admitted=True, receipt_sha256=hashlib.sha256(receipt.read_bytes()).hexdigest())
                except (ValueError, OSError, KeyError, TypeError, ET.ParseError) as error:
                    stage['error'] = str(error)
                    self.gate.errors.append(name + ': ' + str(error))
                print(json.dumps(dict(batch=name, tests=len(nodes), admitted=stage['admitted'],
                                      error=stage.get('error'))), flush=True)
            self.check_sources()
        except (ValueError, OSError, KeyError, TypeError) as error:
            self.gate.errors.append(str(error))
        result = self.gate.report()
        result['paired_suite'] = dict(collection=self.nodes, batch_size=self.batch_size, stages=self.stages)
        (self.output / 'coverage-gate.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(dict(collected=len(self.nodes), batches=len(self.stages), accepted=result['accepted'])))
        return 0 if result['accepted'] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--collect-manifest', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('tests', nargs='*', default=['tests'])
    args = parser.parse_args()
    if args.collect_manifest:
        return CollectionManifest().collect(args.root, args.tests or ['tests'], args.collect_manifest)
    return SuiteRun(args.root, args.output, args.tests or ['tests'], args.batch_size).run()


if __name__ == '__main__':
    sys.exit(main())

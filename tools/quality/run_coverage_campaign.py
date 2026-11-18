"""Collect once, execute isolated native cohorts and enforce one complete gate.

The default controller intentionally has no system BIND command at fixture-owned
paths. Real-checker scenarios run in separate disposable images. Cohort success
means complete paired execution only; the final merge alone enforces coverage.
"""
import argparse
from collections import Counter
import hashlib
import itertools
import json
import os
from pathlib import Path
import re
import sys

from coverage_gate import CoverageGate
from pytest_selection import TestIdentities
from run_coverage_suite import SuiteRun


class CampaignPlan:
    """Bind an unfiltered collection and its exact partition to source bytes."""

    COHORTS = ('main', 'checker', 'public')
    MODULES = {
        'tests/test_management_acl_logging.py': 'checker',
        'tests/test_native_management_playbook.py': 'public',
    }

    @classmethod
    def partition(cls, nodes):
        TestIdentities.validate(nodes)
        groups = {name: [] for name in cls.COHORTS}
        for node in nodes:
            groups[cls.MODULES.get(node.split('::', 1)[0], 'main')].append(node)
        if any(not group for group in groups.values()):
            raise ValueError('Every required native campaign cohort must be present')
        # Distinct node IDs must not collapse to the same JUnit class/name.
        TestIdentities.selected_junit_sha256(nodes)
        return groups

    @classmethod
    def create(cls, root, output):
        """Collect all tests into fresh evidence before assigning native roots."""
        output = Path(output).resolve()
        if output.exists():
            raise ValueError('Campaign evidence requires a fresh directory')
        # SuiteRun validates the source boundary before creating either folder.
        collection = SuiteRun(root, output / 'collection', ['tests'])
        collection.collect()
        collection.check_sources()
        plan = dict(schema=1, source_sha256=collection.initial_sources,
            qa_sha256=collection.initial_qa,
            collection_sha256=hashlib.sha256((collection.output / 'collection.json').read_bytes()).hexdigest(),
            nodeids=collection.nodes, cohorts=cls.partition(collection.nodes))
        with (output / 'campaign.json').open('x', encoding='utf-8') as stream:
            json.dump(plan, stream, indent=2)
        return plan

    @classmethod
    def read(cls, root, output):
        """Refuse any changed collection, partition, production or QA source."""
        output = Path(output).resolve()
        path = output / 'campaign.json'
        plan = TestIdentities.read_json(path)
        fields = {'schema', 'source_sha256', 'qa_sha256', 'collection_sha256', 'nodeids', 'cohorts'}
        if (not isinstance(plan, dict) or set(plan) != fields or
                type(plan['schema']) is not int or plan['schema'] != 1):
            raise ValueError('Malformed native campaign plan')
        collection_path = output / 'collection/collection.json'
        collection = SuiteRun.read_manifest(collection_path)
        if (plan['collection_sha256'] != hashlib.sha256(collection_path.read_bytes()).hexdigest() or
                collection['exitstatus'] != 0 or collection.get('selectors') != ['tests'] or
                any(collection.get(key) != [] for key in ('failures', 'skipped', 'deselected')) or
                collection.get('omissions') != {} or collection['nodeids'] != plan['nodeids'] or
                plan['cohorts'] != cls.partition(plan['nodeids'])):
            raise ValueError('Campaign collection was changed, filtered or repartitioned')
        gate = CoverageGate(root)
        if plan['source_sha256'] != gate.sources or plan['qa_sha256'] != gate.qa_sources:
            raise ValueError('Campaign source or QA revision changed')
        return plan


class CampaignSuite(SuiteRun):
    """Reuse paired execution; only the inspected collection supplies identities."""

    def __init__(self, root, output, campaign, cohort, batch_size):
        self.plan = CampaignPlan.read(root, campaign)
        self.campaign, self.cohort = Path(campaign).resolve(), cohort
        nodes = self.plan['cohorts'][cohort]
        if cohort == 'checker' and (os.environ.get('BIND_MANAGEMENT_CHECKCONF') !=
                '/usr/bin/named-checkconf' or not Path('/usr/bin/named-checkconf').is_file()):
            raise ValueError('The checker cohort requires the genuine native BIND checker')
        if cohort == 'public' and not Path('/usr/sbin/named-checkconf').is_file():
            raise ValueError('The public cohort requires the deployed native checker path')
        super().__init__(root, output, nodes, batch_size)

    def collect(self):
        self.check_sources()
        if CampaignPlan.read(self.root, self.campaign) != self.plan:
            raise ValueError('Campaign plan changed before cohort execution')
        self.nodes = TestIdentities.validate(list(self.selectors))
        data = dict(schema=1, exitstatus=0, selectors=self.selectors,
            nodeids=self.nodes, failures=[], skipped=[], deselected=[], omissions={},
            campaign_sha256=hashlib.sha256((self.campaign / 'campaign.json').read_bytes()).hexdigest())
        with (self.output / 'collection.json').open('x', encoding='utf-8') as stream:
            json.dump(data, stream, indent=2)

    @classmethod
    def batches(cls, nodeids, size):
        def capacity(node):
            module = node.split('::', 1)[0]
            heavy = any(word in module for word in ('front', 'archive', 'configtool',
                'native_bash_observer', 'operation_resilience', 'native_dns_transaction'))
            # Recovery scenarios can invoke the complete script repeatedly.
            # A four-case group exceeded the unchanged 16 MiB observation cap;
            # keep each such case isolated instead of increasing that budget.
            return 1 if heavy else size
        for limit, nodes in itertools.groupby(nodeids, key=capacity):
            yield from super().batches(nodes, limit)

    def run_cohort(self):
        """Return execution completion only; full thresholds await the merge."""
        self.run()
        report = TestIdentities.read_json(self.output / 'coverage-gate.json')
        stages = report['paired_suite']['stages']
        complete = (report['errors'] == [] and stages and
            all(stage.get('admitted') is True for stage in stages) and
            report['paired_suite']['collection'] == self.selectors and
            [node for stage in stages for node in stage['tests']] == self.selectors)
        # A partial component percentage cannot authorize the full campaign.
        # Every skipped/failed/refused test still prevents cohort completion.
        return 0 if complete else 2


class CampaignMerge:
    """Re-admit every sealed receipt and reject gaps, duplicates and refusals."""

    @classmethod
    def run(cls, root, output):
        output = Path(output).resolve()
        plan = CampaignPlan.read(root, output)
        gate = CoverageGate(root)
        seen, receipts = [], []
        for cohort in CampaignPlan.COHORTS:
            folder = output / cohort
            summary = TestIdentities.read_json(folder / 'coverage-gate.json')
            stages = summary['paired_suite']['stages']
            expected = plan['cohorts'][cohort]
            if (summary.get('errors') != [] or not stages or
                    summary['paired_suite']['collection'] != expected or
                    [node for stage in stages for node in stage['tests']] != expected):
                raise ValueError('Missing, refused or altered native campaign cohort')
            for stage in stages:
                if (stage.get('admitted') is not True or
                        re.fullmatch(r'batch-[0-9]{4}', stage.get('name', '')) is None):
                    raise ValueError('A mandatory campaign batch was refused')
                path = folder / stage['name'] / 'run.json'
                raw = path.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                receipt = TestIdentities.read_json(path)
                if digest != stage.get('receipt_sha256') or receipt['tests'] != stage['tests']:
                    raise ValueError('Campaign receipt changed after paired admission')
                gate.add(path, selected_tests=receipt['tests'])
                if gate.runs[-1]['executed_tests'] != len(receipt['tests']):
                    raise ValueError('Campaign JUnit count differs from the exact test selection')
                if gate.runs[-1]['skipped']:
                    raise ValueError('A mandatory campaign test was skipped')
                seen.extend(receipt['tests'])
                receipts.append(dict(cohort=cohort, path=str(path), sha256=digest))
        if len(seen) != len(set(seen)) or Counter(seen) != Counter(plan['nodeids']):
            raise ValueError('Campaign omitted or duplicated required tests')
        if CampaignPlan.read(root, output) != plan:
            raise ValueError('Campaign source, QA or plan changed during merge')
        report = gate.report()
        report['campaign'] = dict(collected=len(plan['nodeids']), executed_unique=len(seen),
            complete=True, duplicate_count=0, receipts=receipts,
            plan_sha256=hashlib.sha256((output / 'campaign.json').read_bytes()).hexdigest())
        with (output / 'coverage-gate.json').open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        return 0 if report['accepted'] else 2


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('plan', 'run', 'merge'))
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cohort', choices=CampaignPlan.COHORTS)
    parser.add_argument('--batch-size', type=int, default=32)
    args = parser.parse_args()
    if args.phase == 'plan':
        CampaignPlan.create(args.root, args.output)
        return 0
    if args.phase == 'merge':
        return CampaignMerge.run(args.root, args.output)
    if args.cohort is None:
        parser.error('run requires --cohort')
    return CampaignSuite(args.root, args.output / args.cohort,
        args.output, args.cohort, args.batch_size).run_cohort()


if __name__ == '__main__':
    sys.exit(main())

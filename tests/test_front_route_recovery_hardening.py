"""Reject ambiguous current journals and receipts before trusting route ownership."""
import copy

import pytest
import yaml

from route_task_expressions import TaskExpressions
from test_native_front_routes import BASE, NativeRouteLab, pytestmark as native_pytestmark
from test_front_routes_ansible import AnsibleRouteSandbox, pytestmark as ansible_pytestmark

pytestmark = native_pytestmark


class TestCurrentJournalHardening:
    """Use genuine transactions; semantic corruption keeps valid checksums."""

    @staticmethod
    def prepare(tmp_path):
        lab = NativeRouteLab(tmp_path)
        result = lab.run(more=['--defer-commit'])
        assert result.returncode == 2, result.stdout
        return lab, lab.state / 'front-routes.pending'

    @staticmethod
    def require_safe_refusal(lab, journal):
        before = copy.deepcopy(lab.data)
        result = lab.run('recover')
        assert result.returncode == 1, result.stdout
        assert lab.data == before, 'Invalid evidence must not authorize a native write'
        assert journal.is_dir(), 'Invalid evidence must remain available for inspection'

    @pytest.mark.parametrize('suffix', [b'\n', b'0 done\n', b'\x00', b'unterminated'])
    def test_progress_must_be_exactly_one_complete_record(self, tmp_path, suffix):
        lab, journal = self.prepare(tmp_path)
        progress = journal / 'progress'
        progress.write_bytes(progress.read_bytes() + suffix)
        self.require_safe_refusal(lab, journal)

    @pytest.mark.parametrize('suffix', ['\n', 'untrusted record\n'])
    def test_metadata_cannot_hide_additional_records(self, tmp_path, suffix):
        lab, journal = self.prepare(tmp_path)
        lab.rewrite_journal('meta', (journal / 'meta').read_text() + suffix)
        self.require_safe_refusal(lab, journal)

    @pytest.mark.parametrize('name', ['checksums', 'profile', 'kernel', 'addresses', 'rules', 'clients'])
    def test_trailing_empty_records_cannot_disappear_from_journal_files(self, tmp_path, name):
        lab, journal = self.prepare(tmp_path)
        if name == 'checksums':
            path = journal / name
            path.write_bytes(path.read_bytes() + b'\n')
        else:
            lab.rewrite_journal(name, (journal / name).read_text() + '\n')
        self.require_safe_refusal(lab, journal)

    @pytest.mark.parametrize('field,value', [(0, '-' * 36), (7, '0')])
    def test_metadata_requires_a_canonical_token_and_positive_metric(self, tmp_path, field, value):
        lab, journal = self.prepare(tmp_path)
        fields = (journal / 'meta').read_text().strip().split('|')
        fields[field] = value
        lab.rewrite_journal('meta', '|'.join(fields) + '\n')
        self.require_safe_refusal(lab, journal)

    @pytest.mark.parametrize('clients', [
        '', 'not-an-address\n', '203.0.113.1/24\n', '0.0.0.0/0\n',
        '198.51.100.25\n', '203.0.113.0/24\x00\n', '203.0.113.0/24\n' * 513],
        ids=['empty', 'invalid', 'noncanonical', 'default', 'unrelated', 'nul', 'oversized'])
    def test_owned_routes_require_a_bounded_valid_matching_client_union(self, tmp_path, clients):
        lab, journal = self.prepare(tmp_path)
        lab.rewrite_journal('clients', clients)
        self.require_safe_refusal(lab, journal)

    def test_duplicate_uncompleted_operations_cannot_authorize_cleanup(self, tmp_path):
        lab, journal = self.prepare(tmp_path)
        lab.rewrite_journal('operations', (journal / 'operations').read_text() * 2)
        self.require_safe_refusal(lab, journal)

    @pytest.mark.parametrize('clients', [
        ['203.0.112.0/24', '203.0.113.0/24'],
        ['203.0.113.25', '172.20.0.0/16']])
    def test_valid_aggregated_or_mixed_client_plans_remain_recoverable(self, tmp_path, clients):
        lab = NativeRouteLab(tmp_path)
        applied = lab.run(clients=clients, more=['--defer-commit'])
        assert applied.returncode == 2, applied.stdout
        recovered = lab.run('recover')
        assert recovered.returncode == 2, recovered.stdout
        assert lab.data['runtime'] == BASE and not lab.data['persistent']
        assert not (lab.state / 'front-routes.pending').exists()

    def test_an_existing_zero_profile_metric_remains_supported(self, tmp_path):
        lab = NativeRouteLab(tmp_path)
        lab.data['persistent'] = [
            {'network': '203.0.113.0/24', 'gateway': '198.51.100.1', 'metric': 0}]
        applied = lab.run(more=['--defer-commit'])
        assert applied.returncode == 2, applied.stdout
        assert lab.data['runtime'][-1]['metric'] == 0
        recovered = lab.run('recover')
        assert recovered.returncode == 2, recovered.stdout
        assert lab.data['runtime'] == BASE
        assert lab.data['persistent'][0]['metric'] == 0


class TestNativeObservationHardening:
    """A malformed observation must not collapse into a valid route identity."""

    @pytest.mark.parametrize('route', [
        'via 198.51.100.254 via 198.51.100.1 dev front0 metric 100',
        'via 198.51.100.1 dev other0 dev front0 metric 100',
        'via 198.51.100.1 dev front0 metric 999 metric 100'])
    def test_duplicate_identity_fields_are_refused_before_route_planning(self, tmp_path, route):
        lab = NativeRouteLab(tmp_path)
        lab.data['persistent'] = [
            {'network': '203.0.113.0/24', 'gateway': '198.51.100.1', 'metric': 100}]
        lab.data['runtime_text'] = (
            'default via 10.0.0.1 dev back0 metric 100\n'
            '198.51.100.0/24 dev front0 proto kernel scope link\n'
            '203.0.113.0/24 ' + route)
        before = copy.deepcopy(lab.data)
        result = lab.run('plan')
        assert result.returncode == 1, result.stdout
        assert 'Duplicate runtime route field' in result.stdout
        assert lab.data == before


@ansible_pytestmark
class TestAnsibleReceiptHardening:
    """Treat a deferred transaction as owned only after one canonical receipt."""

    @pytest.mark.parametrize('ending', ['\n', '\r\n'], ids=['LF', 'CRLF'])
    def test_canonical_receipt_accepts_standard_and_ssh_line_endings(self, ending):
        token = '12345678-1234-4234-8234-123456789abc'
        tasks = TaskExpressions.load('deploy/front_return_routes.yml')
        selected = [task for task in tasks if task['name'] in (
            'Record pending FRONT changes without trusting an unverified receipt',
            'Require an unambiguous receipt before any deferred route operation',
            'Retain only the transaction created by this DNS deployment')]
        result = TaskExpressions({
            'bind_front_action': 'apply', 'bind_front_routes_defer_commit': True,
            'bind_front_operation': {'rc': 2, 'stdout': 'transaction: ' + token + ending}
        }).run('', tasks=selected)
        assert result['bind_front_transaction_token'] == token
        assert result['bind_front_route_transaction_pending'] is True

    @pytest.mark.parametrize('receipt', ['duplicate', 'malformed', 'extra-invalid'])
    def test_ambiguous_receipts_are_refused_and_the_transaction_remains_recoverable(self, tmp_path, receipt):
        sandbox = AnsibleRouteSandbox(tmp_path)
        helper = tmp_path / 'roles/bind9/files/bind-front-routes.sh'
        source = helper.read_text()
        original = "printf 'transaction: %s\\n' \"$TOKEN\""
        if receipt == 'duplicate':
            replacement = original + ' "$TOKEN"'
        elif receipt == 'malformed':
            replacement = "printf 'transaction: %s\\n' '" + '-' * 36 + "'"
        else:
            replacement = original + "; printf 'transaction: invalid\\n'"
        assert source.count(original) == 1
        helper.write_text(source.replace(original, replacement))
        # The failed receipt must keep the deployment pending without carrying
        # a stale token into the existing DNS rollback/lease-release guards.
        wrapper = tmp_path / 'roles/bind9/tasks/tests_receipt_state.yml'
        wrapper.write_text(yaml.safe_dump([{
            'block': [{'ansible.builtin.import_tasks': '{{ role_path }}/tasks/tasks.d/deploy/front_return_routes.yml'}],
            'rescue': [
                {'ansible.builtin.assert': {'that': [
                    'bind_front_route_transaction_pending | bool',
                    "bind_front_transaction_token | default('stale') == ''",
                    'not (bind_front_route_rollback_attempted | bool)']}},
                {'ansible.builtin.fail': {'msg': 'Pending receipt remains unowned and recoverable'}}]
        }]))
        applied = sandbox.run(tasks_from='tests_receipt_state.yml', extra_vars={'bind_front_routes_defer_commit': True})
        assert applied.returncode != 0, applied.stdout
        assert 'receipt is missing, malformed or ambiguous' in applied.stdout
        assert 'Pending receipt remains unowned and recoverable' in applied.stdout
        journal = sandbox.state_root / 'front-routes.pending'
        assert journal.is_dir()
        recovered = sandbox.run(extra_vars={'bind_front_requested_action': 'recover'})
        assert recovered.returncode == 0, recovered.stdout
        assert sandbox.read()['runtime'] == BASE and not sandbox.read()['persistent']
        assert not journal.exists()

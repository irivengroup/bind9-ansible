"""Execute safe refusal/recovery with real Ansible and native CLI doubles."""
import os
import fcntl

from test_front_routes_ansible import AnsibleRouteSandbox, BASE, pytestmark as inherited_pytestmark

pytestmark = inherited_pytestmark


class TestFrontRecoveryPreconditions:
    """All ownership checks must complete before either route store is changed."""

    @staticmethod
    def prepare(tmp_path):
        sandbox = AnsibleRouteSandbox(tmp_path)
        result = sandbox.run(extra_vars={'bind_front_routes_defer_commit': True})
        assert result.returncode == 0, result.stdout
        sandbox.data = sandbox.read()
        journal = sandbox.state_root / 'front-routes.pending'
        path = journal / 'meta'
        return sandbox, journal, path

    def test_changed_kernel_route_does_not_remove_persistent_route(self, tmp_path):
        sandbox, journal, _ = self.prepare(tmp_path)
        sandbox.data['runtime'][-1]['metric'] = 200
        sandbox.save()
        result = sandbox.run(extra_vars={'bind_front_requested_action': 'recover'})
        assert result.returncode != 0, result.stdout
        assert sandbox.read() == sandbox.data
        assert journal.is_dir()

    def test_hardlinked_journal_is_rejected_without_mutation(self, tmp_path):
        sandbox, journal, path = self.prepare(tmp_path)
        os.link(path, tmp_path / 'journal-link.json')
        sandbox.save()
        result = sandbox.run(extra_vars={'bind_front_requested_action': 'recover'})
        assert result.returncode != 0, result.stdout
        assert sandbox.read() == sandbox.data
        assert journal.is_dir()

    def test_gateway_drift_under_lock_refuses_writes_and_releases_only_our_lock(self, tmp_path):
        sandbox = AnsibleRouteSandbox(tmp_path)
        sandbox.data['drift_gateway_on_second_read'] = True
        sandbox.save()
        result = sandbox.run()
        assert result.returncode != 0, result.stdout
        assert 'Routing changed after planning' in result.stdout
        current = sandbox.read()
        assert current['persistent'] == [] and current['runtime'] == BASE
        assert current['operations'] == []
        assert not (sandbox.state_root / 'front-routes.pending').exists()

    def test_invalid_administration_address_identifies_its_source_without_writes(self, tmp_path):
        sandbox = AnsibleRouteSandbox(tmp_path)
        result = sandbox.run(extra_vars={'ansible_host': 'dns-admin.example.test'})
        assert result.returncode != 0, result.stdout
        assert 'Invalid IPv4' in result.stdout
        assert 'dns-admin.example.test' in result.stdout
        assert sandbox.read() == sandbox.data
        assert not (sandbox.state_root / 'front-routes.pending').exists()

    def test_current_recovery_refuses_a_live_route_or_management_process(self, tmp_path):
        sandbox, journal, _ = self.prepare(tmp_path)
        sandbox.save()
        lock = sandbox.state_root / 'operations.flock'
        with lock.open('w+b') as stream:
            lock.chmod(0o600)
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = sandbox.run(extra_vars={'bind_front_requested_action': 'recover'})
            assert result.returncode != 0 and 'Another route or management operation' in result.stdout
            assert sandbox.read() == sandbox.data and journal.is_dir()

    def test_current_recovery_preserves_unexpected_operator_evidence(self, tmp_path):
        sandbox, journal, _ = self.prepare(tmp_path)
        evidence = journal / 'operator-evidence'
        evidence.write_bytes(b'preserve operator data')
        sandbox.save()
        result = sandbox.run(extra_vars={'bind_front_requested_action': 'recover'})
        assert result.returncode != 0, result.stdout
        assert sandbox.read() == sandbox.data
        assert evidence.read_bytes() == b'preserve operator data'

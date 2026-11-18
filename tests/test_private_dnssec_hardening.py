"""Reject unsafe DNSSEC policy/state objects before a blocking read or RNDC."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'roles/bind9/files/bind-private-dnssec.py'


class TestDnssecReadSafety:
    """Malformed local files must not hang named startup or the renewal timer."""

    @pytest.mark.skipif(os.name != 'posix', reason='Requires POSIX FIFO semantics')
    def test_fifo_policy_is_rejected_without_waiting_for_a_writer(self, tmp_path):
        fifo = tmp_path / 'policy'
        os.mkfifo(fifo, 0o600)
        code = ('import importlib.util,sys; s=importlib.util.spec_from_file_location("test_dnssec",sys.argv[1]);'
                'm=importlib.util.module_from_spec(s);s.loader.exec_module(m);m.PrivateDnssec.read(m.Path(sys.argv[2]))')
        try:
            result = subprocess.run([sys.executable, '-c', code, str(SCRIPT), str(fifo)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=2)
        except subprocess.TimeoutExpired:
            pytest.fail('DNSSEC reader blocked on a FIFO before validating its type')
        assert result.returncode != 0
        assert 'Unsafe DNSSEC policy/state' in result.stderr

    @pytest.mark.skipif(not hasattr(os, 'geteuid') or os.geteuid() != 0,
                        reason='Requires root to exercise the root-owned policy contract')
    def test_hardlinked_policy_is_rejected(self, tmp_path):
        spec = importlib.util.spec_from_file_location('test_dnssec_read', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        policy = tmp_path / 'policy'
        policy.write_text('private test policy\n')
        policy.chmod(0o600)
        os.link(policy, tmp_path / 'other-policy')
        with pytest.raises(ValueError, match='Unsafe DNSSEC policy/state'):
            module.PrivateDnssec.read(policy)

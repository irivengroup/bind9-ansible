"""Reuse all firewall model assertions while executing untouched task mappings."""
import os
import subprocess
from unittest.mock import patch

import pytest

from native_ansible_runner import NativeFirewallRun
from test_firewall_ansible import FirewallRunner

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires explicitly enabled disconnected native CLI fixtures')


class NativeFirewallRunner(FirewallRunner):
    """Swap only controller transport, preserving the existing model/oracles."""

    def execute(self, playbook, environment):
        with patch.dict(os.environ, environment):
            status, result = NativeFirewallRun('tasks.d/deploy/firewall.yml', {
                'bind_front_ip': '192.0.2.10', 'bind_back_ip': '10.0.0.10'}).run()
        return subprocess.CompletedProcess(['native-canonical-firewall'], status,
                                           stdout='; '.join(result.failed))


@pytest.mark.parametrize('scenario', ['split', 'shared_default', 'empty_addresses',
                                     'inactive', 'missing_back', 'query_error'])
def test_native_firewall_dispatch_preserves_additive_rules_scopes_and_refusals(tmp_path, scenario):
    NativeFirewallRunner().run(tmp_path, scenario, False)

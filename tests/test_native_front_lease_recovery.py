"""A failed native FRONT rollback must retain the real deployment lease.

The script, journal, inode ownership and Ansible release tasks are unchanged.
Only ip/nmcli use the shared, explicitly bounded failure boundary.
"""
import os
from pathlib import Path

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeLeaseRun
from test_native_front_routes import BASE, NativeRouteLab

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1' or
    os.environ.get('BIND9_CANONICAL_BASH_LAB') != '1', reason='Requires the explicit canonical disconnected Bash root')
STATE = Path('/var/lib/Bind9-Ansible')


def test_native_front_failed_automatic_rollback_retains_lease_until_explicit_recovery(tmp_path):
    with NativeFilesystemScope(NativeLeaseRun):
        status, acquired = NativeLeaseRun('tasks.d/deploy/guard.yml', {'bind_operation_lock_enabled': True}).run()
        assert status == 0 and not acquired.failed
        lease = STATE / '.operation.lock'
        identity = lease.stat().st_ino
        values = dict(acquired.facts, bind_deployment_owned_lease=acquired.registers['bind_deployment_owned_lease'],
            bind_front_route_transaction_pending=False, bind_deployment_retain_lease=False)
        lab = NativeRouteLab(tmp_path)
        lab.data.update(fail_active_once=True, fail_persistent_remove_once=True)

        def execute(action):
            # Move the same owned inode tree into the shared canonical lab.
            # Its context restores all paths; no production source is rewritten.
            STATE.rename(lab.state)
            try:
                return lab.run(action)
            finally:
                lab.state.rename(STATE)

        failed = execute('apply')
        assert failed.returncode == 1 and 'FRONT recovery required:' in failed.stdout, failed.stdout
        assert (STATE / 'front-routes.pending').is_dir()
        assert lab.data['runtime'] == BASE and len(lab.data['persistent']) == 1
        status, retained = NativeLeaseRun('tasks.d/deploy/lease_release.yml', values).run()
        assert status == 2 and retained.failed == ['Preserve the operation lease while FRONT recovery evidence exists']
        assert lease.stat().st_ino == identity
        recovered = execute('recover')
        assert recovered.returncode == 2 and 'FRONT rollback completed' in recovered.stdout, recovered.stdout
        assert lab.data['runtime'] == BASE and lab.data['persistent'] == []
        assert not (STATE / 'front-routes.pending').exists()
        status, released = NativeLeaseRun('tasks.d/deploy/lease_release.yml', values).run()
        assert status == 0 and not released.failed and not lease.exists()

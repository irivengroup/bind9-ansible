"""Exercise the untouched production rescue with genuine Ansible task objects."""
import json
from pathlib import Path
import os
import base64
import pytest

from native_ansible_runner import NativeFilesystemScope, NativeRecoveryRun, NativeUnitBoundary

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')


@pytest.mark.parametrize('existed', [False, True])
def test_native_failed_runtime_restore_preserves_data_continues_dns_and_retains_ownership(tmp_path, existed):
    with NativeFilesystemScope(NativeRecoveryRun):
        with NativeUnitBoundary(tmp_path, {'named': {'active': True, 'enabled': True}}) as state:
            path = Path('/usr/local/sbin/bind-private-dnssec')
            path.mkdir()
            foreign = path / 'operator-evidence'
            foreign.write_text('retain unrelated data', encoding='utf-8')
            marker = tmp_path / 'backup-restored'
            binary = Path('/usr/local/sbin/bind-configtool')
            binary.write_text('#!/bin/sh\n[ "$#" = 5 ] && [ "$1" = restore ] && [ "$2" = --root ] && '
                              '[ "$3" = /tmp/synthetic-backups ] && [ "$4" = --archive ] && '
                              '[ "$5" = known-safe.tar.gz ] || exit 91\nprintf restored > ' + str(marker) + '\n')
            binary.chmod(0o700)
            snapshot = {'item': str(path), 'stat': {'exists': existed, 'uid': 0, 'gid': 0, 'mode': '0700'}}
            status, result = NativeRecoveryRun('tasks.d/runtime_restore.yml', {
                'bind_front_route_transaction_pending': False,
                'bind_runtime_snapshot_ready': True,
                'bind_runtime_files_before': {'results': [snapshot]},
                'bind_runtime_content_before': {'results': ([{'item': snapshot,
                    'content': base64.b64encode(b'previous runtime bytes').decode('ascii')}] if existed else [])},
                'bind_backup_root': '/tmp/synthetic-backups',
                'bind_recovery_archive': '/tmp/synthetic-backups/known-safe.tar.gz'}).run()
            assert status == 2
            if existed:
                assert marker.is_file(), 'Runtime copy failure must not prevent independent DNS backup recovery'
            assert foreign.is_file(), 'Runtime recovery must preserve the unexpected directory'
            assert foreign.read_text(encoding='utf-8') == 'retain unrelated data'
            assert marker.read_text() == 'restored', 'Independent DNS backup recovery must still execute'
            units = json.loads(state.read_text())
            assert units['units']['named']['active'] is False, 'Incomplete runtime recovery must not restart named'
            assert result.facts['bind_deployment_retain_lease'] is True
            assert result.facts['bind_runtime_restore_incomplete'] is True

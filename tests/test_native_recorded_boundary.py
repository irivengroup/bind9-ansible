"""Private command fixtures must preserve occupied paths and reject bad replies."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeRecordedBoundary, NativeVerifyRun

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')


@pytest.mark.parametrize('kind', ['state-link', 'private-file', 'private-link', 'command-file'])
def test_recorded_command_setup_preserves_occupied_objects(tmp_path, kind):
    with NativeFilesystemScope(NativeVerifyRun):
        boundary = NativeRecordedBoundary(tmp_path, NativeVerifyRun, {})
        outside = tmp_path / 'operator-evidence'
        outside.write_text('unrelated state', encoding='utf-8')
        if kind == 'state-link': boundary.state.symlink_to(tmp_path / 'absent')
        elif kind == 'private-file': (tmp_path / 'named-checkconf').write_bytes(outside.read_bytes())
        elif kind == 'private-link': (tmp_path / 'named-checkconf').symlink_to(outside)
        else: Path(NativeVerifyRun.COMMANDS[0]).write_bytes(outside.read_bytes())
        with pytest.raises(ValueError, match='occupied'):
            with boundary:
                pytest.fail('Occupied fixture paths must stop setup')
        assert outside.read_text() == 'unrelated state'
        if kind == 'state-link':
            assert boundary.state.is_symlink() and not (tmp_path / 'absent').exists()
        elif kind == 'private-file': assert (tmp_path / 'named-checkconf').read_bytes() == outside.read_bytes()
        elif kind == 'private-link': assert (tmp_path / 'named-checkconf').is_symlink()
        else: assert Path(NativeVerifyRun.COMMANDS[0]).read_bytes() == outside.read_bytes()


def test_recorded_command_cleanup_preserves_a_replaced_fixture(tmp_path):
    with NativeFilesystemScope(NativeVerifyRun):
        path = Path(NativeVerifyRun.COMMANDS[-1])
        with pytest.raises(ValueError, match='identity changed'):
            with NativeRecordedBoundary(tmp_path, NativeVerifyRun, {}):
                path.unlink()
                path.write_text('replacement must remain for inspection', encoding='utf-8')
        assert path.read_text() == 'replacement must remain for inspection'


@pytest.mark.parametrize('reply', [None, {'rc': True}, {'rc': 0.0}, {'rc': -1}, {'rc': 0, 'stdout': []},
    [{'rc': 0}], {'sequence': []}, {'sequence': 'invalid'}, {'sequence': [None]},
    {'sequence': [{'rc': True}]}, {'sequence': [{'rc': 0}] * 17}])
def test_recorded_cli_refuses_unknown_or_malformed_replies(tmp_path, reply):
    key = json.dumps(['named-checkconf', '-z', '/etc/named.conf'])
    with NativeFilesystemScope(NativeVerifyRun):
        with NativeRecordedBoundary(tmp_path, NativeVerifyRun, {key: reply}) as state:
            result = subprocess.run([NativeVerifyRun.COMMANDS[0], '-z', '/etc/named.conf'],
                                    capture_output=True, timeout=10)
            assert result.returncode == 91 and result.stdout == b''
            assert json.loads(state.read_text())['calls'] == [['named-checkconf', '-z', '/etc/named.conf']]


def test_recorded_retry_sequence_is_consumed_once_and_never_repeats_an_exhausted_reply(tmp_path):
    args = ['-z', '/etc/named.conf']
    argv = ['named-checkconf'] + args
    with NativeFilesystemScope(NativeVerifyRun):
        with NativeRecordedBoundary(tmp_path, NativeVerifyRun, {json.dumps(argv): {
                'sequence': [{'rc': 17, 'stdout': 'retry'}, {'rc': 0, 'stdout': 'ready'}]}}) as state:
            replies = [subprocess.run([NativeVerifyRun.COMMANDS[0]] + args, capture_output=True, timeout=10) for _ in range(3)]
            assert [(reply.returncode, reply.stdout) for reply in replies] == [(17, b'retry'), (0, b'ready'), (91, b'')]
            data = json.loads(state.read_text())
            assert data['calls'] == [argv] * 3
            assert data['replies'][json.dumps(argv)]['sequence'] == []

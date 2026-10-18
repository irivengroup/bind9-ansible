"""Observable recovery and process-lock contracts shared by routes and CRUD."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from test_management import Lab, M, pytestmark as inherited_pytestmark

pytestmark = inherited_pytestmark


class InterruptedTransaction:
    """Create durable crash evidence without calling any new production helper."""

    def __init__(self, lab, status='prepared'):
        self.state = lab.root / 'var/lib/Bind9-Ansible'
        self.state.mkdir(parents=True, mode=0o700)
        self.folder = lab.root / 'var/backups/Bind9-Ansible/management/transaction-interrupted'
        self.folder.mkdir(parents=True, mode=0o700)
        self.folder.parent.chmod(0o700)
        self.manifest = self.folder / 'manifest.json'
        self.manifest.write_text(json.dumps({'schema': 1, 'status': status, 'files': {}}))
        self.manifest.chmod(0o600)
        self.pointer = self.state / 'management.pending.json'
        self.pointer.write_text(json.dumps({'schema': 1,
            'transaction': '/var/backups/Bind9-Ansible/management/transaction-interrupted'}, sort_keys=True))
        self.pointer.chmod(0o600)


def test_unresolved_journal_blocks_next_write_without_preventing_read(tmp_path):
    lab = Lab(tmp_path)
    evidence = InterruptedTransaction(lab)
    before = (lab.root / 'etc/named.conf').read_bytes()
    checksum = lab.manager().run()['checksum']
    manager = lab.manager(action='update', expected_checksum=checksum, value='no')
    with pytest.raises(M.ManagementError, match='Unresolved management transaction'):
        manager.run()
    assert (lab.root / 'etc/named.conf').read_bytes() == before
    assert evidence.pointer.exists() and evidence.manifest.exists()
    assert not lab.calls


def test_a_finished_pointer_does_not_hide_another_unresolved_transaction(tmp_path):
    lab = Lab(tmp_path)
    completed = InterruptedTransaction(lab, 'committed')
    other = completed.folder.parent / 'transaction-other-interrupted'
    other.mkdir(mode=0o700)
    manifest = other / 'manifest.json'
    manifest.write_text(json.dumps({'schema': 1, 'status': 'recovery-required', 'files': {}}))
    manifest.chmod(0o600)
    before = (tmp_path / 'etc/named.conf').read_bytes()
    with pytest.raises(M.ManagementError, match='Unresolved legacy management transaction'):
        lab.manager().recovery_guard(writing=True)
    assert (tmp_path / 'etc/named.conf').read_bytes() == before
    assert manifest.exists() and not lab.calls


def test_reentering_one_manager_cannot_erase_outer_lock_ownership(tmp_path):
    lab = Lab(tmp_path)
    manager = lab.manager()
    with manager.operation():
        descriptor = manager.operation_descriptor
        with pytest.raises(M.ManagementError):
            with manager.operation():
                pytest.fail('The same manager cannot own two operation contexts')
        assert manager.operation_descriptor == descriptor
        (tmp_path / 'var/lib/Bind9-Ansible/operations.flock').unlink()
        with pytest.raises(M.ManagementError, match='Transient operation lock'):
            manager.check_lease()


@pytest.mark.parametrize('status', ['committed', 'rolled-back'])
def test_completed_marker_is_cleared_automatically_without_dns_changes(tmp_path, status):
    lab = Lab(tmp_path)
    evidence = InterruptedTransaction(lab, status)
    before = (lab.root / 'etc/named.conf').read_bytes()
    lab.manager().run()
    assert not evidence.pointer.exists() and evidence.manifest.exists()
    assert (lab.root / 'etc/named.conf').read_bytes() == before


@pytest.mark.parametrize('raw', [b'', b'{', b'{"schema":1,"transaction":"/etc/named.conf"}', b'x' * 4097])
def test_corrupt_pending_marker_cannot_authorize_a_write(tmp_path, raw):
    lab = Lab(tmp_path)
    evidence = InterruptedTransaction(lab)
    evidence.pointer.write_bytes(raw)
    with pytest.raises(M.ManagementError, match='pending|Pending|Malformed|private'):
        lab.manager().run()
    assert evidence.pointer.read_bytes() == raw and not lab.calls


def test_existing_empty_lock_file_is_reusable_but_a_live_owner_excludes_crud(tmp_path):
    lab = Lab(tmp_path)
    first, second = lab.manager(), lab.manager()
    with first.operation():
        with pytest.raises(M.ManagementError, match='Another route or management operation'):
            with second.operation():
                pytest.fail('Concurrent operation acquired the same lock')
    with second.operation():
        assert second.run()['found']
    assert (tmp_path / 'var/lib/Bind9-Ansible/operations.flock').is_file()


def test_sigkill_releases_kernel_lock_without_operator_cleanup(tmp_path):
    lab = Lab(tmp_path)
    ready = tmp_path / 'ready'
    module = Path(M.__file__)
    source = ('import importlib.util,time; from pathlib import Path; '
              's=importlib.util.spec_from_file_location("m",' + repr(str(module)) + '); '
              'm=importlib.util.module_from_spec(s); s.loader.exec_module(m); '
              'x=m.Manager({"resource":"option","action":"read","name":"minimal-responses"},{},root=' + repr(str(tmp_path)) + '); '
              'lease=x.operation(); lease.__enter__(); Path(' + repr(str(ready)) + ').touch(); time.sleep(30)')
    process = subprocess.Popen([sys.executable, '-c', source])
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert ready.exists()
        os.kill(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        with lab.manager().operation():
            pass
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'directory', 'writable', 'content'])
def test_unsafe_lock_is_refused_without_replacing_it(tmp_path, kind):
    lab = Lab(tmp_path)
    state = lab.manager().private_directory('/var/lib/Bind9-Ansible')
    path = state / 'operations.flock'
    if kind == 'symlink':
        path.symlink_to(lab.root / 'etc/named.conf')
    elif kind == 'directory':
        path.mkdir(mode=0o700)
    else:
        path.touch(mode=0o600)
        if kind == 'hardlink':
            os.link(str(path), str(state / 'operator-copy'))
        elif kind == 'writable':
            path.chmod(0o666)
        elif kind == 'content':
            path.write_bytes(b'operator content')
    identity = path.lstat().st_ino
    with pytest.raises((M.ManagementError, OSError)):
        with lab.manager().operation():
            pytest.fail('Unsafe lock was accepted')
    assert path.lstat().st_ino == identity


def test_rollback_reports_original_controlled_failure(tmp_path):
    lab = Lab(tmp_path)
    checksum = lab.manager().run()['checksum']
    attempts = []
    def fail_first_reload(argv):
        if Path(argv[0]).name == 'rndc' and argv[-1] == 'reload':
            attempts.append(True)
            if len(attempts) == 1:
                raise M.ManagementError('Native validation/reload failed: rndc (rc 9)')
    lab.fail = fail_first_reload
    before = (lab.root / 'etc/named.conf').read_bytes()
    with pytest.raises(M.ManagementError, match='Cause: Native validation/reload failed: rndc'):
        lab.manager(action='update', expected_checksum=checksum, value='no').run()
    assert (lab.root / 'etc/named.conf').read_bytes() == before
    assert not (lab.root / 'var/lib/Bind9-Ansible/management.pending.json').exists()


def test_live_update_checks_its_transient_lock_and_clears_only_its_marker(tmp_path):
    lab = Lab(tmp_path)
    checksum = lab.manager().run()['checksum']
    manager = lab.manager(action='update', expected_checksum=checksum, value='no')
    with manager.operation():
        assert manager.run()['changed']
    assert not (tmp_path / 'var/lib/Bind9-Ansible/management.pending.json').exists()


@pytest.mark.parametrize('change', ['remove', 'replace'])
def test_lost_runtime_lock_cannot_authorize_publication(tmp_path, change):
    lab = Lab(tmp_path)
    checksum = lab.manager().run()['checksum']
    manager = lab.manager(action='update', expected_checksum=checksum, value='no')
    before = (tmp_path / 'etc/named.conf').read_bytes()
    with manager.operation():
        path = tmp_path / 'var/lib/Bind9-Ansible/operations.flock'
        path.unlink()
        if change == 'replace':
            path.touch(mode=0o600)
        with pytest.raises(M.ManagementError, match='Transient operation lock'):
            manager.run()
    assert (tmp_path / 'etc/named.conf').read_bytes() == before


@pytest.mark.parametrize('filename', ['front-routes.pending', 'front-routes.lock'])
def test_pending_front_transaction_prevents_overlapping_dns_publication(tmp_path, filename):
    lab = Lab(tmp_path)
    checksum = lab.manager().run()['checksum']
    folder = tmp_path / 'var/lib/Bind9-Ansible'
    folder.mkdir(parents=True, mode=0o700)
    (folder / filename).mkdir(mode=0o700)
    with pytest.raises(M.ManagementError, match='pending FRONT transaction'):
        lab.manager(action='update', expected_checksum=checksum, value='no').run()
    assert not lab.calls


@pytest.mark.parametrize('failure', ['schema', 'untrusted', 'broken', 'missing', 'ancestor'])
def test_unsafe_recovery_manifest_never_triggers_a_new_write(tmp_path, failure):
    lab = Lab(tmp_path)
    evidence = InterruptedTransaction(lab)
    if failure == 'schema':
        evidence.manifest.write_text('{"schema":2,"status":"committed"}')
    elif failure == 'untrusted':
        evidence.manifest.chmod(0o666)
    elif failure == 'broken':
        evidence.manifest.write_text('{')
    elif failure == 'missing':
        evidence.manifest.unlink()
    else:
        evidence.folder.chmod(0o777)
    with pytest.raises(M.ManagementError, match='pending|Pending|Unsafe|Unsupported|private'):
        lab.manager().run()
    assert evidence.pointer.exists() and not lab.calls


def test_foreign_pending_marker_is_never_removed(tmp_path):
    lab = Lab(tmp_path)
    evidence = InterruptedTransaction(lab)
    evidence.pointer.write_bytes(b'operator data')
    with pytest.raises(M.ManagementError, match='ownership changed'):
        lab.manager().clear_pending(evidence.folder)
    assert evidence.pointer.read_bytes() == b'operator data'


@pytest.mark.parametrize('status,untouched', [('prepared', True), ('prepared', False), ('recovery-required', True), ('unknown', True)])
def test_legacy_journals_are_reconciled_only_when_no_publication_occurred(tmp_path, status, untouched):
    import hashlib
    lab = Lab(tmp_path)
    evidence = InterruptedTransaction(lab, status)
    evidence.pointer.unlink()
    before = (tmp_path / 'etc/named.conf').read_bytes()
    checksum = hashlib.sha256(before).hexdigest() if untouched else '0' * 64
    evidence.manifest.write_text(json.dumps({'schema': 1, 'status': status,
        'files': {'/etc/named.conf': {'before': checksum, 'after': 'f' * 64}}}))
    if status == 'prepared' and untouched:
        lab.manager().recovery_guard(writing=True)
        assert json.loads(evidence.manifest.read_text())['status'] == 'aborted-before-publication'
    else:
        with pytest.raises(M.ManagementError, match='Unresolved legacy'):
            lab.manager().recovery_guard(writing=True)
    assert (tmp_path / 'etc/named.conf').read_bytes() == before


@pytest.mark.parametrize('raw', [b'null', b'[]', b'"string"', b'\xff', b'{"schema":1,"schema":2}'])
def test_private_json_state_must_be_complete_utf8_mapping(tmp_path, raw):
    path = tmp_path / 'state.json'
    path.write_bytes(raw)
    path.chmod(0o600)
    with pytest.raises(M.ManagementError, match='Invalid private'):
        M.VerifiedIO.read_json(path, 4096)


@pytest.mark.parametrize('change', ['short-read', 'metadata'])
def test_private_json_reader_rejects_partial_or_changed_bytes(tmp_path, monkeypatch, change):
    import contextlib
    path = tmp_path / 'state.json'
    path.write_bytes(b'{"schema":1}')
    path.chmod(0o600)
    original = M.os.fdopen
    class AlteredRead:
        """Inject one observation fault at the real descriptor boundary."""
        def __init__(self, stream):
            self.stream = stream
        def fileno(self):
            return self.stream.fileno()
        def read(self, limit):
            data = self.stream.read(limit)
            if change == 'short-read':
                return data[:-1]
            info = path.stat()
            os.utime(str(path), ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
            return data
    @contextlib.contextmanager
    def altered(descriptor, mode):
        with original(descriptor, mode) as stream:
            yield AlteredRead(stream)
    monkeypatch.setattr(M.os, 'fdopen', altered)
    with pytest.raises(M.ManagementError, match='Incomplete private|changed during observation'):
        M.VerifiedIO.read_json(path, 4096)


@pytest.mark.parametrize('entry', [42, {'before': 'f' * 64}])
def test_malformed_legacy_file_identity_never_authorizes_recovery(tmp_path, entry):
    lab = Lab(tmp_path)
    evidence = InterruptedTransaction(lab)
    evidence.pointer.unlink()
    evidence.manifest.write_text(json.dumps({'schema': 1, 'status': 'prepared',
                                            'files': {'/etc/named.conf': entry}}))
    with pytest.raises(M.ManagementError, match='Unresolved legacy'):
        lab.manager().recovery_guard(writing=True)


def test_route_helper_and_management_share_one_live_process_lock(tmp_path):
    from test_native_front_routes import NativeRouteLab
    folder = tmp_path / 'native'
    folder.mkdir(mode=0o700)
    route = NativeRouteLab(folder)
    manager = M.Manager({'resource': 'option', 'action': 'read', 'name': 'minimal-responses'}, {}, root=tmp_path)
    state = manager.private_directory('/var/lib/Bind9-Ansible')
    route.script.write_text(route.script.read_text().replace('STATE=' + str(route.state), 'STATE=' + str(state)))
    with manager.operation():
        result = route.run('plan')
        assert result.returncode == 1 and 'Another route or management operation' in result.stdout
        assert not route.data['operations']
    result = route.run('plan')
    assert result.returncode == 2, result.stdout


def test_legacy_unsafe_target_path_cannot_authorize_a_new_write(tmp_path):
    lab = Lab(tmp_path)
    evidence = InterruptedTransaction(lab)
    evidence.pointer.unlink()
    evidence.manifest.write_text(json.dumps({'schema': 1, 'status': 'prepared',
        'files': {'/outside/unsafe': {'before': None, 'after': 'f' * 64}}}))
    with pytest.raises(M.ManagementError, match='Unresolved legacy'):
        lab.manager().recovery_guard(writing=True)

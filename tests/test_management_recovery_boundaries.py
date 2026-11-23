"""Refuse damaged recovery evidence without erasing files or operator work."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from test_management import CONF, Lab, M, pytestmark as base_pytestmark
from test_operation_resilience import InterruptedTransaction

pytestmark = base_pytestmark


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


@pytest.mark.parametrize('kind', ['fifo', 'owner', 'hardlink'])
def test_private_json_state_refuses_foreign_or_shared_objects(lab, kind):
    path = lab.root / 'evidence.json'
    if kind == 'fifo':
        os.mkfifo(path, 0o600)
    else:
        path.write_text('{}', encoding='utf-8')
        path.chmod(0o600)
        if kind == 'owner':
            os.chown(path, 1, -1)
        else:
            os.link(path, lab.root / 'alias.json')
    with pytest.raises(M.ManagementError, match='Unsafe private'):
        M.VerifiedIO.read_json(path, 4096)
    assert path.exists()


@pytest.mark.parametrize('kind', ['schema', 'transaction-type', 'ancestor-owner', 'ancestor-link'])
def test_pending_guard_preserves_malformed_identity_and_ancestors(lab, kind):
    evidence = InterruptedTransaction(lab)
    if kind == 'schema':
        value = json.loads(evidence.pointer.read_text())
        value['schema'] = 2
        evidence.pointer.write_text(json.dumps(value), encoding='utf-8')
    elif kind == 'transaction-type':
        evidence.pointer.write_text('{"schema": 1, "transaction": 42}', encoding='utf-8')
    elif kind == 'ancestor-owner':
        os.chown(evidence.folder, 1, -1)
    else:
        target = evidence.folder.with_name('original-folder')
        evidence.folder.rename(target)
        evidence.folder.symlink_to(target, target_is_directory=True)
    with pytest.raises(M.ManagementError):
        lab.manager().recovery_guard(writing=True)
    assert evidence.pointer.exists()
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert not lab.calls


@pytest.mark.parametrize('kind', ['owner', 'link'])
def test_private_directory_rejects_nonroot_ownership_and_non_directory(lab, kind):
    path = lab.root / 'var/lib'
    path.mkdir(mode=0o700)
    if kind == 'owner':
        os.chown(path, 1, -1)
    else:
        path.rename(path.with_name('original-lib'))
        path.symlink_to(path.with_name('original-lib'), target_is_directory=True)
    with pytest.raises(M.ManagementError, match='Unsafe management state directory'):
        lab.manager().private_directory('/var/lib/Bind9-Ansible')


@pytest.mark.parametrize('kind', ['owner', 'fifo'])
def test_transient_lock_metadata_is_checked_before_flock(lab, kind):
    manager = lab.manager()
    path = manager.private_directory('/var/lib/Bind9-Ansible') / 'operations.flock'
    if kind == 'fifo':
        os.mkfifo(path, 0o600)
    else:
        path.touch(mode=0o600)
        os.chown(path, 1, -1)
    with pytest.raises(M.ManagementError, match='Unsafe transient'):
        with manager.operation():
            pytest.fail('An unsafe lock was used')


@pytest.mark.parametrize('kind', ['file', 'owner', 'mode', 'schema'])
def test_legacy_guard_preserves_unsafe_transaction_evidence(lab, kind):
    evidence = InterruptedTransaction(lab)
    evidence.pointer.unlink()
    if kind == 'file':
        evidence.manifest.unlink()
        evidence.folder.rmdir()
        evidence.folder.write_bytes(b'operator evidence')
    elif kind == 'owner':
        os.chown(evidence.folder, 1, -1)
    elif kind == 'mode':
        evidence.folder.chmod(0o755)
    else:
        evidence.manifest.write_text('{"schema": 2, "status": "prepared", "files": {}}', encoding='utf-8')
    with pytest.raises(M.ManagementError):
        lab.manager().legacy_recovery_guard()
    assert evidence.folder.exists()
    assert (lab.root / 'etc/named.conf').read_text() == CONF


def test_legacy_catalog_bound_counts_unrelated_entries_too(lab):
    state = lab.manager().private_directory('/var/backups/Bind9-Ansible/management')
    for index in range(4097):
        (state / ('operator-note-' + str(index))).touch()
    with pytest.raises(M.ManagementError, match='4096 entries'):
        lab.manager().legacy_recovery_guard()
    assert len(list(state.iterdir())) == 4097


@pytest.mark.parametrize('entries', [None, [], {'/etc/unpublished.conf': {'before': None}}])
def test_legacy_prepared_state_requires_an_exact_mapping_of_observed_originals(lab, entries):
    evidence = InterruptedTransaction(lab)
    evidence.pointer.unlink()
    evidence.manifest.write_text(json.dumps({'schema': 1, 'status': 'prepared', 'files': entries}), encoding='utf-8')
    if isinstance(entries, dict):
        lab.manager().legacy_recovery_guard()
        assert json.loads(evidence.manifest.read_text())['status'] == 'aborted-before-publication'
    else:
        with pytest.raises(M.ManagementError, match='Unresolved legacy'):
            lab.manager().legacy_recovery_guard()
        assert json.loads(evidence.manifest.read_text())['status'] == 'prepared'


@pytest.mark.parametrize('kind', ['fifo', 'owner', 'hardlink', 'mode'])
def test_cleanup_never_removes_an_unsafe_pending_marker(lab, kind):
    evidence = InterruptedTransaction(lab, 'committed')
    if kind == 'fifo':
        evidence.pointer.unlink()
        os.mkfifo(evidence.pointer, 0o600)
    elif kind == 'owner':
        os.chown(evidence.pointer, 1, -1)
    elif kind == 'hardlink':
        os.link(evidence.pointer, evidence.pointer.with_name('operator-copy'))
    else:
        evidence.pointer.chmod(0o644)
    with pytest.raises(M.ManagementError, match='ownership changed'):
        lab.manager().clear_pending(evidence.folder)
    assert evidence.pointer.exists()


@pytest.mark.parametrize('kind', ['file', 'owner', 'hardlink', 'mode'])
@pytest.mark.parametrize('helper', ['backup', 'dnssec-validation', 'dnssec-refresh'])
def test_native_helpers_must_have_safe_identity_before_execution(lab, kind, helper):
    if helper != 'backup':
        text = CONF.replace('dnssec-validation no', 'dnssec-validation auto')
        text += '// Bind9-Ansible DNSSEC-PRIVATE corp.test\nzone "corp.test" { type forward; forwarders { 198.51.100.53; }; };\n'
        lab.put('/etc/named.conf', text)
        path = lab.put('/usr/local/sbin/bind-private-dnssec', '# native helper\n', 0o700)
    else:
        path = lab.root / 'usr/local/sbin/bind-configtool'
    if kind == 'file':
        path.unlink()
        path.mkdir()
    elif kind == 'owner':
        os.chown(path, 1, -1)
    elif kind == 'hardlink':
        os.link(path, path.with_name('operator-copy'))
    else:
        path.chmod(0o777)
    with pytest.raises(M.ManagementError, match='(helper|backup tool)'):
        if helper == 'dnssec-refresh':
            manager = lab.manager()
            manager.config = M.Configuration(manager.files)
            manager.nta()
        else:
            lab.write(value='no')
    assert not any(Path(call[0]).name in ('bind-configtool', 'bind-private-dnssec') for call in lab.calls)


def test_absent_target_guard_detects_an_external_creator(lab):
    name = '/etc/operator.conf'
    path = lab.put(name, '// operator work\n')
    with pytest.raises(M.ManagementError, match='absent target'):
        lab.manager().check_target(name, None, None)
    assert path.read_text() == '// operator work\n'


def test_existing_target_can_be_checked_without_a_previous_stamp(lab):
    lab.manager().check_target('/etc/named.conf', CONF.encode(), None)
    assert not lab.calls


def test_removed_snapshot_asset_is_never_reread_from_live_files(lab):
    snapshot = M.SnapshotFiles(M.SafeFiles(lab.root), {'/etc/named.conf': None})
    with pytest.raises(M.ManagementError, match='removed static asset'):
        snapshot.read('/etc/named.conf')
    assert (lab.root / 'etc/named.conf').read_text() == CONF


@pytest.mark.parametrize('location', ['pointer', 'manifest', 'legacy'])
@pytest.mark.parametrize('version', [True, 1.0])
def test_schema_identity_requires_an_integer_version(lab, location, version):
    evidence = InterruptedTransaction(lab)
    if location == 'pointer':
        path = evidence.pointer
    else:
        path = evidence.manifest
    value = json.loads(path.read_text())
    value['schema'] = version
    if location == 'legacy':
        evidence.pointer.unlink()
        value['files'] = {'/etc/named.conf': {'before': hashlib.sha256(CONF.encode()).hexdigest()}}
    path.write_text(json.dumps(value), encoding='utf-8')
    before = path.read_bytes()
    with pytest.raises(M.ManagementError, match='(Malformed|Unsupported)'):
        if location == 'legacy':
            lab.manager().legacy_recovery_guard()
        else:
            lab.manager().recovery_guard(writing=False)
    assert path.read_bytes() == before

"""Batch contracts: complete planning, guarded publication and joint recovery."""
import json
import os
from pathlib import Path

import pytest

from test_management import CONF, CONTEXT, Lab, M, ZONE

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.geteuid() != 0,
                                reason='Requires isolated Linux root')


class BatchLab(Lab):
    """Exercise the public engine with the existing isolated command boundary."""

    def batch(self, operations, action='read', preview=None, check=False, **fields):
        request = dict(resource='batch', action=action, operations=operations)
        if preview:
            request.update(expected_checksum=preview['checksum'], expected_plan_checksum=preview['plan_checksum'])
        request.update(fields)
        return M.BatchManager(request, dict(CONTEXT), root=self.root, runner=self.runner, check=check)

    def execute(self, operations, action='read', preview=None, check=False, **fields):
        manager = self.batch(operations, action, preview, check, **fields)
        with manager.lease():
            return manager.run()


@pytest.fixture
def lab(tmp_path):
    return BatchLab(tmp_path)


def mixed():
    return [dict(resource='option', action='update', name='minimal-responses', value='no'),
        dict(resource='zone', action='create', name='new.test', kind='authoritative', serial=7),
        dict(resource='record', action='create', zone='new.test', name='app', type='A', values=['192.0.2.90']),
        dict(resource='record', action='create', zone='new.test', name='v6', type='AAAA', values=['2001:db8::90']),
        dict(resource='record', action='update', zone='example.test', name='www', type='A', values=['192.0.2.99']),
        dict(resource='zone', action='create', name='forward.test', kind='forward', forwarders=[{'address': '10.0.0.53'}])]


def test_mixed_batch_uses_one_backup_reload_and_final_candidate(lab):
    operations = mixed()
    preview = lab.execute(operations)
    assert not preview['changed'] and preview['would_change']
    assert preview['operations'][2]['before'] == {'found': False, 'zone_found': False}
    assert preview['operations'][2]['after']['values'] == ['192.0.2.90']
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert not (lab.root / 'var/backups').exists()
    lab.calls.clear()
    result = lab.execute(operations, 'apply', preview)
    assert result['changed'] and result['status'] == 'committed'
    assert len([c for c in lab.calls if Path(c[0]).name == 'bind-configtool']) == 1
    assert len([c for c in lab.calls if Path(c[0]).name == 'named-checkconf']) == 1
    assert len([c for c in lab.calls if c[-1] == 'reload']) == 1
    assert result['serials']['new.test.'] == '7'
    assert result['serials']['example.test.'] == '42'
    assert result['serials']['.'] == '8'
    journal = json.loads(Path(result['backup'], 'manifest.json').read_text())
    assert journal['operations_count'] == 6 and journal['plan_checksum'] == preview['plan_checksum']
    assert journal['batch_digest'] == result['batch_digest']
    assert 'operations' not in journal  # Sensitive values remain in protected file backups only.
    assert '// operator comment' in (lab.root / 'etc/named.conf').read_text()
    assert M.Configuration(M.SafeFiles(lab.root)).revision() == result['checksum']


def test_many_edits_advance_an_existing_soa_only_once(lab):
    operations = [dict(resource='record', action='update', name='www', zone='example.test', type='A', values=['192.0.2.99']),
        dict(resource='record', action='create', name='other', zone='example.test', type='A', values=['192.0.2.98']),
        dict(resource='zone', action='update', name='example.test', settings={'max-zone-ttl': '3600'})]
    result = lab.execute(operations, 'apply', lab.execute(operations))
    assert result['serials'] == {'example.test.': '42'}


def test_check_mode_does_not_publish_or_backup(lab):
    operations = mixed()
    preview = lab.execute(operations)
    result = lab.execute(operations, 'apply', preview, check=True)
    assert result['check_mode'] and result['changed']
    assert result['plan_checksum'] == preview['plan_checksum']
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert not (lab.root / 'var/named/zones/new.test.zone').exists()
    assert not (lab.root / 'var/backups').exists()


@pytest.mark.parametrize('mode', ['missing-revision', 'stale-revision', 'missing-plan', 'different-plan', 'changed-intent', 'changed-context'])
def test_revision_and_plan_approval_are_both_required(lab, mode):
    operations = mixed()
    preview = lab.execute(operations)
    manager = lab.batch(operations, 'apply', preview)
    if mode == 'missing-revision':
        manager.request.pop('expected_checksum')
    elif mode == 'stale-revision':
        lab.put('/etc/named.conf', CONF + '// manual edit\n')
    elif mode == 'missing-plan':
        manager.request.pop('expected_plan_checksum')
    elif mode == 'different-plan':
        manager.request['expected_plan_checksum'] = '0' * 64
    elif mode == 'changed-intent':
        operations[0]['value'] = 'yes'
        manager = lab.batch(operations, 'apply', preview)
    else:
        manager.context['secondary_back'] = '10.0.0.13'
    lab.calls.clear()
    with pytest.raises(M.ManagementError, match='[Cc]hecksum'):
        with manager.lease():
            manager.run()
    assert not lab.calls
    assert not (lab.root / 'var/backups').exists()


@pytest.mark.parametrize('tail', [
    dict(resource='record', action='update', name='www', zone='example.test', type='A', values=[]),
    dict(resource='record', action='create', name='app', zone='absent.test', type='A', values=['192.0.2.90']),
    dict(resource='option', action='update', name='absent-option', value='yes'),
])
def test_late_invalid_operation_leaves_the_entire_live_graph_untouched(lab, tail):
    with pytest.raises(M.ManagementError):
        lab.execute([mixed()[0], tail])
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert (lab.root / 'var/named/zones/example.test.zone').read_text() == ZONE
    assert not lab.calls


def test_native_candidate_rejection_never_publishes_the_prefix(lab):
    lab.fail = lambda argv: (_ for _ in ()).throw(M.ManagementError('candidate rejected')) if Path(argv[0]).name == 'named-checkconf' else None
    with pytest.raises(M.ManagementError, match='candidate'):
        lab.execute(mixed())
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert not (lab.root / 'var/backups').exists()


@pytest.mark.parametrize('failure', ['publication', 'reload', 'health'])
def test_failure_restores_all_batch_files_and_removes_new_files(lab, monkeypatch, failure):
    operations = mixed()
    preview = lab.execute(operations)
    failed = []
    publish = M.Manager.publish
    monkeypatch.setattr(M.time, 'sleep', lambda seconds: None)
    def publishing(manager, *args, **kwargs):
        publish(manager, *args, **kwargs)
        if failure == 'publication' and not failed:
            failed.append(True)
            raise OSError('simulated fsync error after first publication')
    monkeypatch.setattr(M.Manager, 'publish', publishing)
    def boundary(argv):
        if failure == 'reload' and argv[-1] == 'reload' and not failed:
            failed.append(True)
            raise M.ManagementError('simulated reload failure')
        if failure == 'health' and Path(argv[0]).name == 'dig':
            raise M.ManagementError('simulated DNS failure')
    lab.fail = boundary
    with pytest.raises(M.ManagementError, match='rolled-back'):
        lab.execute(operations, 'apply', preview)
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert (lab.root / 'var/named/zones/example.test.zone').read_text() == ZONE
    assert not (lab.root / 'var/named/zones/new.test.zone').exists()
    assert not (lab.root / 'var/lib/Bind9-Ansible/.operation.lock').exists()


def test_rollback_conflict_preserves_external_data_and_recovery_lock(lab):
    operations = mixed()
    preview = lab.execute(operations)
    def conflict(argv):
        if argv[-1] == 'reload':
            lab.put('/etc/named.conf', CONF + '// external writer during commit\n')
            raise M.ManagementError('reload failed')
    lab.fail = conflict
    with pytest.raises(M.ManagementError, match='recovery-required'):
        lab.execute(operations, 'apply', preview)
    assert 'external writer' in (lab.root / 'etc/named.conf').read_text()
    assert (lab.root / 'var/lib/Bind9-Ansible/.operation.lock').is_dir()
    assert not (lab.root / 'var/named/zones/new.test.zone').exists()


def test_post_reload_unrelated_drift_cannot_be_reported_as_approved_success(lab):
    lab.put('/etc/shared.conf', '// initial manual include\n')
    lab.put('/etc/named.conf', CONF + 'include "/etc/shared.conf";\n')
    original = (lab.root / 'etc/named.conf').read_bytes()
    preview = lab.execute([mixed()[0]])
    def intervene(argv):
        if Path(argv[0]).name == 'dig':
            lab.put('/etc/shared.conf', '// manual change during health\n')
    lab.fail = intervene
    with pytest.raises(M.ManagementError, match='rolled-back'):
        lab.execute([mixed()[0]], 'apply', preview)
    assert (lab.root / 'etc/named.conf').read_bytes() == original
    assert 'manual change' in (lab.root / 'etc/shared.conf').read_text()


def test_noop_still_requires_approval_but_creates_no_backup(lab):
    operations = [dict(resource='option', action='update', name='minimal-responses', value='yes')]
    preview = lab.execute(operations)
    assert not preview['would_change']
    result = lab.execute(operations, 'apply', preview)
    assert not result['changed'] and result['paths'] == []
    assert not lab.calls


def test_record_before_its_zone_is_a_safe_ordering_error(lab):
    operations = mixed()[1:3][::-1]
    with pytest.raises(M.ManagementError, match='not declared'):
        lab.execute(operations)
    assert (lab.root / 'etc/named.conf').read_text() == CONF


def test_secondary_skips_records_explicitly_but_plans_other_resource_types(lab):
    operations = mixed()[:4]
    manager = lab.batch(operations)
    manager.context['primary'] = False
    with manager.lease():
        result = manager.run()
    assert [op['index'] for op in result['operations'] if op['skipped']] == [3, 4]
    assert not (lab.root / 'var/named/slaves/new.test.zone').exists()
    assert 'type slave;' in manager.changes['/etc/named.conf'].decode()


@pytest.mark.parametrize('operations', [[], [mixed()[0]] * 65, [None],
    [dict(resource='option', action='read', name='minimal-responses')],
    [dict(mixed()[0], expected_checksum='0' * 64)],
    [mixed()[0], mixed()[0]],
    [dict(resource='zone', action='delete', name='example.test'), mixed()[4]],
    [dict(resource='record', action='update', name='www', zone='example.test', type='A', values=['192.0.2.90']),
     dict(resource='record', action='delete', name='www.example.test.', zone='EXAMPLE.TEST.', type='a')],
    [dict(resource='zone', action='create', name='new.test', kind='authoritative'), dict(resource='zone', action='delete', name='NEW.TEST.')],
    [dict(mixed()[0], value='x' * (256 * 1024))],
    [dict(mixed()[0], value=float('nan'))],
])
def test_ambiguous_oversized_or_unsupported_batches_are_refused(lab, operations):
    with pytest.raises(M.ManagementError):
        lab.execute(operations)
    assert not lab.calls


@pytest.mark.parametrize('envelope', [None, [], {}, dict(resource='batch', action='delete', operations=[mixed()[0]]),
    dict(resource='batch', action='read', operations=[mixed()[0]], extra=True)])
def test_invalid_envelope_has_a_controlled_refusal(lab, envelope):
    with pytest.raises(M.ManagementError):
        M.BatchManager(envelope, CONTEXT, root=lab.root)


def test_delete_with_purge_and_an_option_update_share_the_same_transaction(lab):
    operations = [mixed()[0], dict(resource='zone', action='delete', name='example.test', purge_data=True)]
    result = lab.execute(operations, 'apply', lab.execute(operations))
    assert result['changed'] and not (lab.root / 'var/named/zones/example.test.zone').exists()


def test_snapshot_never_reads_a_new_unapproved_asset(lab):
    snapshot = M.SnapshotFiles(M.SafeFiles(lab.root), {})
    with pytest.raises(M.ManagementError, match='unobserved'):
        snapshot.read('/etc/named.conf')


def test_batch_requires_root_even_for_preview(lab, monkeypatch):
    manager = lab.batch([mixed()[0]])
    monkeypatch.setattr(M.os, 'geteuid', lambda: 1000)
    with pytest.raises(M.ManagementError, match='root'):
        manager.run()


@pytest.mark.parametrize('bound', ['MAX_BYTES', 'MAX_FILES'])
def test_batch_discovery_and_snapshot_are_bounded(lab, monkeypatch, bound):
    monkeypatch.setattr(M.BoundedFiles, bound, 0)
    with pytest.raises(M.ManagementError, match='bound'):
        lab.execute([mixed()[0]])
    with pytest.raises(M.ManagementError, match='bound'):
        M.SnapshotFiles(M.SafeFiles(lab.root), {'/etc/named.conf': b'content'})


@pytest.mark.parametrize('mode', ['single', 'batch', 'purge'])
@pytest.mark.parametrize('filename', ['zones/example.test.zone', 'zones/./example.test.zone', '/var/named/zones//example.test.zone'])
def test_shared_primary_data_cannot_change_another_authority(lab, mode, filename):
    source = CONF + 'zone "alias.test" { type master; file "' + filename + '"; };\n'
    lab.put('/etc/named.conf', source)
    with pytest.raises(M.ManagementError, match='Shared primary data'):
        if mode == 'single':
            lab.write('record', 'update', 'www', zone='example.test', type='A', values=['192.0.2.99'])
        elif mode == 'batch':
            lab.execute([mixed()[4]])
        else:
            lab.execute([dict(resource='zone', action='delete', name='example.test', purge_data=True)])
    assert (lab.root / 'etc/named.conf').read_text() == source
    assert (lab.root / 'var/named/zones/example.test.zone').read_text() == ZONE
    assert not lab.calls


def test_unchanged_shared_native_style_data_remains_readable(lab):
    lab.put('/etc/named.conf', CONF + 'zone "alias.test" { type master; file "zones/example.test.zone"; };\n')
    preview = lab.execute([mixed()[0]])
    assert preview['would_change']

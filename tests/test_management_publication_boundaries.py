"""Observe real late operator edits across planning, commit and rollback."""
from pathlib import Path

import pytest

from test_management import CONF, ZONE, Lab, M, pytestmark as base_pytestmark

pytestmark = base_pytestmark


@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


def test_edit_during_the_full_backup_invalidates_the_candidate(lab):
    manual = CONF + '// operator edit during backup\n'
    def intervene(argv):
        if Path(argv[0]).name == 'bind-configtool':
            lab.put('/etc/named.conf', manual)
    lab.fail = intervene
    with pytest.raises(M.ManagementError, match='changed while the candidate'):
        lab.write(value='no')
    assert (lab.root / 'etc/named.conf').read_text() == manual
    assert not any(call[-1] == 'reload' for call in lab.calls)


def test_new_target_created_after_precommit_observation_is_preserved(lab, monkeypatch):
    native = M.Manager.private_directory
    path = lab.root / 'var/named/zones/new.test.zone'
    def private(manager, virtual):
        result = native(manager, virtual)
        if virtual == '/var/backups/Bind9-Ansible/management':
            path.write_text('; operator file\n', encoding='utf-8')
        return result
    monkeypatch.setattr(M.Manager, 'private_directory', private)
    with pytest.raises(M.ManagementError, match='new target appeared'):
        lab.write('zone', 'create', 'new.test', kind='authoritative')
    assert path.read_text() == '; operator file\n'
    assert (lab.root / 'etc/named.conf').read_text() == CONF


@pytest.mark.parametrize('phase', ['fresh-graph', 'final-read'])
def test_operator_edit_after_health_is_preserved_and_requires_recovery(lab, monkeypatch, phase):
    armed, modified = [], []
    manual = CONF.replace('minimal-responses yes;', 'minimal-responses no;') + '// late operator edit\n'
    def ready(argv):
        if Path(argv[0]).name == 'dig':
            armed.append(True)
    lab.fail = ready
    if phase == 'fresh-graph':
        native = M.Configuration.__init__
        def construct(config, files):
            if armed and not modified:
                lab.put('/etc/named.conf', manual)
                modified.append(True)
            native(config, files)
        monkeypatch.setattr(M.Configuration, '__init__', construct)
    else:
        native = M.Configuration.revision
        def revision(config):
            result = native(config)
            if armed and not modified:
                lab.put('/etc/named.conf', manual)
                modified.append(True)
            return result
        monkeypatch.setattr(M.Configuration, 'revision', revision)
    with pytest.raises(M.ManagementError, match='recovery-required'):
        lab.write(value='no')
    assert modified
    assert (lab.root / 'etc/named.conf').read_text() == manual
    assert (lab.root / 'var/lib/Bind9-Ansible/management.pending.json').exists()


@pytest.mark.parametrize('operation', ['operator-restored', 'purge'])
def test_failed_reload_recovers_only_owned_changes_and_restores_purged_data(lab, operation):
    attempts = []
    def fail_once(argv):
        if argv[-1] == 'reload' and not attempts:
            attempts.append(True)
            if operation == 'operator-restored':
                lab.put('/etc/named.conf', CONF)
            raise M.ManagementError('Controlled reload failure')
    lab.fail = fail_once
    with pytest.raises(M.ManagementError, match='rolled-back'):
        if operation == 'purge':
            lab.write('zone', 'delete', 'example.test', purge_data=True)
        else:
            lab.write(value='no')
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert (lab.root / 'var/named/zones/example.test.zone').read_text() == ZONE
    assert not (lab.root / 'var/lib/Bind9-Ansible/management.pending.json').exists()


def test_an_identical_foreign_inode_cannot_be_claimed_as_our_publication(lab, monkeypatch):
    native = M.os.replace
    intervened = []
    def replace(source, target, **kwargs):
        result = native(source, target, **kwargs)
        if Path(target) == lab.root / 'etc/named.conf' and Path(source).name.startswith('.bind9-'):
            other = Path(target).with_name('operator-copy')
            other.write_bytes(Path(target).read_bytes())
            other.chmod(0o640)
            native(other, target)
            intervened.append(Path(target).stat().st_ino)
        return result
    monkeypatch.setattr(M.os, 'replace', replace)
    with pytest.raises(M.ManagementError, match='recovery-required'):
        lab.write(value='no')
    path = lab.root / 'etc/named.conf'
    assert intervened and path.stat().st_ino == intervened[-1]
    assert 'minimal-responses no;' in path.read_text()
    assert not any(call[-1] == 'reload' for call in lab.calls)

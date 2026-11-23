"""Preserve live operator work at observation, publication and rollback boundaries."""
import multiprocessing
import os
from pathlib import Path

import pytest

from test_management import CONF, CONTEXT, Lab, M, ZONE, pytestmark as base_pytestmark
from test_management_batch import BatchLab

pytestmark = base_pytestmark

@pytest.fixture
def lab(tmp_path):
    return Lab(tmp_path)


def included_option(lab):
    """Move an ordinary option into an operator-owned nested options include."""
    lab.put('/etc/manual-options.conf', 'minimal-responses no; // operator value\n')
    lab.put('/etc/named.conf', CONF.replace('minimal-responses yes;',
                                           'include "/etc/manual-options.conf";'))


def test_read_option_resolves_its_live_include(lab):
    included_option(lab)
    assert lab.manager().run()['value'] == 'no'


@pytest.mark.parametrize('action', ['create', 'update', 'delete'])
def test_include_option_cannot_be_duplicated_or_replaced(lab, action):
    included_option(lab)
    before = M.Configuration(M.SafeFiles(lab.root)).contents
    fields = {} if action == 'delete' else {'value': 'yes'}
    with pytest.raises(M.ManagementError):
        lab.write(action=action, **fields)
    assert M.Configuration(M.SafeFiles(lab.root)).contents == before
    assert not lab.calls


@pytest.mark.parametrize('kind', ['A', 'AAAA', 'TXT'])
def test_create_refuses_an_existing_owner_regardless_of_record_type(lab, kind):
    with pytest.raises(M.ManagementError, match='exists'):
        lab.write('record', 'create', 'WWW.EXAMPLE.TEST.', zone='example.test',
                  type=kind, values=['192.0.2.91'])
    assert (lab.root / 'var/named/zones/example.test.zone').read_text() == ZONE
    assert not lab.calls


def test_mixed_batch_refuses_two_creates_with_the_same_owner(tmp_path):
    lab = BatchLab(tmp_path)
    ops = [dict(resource='record', action='create', zone='example.test',
                name='new', type=kind, values=[value])
           for kind, value in [('A', '192.0.2.91'), ('AAAA', '2001:db8::91')]]
    with pytest.raises(M.ManagementError, match='exists'):
        lab.execute(ops)
    assert not lab.calls


def test_single_write_detects_manual_edit_of_an_unchanged_include(lab):
    lab.put('/etc/manual.conf', '// initial\n')
    lab.put('/etc/named.conf', CONF + 'include "/etc/manual.conf";\n')
    def intervene(argv):
        if Path(argv[0]).name == 'dig':
            lab.put('/etc/manual.conf', '// operator edit during health\n')
    lab.fail = intervene
    with pytest.raises(M.ManagementError):
        lab.write(value='no')
    assert (lab.root / 'etc/manual.conf').read_text() == '// operator edit during health\n'
    assert 'minimal-responses yes;' in (lab.root / 'etc/named.conf').read_text()


def test_manual_chmod_during_validation_is_preserved(lab):
    def intervene(argv):
        if Path(argv[0]).name == 'named-checkconf':
            (lab.root / 'etc/named.conf').chmod(0o600)
    lab.fail = intervene
    with pytest.raises(M.ManagementError):
        lab.write(value='no')
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert (lab.root / 'etc/named.conf').stat().st_mode & 0o777 == 0o600


def test_manual_write_during_temporary_file_fsync_is_not_overwritten(lab, monkeypatch):
    fsync = M.os.fsync
    manual = CONF + '// operator edit at publication boundary\n'
    def intervene(fd):
        if '/etc/.bind9-' in os.readlink('/proc/self/fd/' + str(fd)):
            lab.put('/etc/named.conf', manual)
        return fsync(fd)
    monkeypatch.setattr(M.os, 'fsync', intervene)
    with pytest.raises(M.ManagementError):
        lab.write(value='no')
    assert (lab.root / 'etc/named.conf').read_text() == manual


def test_failed_create_never_rolls_back_an_identical_foreign_file(lab, monkeypatch):
    link = M.os.link
    def intervene(source, target, **kwargs):
        if str(target).endswith('/zones/new.test.zone'):
            Path(target).write_bytes(Path(source).read_bytes())
            Path(target).chmod(0o640)
        return link(source, target, **kwargs)
    monkeypatch.setattr(M.os, 'link', intervene)
    with pytest.raises(M.ManagementError):
        lab.write('zone', 'create', 'new.test', kind='authoritative')
    assert (lab.root / 'var/named/zones/new.test.zone').exists()
    assert (lab.root / 'etc/named.conf').read_text() == CONF


def test_replaced_identical_new_file_is_preserved_during_rollback(lab):
    path = lab.root / 'var/named/zones/new.test.zone'
    def intervene(argv):
        if argv[-1] == 'reload':
            replacement = path.with_suffix('.operator')
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(0o640)
            replacement.replace(path)
            raise M.ManagementError('reload failed after operator save')
    lab.fail = intervene
    with pytest.raises(M.ManagementError, match='recovery-required'):
        lab.write('zone', 'create', 'new.test', kind='authoritative')
    assert path.exists()
    assert (lab.root / 'var/lib/Bind9-Ansible/.operation.lock').is_dir()


def test_lease_replacement_before_publication_refuses_all_writes(lab):
    lease = lab.root / 'var/lib/Bind9-Ansible/.operation.lock'
    def intervene(argv):
        if Path(argv[0]).name == 'named-checkconf':
            lease.rename(lease.with_name('operator-lease'))
            lease.mkdir(mode=0o700)
    lab.fail = intervene
    with pytest.raises(M.ManagementError, match='lease'):
        lab.write(value='no')
    assert (lab.root / 'etc/named.conf').read_text() == CONF
    assert lease.is_dir()


def test_dangling_zone_journal_blocks_static_maintenance(lab):
    path = lab.root / 'var/named/zones/example.test.zone.jnl'
    path.symlink_to(path.with_name('unavailable-journal'))
    with pytest.raises(M.ManagementError, match='journal'):
        lab.write(value='no')
    assert not lab.calls


def lease_contender(root, queue):
    """A real second process must never enter while the first owns the lease."""
    manager = M.Manager(dict(resource='option', action='read', name='minimal-responses'),
                        CONTEXT, root=root)
    try:
        with manager.lease():
            queue.put('entered')
    except M.ManagementError:
        queue.put('refused')


def test_two_processes_cannot_own_the_same_host_lease(lab):
    context = multiprocessing.get_context('fork')
    queue = context.Queue()
    with lab.manager().lease():
        child = context.Process(target=lease_contender, args=(lab.root, queue))
        child.start()
        try:
            assert queue.get(timeout=10) == 'refused'
            child.join(timeout=10)
            assert child.exitcode == 0
        finally:
            if child.is_alive():
                child.terminate()
                child.join(timeout=10)
    queue.close()


@pytest.mark.parametrize('mutation', ['replace', 'chmod', 'write'])
def test_observation_refuses_an_asset_changed_while_reading_other_assets(lab, monkeypatch, mutation):
    files = M.SafeFiles(lab.root)
    read = files.read
    changed = []
    path = lab.root / 'etc/named.conf'
    def intervene(name):
        data = read(name)
        if name.endswith('example.test.zone') and not changed:
            changed.append(True)
            if mutation == 'replace':
                alternative = path.with_suffix('.operator')
                alternative.write_bytes(path.read_bytes())
                alternative.chmod(0o640)
                alternative.replace(path)
            elif mutation == 'chmod':
                path.chmod(0o600)
            else:
                path.write_text(CONF + '// observed operator addition\n')
        return data
    monkeypatch.setattr(files, 'read', intervene)
    with pytest.raises(M.ManagementError, match='observing'):
        M.Configuration(files)


def test_journal_appearing_during_validation_prevents_publication(lab):
    def intervene(argv):
        if Path(argv[0]).name == 'named-checkconf':
            lab.put('/var/named/zones/example.test.zone.jnl', 'operator dynamic update journal')
    lab.fail = intervene
    with pytest.raises(M.ManagementError):
        lab.write(value='no')
    assert (lab.root / 'etc/named.conf').read_text() == CONF


def write_contender(lab, checksum, start, queue):
    """Competing writers share one approval; at most one may commit it."""
    manager = lab.manager(action='update', value='no', expected_checksum=checksum)
    start.wait(timeout=10)
    try:
        with manager.lease():
            queue.put(manager.run()['status'])
    except M.ManagementError:
        queue.put('refused')


def test_two_processes_with_the_same_approval_commit_only_once(lab):
    checksum = lab.manager().run()['checksum']
    context = multiprocessing.get_context('fork')
    start, queue = context.Event(), context.Queue()
    children = [context.Process(target=write_contender,
                args=(lab, checksum, start, queue)) for _ in range(2)]
    for child in children:
        child.start()
    try:
        start.set()
        assert sorted(queue.get(timeout=10) for _ in children) == ['committed', 'refused']
        for child in children:
            child.join(timeout=10)
            assert child.exitcode == 0
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(timeout=10)
        queue.close()
    assert (lab.root / 'etc/named.conf').read_text() == CONF.replace('minimal-responses yes;', 'minimal-responses no;')
    assert len(list((lab.root / 'var/backups').rglob('manifest.json'))) == 1


def test_file_read_refuses_an_in_place_change_before_its_final_fstat(lab, monkeypatch):
    fstat, calls = M.os.fstat, []
    def intervene(fd):
        if os.readlink('/proc/self/fd/' + str(fd)).endswith('/etc/named.conf'):
            calls.append(fd)
            if len(calls) == 2:
                lab.put('/etc/named.conf', CONF + '// operator write during read\n')
        return fstat(fd)
    monkeypatch.setattr(M.os, 'fstat', intervene)
    with pytest.raises(M.ManagementError, match='during a static file read'):
        lab.manager().run()
    assert 'operator write during read' in (lab.root / 'etc/named.conf').read_text()


def test_ambiguous_live_option_is_not_selected_arbitrarily(lab):
    lab.put('/etc/manual-options.conf', 'minimal-responses no;\n')
    lab.put('/etc/named.conf', CONF.replace('minimal-responses yes;',
        'minimal-responses yes; include "/etc/manual-options.conf";'))
    with pytest.raises(M.ManagementError, match='Ambiguous option'):
        lab.manager().run()
    assert not lab.calls


def test_missing_lease_before_publication_never_writes(lab):
    def intervene(argv):
        if Path(argv[0]).name == 'named-checkconf':
            (lab.root / 'var/lib/Bind9-Ansible/.operation.lock').rmdir()
    lab.fail = intervene
    with pytest.raises(M.ManagementError, match='lease disappeared'):
        lab.write(value='no')
    assert (lab.root / 'etc/named.conf').read_text() == CONF


def test_manual_edit_during_relabel_is_preserved(lab):
    manual = CONF + '// operator edit during relabel\n'
    def intervene(argv):
        if Path(argv[0]).name == 'restorecon':
            lab.put('/etc/named.conf', manual)
    lab.fail = intervene
    with pytest.raises(M.ManagementError, match='recovery-required'):
        lab.write(value='no')
    assert (lab.root / 'etc/named.conf').read_text() == manual
    assert (lab.root / 'var/lib/Bind9-Ansible/.operation.lock').exists()


def test_rollback_does_not_reload_an_invalid_manual_include(lab):
    lab.put('/etc/manual.conf', '// initial\n')
    lab.put('/etc/named.conf', CONF + 'include "/etc/manual.conf";\n')
    def intervene(argv):
        if argv[-1] == 'reload':
            (lab.root / 'etc/manual.conf').write_bytes(b'\xff operator bytes')
            raise M.ManagementError('reload failed')
    lab.fail = intervene
    with pytest.raises(M.ManagementError, match='recovery-required'):
        lab.write(value='no')
    assert (lab.root / 'etc/manual.conf').read_bytes() == b'\xff operator bytes'
    assert sum(call[-1] == 'reload' for call in lab.calls) == 1
    assert (lab.root / 'var/lib/Bind9-Ansible/.operation.lock').exists()


def test_native_el8_sticky_named_directory_does_not_require_chmod(lab):
    parent = lab.root / 'var/named'
    parent.chmod(0o1770)
    assert lab.manager().run()['found']
    assert lab.write(value='no')['changed']
    assert parent.stat().st_mode & 0o7777 == 0o1770


@pytest.mark.parametrize('relative,mode', [('var/named', 0o770), ('var/named', 0o1777),
                                        ('var/named/zones', 0o1770), ('etc', 0o1770)])
def test_native_directory_exception_does_not_allow_other_writable_ancestors(lab, relative, mode):
    (lab.root / relative).chmod(mode)
    with pytest.raises(M.ManagementError, match='ancestor'):
        lab.manager().run()


def sticky_contender(parent, queue):
    """Exercise the actual kernel sticky-bit protection after dropping root."""
    os.chdir(str(parent))
    os.setgroups([])
    os.setgid(parent.stat().st_gid)
    os.setuid(65534)
    Path('untrusted').write_text('must never replace root data')
    refused = 0
    for operation in (lambda: Path('protected').unlink(),
                      lambda: Path('untrusted').replace('protected')):
        try:
            operation()
        except PermissionError:
            refused += 1
    queue.put(refused)


def test_native_sticky_directory_protects_root_entries_from_group_writers(lab):
    parent = lab.root / 'var/named'
    parent.chmod(0o1770)
    protected = parent / 'protected'
    protected.write_text('operator root data')
    protected.chmod(0o640)
    context = multiprocessing.get_context('fork')
    queue = context.Queue()
    child = context.Process(target=sticky_contender, args=(parent, queue))
    child.start()
    try:
        assert queue.get(timeout=10) == 2
        child.join(timeout=10)
        assert child.exitcode == 0
    finally:
        if child.is_alive():
            child.terminate()
            child.join(timeout=10)
        queue.close()
    assert protected.read_text() == 'operator root data'

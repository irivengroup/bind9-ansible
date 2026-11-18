"""Native utility errors must preserve routes and retain unresolved evidence."""
import json
import copy
import shutil
import signal
import ipaddress
import os
from pathlib import Path

import pytest

from test_native_front_routes import BASE, NativeRouteLab, ROOT, pytestmark as native_marks

pytestmark = native_marks


class UtilityFaultLab:
    """Reuse the network lab and alter only the explicitly owned CLI boundary."""

    def __init__(self, folder, fault):
        self.lab = NativeRouteLab(folder)
        rule = fault if isinstance(fault, dict) else None
        if rule is not None:
            assert rule['command'] in ('stat', 'cat', 'mkdir', 'tr', 'wc', 'sort', 'sync')
            commands = (rule['command'],)
            fault = 'rule'
        else:
            commands = {'nonce': ('cat',), 'prepared': ('stat',), 'prepared-cleanup': ('stat', 'rm', 'mv'),
                'after-apply-cleanup': ('rm',), 'commit-journal-cleanup': ('rm', 'mv'),
                'retirement-after-success': ('rm', 'mv'),
                'retirement-foreign-transition': ('mv',),
                'retirement-empty-reservation-error': ('rmdir',),
                'retirement-aliased-identities': ('stat',),
                'retirement-back-rename-error': ('rm', 'mv'),
                'retirement-allocation-error': ('mktemp',),
                'retirement-compensation-sync-error': ('sync',),
                'retirement-unexpected-entry': ('stat',),
                'retirement-foreign-active': ('rm', 'mv'),
                'retirement-foreign-destination': ('mv',)}[fault]
        delegates = {name: shutil.which(name) for name in commands}
        if 'cat' in commands:
            directory = folder / 'utility-delegates'
            directory.mkdir()
            original_cat = directory / 'cat'
            shutil.copyfile(folder / 'bin/cat', original_cat)
            original_cat.chmod(0o755)
            delegates['cat'] = str(original_cat)
        assert all(delegates.values()), 'The selected native utility must exist in the isolated lab'
        self.spec = folder / 'utility-fault.json'
        self.spec.write_text(json.dumps(dict(fault=fault, triggered=False, cleanup_triggered=False,
            delegates=delegates, rule=rule, matches=0, armed=True, script=str(self.lab.script))))
        if fault == 'retirement-aliased-identities':
            state = json.loads(self.spec.read_text())
            state.update(mount_delegate=shutil.which('mount'), unmount_delegate=shutil.which('umount'))
            assert state['mount_delegate'] and state['unmount_delegate']
            self.spec.write_text(json.dumps(state))
        self.spec.chmod(0o600)
        self.lab.env.update(BIND_ROUTE_UTILITY_SPEC=str(self.spec),
            BIND_ROUTE_UTILITY_PENDING=str(self.lab.state / 'front-routes.pending'))
        # Leave unrelated native utilities untouched. Only the utility carrying
        # the selected error needs a facade and delegates unmatched arguments.
        for name in commands:
            command = folder / 'bin' / name
            shutil.copyfile(ROOT / 'tests/fixtures/native_route_utility_fault.py', command)
            command.chmod(0o755)

    def set_rule_armed(self, armed):
        """Use the exact delegate in healthy phases, and the facade only for a fault.

        A recovery validates many journal objects. Starting another Python
        process for every healthy stat can exhaust the unchanged action deadline
        under parallel coverage. Bypass only this inactive one-shot fixture;
        all production checks and the selected armed error remain unchanged.
        """
        state = json.loads(self.spec.read_text())
        assert state['fault'] == 'rule' and type(armed) is bool
        name = state['rule']['command']
        command = self.lab.folder / 'bin' / name
        delegate = Path(state['delegates'][name])
        facade = ROOT / 'tests/fixtures/native_route_utility_fault.py'
        assert delegate.is_absolute() and delegate.is_file() and delegate != command
        if command.is_symlink():
            assert command.resolve() == delegate.resolve()
        else:
            assert command.is_file() and command.read_bytes() == facade.read_bytes()
        # Unlink the owned fixture first: copyfile must never follow the
        # passthrough link and overwrite the real system delegate.
        command.unlink()
        if armed:
            shutil.copyfile(facade, command)
            command.chmod(0o755)
        else:
            command.symlink_to(delegate)
        state['armed'] = armed
        self.spec.write_text(json.dumps(state))

    def assert_unchanged_routes(self):
        assert self.lab.data['runtime'] == BASE
        assert self.lab.data['persistent'] == [] and self.lab.data['operations'] == []


@pytest.mark.parametrize('created', ['directory', 'symlink'])
def test_failed_state_creation_revalidates_the_object_that_appeared(tmp_path, created):
    """A failing mkdir may leave a real directory, but never grants link ownership."""
    lab = NativeRouteLab(tmp_path)
    foreign = tmp_path / 'operator-state-target'
    foreign.mkdir(mode=0o700)
    keep = foreign / 'keep'
    keep.write_bytes(b'operator state bytes\n')
    marker = tmp_path / 'creation-boundary-observed'
    delegate = shutil.which('mkdir')
    assert delegate
    facade = tmp_path / 'bin/mkdir'
    facade.write_text('#!/usr/bin/env python3\nimport os, subprocess, sys\nfrom pathlib import Path\n'
        'args=sys.argv[1:]\n'
        'if args == ["-m", "0700", "--", os.environ["BIND_CREATION_STATE"]]:\n'
        ' if os.environ["BIND_CREATION_SHAPE"] == "directory":\n'
        '  result=subprocess.run(['+repr(delegate)+']+args,timeout=10)\n'
        '  if result.returncode: sys.exit(result.returncode)\n'
        ' else: os.symlink(os.environ["BIND_CREATION_FOREIGN"],args[-1])\n'
        ' Path(os.environ["BIND_CREATION_MARKER"]).touch()\n'
        ' sys.exit(1)\n'
        'os.execv('+repr(delegate)+', ['+repr(delegate)+']+args)\n')
    facade.chmod(0o755)
    lab.env.update(BIND_CREATION_STATE=str(lab.state), BIND_CREATION_SHAPE=created,
                   BIND_CREATION_FOREIGN=str(foreign), BIND_CREATION_MARKER=str(marker))
    before = copy.deepcopy(lab.data)
    identity = (keep.stat().st_dev, keep.stat().st_ino)
    result = lab.run('plan')
    assert marker.exists(), 'The failing command must actually create the selected native object'
    assert lab.data == before and keep.read_bytes() == b'operator state bytes\n'
    assert (keep.stat().st_dev, keep.stat().st_ino) == identity
    assert list(foreign.iterdir()) == [keep]
    if created == 'directory':
        assert result.returncode == 2, result.stdout
        assert lab.state.is_dir() and not lab.state.is_symlink()
        assert lab.state.stat().st_mode & 0o7777 == 0o700
        assert [p.name for p in lab.state.iterdir()] == ['operations.flock']
        assert (lab.state / 'operations.flock').read_bytes() == b''
    else:
        assert result.returncode == 1 and 'Cannot create private directory' in result.stdout
        assert lab.state.is_symlink() and os.readlink(str(lab.state)) == str(foreign)


@pytest.mark.parametrize('boundary', ['initial-work', 'journal-write', 'recovery-work'])
def test_successful_temporary_path_never_owns_operator_files(tmp_path, boundary):
    """Reject foreign output before writes, including an EXIT rollback workspace."""
    fixture = UtilityFaultLab(tmp_path, 'commit-journal-cleanup')
    foreign = tmp_path / 'operator-object'
    if boundary == 'journal-write':
        foreign.write_text('operator journal bytes\n')
        foreign.chmod(0o600)
        before = {'file': foreign.read_bytes()}
    else:
        foreign.mkdir(mode=0o700)
        for name in ('out', 'err', 'keep'):
            (foreign / name).write_text('operator '+name+' bytes\n')
        before = {p.name: p.read_bytes() for p in foreign.iterdir()}
    marker = tmp_path / 'allocator-response-observed'
    delegate = shutil.which('mktemp')
    assert delegate
    facade = tmp_path / 'bin/mktemp'
    # Only return a path: the allocator facade never modifies operator objects.
    facade.write_text('#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\n'
        'boundary=os.environ["BIND_BAD_TEMP_BOUNDARY"]\n'
        'template=sys.argv[-1]\n'
        'selected = ((boundary == "journal-write" and template.endswith("/.write.XXXXXXXX")) or\n'
        ' (boundary != "journal-write" and template.endswith("/.route-work.XXXXXXXX") and\n'
        '  (boundary == "initial-work" or json.loads(Path(os.environ["BIND_ROUTE_UTILITY_SPEC"]).read_text())["triggered"])))\n'
        'if selected:\n Path(os.environ["BIND_BAD_TEMP_MARKER"]).touch()\n print(os.environ["BIND_BAD_TEMP_OBJECT"])\n'
        'else:\n os.execv('+repr(delegate)+', ['+repr(delegate)+']+sys.argv[1:])\n')
    facade.chmod(0o755)
    fixture.lab.env.update(BIND_BAD_TEMP_BOUNDARY=boundary, BIND_BAD_TEMP_OBJECT=str(foreign),
        BIND_BAD_TEMP_MARKER=str(marker))
    result = fixture.lab.run()
    assert marker.exists(), 'The native transaction must reach the selected allocator'
    current = {p.name: p.read_bytes() for p in foreign.iterdir()} if foreign.is_dir() else {'file': foreign.read_bytes()}
    assert current == before, 'Temporary cleanup must never delete operator files'
    assert result.returncode == 1, result.stdout
    if boundary == 'recovery-work':
        pending = fixture.lab.state / 'front-routes.pending'
        assert pending.is_dir() and len(list(pending.iterdir())) == 9
        assert fixture.lab.data['operations'], 'The failure must follow real route additions'
        facade.unlink()
        recovered = fixture.lab.run('recover')
        assert recovered.returncode == 2, recovered.stdout
        assert not pending.exists()
    if boundary != 'recovery-work':
        fixture.assert_unchanged_routes()
    assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == []


@pytest.mark.parametrize('shape', ['mode', 'owner', 'link', 'missing', 'suffix',
    'stat-error', 'hidden', 'double-dot', 'dangling', 'allocation-error'])
def test_front_workspace_contract_precedes_any_cleanup_ownership(tmp_path, shape):
    """An expected prefix alone cannot authorize deletion of a private object."""
    lab = NativeRouteLab(tmp_path)
    lab.state.mkdir(mode=0o700)
    candidate = lab.state / ('.route-work.' + ('abcdefghi' if shape == 'suffix' else 'abcdefgh'))
    operator = tmp_path / 'operator-data'
    operator.write_text('preserve operator bytes\n')
    operator.chmod(0o600)
    if shape == 'link':
        candidate.symlink_to(operator)
    elif shape != 'missing':
        candidate.mkdir(mode=0o700)
        if shape in ('hidden', 'double-dot'):
            (candidate / ('.hidden' if shape == 'hidden' else '..hidden')).write_text('preserve hidden bytes\n')
        elif shape == 'dangling':
            (candidate / '.orphan').symlink_to(tmp_path / 'missing-target')
        if shape == 'mode':
            candidate.chmod(0o750)
        elif shape == 'owner':
            os.chown(str(candidate), 1000, 1000)

    def snapshot():
        values = {}
        for path in [operator, candidate] + ([] if candidate.is_symlink() else list(candidate.rglob('*'))):
            if not path.exists() and not path.is_symlink():
                values[str(path)] = None
                continue
            stat = path.lstat()
            values[str(path)] = (stat.st_mode, stat.st_uid, stat.st_gid, stat.st_nlink,
                os.readlink(str(path)) if path.is_symlink() else path.read_bytes() if path.is_file() else None)
        return values

    before = snapshot()
    marker = tmp_path / 'temporary-object-reached'
    lab.env.update(BIND_TEMP_OBJECT=str(candidate), BIND_TEMP_REACHED=str(marker))
    facade = tmp_path / 'bin/mktemp'
    facade.write_text('#!/bin/bash\nif [[ ${@: -1} == */.route-work.XXXXXXXX ]]; then\n'
        '  : > "$BIND_TEMP_REACHED"\n  printf "%s\\n" "$BIND_TEMP_OBJECT"; exit "${BIND_TEMP_ALLOCATION_RC:-0}"\nfi\n'
        'exec /usr/bin/mktemp "$@"\n')
    facade.chmod(0o755)
    lab.env['BIND_TEMP_ALLOCATION_RC'] = '1' if shape == 'allocation-error' else '0'
    failed = tmp_path / 'temporary-stat-failed'
    if shape == 'stat-error':
        lab.env['BIND_TEMP_STAT_FAILED'] = str(failed)
        facade = tmp_path / 'bin/stat'
        facade.write_text('#!/bin/bash\nif [[ ${@: -1} == "$BIND_TEMP_OBJECT" ]]; then\n'
            '  : > "$BIND_TEMP_STAT_FAILED"\n  /usr/bin/stat "$@"; exit 1\nfi\n'
            'exec /usr/bin/stat "$@"\n')
        facade.chmod(0o755)
    result = lab.run()
    assert marker.exists(), 'The native transaction must observe the selected workspace'
    if shape == 'stat-error':
        assert failed.exists(), 'The actual workspace metadata producer must fail'
    assert snapshot() == before
    assert result.returncode == 1, result.stdout
    assert lab.data['runtime'] == BASE and lab.data['persistent'] == [] and lab.data['operations'] == []
    assert not (lab.state / 'front-routes.pending').exists()


@pytest.mark.parametrize('boundary', ['allocation', 'empty-size'])
def test_failed_journal_temporary_producer_cannot_publish_complete_looking_output(tmp_path, boundary):
    """A real empty inode and valid stdout do not override a failed producer."""
    lab = NativeRouteLab(tmp_path)
    reached = tmp_path / 'journal-producer-failed'
    candidate_record = tmp_path / 'allocated-journal-path'
    lab.env.update(BIND_JOURNAL_FAILURE=str(reached), BIND_JOURNAL_PATH=str(candidate_record),
                   BIND_JOURNAL_BOUNDARY=boundary)
    allocator = tmp_path / 'bin/mktemp'
    allocator.write_text('#!/bin/bash\n'
        'if [[ ${@: -1} == */.write.XXXXXXXX && ! -e $BIND_JOURNAL_PATH ]]; then\n'
        '  candidate=$(/usr/bin/mktemp "$@") || exit $?\n'
        '  printf "%s\\n" "$candidate" > "$BIND_JOURNAL_PATH"\n'
        '  printf "%s\\n" "$candidate"\n'
        '  if [[ $BIND_JOURNAL_BOUNDARY == allocation ]]; then : > "$BIND_JOURNAL_FAILURE"; exit 1; fi\n'
        '  exit 0\nfi\nexec /usr/bin/mktemp "$@"\n')
    allocator.chmod(0o755)
    if boundary == 'empty-size':
        observer = tmp_path / 'bin/stat'
        # safe_file observes size once. Fail the allocator's later empty-file
        # observation, after the ownership and size-limit checks succeeded.
        observer.write_text('#!/bin/bash\n'
            'if [[ $1 == -c && $2 == %s && ${@: -1} == */.write.* ]]; then\n'
            '  if [[ -e $BIND_JOURNAL_FAILURE.count ]]; then\n'
            '    /usr/bin/stat "$@" || exit $?\n'
            '    : > "$BIND_JOURNAL_FAILURE"; exit 1\n'
            '  fi\n  : > "$BIND_JOURNAL_FAILURE.count"\nfi\nexec /usr/bin/stat "$@"\n')
        observer.chmod(0o755)
    result = lab.run()
    assert reached.exists(), 'The selected native producer must fail after emitting valid output'
    assert result.returncode == 1 and 'Unsafe journal temporary allocation' in result.stdout, result.stdout
    assert lab.data['runtime'] == BASE and lab.data['persistent'] == [] and lab.data['operations'] == []
    candidate = candidate_record.read_text().strip()
    # Canonical execution moves the state tree back to the fixture after exit.
    relative = candidate.split('/front-routes.pending/', 1)[1]
    pending = lab.state / 'front-routes.pending'
    retained = pending / relative
    assert retained.is_file() and retained.read_bytes() == b''
    assert retained.stat().st_mode & 0o777 == 0o600
    assert not (pending / 'meta').exists() and not list(lab.state.glob('.route-work.*'))


def test_operator_evidence_arriving_at_commit_prevents_journal_cleanup(tmp_path):
    """Exercise the late entry count, after genuine writes and file checks."""
    lab = NativeRouteLab(tmp_path)
    reached = tmp_path / 'late-journal-entry-created'
    lab.env['BIND_LATE_JOURNAL_ENTRY'] = str(reached)
    observer = tmp_path / 'bin/stat'
    observer.write_text('#!/bin/bash\n'
        'if [[ $1 == -c && $2 == %u:%a:%h && ${@: -1} == */front-routes.pending/progress && ! -e $BIND_LATE_JOURNAL_ENTRY ]]; then\n'
        '  /usr/bin/stat "$@" || exit $?\n'
        '  pending=${@: -1}; pending=${pending%/progress}\n'
        '  printf "%s\\n" "operator evidence" > "$pending/operator-evidence"\n'
        '  : > "$BIND_LATE_JOURNAL_ENTRY"; exit 0\nfi\nexec /usr/bin/stat "$@"\n')
    observer.chmod(0o755)
    result = lab.run()
    pending = lab.state / 'front-routes.pending'
    evidence = pending / 'operator-evidence'
    assert reached.exists() and result.returncode == 1, result.stdout
    assert 'Unexpected journal entry prevents cleanup' in result.stdout
    assert 'FRONT recovery required:' in result.stdout
    assert evidence.read_text() == 'operator evidence\n' and len(list(pending.iterdir())) == 10
    assert len(lab.data['operations']) == 2
    assert all(row['action'] == '+ipv4.routes' for row in lab.data['operations'])
    # The operator explicitly releases its own evidence before a subsequent
    # recovery; no production cleanup is allowed to delete that extra object.
    evidence.unlink()
    recovered = lab.run('recover')
    assert recovered.returncode == 2, recovered.stdout
    assert lab.data['runtime'] == BASE and lab.data['persistent'] == [] and not pending.exists()


def test_journal_temporary_must_be_empty_before_publication(tmp_path):
    """Refuse a nonempty allocator object before replacing its original bytes."""
    lab = NativeRouteLab(tmp_path)
    marker = tmp_path / 'nonempty-journal-returned'
    lab.env['BIND_TEMP_REACHED'] = str(marker)
    facade = tmp_path / 'bin/mktemp'
    # Create a synthetic occupied object at this late native boundary. It is
    # never a production object; the facade records exactly when it is created.
    facade.write_text('#!/bin/bash\nif [[ ${@: -1} == */.write.XXXXXXXX && ! -e $BIND_TEMP_REACHED ]]; then\n'
        '  candidate=${@: -1}; candidate=${candidate%XXXXXXXX}abcdefgh\n'
        '  printf "preserve occupied journal bytes\\n" > "$candidate"\n'
        '  chmod 0600 "$candidate"\n  : > "$BIND_TEMP_REACHED"\n'
        '  printf "%s\\n" "$candidate"; exit 0\nfi\nexec /usr/bin/mktemp "$@"\n')
    facade.chmod(0o755)
    result = lab.run()
    assert marker.exists()
    candidate = lab.state / 'front-routes.pending/.write.abcdefgh'
    assert candidate.read_text() == 'preserve occupied journal bytes\n'
    assert candidate.stat().st_mode & 0o777 == 0o600
    assert result.returncode == 1, result.stdout
    assert lab.data['runtime'] == BASE and lab.data['persistent'] == [] and lab.data['operations'] == []


@pytest.mark.parametrize('kind', ['file', 'dangling-symlink', 'vanishing-file'])
def test_nonempty_observation_allocation_keeps_operator_evidence(tmp_path, kind):
    """Reject occupied allocations, including a genuinely disappearing entry."""
    if kind == 'vanishing-file':
        # The DEBUG race barrier is a functional negative control, never a
        # coverage mechanism. Keep the measured profile's bootstrap refusal.
        import hashlib
        import subprocess
        import sys
        probe = ROOT / 'tests/fixtures/allocation_race_probe.py'
        result = subprocess.run(['/usr/bin/unshare', '--mount', '--propagation', 'private',
                                 sys.executable, '-I', str(probe), str(ROOT),
                                 os.readlink('/proc/self/ns/mnt')],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                universal_newlines=True, timeout=60, check=False)
        assert result.returncode == 0, result.stdout + result.stderr
        source = ROOT / 'roles/bind9/files/bind-front-routes.sh'
        assert json.loads(result.stdout) == {
            'case': kind, 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
    else:
        _assert_nonempty_observation_allocation(tmp_path, kind)


def _assert_nonempty_observation_allocation(tmp_path, kind):
    """Share all filesystem and rollback oracles with the isolated race actor."""
    lab = NativeRouteLab(tmp_path)
    marker = tmp_path / 'occupied-observation'
    wrapper = tmp_path / 'bin/mktemp'
    wrapper.write_text('#!/bin/bash\n'
        'if [[ $1 == -d && ${@: -1} == */.route-work.XXXXXXXX && ! -e $BIND_OCCUPIED_ALLOCATION ]]; then\n'
        '  candidate=$(/usr/bin/mktemp "$@") || exit $?\n'
        '  if [[ $BIND_ALLOCATION_ENTRY_KIND == file || $BIND_ALLOCATION_ENTRY_KIND == vanishing-file ]]; then\n'
        '    printf "operator allocation bytes\\n" > "$candidate/operator-evidence"\n'
        '  else /usr/bin/ln -s operator-missing "$candidate/operator-evidence"; fi\n'
        '  printf "%s\\n" "${candidate##*/}" > "$BIND_OCCUPIED_ALLOCATION"\n'
        '  printf "%s\\n" "$candidate"; exit 0\nfi\nexec /usr/bin/mktemp "$@"\n')
    wrapper.chmod(0o755)
    lab.env.update(BIND_OCCUPIED_ALLOCATION=str(marker), BIND_ALLOCATION_ENTRY_KIND=kind)
    vanished = tmp_path / 'removed-operator-evidence'
    if kind == 'vanishing-file':
        # A deterministic external actor removes its own entry after Bash has
        # enumerated it. No filesystem result or coverage event is fabricated.
        hook = tmp_path / 'allocation-race-env.sh'
        hook.write_text('#!/bin/bash\n'
            '_qa_remove_after_enumeration() {\n'
            '  if [[ ${FUNCNAME[1]:-} == allocate_temporary && '
            '${entry-} == */.route-work.????????/operator-evidence && '
            '! -e $BIND_REMOVED_ALLOCATION ]]; then\n'
            '    /usr/bin/cp -- "$entry" "$BIND_REMOVED_ALLOCATION" || return $?\n'
            '    /usr/bin/rm -- "$entry" || return $?\n'
            '  fi\n  return 0\n}\n'
            'set -o functrace\n'
            "trap '_qa_remove_after_enumeration' DEBUG\n")
        lab.env.update(BASH_ENV=str(hook), BIND_REMOVED_ALLOCATION=str(vanished))
    result = lab.run()
    assert marker.is_file(), result.stdout
    if kind == 'vanishing-file':
        assert vanished.read_bytes() == b'operator allocation bytes\n', result.stdout
    assert result.returncode == 1 and 'Unsafe observation temporary allocation' in result.stdout, result.stdout
    candidate = lab.state / marker.read_text().strip()
    assert candidate.is_dir() and not candidate.is_symlink()
    evidence = candidate / 'operator-evidence'
    if kind == 'file':
        assert evidence.read_bytes() == b'operator allocation bytes\n'
    elif kind == 'dangling-symlink':
        assert evidence.is_symlink() and not evidence.exists()
        assert os.readlink(str(evidence)) == 'operator-missing'
    else:
        assert not evidence.exists() and not evidence.is_symlink()
        assert list(candidate.iterdir()) == []
    assert lab.data['runtime'] == BASE and lab.data['persistent'] == [] and lab.data['operations'] == []
    assert not (lab.state / 'front-routes.pending').exists()


def test_permission_revocation_during_lock_creation_never_repairs_operator_state(tmp_path):
    """Use actual EACCES after a real metadata read, retaining EUID zero."""
    from canonical_shell_lab import RestrictedRootExec
    lab = NativeRouteLab(tmp_path)
    lab.state.mkdir(mode=0o700)
    identity = (lab.state.stat().st_dev, lab.state.stat().st_ino)
    marker = tmp_path / 'state-permission-revoked'
    wrapper = tmp_path / 'bin/stat'
    wrapper.write_text('#!/bin/bash\n'
        'if [[ $1 == -c && $2 == %u:%a && ${@: -1} == "$BIND_LOCK_STATE" && ! -e $BIND_LOCK_REVOKED ]]; then\n'
        '  observed=$(/usr/bin/stat "$@") || exit $?\n'
        '  /usr/bin/chmod 0500 -- "$BIND_LOCK_STATE" || exit $?\n'
        '  printf "revoked\\n" > "$BIND_LOCK_REVOKED"\n'
        '  printf "%s\\n" "$observed"; exit 0\nfi\nexec /usr/bin/stat "$@"\n')
    wrapper.chmod(0o755)
    lab.env.update(BIND_LOCK_STATE=str(lab.state), BIND_LOCK_REVOKED=str(marker))
    try:
        with RestrictedRootExec():
            result = lab.run()
        assert marker.read_text() == 'revoked\n'
        assert result.returncode == 1 and 'Unsafe journal file:' in result.stdout, result.stdout
        assert (lab.state.stat().st_dev, lab.state.stat().st_ino) == identity
        assert lab.state.stat().st_mode & 0o777 == 0o500
        assert not (lab.state / 'operations.flock').exists()
        assert lab.data['runtime'] == BASE and lab.data['persistent'] == [] and lab.data['operations'] == []
    finally:
        lab.state.chmod(0o700)


@pytest.mark.parametrize('kind', ['noncanonical', 'duplicate', 'foreign', 'missing', 'empty', 'malformed'])
def test_successful_client_sort_observation_cannot_widen_or_duplicate_routes(tmp_path, kind):
    """Check defensive output validation even when the producer reports success."""
    def numeric_row(network, offset=0):
        value = ipaddress.IPv4Network(network)
        return '{}|{}|{}'.format(int(value.network_address)+offset,
            int(value.broadcast_address), value.prefixlen)

    first, second = '203.0.113.0/24', '172.20.0.0/24'
    observations = {'noncanonical': numeric_row(first, offset=1),
        'duplicate': '\n'.join((numeric_row(first), numeric_row(second), numeric_row(first))),
        'foreign': numeric_row('203.0.114.0/24'), 'missing': numeric_row(first),
        'empty': '', 'malformed': 'invalid'}
    output = observations[kind]
    fixture = UtilityFaultLab(tmp_path, dict(command='sort', args=['-t|', '-k1,1n', '-k3,3n'],
        stdout=output+'\n', returncode=0, drain_stdin=True))
    clients = [first] if kind in ('noncanonical', 'foreign', 'malformed') else [first, second]
    result = fixture.lab.run(clients=clients)
    assert json.loads(fixture.spec.read_text())['triggered'] is True
    if kind == 'noncanonical':
        assert result.returncode == 1 and 'Noncanonical client subnet' in result.stdout, result.stdout
        fixture.assert_unchanged_routes()
    elif kind != 'duplicate':
        assert result.returncode == 1, result.stdout
        fixture.assert_unchanged_routes()
    else:
        assert result.returncode == 2, result.stdout
        assert len(fixture.lab.data['persistent']) == 2
        assert {row['network'] for row in fixture.lab.data['persistent']} == set(clients)
        assert fixture.lab.data['runtime'][:len(BASE)] == BASE
        assert len(fixture.lab.data['runtime']) == len(BASE)+2
        assert {row['dst'] for row in fixture.lab.data['runtime'][len(BASE):]} == set(clients)
        before = copy.deepcopy(fixture.lab.data)
        assert fixture.lab.run(clients=clients).returncode == 0
        assert fixture.lab.data == before
    assert not (fixture.lab.state / 'front-routes.pending').exists()
    assert not list(fixture.lab.state.glob('.route-work.*'))


@pytest.mark.parametrize('response', ['', 'invalid-nonce',
    '00000000-0000-4000-8000-000000000000\nextra-record\n'])
def test_successful_nonce_observation_must_establish_a_recoverable_identity(tmp_path, response):
    """A zero exit status cannot publish a journal that recovery would reject."""
    fixture = UtilityFaultLab(tmp_path, dict(command='cat',
        args=['/proc/sys/kernel/random/uuid'], returncode=0, stdout=response))
    result = fixture.lab.run(more=['--defer-commit'])
    assert json.loads(fixture.spec.read_text())['triggered'] is True
    assert result.returncode == 1, result.stdout
    assert 'Invalid native transaction nonce' in result.stdout
    fixture.assert_unchanged_routes()
    pending = fixture.lab.state / 'front-routes.pending'
    assert pending.is_dir() and not list(pending.iterdir())
    assert not list(fixture.lab.state.glob('.route-work.*'))


def test_an_uncaught_native_rc2_cannot_impersonate_success(tmp_path):
    fixture = UtilityFaultLab(tmp_path, 'nonce')
    result = fixture.lab.run()
    assert json.loads(fixture.spec.read_text())['triggered'] is True
    assert result.returncode == 1, result.stdout
    assert 'FRONT internal command failed with rc 2; refusing a success result.' in result.stdout
    fixture.assert_unchanged_routes()
    # Preparation did not complete; retain its empty private directory as
    # evidence rather than trusting it as a complete rollback journal.
    pending = fixture.lab.state / 'front-routes.pending'
    assert pending.is_dir() and not list(pending.iterdir())


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_observation_failure_after_preparation_never_writes_a_route(tmp_path, cleanup_fails):
    fixture = UtilityFaultLab(tmp_path, 'prepared-cleanup' if cleanup_fails else 'prepared')
    result = fixture.lab.run()
    state = json.loads(fixture.spec.read_text())
    assert state['triggered'] is True and state['cleanup_triggered'] is cleanup_fails
    assert result.returncode == 1 and 'Observation size read failed' in result.stdout, result.stdout
    fixture.assert_unchanged_routes()
    pending = fixture.lab.state / 'front-routes.pending'
    assert pending.exists() is cleanup_fails
    assert ('Prepared journal retained:' in result.stdout) is cleanup_fails
    if cleanup_fails:
        before = {path.name: path.read_bytes() for path in pending.iterdir()}
        assert len(before) == 9
        blocked = fixture.lab.run()
        assert blocked.returncode == 1 and 'Pending FRONT journal:' in blocked.stdout
        assert {path.name: path.read_bytes() for path in pending.iterdir()} == before
        recovered = fixture.lab.run('recover')
        assert recovered.returncode == 2 and 'FRONT rollback completed' in recovered.stdout, recovered.stdout
        fixture.assert_unchanged_routes()
        assert not pending.exists()


@pytest.mark.parametrize('fault,deferred', [
    ('after-apply-cleanup', False), ('after-apply-cleanup', True), ('commit-journal-cleanup', False),
    ('retirement-after-success', False),
])
def test_cleanup_failure_after_applying_routes_uses_the_owned_rollback(tmp_path, fault, deferred):
    fixture = UtilityFaultLab(tmp_path, fault)
    result = fixture.lab.run(more=['--defer-commit'] if deferred else [])
    assert json.loads(fixture.spec.read_text())['triggered'] is True
    assert result.returncode == 1, result.stdout
    assert 'FRONT rollback completed; preexisting routes preserved.' in result.stdout
    assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == []
    assert fixture.lab.data['operations'], 'The failure must follow actual native route additions'
    assert not (fixture.lab.state / 'front-routes.pending').exists()
    assert not list(fixture.lab.state.glob('.route-work.*'))


def test_completed_retirement_preserves_all_journal_inodes_and_is_not_replayed(tmp_path):
    """Commit changes one directory name; it never unlinks recovery metadata."""
    lab = NativeRouteLab(tmp_path)
    assert lab.run(more=['--defer-commit']).returncode == 2
    pending = lab.state / 'front-routes.pending'
    before = {p.name: (p.stat().st_dev, p.stat().st_ino, p.read_bytes()) for p in pending.iterdir()}
    identity = (pending.stat().st_dev, pending.stat().st_ino)
    token = (pending / 'meta').read_text().split('|')[0]
    committed = lab.run('commit', more=['--token', token])
    assert committed.returncode == 0, committed.stdout
    retired = list(lab.state.glob('.front-routes.retired.*'))
    assert len(retired) == 1 and not pending.exists()
    assert (retired[0].stat().st_dev, retired[0].stat().st_ino) == identity
    assert {p.name: (p.stat().st_dev, p.stat().st_ino, p.read_bytes()) for p in retired[0].iterdir()} == before
    assert retired[0].stat().st_mode & 0o7777 == 0o700
    assert all(p.stat().st_mode & 0o7777 == 0o600 and p.stat().st_nlink == 1 for p in retired[0].iterdir())
    network = copy.deepcopy(lab.data)
    assert lab.run().returncode == 0
    refused = lab.run('recover')
    assert refused.returncode == 1 and 'No pending FRONT transaction' in refused.stdout
    assert lab.data == network and list(lab.state.glob('.front-routes.retired.*')) == retired


def test_retirement_sync_failure_rolls_back_with_a_complete_journal(tmp_path):
    fixture = UtilityFaultLab(tmp_path, dict(command='sync', prefix=['--'], target='state'))
    result = fixture.lab.run()
    assert json.loads(fixture.spec.read_text())['triggered'] is True
    assert result.returncode == 1 and 'Journal retirement sync failed' in result.stdout, result.stdout
    assert 'FRONT rollback completed; preexisting routes preserved.' in result.stdout
    assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == []
    retired = list(fixture.lab.state.glob('.front-routes.retired.*'))
    assert len(retired) == 1 and len(list(retired[0].iterdir())) == 9
    assert (retired[0] / 'progress').read_text() == '0 done\n'
    assert not (fixture.lab.state / 'front-routes.pending').exists()


@pytest.mark.parametrize('signum,expected', [('SIGHUP', 129), ('SIGINT', 130), ('SIGTERM', 143)])
def test_interrupted_atomic_retirement_restores_the_journal_before_rollback(tmp_path, signum, expected):
    fixture = UtilityFaultLab(tmp_path, dict(command='sync', prefix=['--'], target='state', signal=signum))
    result = fixture.lab.run()
    assert json.loads(fixture.spec.read_text())['triggered'] is True
    assert result.returncode == expected, result.stdout
    assert 'FRONT rollback completed; preexisting routes preserved.' in result.stdout
    assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == []
    assert not (fixture.lab.state / 'front-routes.pending').exists()
    retired = list(fixture.lab.state.glob('.front-routes.retired.*'))
    assert len(retired) == 1 and len(list(retired[0].iterdir())) == 9
    assert (retired[0] / 'progress').read_text() == '0 done\n'


@pytest.mark.parametrize('shape', ['file', 'directory', 'symlink', 'dangling-symlink'])
def test_retirement_does_not_overwrite_a_foreign_destination(tmp_path, shape):
    fixture = UtilityFaultLab(tmp_path, 'retirement-foreign-destination')
    operator = tmp_path / 'operator-retirement-target'
    operator.mkdir(mode=0o700)
    keep = operator / 'keep'
    keep.write_bytes(b'operator retirement bytes\n')
    spec = json.loads(fixture.spec.read_text())
    spec.update(foreign_shape=shape, foreign_target=str(operator / 'missing') if shape == 'dangling-symlink' else str(operator))
    fixture.spec.write_text(json.dumps(spec))
    result = fixture.lab.run()
    state = json.loads(fixture.spec.read_text())
    assert state['triggered'] is True and state['native_retirement_returncode'] == 0
    assert result.returncode == 1 and 'Journal retirement identity changed' in result.stdout, result.stdout
    assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == []
    foreign = fixture.lab.state / Path(state['retirement_destination']).name
    if shape == 'file':
        assert foreign.read_bytes() == b'operator destination bytes\n'
    elif shape == 'directory':
        assert foreign.is_dir() and list(foreign.iterdir()) == []
    else:
        assert foreign.is_symlink() and os.readlink(str(foreign)) == state['foreign_target']
    assert (foreign.lstat().st_dev, foreign.lstat().st_ino) == tuple(state['foreign_identity'])
    assert keep.read_bytes() == b'operator retirement bytes\n'
    assert not (fixture.lab.state / 'front-routes.pending').exists()


def _assert_foreign_active_path_preserved(tmp_path, shape='directory', report_success=False):
    """A foreign active pathname cannot authorize replacement or inverse writes."""
    fixture = UtilityFaultLab(tmp_path, 'retirement-foreign-active')
    operator = tmp_path / 'operator-active-target'
    operator.mkdir(mode=0o700)
    keep = operator / 'operator-evidence'
    keep.write_bytes(b'operator active-path bytes\n')
    spec = json.loads(fixture.spec.read_text())
    spec.update(foreign_active_shape=shape,
                foreign_target=str(operator / 'missing') if shape == 'dangling-symlink' else str(operator))
    if report_success:
        spec['retirement_report_success'] = True
    fixture.spec.write_text(json.dumps(spec))
    result = fixture.lab.run()
    state = json.loads(fixture.spec.read_text())
    assert state['triggered'] is True and result.returncode == 1
    assert 'retirement compensation requires inspection' in result.stdout
    pending = fixture.lab.state / 'front-routes.pending'
    if shape == 'file':
        assert pending.read_bytes() == b'operator active-path bytes\n'
    elif shape == 'dangling-symlink':
        assert pending.is_symlink() and not pending.exists()
        assert os.readlink(str(pending)) == spec['foreign_target']
    else:
        assert (pending / 'operator-evidence').read_bytes() == b'operator active-path bytes\n'
        if shape == 'symlink':
            assert pending.is_symlink() and os.readlink(str(pending)) == str(operator)
    assert (pending.lstat().st_dev, pending.lstat().st_ino) == tuple(state['foreign_identity'])
    retired = fixture.lab.state / Path(state['completed_cleanup_command'][-1]).name
    assert len(list(retired.iterdir())) == 9
    assert (retired.stat().st_dev, retired.stat().st_ino) == tuple(state['retired_identity'])
    assert fixture.lab.data['persistent'] and len(fixture.lab.data['runtime']) == len(BASE)+1
    assert len(fixture.lab.data['operations']) == 2, 'A foreign active journal cannot authorize an inverse write'
    assert keep.read_bytes() == b'operator active-path bytes\n'
    if report_success:
        assert state['retirement_native_returncode'] == 0
        assert 'Journal retirement identity changed' in result.stdout
        # Only the synthetic operator resolves its own dangling replacement.
        pending.unlink()
        retired.rename(pending)
        recovered = fixture.lab.run('recover')
        assert recovered.returncode == 2, recovered.stdout
        assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == []
        assert not pending.exists()


def test_a_foreign_active_path_blocks_compensation_without_losing_the_complete_journal(tmp_path):
    _assert_foreign_active_path_preserved(tmp_path)


@pytest.mark.parametrize('shape', ['file', 'symlink', 'dangling-symlink'])
def test_a_foreign_active_file_or_link_cannot_be_overwritten_by_compensation(tmp_path, shape):
    _assert_foreign_active_path_preserved(tmp_path, shape)



def test_a_dangling_active_link_after_a_successful_retirement_cannot_authorize_cleanup(tmp_path):
    """A real successful mv must still refuse a new dangling active pathname."""
    _assert_foreign_active_path_preserved(tmp_path, 'dangling-symlink', report_success=True)


def test_a_real_bind_alias_cannot_be_mistaken_for_a_distinct_empty_reservation(tmp_path):
    """Exercise equal identities using an actual private bind mount, not fake stat."""
    fixture = UtilityFaultLab(tmp_path, 'retirement-aliased-identities')
    failed = fixture.lab.run()
    state = json.loads(fixture.spec.read_text())
    assert state['triggered'] is True and state['alias_unmounted'] is True, failed.stdout
    assert state['alias_identity'] == state['journal_identity']
    assert state['reservation_after_unmount_identity'] != state['journal_identity']
    assert state['native_alias_stat'] == ':'.join(map(str, state['journal_identity'])) + '\n'
    assert failed.returncode == 1 and 'Invalid journal retirement identities' in failed.stdout
    assert 'FRONT rollback completed; preexisting routes preserved.' in failed.stdout
    assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == []
    assert not (fixture.lab.state / 'front-routes.pending').exists()
    retired = list(fixture.lab.state.glob('.front-routes.retired.*'))
    complete = [p for p in retired if (p / 'meta').is_file()]
    assert len(retired) == 2 and len(complete) == 1
    assert sum(not list(p.iterdir()) for p in retired) == 1
    closed = complete[0]
    assert (closed.stat().st_dev, closed.stat().st_ino) == tuple(state['journal_identity'])
    assert len(list(closed.iterdir())) == 9 and (closed / 'progress').read_text() == '0 done\n'
    assert {name: [p.stat().st_ino, p.read_bytes().hex()] for name, p in
            ((name, closed / name) for name in state['immutable_journal_before_alias'])} == state['immutable_journal_before_alias']


def test_a_failed_compensation_rename_keeps_the_whole_journal_for_the_exit_retry(tmp_path):
    fixture = UtilityFaultLab(tmp_path, 'retirement-back-rename-error')
    result = fixture.lab.run()
    state = json.loads(fixture.spec.read_text())
    assert state['triggered'] is True and state['cleanup_triggered'] is True, result.stdout
    assert result.returncode == 1 and 'retirement compensation requires inspection' in result.stdout
    assert 'FRONT rollback completed; preexisting routes preserved.' in result.stdout
    assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == []
    assert not (fixture.lab.state / 'front-routes.pending').exists()
    retired = list(fixture.lab.state.glob('.front-routes.retired.*'))
    assert len(retired) == 1 and len(list(retired[0].iterdir())) == 9
    assert (retired[0] / 'progress').read_text() == '0 done\n'


@pytest.mark.parametrize('completed', [4095, 4096])
def test_completed_evidence_limit_blocks_new_writes_but_keeps_plans_and_idempotence(tmp_path, completed):
    lab = NativeRouteLab(tmp_path)
    lab.state.mkdir(mode=0o700)
    # Even unexpected entries carrying the reserved prefix are retained and
    # counted conservatively; none is interpreted as an active journal.
    for number in range(completed):
        (lab.state / ('.front-routes.retired.fixture.%04d' % number)).mkdir(mode=0o700)
    before = copy.deepcopy(lab.data)
    result = lab.run()
    if completed == 4096:
        assert result.returncode == 1 and 'limit 4096' in result.stdout, result.stdout
        assert lab.data == before and not (lab.state / 'front-routes.pending').exists()
    else:
        assert result.returncode == 2, result.stdout
        after = copy.deepcopy(lab.data)
        assert lab.run().returncode == 0 and lab.data == after
    assert len(list(lab.state.glob('.front-routes.retired.*'))) == 4096
    after = copy.deepcopy(lab.data)
    planned = lab.run('plan', clients=['203.0.114.0/24'])
    assert planned.returncode == 2 and lab.data == after


def test_failed_explicit_commit_keeps_all_nine_files_for_the_callers_recovery(tmp_path):
    fixture = UtilityFaultLab(tmp_path, dict(command='sync', prefix=['--'], target='state'))
    lab = fixture.lab
    assert lab.run(more=['--defer-commit']).returncode == 2
    pending = lab.state / 'front-routes.pending'
    before = {p.name: (p.stat().st_ino, p.read_bytes()) for p in pending.iterdir()}
    token = (pending / 'meta').read_text().split('|')[0]
    failed = lab.run('commit', more=['--token', token])
    assert failed.returncode == 1 and 'Journal retirement sync failed' in failed.stdout, failed.stdout
    assert json.loads(fixture.spec.read_text())['triggered'] is True
    assert {p.name: (p.stat().st_ino, p.read_bytes()) for p in pending.iterdir()} == before
    recovered = lab.run('recover')
    assert recovered.returncode == 2, recovered.stdout
    assert lab.data['runtime'] == BASE and lab.data['persistent'] == [] and not pending.exists()


@pytest.mark.parametrize('target,skip,output,message', [
    ('pending', 0, None, 'Journal retirement identity read failed'),
    ('retirement', 0, None, 'Retirement directory identity read failed'),
    ('retirement', 1, None, 'Journal retirement identity read failed'),
    ('pending', 0, 'invalid\n', 'Invalid journal retirement identities'),
    ('retirement', 0, 'invalid\n', 'Invalid journal retirement identities'),
], ids=['original-id-error', 'reservation-id-error', 'renamed-id-error',
        'original-id-malformed', 'reservation-id-malformed'])
def test_retirement_metadata_failure_keeps_complete_evidence_and_restores_routes(
        tmp_path, target, skip, output, message):
    """A failed or malformed identity observation never authorizes lost recovery data."""
    rule = dict(command='stat', prefix=['-c', '%d:%i', '--'], target=target, skip_matches=skip)
    if output is not None:
        rule.update(returncode=0, stdout=output)
    fixture = UtilityFaultLab(tmp_path, rule)
    result = fixture.lab.run()
    state = json.loads(fixture.spec.read_text())
    assert state['triggered'] is True, result.stdout
    healthy_stat = tmp_path / 'bin/stat'
    assert healthy_stat.is_symlink() and healthy_stat.resolve() == Path(state['delegates']['stat']).resolve()
    assert result.returncode == 1 and message in result.stdout, result.stdout
    assert 'FRONT rollback completed; preexisting routes preserved.' in result.stdout
    assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == []
    assert not (fixture.lab.state / 'front-routes.pending').exists()
    retired = list(fixture.lab.state.glob('.front-routes.retired.*'))
    complete = [p for p in retired if (p / 'meta').is_file()]
    assert len(complete) == 1 and len(list(complete[0].iterdir())) == 9
    assert (complete[0] / 'progress').read_text() == '0 done\n'
    # An allocated empty reservation is retained as evidence after a read
    # failure. It never permits removal of a foreign or incomplete object.
    assert all(p == complete[0] or list(p.iterdir()) == [] for p in retired)


@pytest.mark.parametrize('boundary,first_failure', [('renamed', 'retirement-after-success'),
    ('restored', 'retirement-after-success'), ('restored', 'commit-journal-cleanup')],
    ids=['renamed', 'restored', 'before-move'])
def test_a_second_identity_error_during_retirement_compensation_is_retried_safely(tmp_path, boundary, first_failure):
    """Observe a real successful rename, then fail one compensation metadata read."""
    fixture = UtilityFaultLab(tmp_path, first_failure)
    marker = tmp_path / 'compensation-stat-error'
    delegate = shutil.which('stat')
    assert delegate
    facade = tmp_path / 'bin/stat'
    facade.write_text('#!/usr/bin/env python3\nimport json, os, sys\nfrom pathlib import Path\n'
        'args=sys.argv[1:]\npending=Path(os.environ["BIND_ROUTE_UTILITY_PENDING"])\n'
        'state=json.loads(Path(os.environ["BIND_ROUTE_UTILITY_SPEC"]).read_text())\n'
        'marker=Path(os.environ["BIND_COMPENSATION_STAT_MARKER"])\n'
        'selected=(args[:3] == ["-c", "%d:%i", "--"] and bool(args) and\n'
        ' ((os.environ["BIND_COMPENSATION_STAT_BOUNDARY"] == "restored" and args[-1] == str(pending)) or\n'
        '  (os.environ["BIND_COMPENSATION_STAT_BOUNDARY"] == "renamed" and\n'
        '   Path(args[-1]).parent == pending.parent and Path(args[-1]).name.startswith(".front-routes.retired."))))\n'
        'if selected and state["triggered"] and not marker.exists():\n marker.touch(); sys.exit(1)\n'
        'os.execv('+repr(delegate)+', ['+repr(delegate)+']+args)\n')
    facade.chmod(0o755)
    fixture.lab.env.update(BIND_COMPENSATION_STAT_MARKER=str(marker),
                           BIND_COMPENSATION_STAT_BOUNDARY=boundary)
    result = fixture.lab.run()
    assert json.loads(fixture.spec.read_text())['triggered'] is True and marker.exists(), result.stdout
    assert result.returncode == 1 and 'retirement compensation requires inspection' in result.stdout
    assert 'FRONT rollback completed; preexisting routes preserved.' in result.stdout
    assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == []
    assert not (fixture.lab.state / 'front-routes.pending').exists()
    retired = list(fixture.lab.state.glob('.front-routes.retired.*'))
    assert len(retired) == 1 and len(list(retired[0].iterdir())) == 9
    assert (retired[0] / 'progress').read_text() == '0 done\n'


@pytest.mark.parametrize('fault,message', [
    ('retirement-allocation-error', 'Unsafe journal retirement allocation'),
    ('retirement-compensation-sync-error', 'retirement compensation requires inspection'),
])
def test_retirement_allocation_or_repeated_sync_failure_preserves_the_complete_rollback(tmp_path, fault, message):
    """A second I/O failure cannot consume the only durable recovery directory."""
    fixture = UtilityFaultLab(tmp_path, fault)
    result = fixture.lab.run()
    state = json.loads(fixture.spec.read_text())
    assert state['triggered'] is True and result.returncode == 1 and message in result.stdout, result.stdout
    if fault == 'retirement-compensation-sync-error':
        assert state['state_sync_errors'] == 2
    assert 'FRONT rollback completed; preexisting routes preserved.' in result.stdout
    assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == []
    assert not (fixture.lab.state / 'front-routes.pending').exists()
    retired = list(fixture.lab.state.glob('.front-routes.retired.*'))
    assert len(retired) == 1 and len(list(retired[0].iterdir())) == 9
    assert (retired[0] / 'progress').read_text() == '0 done\n'


def test_an_entry_appearing_during_retirement_cannot_be_removed_or_authorize_an_inverse_write(tmp_path):
    """A late foreign entry preserves all nine files until explicit inspection."""
    fixture = UtilityFaultLab(tmp_path, 'retirement-unexpected-entry')
    result = fixture.lab.run()
    state = json.loads(fixture.spec.read_text())
    assert state['triggered'] is True, result.stdout
    assert result.returncode == 1 and 'Unexpected journal entry prevents cleanup' in result.stdout, result.stdout
    pending = fixture.lab.state / 'front-routes.pending'
    foreign = pending / 'operator-evidence'
    assert foreign.read_bytes() == b'operator late journal bytes\n'
    assert (foreign.stat().st_dev, foreign.stat().st_ino) == tuple(state['foreign_identity'])
    assert {p.name: [p.stat().st_ino, p.read_bytes().hex()] for p in pending.iterdir() if p != foreign} == state['journal_before_foreign_entry']
    assert len(fixture.lab.data['operations']) == 2 and fixture.lab.data['persistent']
    assert len(fixture.lab.data['runtime']) == len(BASE)+1
    # This synthetic operator owns the extra entry and explicitly removes it;
    # production recovery must never infer permission to do so automatically.
    foreign.unlink()
    recovered = fixture.lab.run('recover')
    assert recovered.returncode == 2, recovered.stdout
    assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == []
    assert not pending.exists()


@pytest.mark.parametrize('stage,shape', [('outbound', 'file'), ('outbound', 'directory'),
    ('outbound', 'symlink'), ('outbound', 'dangling-symlink'), ('restored', 'file'),
    ('restored', 'directory'), ('restored', 'symlink'), ('restored', 'dangling-symlink'),
    ('released', 'file'), ('released', 'dangling-symlink')])
def test_operator_replacement_after_a_native_rename_preserves_both_objects(tmp_path, stage, shape):
    """Real successful renames never authorize deletion of a later operator object."""
    fixture = UtilityFaultLab(tmp_path, 'retirement-foreign-transition')
    operator = tmp_path / 'operator-transition-target'
    operator.mkdir(mode=0o700)
    keep = operator / 'keep'
    keep.write_bytes(b'operator target bytes\n')
    spec = json.loads(fixture.spec.read_text())
    spec.update(transition_stage=stage, foreign_shape=shape,
                foreign_target=str(operator / 'missing') if shape == 'dangling-symlink' else str(operator))
    fixture.spec.write_text(json.dumps(spec))
    failed = fixture.lab.run()
    state = json.loads(fixture.spec.read_text())
    assert state['triggered'] is True and state['outbound_seen'] is True, failed.stdout
    assert failed.returncode == 1, failed.stdout
    foreign = fixture.lab.state / Path(state['foreign_path']).name
    assert (foreign.lstat().st_dev, foreign.lstat().st_ino) == tuple(state['foreign_identity'])
    if shape == 'file':
        assert foreign.read_bytes() == b'operator transition bytes\n'
    elif shape == 'directory':
        assert (foreign / 'operator-evidence').read_bytes() == b'operator transition bytes\n'
    else:
        assert foreign.is_symlink() and os.readlink(str(foreign)) == spec['foreign_target']
    assert keep.read_bytes() == b'operator target bytes\n'
    pending = fixture.lab.state / 'front-routes.pending'
    if stage == 'released':
        assert 'FRONT rollback completed; preexisting routes preserved.' in failed.stdout
        assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == [] and not pending.exists()
        complete = [p for p in fixture.lab.state.glob('.front-routes.retired.*') if p != foreign and p.is_dir()]
        assert len(complete) == 1 and len(list(complete[0].iterdir())) == 9
        assert (complete[0].stat().st_dev, complete[0].stat().st_ino) == tuple(state['original_identity'])
        assert (complete[0] / 'progress').read_text() == '0 done\n'
    else:
        assert len(fixture.lab.data['operations']) == 2, 'Conflicting journal identity cannot authorize an inverse'
        assert all(row in fixture.lab.data['runtime'] for row in BASE)
        assert fixture.lab.data['persistent'] and len(fixture.lab.data['runtime']) == len(BASE)+1
        held = fixture.lab.state / Path(state['held_path']).name
        assert {p.name: [p.stat().st_ino, p.read_bytes().hex()] for p in held.iterdir()} == state['held_journal']
        assert len(state['held_journal']) == 9
        # The synthetic operator explicitly resolves its own replacement;
        # automatic recovery must leave both its object and the owned data intact.
        if shape == 'directory':
            (foreign / 'operator-evidence').unlink()
            foreign.rmdir()
        else:
            foreign.unlink()
        if pending.exists():
            assert pending.is_dir() and not pending.is_symlink() and list(pending.iterdir()) == []
            pending.rmdir()
        held.rename(pending)
        recovered = fixture.lab.run('recover')
        assert recovered.returncode == 2, recovered.stdout
        assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == [] and not pending.exists()


def test_reservation_release_failure_retains_complete_rollback_evidence(tmp_path):
    fixture = UtilityFaultLab(tmp_path, 'retirement-empty-reservation-error')
    failed = fixture.lab.run()
    assert json.loads(fixture.spec.read_text())['triggered'] is True
    assert failed.returncode == 1 and 'FRONT rollback completed; preexisting routes preserved.' in failed.stdout
    assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == []
    assert not (fixture.lab.state / 'front-routes.pending').exists()
    retired = list(fixture.lab.state.glob('.front-routes.retired.*'))
    assert len(retired) == 2 and all(p.is_dir() and not p.is_symlink() for p in retired)
    complete = [p for p in retired if (p / 'meta').exists()]
    assert len(complete) == 1 and len(list(complete[0].iterdir())) == 9
    assert (complete[0] / 'progress').read_text() == '0 done\n'
    assert sum(not list(p.iterdir()) for p in retired) == 1


RULES = [
    ({'command': 'stat', 'prefix': ['-c', '%u'], 'target': 'parent'}, 'State ancestor owner read failed'),
    ({'command': 'stat', 'prefix': ['-c', '%u'], 'target': 'parent', 'returncode': 0, 'stdout': '1000\n'}, 'Untrusted state ancestor'),
    ({'command': 'stat', 'prefix': ['-c', '%a'], 'target': 'parent'}, 'State ancestor mode read failed'),
    ({'command': 'stat', 'prefix': ['-c', '%a'], 'target': 'parent', 'returncode': 0, 'stdout': '777\n'}, 'Writable state ancestor'),
    ({'command': 'stat', 'prefix': ['-c', '%a'], 'target': 'parent', 'returncode': 0, 'stdout': 'invalid\n'}, 'Writable state ancestor'),
    ({'command': 'mkdir', 'prefix': ['-m', '0700'], 'target': 'state'}, 'Cannot create private directory'),
    ({'command': 'stat', 'prefix': ['-c', '%u:%a'], 'target': 'state'}, 'Private state metadata read failed'),
    ({'command': 'stat', 'prefix': ['-c', '%s'], 'target': 'lock', 'skip_matches': 1}, 'Operation lock size read failed'),
    ({'command': 'stat', 'prefix': ['-c', '%s'], 'target': 'lock', 'skip_matches': 1, 'returncode': 0, 'stdout': '1\n'}, 'Operation lock file must remain empty'),
    ({'command': 'stat', 'prefix': ['-Lc', '%d:%i'], 'target': 'fd'}, 'Operation descriptor identity read failed'),
    ({'command': 'stat', 'prefix': ['-c', '%d:%i'], 'target': 'lock'}, 'Operation lock identity read failed'),
    ({'command': 'stat', 'prefix': ['-c', '%d:%i'], 'target': 'lock', 'returncode': 0, 'stdout': '999:999\n'}, 'Operation lock changed while opening'),
    ({'command': 'stat', 'prefix': ['-c', '%s'], 'target': 'out', 'returncode': 0, 'stdout': '1048577\n'}, 'Observation exceeds 1 MiB'),
    ({'command': 'stat', 'prefix': ['-c', '%s'], 'target': 'out', 'returncode': 0, 'stdout': 'invalid\n'}, 'Observation exceeds 1 MiB'),
    ({'command': 'tr', 'args': ['-d', '\\000']}, 'Observation byte validation failed'),
    ({'command': 'wc', 'args': ['-c'], 'skip_matches': 1}, 'Observation byte count failed'),
    ({'command': 'sort', 'args': [], 'skip_matches': 1}, 'Client journal normalization failed'),
]


@pytest.mark.parametrize('rule,message', RULES, ids=[
    message + (' (invalid metadata)' if rule.get('stdout') == 'invalid\n' else '') for rule, message in RULES])
def test_utility_refusal_cannot_publish_any_route(tmp_path, rule, message):
    fixture = UtilityFaultLab(tmp_path, rule)
    result = fixture.lab.run()
    assert json.loads(fixture.spec.read_text())['triggered'] is True, result.stdout
    assert result.returncode == 1 and message in result.stdout, result.stdout
    fixture.assert_unchanged_routes()
    assert not (fixture.lab.state / 'front-routes.pending').exists()
    assert not list(fixture.lab.state.glob('.route-work.*'))


DEEP_RULES = [
    ({'command': 'stat', 'prefix': ['-Lc', '%d:%i:%s'], 'target': 'fd'}, 'Operation descriptor metadata read failed'),
    ({'command': 'stat', 'prefix': ['-c', '%d:%i:%s'], 'target': 'lock'}, 'Operation lock metadata read failed'),
    ({'command': 'stat', 'prefix': ['-c', '%s'], 'target': 'lock', 'skip_matches': 1, 'progress': '0 intent'}, 'Operation lock size read failed'),
    ({'command': 'stat', 'prefix': ['-c', '%d:%i:%s'], 'target': 'lock', 'returncode': 0, 'stdout': '999:999:0\n'}, 'Transient operation lock changed'),
    ({'command': 'sort', 'args': [], 'progress': '2 done', 'skip_matches': 3}, 'Expected kernel route normalization failed'),
]


@pytest.mark.parametrize('rule,message', DEEP_RULES, ids=[message for _, message in DEEP_RULES])
def test_failure_inside_transaction_uses_guarded_rollback(tmp_path, rule, message):
    fixture = UtilityFaultLab(tmp_path, rule)
    result = fixture.lab.run()
    assert json.loads(fixture.spec.read_text())['triggered'] is True, result.stdout
    assert result.returncode == 1 and message in result.stdout, result.stdout
    assert fixture.lab.data['persistent'] == []
    from test_native_front_routes import BASE
    assert fixture.lab.data['runtime'] == BASE
    assert not (fixture.lab.state / 'front-routes.pending').exists()
    assert not list(fixture.lab.state.glob('.route-work.*'))


RECOVERY_RULES = [
    ({'command': 'stat', 'prefix': ['-c', '%u:%a:%h'], 'target': 'meta'}, 'Journal metadata read failed'),
    ({'command': 'stat', 'prefix': ['-c', '%u:%a:%h'], 'target': 'meta', 'returncode': 0, 'stdout': '0:644:1\n'}, 'Unsafe journal file'),
    ({'command': 'stat', 'prefix': ['-c', '%s'], 'target': 'meta'}, 'Journal size read failed'),
    ({'command': 'stat', 'prefix': ['-c', '%s'], 'target': 'meta', 'returncode': 0, 'stdout': '1048577\n'}, 'Oversized journal file'),
    ({'command': 'stat', 'prefix': ['-c', '%s'], 'target': 'meta', 'returncode': 0, 'stdout': 'invalid\n'}, 'Oversized journal file'),
    ({'command': 'stat', 'prefix': ['-c', '%s'], 'target': 'meta', 'skip_matches': 1}, 'Journal framing size read failed'),
    ({'command': 'stat', 'prefix': ['-c', '%s'], 'target': 'meta', 'skip_matches': 1, 'returncode': 0, 'stdout': '0\n'}, 'Malformed journal text framing'),
    ({'command': 'sort', 'args': [], 'skip_matches': 2}, 'Rollback persistent route normalization failed'),
    ({'command': 'sort', 'args': [], 'skip_matches': 3}, 'Rollback kernel route normalization failed'),
]


@pytest.mark.parametrize('rule,message', RECOVERY_RULES, ids=[message for _, message in RECOVERY_RULES])
def test_recovery_read_failure_preserves_ownership_until_a_later_recovery(tmp_path, rule, message):
    fixture = UtilityFaultLab(tmp_path, rule)
    fixture.set_rule_armed(False)
    delegate = Path(json.loads(fixture.spec.read_text())['delegates'][rule['command']])
    assert (tmp_path / 'bin' / rule['command']).resolve() == delegate.resolve()
    assert fixture.lab.run(more=['--defer-commit']).returncode == 2
    pending = fixture.lab.state / 'front-routes.pending'
    journal = {path.name: path.read_bytes() for path in pending.iterdir()}
    before = copy.deepcopy(fixture.lab.data)
    fixture.set_rule_armed(True)
    refused = fixture.lab.run('recover')
    assert json.loads(fixture.spec.read_text())['triggered'] is True, refused.stdout
    assert refused.returncode == 1 and message in refused.stdout, refused.stdout
    assert fixture.lab.data == before, 'An incomplete recovery observation cannot authorize deletion'
    assert {path.name: path.read_bytes() for path in pending.iterdir()} == journal
    assert not list(fixture.lab.state.glob('.route-work.*'))
    fixture.set_rule_armed(False)
    assert (tmp_path / 'bin' / rule['command']).resolve() == delegate.resolve()
    recovered = fixture.lab.run('recover')
    assert recovered.returncode == 2 and 'FRONT rollback completed' in recovered.stdout, recovered.stdout
    from test_native_front_routes import BASE
    assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == []
    assert not pending.exists()


@pytest.mark.parametrize('action,more,message', [
    ('recover', [], 'No pending FRONT transaction to recover'),
    ('apply', ['--unknown'], 'Unknown argument: --unknown'),
    ('invalid', [], 'Unsupported route action'),
    ('apply', ['--adm-ip', '198.51.100.2'], 'FRONT/BACK/ADM addresses must differ'),
    ('apply', ['--front-ip', '10.0.0.2'], 'FRONT/BACK/ADM addresses must differ'),
])
def test_invalid_public_action_cannot_publish_a_route(tmp_path, action, more, message):
    from test_native_front_routes import NativeRouteLab, BASE
    lab = NativeRouteLab(tmp_path)
    result = lab.run(action, more=more)
    assert result.returncode == 1 and message in result.stdout, result.stdout
    assert lab.data['runtime'] == BASE and lab.data['persistent'] == []
    assert lab.data['operations'] == []


def test_help_reports_the_actual_success_and_refusal_contract_without_writing(tmp_path):
    from test_native_front_routes import NativeRouteLab, BASE
    lab = NativeRouteLab(tmp_path)
    result = lab.run(more=['--help'])
    assert result.returncode == 0 and 'rc 0 unchanged, 2 changed/planned, 1 refused.' in result.stdout
    assert lab.data['runtime'] == BASE and lab.data['operations'] == []


def test_missing_transaction_token_cannot_take_ownership_of_another_journal(tmp_path):
    from test_native_front_routes import NativeRouteLab
    lab = NativeRouteLab(tmp_path)
    assert lab.run(more=['--defer-commit']).returncode == 2
    before = copy.deepcopy(lab.data)
    result = lab.run('rollback')
    assert result.returncode == 1 and 'Transaction token is required' in result.stdout
    assert lab.data == before and (lab.state / 'front-routes.pending').is_dir()


@pytest.mark.parametrize('stage', ['0 intent', '1 done', '2 done'])
@pytest.mark.parametrize('signame', ['SIGHUP', 'SIGINT', 'SIGTERM'])
def test_actual_signal_restores_each_durably_acknowledged_route(tmp_path, stage, signame):
    # Send a real signal after the selected directory sync has succeeded. No
    # timing assumption or synthetic trap invocation substitutes for interruption.
    fixture = UtilityFaultLab(tmp_path, dict(command='sync', prefix=['--'], target='pending',
        progress=stage, signal=signame))
    result = fixture.lab.run()
    assert json.loads(fixture.spec.read_text())['triggered'] is True
    assert result.returncode == 128 + getattr(signal, signame), result.stdout
    assert 'FRONT rollback completed; preexisting routes preserved.' in result.stdout
    assert 'FRONT recovery required:' not in result.stdout
    assert fixture.lab.data['runtime'] == BASE and fixture.lab.data['persistent'] == []
    assert not (fixture.lab.state / 'front-routes.pending').exists()
    assert not list(fixture.lab.state.glob('.route-work.*'))
    count = int(stage.split()[0])
    additions = fixture.lab.data['operations'][:count]
    removals = fixture.lab.data['operations'][count:]
    assert len(additions) == len(removals) == count
    assert all(operation['action'] == '+ipv4.routes' for operation in additions)
    assert all(operation['action'] == '-ipv4.routes' for operation in removals)
    assert [item['persistent'] for item in removals] == [item['persistent'] for item in reversed(additions)]

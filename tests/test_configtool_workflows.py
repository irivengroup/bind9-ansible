"""Exercise archive failures through direct launches of the public CLI.

The shared fixture owns all live paths in a disconnected container. Successful
restore and failed-validation restoration use the stronger existing scenarios
in test_native_archive_cli rather than duplicating them here.
"""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from configtool_lab import configtool_lab as _configtool_fixture
from test_native_archive_cli import archive_for_restore

configtool_lab = _configtool_fixture
pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
    reason='Requires an explicitly enabled disconnected clean Linux root')


def fingerprints(*paths):
    """Record exact bytes before a failed operation, independently of Bash."""
    return {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}


def restored_input(lab):
    return archive_for_restore(lab.invoke, lab.root)


def assert_original(lab):
    assert lab.configuration.read_text() == 'original configuration\n'
    assert (lab.zones / 'original.zone').read_text() == 'original zone\n'


def fixed_backup_name(lab):
    """Make a genuine identifier collision reproducible without replacing TAR."""
    lab.command('date', '[[ "$*" == "-u +%Y%m%dT%H%M%SZ" ]] && { printf "20260101T000000Z\\n"; exit 0; }\n'
        'exec /usr/bin/date "$@"\n')
    lab.command('od', 'printf " aabbccdd\\n"\n')
    return lab.root / 'named-config-20260101T000000Z-aabbccdd.tar.gz'


class TestConfigtoolWorkflows:
    """Use real archives and filesystem effects; inject only named CLI faults."""

    def test_public_cli_refuses_a_real_unprivileged_process_before_opening_backup_state(self):
        from canonical_shell_lab import UnprivilegedProcess
        source = Path(__file__).resolve().parents[1] / 'roles/bind9/files/bind-configtool.sh'
        result = UnprivilegedProcess.run(['/bin/bash', str(source), 'list'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=10)
        assert result.returncode == 1 and result.stdout == '' and 'Root required' in result.stderr

    @pytest.mark.parametrize('boundary', ['capture', 'backup', 'restore-work', 'restore-safety'])
    def test_successful_temporary_path_cannot_authorize_foreign_data_mutation(self, configtool_lab, boundary):
        """A successful allocator response is data, never ownership evidence."""
        lab = configtool_lab
        archive = restored_input(lab)
        foreign = lab.folder / 'operator-object'
        if boundary in ('capture', 'restore-work'):
            foreign.mkdir(mode=0o700)
            for name in ('out', 'err', 'keep'):
                (foreign / name).write_text('operator '+name+' bytes\n')
            before = fingerprints(*foreign.iterdir())
        else:
            foreign.write_text('operator archive bytes\n')
            foreign.chmod(0o600)
            before = fingerprints(foreign)
        pattern = {'capture': '.verify.', 'backup': '.named-backup.',
                   'restore-work': '.restore-state.', 'restore-safety': '.restore-safety.'}[boundary]
        reached = lab.folder / 'allocator-response-observed'
        lab.command('mktemp', 'if [[ ${@: -1} == "$BIND_BACKUP_ROOT"/"$BIND_TEMP_PREFIX"XXXXXXXX ]]; then\n'
            '  : > "$BIND_TEMP_REACHED"\n  printf "%s\\n" "$BIND_TEMP_FOREIGN"; exit 0\nfi\n'
            'exec /usr/bin/mktemp "$@"\n')
        arguments = ['restore', '--archive', archive.name] if boundary.startswith('restore-') else [
            'list' if boundary == 'capture' else 'backup']
        result = lab.invoke(*arguments, BIND_BACKUP_ROOT=str(lab.root), BIND_TEMP_PREFIX=pattern,
            BIND_TEMP_FOREIGN=str(foreign), BIND_TEMP_REACHED=str(reached))
        assert reached.exists(), 'The public operation must reach the selected allocator'
        current = fingerprints(*[p for p in foreign.rglob('*') if p.is_file()]) if foreign.is_dir() else fingerprints(foreign)
        assert current == before, 'Refused temporary paths must preserve every operator byte'
        assert result.returncode != 0 and 'RESTORED ' not in result.stdout
        assert_original(lab)

    @pytest.mark.parametrize('boundary,shape', [
        (boundary, shape) for boundary in ('capture', 'backup')
        for shape in ('mode', 'owner', 'link', 'missing', 'suffix', 'stat-error')
    ] + [('capture', shape) for shape in ('hidden', 'double-dot', 'dangling')] + [
        ('backup', 'content'), ('backup', 'allocation-error'), ('backup', 'content-stat-error')])
    def test_private_temporary_object_must_match_its_allocation_contract(self, configtool_lab, boundary, shape):
        """Reject unsafe objects even inside the expected private namespace."""
        lab = configtool_lab
        restored_input(lab)
        is_directory = boundary == 'capture'
        prefix = '.verify.' if is_directory else '.named-backup.'
        candidate = lab.root / (prefix + ('abcdefghi' if shape == 'suffix' else 'abcdefgh'))
        operator = lab.folder / 'operator-data'
        operator.write_text('preserve operator data\n')
        operator.chmod(0o600)
        if shape == 'link':
            candidate.symlink_to(operator)
        elif shape != 'missing':
            if is_directory:
                candidate.mkdir(mode=0o700)
                if shape in ('hidden', 'double-dot'):
                    (candidate / ('.hidden' if shape == 'hidden' else '..hidden')).write_text('preserve hidden data\n')
                elif shape == 'dangling':
                    (candidate / '.orphan').symlink_to(lab.folder / 'missing-target')
            else:
                candidate.write_bytes(b'preserve existing archive\n' if shape == 'content' else b'')
                candidate.chmod(0o600)
            if shape == 'mode':
                candidate.chmod(0o750 if is_directory else 0o640)
            elif shape == 'owner':
                os.chown(str(candidate), 1000, 1000)

        def snapshot():
            paths = [operator, candidate] + (list(candidate.rglob('*')) if is_directory and not candidate.is_symlink() else [])
            values = {}
            for path in paths:
                if not path.exists() and not path.is_symlink():
                    values[str(path)] = None
                    continue
                stat = path.lstat()
                values[str(path)] = (stat.st_mode, stat.st_uid, stat.st_gid, stat.st_nlink,
                    os.readlink(str(path)) if path.is_symlink() else path.read_bytes() if path.is_file() else None)
            return values

        before = snapshot()
        reached = lab.folder / 'temporary-object-reached'
        lab.command('mktemp', 'if [[ ${@: -1} == "$BIND_BACKUP_ROOT"/"$BIND_TEMP_PREFIX"XXXXXXXX ]]; then\n'
            '  : > "$BIND_TEMP_REACHED"\n  printf "%s\\n" "$BIND_TEMP_OBJECT"; exit "$BIND_TEMP_ALLOCATION_RC"\nfi\n'
            'exec /usr/bin/mktemp "$@"\n')
        failed = lab.folder / 'temporary-stat-failed'
        if shape in ('stat-error', 'content-stat-error'):
            # A plausible metadata prefix cannot conceal its failing producer.
            format_guard = ' && $2 == %a:%s' if shape == 'content-stat-error' else ''
            lab.command('stat', 'if [[ ${@: -1} == "$BIND_TEMP_OBJECT"' + format_guard + ' ]]; then\n'
                '  : > "$BIND_TEMP_STAT_FAILED"\n  /usr/bin/stat "$@"; exit 1\nfi\n'
                'exec /usr/bin/stat "$@"\n')
        result = lab.invoke('list' if is_directory else 'backup',
            BIND_BACKUP_ROOT=str(lab.root), BIND_TEMP_PREFIX=prefix,
            BIND_TEMP_OBJECT=str(candidate), BIND_TEMP_REACHED=str(reached), BIND_TEMP_STAT_FAILED=str(failed),
            BIND_TEMP_ALLOCATION_RC='1' if shape == 'allocation-error' else '0')
        assert reached.exists(), 'The selected allocation must be observed'
        if shape in ('stat-error', 'content-stat-error'):
            assert failed.exists(), 'Metadata refusal must reach the actual temporary object'
        assert snapshot() == before, 'Refused objects retain their type, permissions, owner and bytes'
        assert result.returncode != 0
        assert_original(lab)

    @pytest.mark.parametrize('boundary', ['ancestor', 'private-file'])
    @pytest.mark.parametrize('response', ['0+0', '0+BASH_VERSINFO[$(touch${IFS}${BIND_MODE_MARKER})]'])
    def test_native_permission_observation_is_literal_octal_data(self, configtool_lab, boundary, response):
        lab = configtool_lab
        marker = lab.folder / 'unexpected-mode-execution'
        field = '%u %a' if boundary == 'ancestor' else '%u %a %h'
        record = '0 %s\\n' if boundary == 'ancestor' else '0 %s 1\\n'
        lab.command('stat', 'if [[ $1 == -c && $2 == \'' + field + '\' ]]; then\n'
            "  printf '" + record + "' \"$BIND_MODE_RESPONSE\"; exit 0\nfi\n"
            'exec /usr/bin/stat "$@"\n')
        result = lab.invoke('list', BIND_MODE_RESPONSE=response, BIND_MODE_MARKER=str(marker))
        assert not marker.exists(), 'Native mode text must never execute as arithmetic code'
        assert result.returncode != 0, 'A successful producer cannot authorize a non-octal mode'
        if boundary == 'ancestor':
            assert not lab.root.exists()
        assert_original(lab)

    @pytest.mark.parametrize('boundary', ['ancestor', 'private-file'])
    @pytest.mark.parametrize('shape', ['padding', 'second-valid', 'second-invalid'])
    def test_native_metadata_cannot_hide_extra_records_after_a_valid_prefix(self, configtool_lab, boundary, shape):
        """A successful stat result must describe one complete metadata record."""
        lab = configtool_lab
        lab.root.mkdir(parents=True, mode=0o700)
        target = lab.root if boundary == 'ancestor' else lab.root / '.lock'
        field = '%u %a' if boundary == 'ancestor' else '%u %a %h'
        reached = lab.folder / 'malformed-metadata-observed'
        lab.command('stat', 'if [[ $1 == -c && $2 == "$BIND_METADATA_FIELD" && ${@: -1} == "$BIND_METADATA_TARGET" ]]; then\n'
            '  value=$(/usr/bin/stat "$@") || exit $?\n'
            '  : > "$BIND_METADATA_REACHED"\n'
            '  case $BIND_METADATA_SHAPE in\n'
            '    padding) printf " %s \\n" "$value";;\n'
            '    second-valid) printf "%s\\n%s\\n" "$value" "$value";;\n'
            '    second-invalid) printf "%s\\nuntrusted trailing record\\n" "$value";;\n'
            '  esac\n  exit 0\nfi\nexec /usr/bin/stat "$@"\n')
        result = lab.invoke('list', BIND_METADATA_TARGET=str(target), BIND_METADATA_FIELD=field,
            BIND_METADATA_SHAPE=shape, BIND_METADATA_REACHED=str(reached))
        assert reached.exists(), 'The successful malformed producer must reach the selected native boundary'
        assert result.returncode != 0, 'A valid first line must not authorize malformed complete metadata'
        assert result.stdout == '' and not list(lab.root.glob('.verify.*'))
        assert_original(lab)

    @pytest.mark.parametrize('action', ['backup', 'list'])
    def test_archive_option_is_accepted_only_for_restore(self, configtool_lab, action):
        lab = configtool_lab
        result = lab.invoke(action, '--archive', 'named-config-20000101T000000Z-abcdef01.tar.gz')
        assert result.returncode == 2 and result.stderr.startswith('usage: bind-configtool ')
        assert not lab.root.exists()
        assert_original(lab)

    @pytest.mark.parametrize('fault', ['compressed-size', 'checksum-size', 'checksum-record',
        'checksum-mismatch', 'checksum-read', 'checksum-producer', 'gzip', 'gzip-decompression-producer', 'tar-details',
        'empty-catalog-details', 'duplicate-catalog', 'capture-counter', 'capture-cleanup',
        'checksum-name-producer', 'compressed-size-invalid', 'checksum-size-invalid',
        'tar-details-size-invalid', 'archive-link', 'archive-foreign-owner',
        'path-sort', 'failed-diagnostic', 'capture-rmdir', 'checksum-name',
        'checksum-link', 'checksum-hardlink', 'checksum-open-mode',
        'missing-details', 'extra-details', 'gzip-size-empty', 'gzip-size-header-only',
        'gzip-size-missing-header', 'gzip-size-extra', 'gzip-size-invalid',
        'gzip-size-leading-zero', 'gzip-size-overflow', 'tar-byte-expression',
        'tar-byte-oversize', 'tar-tail-expression'])
    def test_archive_observation_failures_preserve_inputs_and_live_data(self, configtool_lab, fault):
        lab = configtool_lab
        archive = restored_input(lab)
        checksum = Path(str(archive) + '.sha256')
        if fault in ('compressed-size', 'checksum-size', 'checksum-read',
                     'compressed-size-invalid', 'checksum-size-invalid'):
            suffix = '*.tar.gz' if fault.startswith('compressed-size') else '*.sha256'
            action = ('printf "1140860929\\n"' if fault == 'compressed-size' else
                'printf "513\\n"' if fault == 'checksum-size' else
                'printf "not-a-size\\n"' if fault.endswith('-invalid') else 'exit 1')
            lab.command('stat', 'if [[ $1 == -c && $2 == %s && ${@: -1} == ' + suffix + ' ]]; then\n'
                '  ' + action + '; exit 0\nfi\nexec /usr/bin/stat "$@"\n')
        elif fault in ('checksum-record', 'checksum-mismatch'):
            checksum.write_text(('invalid' if fault == 'checksum-record' else '0' * 64) +
                '  ' + archive.name + '\n', encoding='ascii')
        elif fault == 'checksum-name':
            checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() +
                '  different-operator-archive.tar.gz\n', encoding='ascii')
        elif fault == 'checksum-link':
            saved = lab.folder / 'operator-checksum'
            checksum.rename(saved)
            checksum.symlink_to(saved)
        elif fault == 'checksum-hardlink':
            saved = lab.folder / 'operator-checksum'
            os.link(str(checksum), str(saved))
        elif fault == 'checksum-open-mode':
            checksum.chmod(0o640)
        elif fault in ('checksum-producer', 'gzip'):
            command = 'sha256sum' if fault == 'checksum-producer' else 'gzip'
            lab.command(command, 'exit 1\n')
        elif fault == 'gzip-decompression-producer':
            # A complete valid stream cannot authorize success when its
            # producer fails. Only the first expansion fails; later checks
            # would succeed if the first pipeline status were ignored.
            lab.command('gzip', 'if [[ $1 == -cd && ! -e $BIND_GZIP_DECOMPRESSION ]]; then\n'
                '  /usr/bin/gzip "$@" || exit $?\n'
                '  printf "reached\\n" > "$BIND_GZIP_DECOMPRESSION"; exit 1\nfi\n'
                'exec /usr/bin/gzip "$@"\n')
        elif fault.startswith('gzip-size-'):
            # A successful metadata producer remains untrusted. The refusal
            # must leave the archive, checksum and all live data byte-identical.
            lab.command('gzip', 'if [[ $1 == --list ]]; then\n'
                '  : > "$BIND_GZIP_OBSERVED"\n'
                '  printf "%s" "$BIND_GZIP_OBSERVATION"; exit 0\nfi\n'
                'exec /usr/bin/gzip "$@"\n')
        elif fault in ('tar-byte-expression', 'tar-byte-oversize', 'tar-tail-expression'):
            # Consume the genuine stream with wc, then inject one abnormal
            # observation. Counter text must never be evaluated as arithmetic.
            lab.command('wc', 'observed=$(/usr/bin/wc "$@") || exit $?\n'
                'if [[ $observed == "$BIND_WC_TRIGGER" ]]; then\n'
                '  printf "%s\\n" "$observed" > "$BIND_WC_OBSERVED"\n'
                '  printf "%s\\n" "$BIND_WC_OUTPUT"\nelse\n'
                '  printf "%s\\n" "$observed"\nfi\n')
        elif fault == 'checksum-name-producer':
            lab.command('basename', '/usr/bin/basename "$@"\nexit 1\n')
            # Restore must refuse the failed identity observation before it can
            # overwrite operator changes, even though the printed name is valid.
            lab.configuration.write_text('operator configuration\n', encoding='ascii')
            (lab.zones / 'original.zone').write_text('operator zone\n', encoding='ascii')
        elif fault == 'tar-details':
            lab.command('tar', '[[ " $* " == *" --verbose "* ]] && exit 1\nexec /usr/bin/tar "$@"\n')
        elif fault == 'tar-details-size-invalid':
            lab.command('tar', 'if [[ $1 == --list && " $* " == *" --verbose "* ]]; then\n'
                '  printf "%s\\n" "-rw------- 0/0 not-a-size 2000-01-01 00:00 etc/named.conf"; exit 0\n'
                'fi\nexec /usr/bin/tar "$@"\n')
        elif fault in ('missing-details', 'extra-details'):
            # Retain the genuine native metadata, then omit or repeat one
            # actual record. Both successful observations must describe the
            # same number of members before the archive can be trusted.
            lab.command('tar', 'if [[ $1 == --list && " $* " == *" --verbose "* ]]; then\n'
                '  /usr/bin/tar "$@" > "$BIND_DETAILS_ROWS" || exit $?\n'
                '  : > "$BIND_DETAILS_REACHED"\n'
                '  if [[ $BIND_DETAILS_KIND == missing-details ]]; then\n'
                '    exec /usr/bin/tail -n +2 -- "$BIND_DETAILS_ROWS"\nfi\n'
                '  /usr/bin/cat -- "$BIND_DETAILS_ROWS" || exit $?\n'
                '  exec /usr/bin/head -n 1 -- "$BIND_DETAILS_ROWS"\nfi\n'
                'exec /usr/bin/tar "$@"\n')
        elif fault == 'archive-link':
            saved = lab.folder / 'operator-archive'
            archive.rename(saved)
            archive.symlink_to(saved)
        elif fault == 'archive-foreign-owner':
            os.chown(str(archive), 1000, -1)
        elif fault == 'path-sort':
            # The initial archive catalog uses -r; fail only the later member
            # normalization so a partial path observation cannot authorize use.
            lab.command('sort', '[[ $# == 0 ]] && exit 1\nexec /usr/bin/sort "$@"\n')
        elif fault == 'failed-diagnostic':
            lab.command('tar', '[[ $1 == --list ]] && exit 1\nexec /usr/bin/tar "$@"\n')
            lab.command('head', '[[ $1 == -c && $2 == 2048 ]] && exit 1\nexec /usr/bin/head "$@"\n')
        elif fault == 'empty-catalog-details':
            lab.command('tar', 'if [[ $1 == --list && " $* " != *" --verbose "* ]]; then exit 0; fi\n'
                'exec /usr/bin/tar "$@"\n')
        elif fault == 'capture-counter':
            lab.command('tr', '[[ -e $BIND_TR_SEEN ]] && exit 1\n: > "$BIND_TR_SEEN"\nexec /usr/bin/tr "$@"\n')
        elif fault == 'capture-cleanup':
            lab.command('rm', '[[ ${@: -1} == */.verify.*/err ]] && exit 1\nexec /usr/bin/rm "$@"\n')
        elif fault == 'capture-rmdir':
            lab.command('rmdir', '[[ ${@: -1} == */.verify.* ]] && exit 1\nexec /usr/bin/rmdir "$@"\n')
        else:
            lab.command('uniq', 'exit 1\n')
        before = fingerprints(archive, checksum, lab.configuration, lab.zones / 'original.zone')
        arguments = ('restore', '--archive', archive.name) if fault == 'checksum-name-producer' else ('list',)
        gzip_observations = {'gzip-size-empty': '', 'gzip-size-header-only': 'header\n',
            'gzip-size-missing-header': ' 1 10240 0% stdin\n',
            'gzip-size-extra': 'header\n 1 10240 0% stdin\n 1 10240 0% stdin\n',
            'gzip-size-invalid': 'header\n 1 invalid 0% stdin\n',
            'gzip-size-leading-zero': 'header\n 1 010240 0% stdin\n',
            'gzip-size-overflow': 'header\n 1 4294967296 0% stdin\n'}
        wc_observations = {'tar-byte-expression': ('10240', '10240+0'),
            'tar-byte-oversize': ('10240', '1140860929'), 'tar-tail-expression': ('0', '1-1')}
        wc_trigger, wc_output = wc_observations.get(fault, ('', ''))
        result = lab.invoke(*arguments, BIND_TR_SEEN=str(lab.folder / 'tr-seen'),
            BIND_DETAILS_KIND=fault, BIND_DETAILS_ROWS=str(lab.folder / 'native-details'),
            BIND_DETAILS_REACHED=str(lab.folder / 'details-observed'),
            BIND_GZIP_OBSERVED=str(lab.folder / 'gzip-observed'),
            BIND_GZIP_DECOMPRESSION=str(lab.folder / 'gzip-decompression'),
            BIND_GZIP_OBSERVATION=gzip_observations.get(fault, ''),
            BIND_WC_TRIGGER=wc_trigger, BIND_WC_OUTPUT=wc_output,
            BIND_WC_OBSERVED=str(lab.folder / 'wc-observed'))
        if fault == 'gzip-decompression-producer':
            assert (lab.folder / 'gzip-decompression').read_text() == 'reached\n'
        if fault.startswith('gzip-size-'):
            assert (lab.folder / 'gzip-observed').exists()
        if fault in wc_observations:
            assert (lab.folder / 'wc-observed').read_text() == wc_trigger + '\n'
        if fault in ('missing-details', 'extra-details'):
            assert (lab.folder / 'details-observed').exists(), 'The native detailed listing must be observed'
        assert result.returncode != 0 and result.stdout == ''
        assert fingerprints(archive, checksum, lab.configuration, lab.zones / 'original.zone') == before
        if fault == 'archive-link':
            assert archive.is_symlink() and saved.is_file()
        elif fault == 'checksum-link':
            assert checksum.is_symlink() and saved.is_file()
        elif fault == 'checksum-hardlink':
            assert checksum.stat().st_nlink == 2 and os.path.samefile(str(checksum), str(saved))
        elif fault == 'checksum-open-mode':
            assert checksum.stat().st_mode & 0o777 == 0o640
        assert bool(list(lab.root.glob('.verify.*'))) is (fault in ('capture-cleanup', 'capture-rmdir'))
        if fault == 'capture-rmdir':
            assert all(not list(path.iterdir()) for path in lab.root.glob('.verify.*'))

    def test_existing_backup_name_is_never_overwritten_on_identifier_collision(self, configtool_lab):
        lab = configtool_lab
        lab.root.mkdir(parents=True, mode=0o700)
        existing = fixed_backup_name(lab)
        existing.write_bytes(b'prior operator archive')
        before = fingerprints(existing)
        result = lab.invoke('backup')
        assert result.returncode != 0 and 'Backup archive already exists' in result.stderr
        assert fingerprints(existing) == before and not list(lab.root.glob('.named-backup.*'))
        assert_original(lab)

    @pytest.mark.parametrize('shape', ['archive-dangling', 'checksum-file', 'checksum-link',
                                     'checksum-dangling', 'checksum-directory'])
    def test_backup_collision_preserves_every_operator_object_before_publication(self, configtool_lab, shape):
        """A missing archive never authorizes truncating an existing checksum."""
        lab = configtool_lab
        lab.root.mkdir(parents=True, mode=0o700)
        archive = fixed_backup_name(lab)
        checksum = Path(str(archive) + '.sha256')
        operator = lab.folder / 'operator-data'
        operator.write_bytes(b'operator bytes must survive\n')
        operator.chmod(0o600)
        missing = lab.folder / 'operator-absent'
        candidate = archive if shape.startswith('archive-') else checksum
        if shape.endswith('dangling'):
            candidate.symlink_to(missing)
        elif shape.endswith('link'):
            candidate.symlink_to(operator)
        elif shape.endswith('directory'):
            candidate.mkdir(mode=0o700)
        else:
            candidate.write_bytes(b'operator checksum must survive\n')
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        identity = tuple(getattr(candidate.lstat(), field) for field in fields)
        link = os.readlink(str(candidate)) if candidate.is_symlink() else None
        before = fingerprints(operator, *([candidate] if shape == 'checksum-file' else []))
        result = lab.invoke('backup')
        assert fingerprints(*[Path(name) for name in before]) == before, 'Operator bytes changed before backup refusal'
        assert tuple(getattr(candidate.lstat(), field) for field in fields) == identity, 'Collision metadata changed'
        if link is not None:
            assert candidate.is_symlink() and os.readlink(str(candidate)) == link
        assert not missing.exists(), 'A dangling checksum link must not create an operator target'
        assert result.returncode != 0 and 'BACKUP_CREATED ' not in result.stdout
        other = checksum if candidate == archive else archive
        assert not os.path.lexists(str(other)), 'A collision must not leave a new half-published backup'
        assert not list(lab.root.glob('.named-backup.*'))
        assert_original(lab)

    @pytest.mark.parametrize('destination', ['archive', 'checksum'])
    @pytest.mark.parametrize('shape', ['file', 'link', 'directory'])
    def test_late_backup_collision_is_atomic_and_rolls_back_only_owned_names(self, configtool_lab, destination, shape):
        """Create a real conflicting object exactly at the publication boundary."""
        lab = configtool_lab
        archive = fixed_backup_name(lab)
        checksum = Path(str(archive) + '.sha256')
        candidate = archive if destination == 'archive' else checksum
        operator = lab.folder / 'operator-data'
        operator.write_bytes(b'operator bytes must survive\n')
        reached = lab.folder / 'late-collision-observed'
        lab.command('ln', 'if [[ ${@: -1} == "$BIND_COLLISION_TARGET" && ! -e $BIND_COLLISION_REACHED ]]; then\n'
            '  : > "$BIND_COLLISION_REACHED"\n'
            '  case $BIND_COLLISION_SHAPE in\n'
            '    file) printf "operator collision bytes\\n" > "$BIND_COLLISION_TARGET";;\n'
            '    link) /usr/bin/ln -s -- "$BIND_COLLISION_OPERATOR" "$BIND_COLLISION_TARGET";;\n'
            '    directory) /usr/bin/mkdir -- "$BIND_COLLISION_TARGET";;\n'
            '  esac\nfi\nexec /usr/bin/ln "$@"\n')
        result = lab.invoke('backup', BIND_COLLISION_TARGET=str(candidate), BIND_COLLISION_REACHED=str(reached),
            BIND_COLLISION_SHAPE=shape, BIND_COLLISION_OPERATOR=str(operator))
        assert reached.exists(), 'The conflicting object must appear after the initial absence check'
        assert result.returncode != 0 and 'BACKUP_CREATED ' not in result.stdout
        assert operator.read_bytes() == b'operator bytes must survive\n'
        if shape == 'file':
            assert candidate.read_bytes() == b'operator collision bytes\n'
        elif shape == 'link':
            assert candidate.is_symlink() and os.readlink(str(candidate)) == str(operator)
        else:
            assert candidate.is_dir() and not list(candidate.iterdir()), 'Never publish inside a conflicting directory'
        other = checksum if destination == 'archive' else archive
        assert not os.path.lexists(str(other)), 'Rollback must remove only its own half-published pair'
        assert not list(lab.root.glob('.named-backup.*'))
        assert_original(lab)

    @pytest.mark.parametrize('destination', ['archive', 'checksum'])
    def test_failed_link_status_after_publication_still_rolls_back_its_own_inode(self, configtool_lab, destination):
        lab = configtool_lab
        archive = fixed_backup_name(lab)
        failed = archive if destination == 'archive' else Path(str(archive) + '.sha256')
        reached = lab.folder / 'published-before-link-failure'
        lab.command('ln', '/usr/bin/ln "$@" || exit $?\n'
            'if [[ ${@: -1} == "$BIND_LINK_FAIL_TARGET" ]]; then : > "$BIND_LINK_FAIL_REACHED"; exit 1; fi\n')
        result = lab.invoke('backup', BIND_LINK_FAIL_TARGET=str(failed), BIND_LINK_FAIL_REACHED=str(reached))
        assert reached.exists(), 'The failing producer must have really published its link first'
        assert result.returncode != 0 and 'BACKUP_CREATED ' not in result.stdout
        assert not list(lab.root.glob('named-config-*')) and not list(lab.root.glob('.named-backup.*'))
        assert_original(lab)

    def test_failed_backup_publication_never_removes_an_externally_replaced_archive(self, configtool_lab):
        lab = configtool_lab
        archive = fixed_backup_name(lab)
        checksum = Path(str(archive) + '.sha256')
        replacement = lab.folder / 'operator-replacement'
        replacement.write_bytes(b'operator replacement archive\n')
        reached = lab.folder / 'archive-replaced'
        lab.command('ln', 'if [[ ${@: -1} == "$BIND_LATE_CHECKSUM" ]]; then\n'
            '  /usr/bin/mv -T -- "$BIND_REPLACEMENT" "$BIND_LATE_ARCHIVE" || exit 1\n'
            '  printf "operator checksum\\n" > "$BIND_LATE_CHECKSUM"\n'
            '  : > "$BIND_REPLACEMENT_REACHED"\nfi\nexec /usr/bin/ln "$@"\n')
        result = lab.invoke('backup', BIND_LATE_ARCHIVE=str(archive), BIND_LATE_CHECKSUM=str(checksum),
            BIND_REPLACEMENT=str(replacement), BIND_REPLACEMENT_REACHED=str(reached))
        assert reached.exists() and result.returncode != 0
        assert archive.read_bytes() == b'operator replacement archive\n' and checksum.read_bytes() == b'operator checksum\n'
        assert not list(lab.root.glob('.named-backup.*')) and 'BACKUP_CREATED ' not in result.stdout
        assert_original(lab)

    def test_failed_backup_rollback_retains_private_inode_references(self, configtool_lab):
        lab = configtool_lab
        archive = fixed_backup_name(lab)
        checksum = Path(str(archive) + '.sha256')
        reached = lab.folder / 'rollback-removal-failed'
        lab.command('ln', 'if [[ ${@: -1} == "$BIND_LATE_CHECKSUM" ]]; then\n'
            '  printf "operator checksum\\n" > "$BIND_LATE_CHECKSUM"\nfi\nexec /usr/bin/ln "$@"\n')
        lab.command('rm', 'if [[ ${@: -1} == "$BIND_LATE_ARCHIVE" ]]; then\n'
            '  : > "$BIND_ROLLBACK_REACHED"; exit 1\nfi\nexec /usr/bin/rm "$@"\n')
        result = lab.invoke('backup', BIND_LATE_ARCHIVE=str(archive), BIND_LATE_CHECKSUM=str(checksum),
            BIND_ROLLBACK_REACHED=str(reached))
        assert reached.exists() and result.returncode != 0 and 'Backup publication recovery required' in result.stderr
        assert checksum.read_bytes() == b'operator checksum\n'
        private = list(lab.root.glob('.named-backup.*'))
        assert len(private) == 2 and sum(os.path.samefile(str(path), str(archive)) for path in private) == 1
        assert 'BACKUP_CREATED ' not in result.stdout
        assert_original(lab)

    def test_failed_second_backup_allocation_removes_only_its_first_private_file(self, configtool_lab):
        lab = configtool_lab
        reached = lab.folder / 'first-backup-allocation'
        failed = lab.folder / 'second-backup-allocation-failed'
        lab.command('mktemp', 'if [[ ${@: -1} == */.named-backup.XXXXXXXX ]]; then\n'
            '  if [[ -e $BIND_FIRST_ALLOCATION ]]; then : > "$BIND_SECOND_ALLOCATION"; exit 1; fi\n'
            '  : > "$BIND_FIRST_ALLOCATION"\nfi\nexec /usr/bin/mktemp "$@"\n')
        result = lab.invoke('backup', BIND_FIRST_ALLOCATION=str(reached), BIND_SECOND_ALLOCATION=str(failed))
        assert reached.exists() and failed.exists() and result.returncode != 0
        assert not list(lab.root.glob('.named-backup.*')) and not list(lab.root.glob('named-config-*'))
        assert_original(lab)

    def test_native_checksum_write_refusal_cannot_publish_a_backup_pair(self, configtool_lab):
        """Exercise an actual kernel permission failure, with UID still root."""
        from canonical_shell_lab import RestrictedRootExec
        lab = configtool_lab
        reached = lab.folder / 'checksum-made-readonly'
        allocation = lab.folder / 'checksum-allocation'
        lab.command('mktemp', 'candidate=$(/usr/bin/mktemp "$@") || exit $?\n'
            'if [[ ${@: -1} == */.named-backup.XXXXXXXX ]]; then\n'
            '  printf "%s\\n" "$candidate" > "$BIND_CHECKSUM_ALLOCATION"\nfi\n'
            'printf "%s\\n" "$candidate"\n')
        lab.command('sha256sum', '/usr/bin/sha256sum "$@" || exit $?\n'
            'if [[ $# == 0 && -e $BIND_CHECKSUM_ALLOCATION ]]; then\n'
            '  IFS= read -r candidate < "$BIND_CHECKSUM_ALLOCATION"\n'
            '  /usr/bin/chmod 0400 -- "$candidate" || exit $?\n'
            '  : > "$BIND_CHECKSUM_READONLY"\nfi\n')
        # Linux otherwise grants a root child fresh DAC override capabilities
        # at exec. This existing adapter suppresses that uplift, preserving UID
        # and restoring the parent's original policy in every outcome.
        with RestrictedRootExec():
            result = lab.invoke('backup', BIND_CHECKSUM_ALLOCATION=str(allocation),
                                BIND_CHECKSUM_READONLY=str(reached))
        assert reached.exists(), 'The checksum inode must change only after native hashing succeeded'
        assert result.returncode != 0 and 'Permission denied' in result.stderr, result.stderr
        assert 'BACKUP_CREATED ' not in result.stdout
        assert not list(lab.root.glob('named-config-*')) and not list(lab.root.glob('.named-backup.*'))
        assert_original(lab)

    @pytest.mark.parametrize('output,code', [('0'*64 + '  -', 1), ('malformed', 0), ('', 0)])
    def test_backup_checksum_must_be_complete_and_its_producer_successful(self, configtool_lab, output, code):
        lab = configtool_lab
        reached = lab.folder / 'checksum-producer-observed'
        lab.command('sha256sum', ': > "$BIND_CHECKSUM_REACHED"\nprintf "%s\\n" "$BIND_CHECKSUM_OUTPUT"\nexit "$BIND_CHECKSUM_RC"\n')
        result = lab.invoke('backup', BIND_CHECKSUM_REACHED=str(reached), BIND_CHECKSUM_OUTPUT=output, BIND_CHECKSUM_RC=str(code))
        assert reached.exists() and result.returncode != 0 and 'BACKUP_CREATED ' not in result.stdout
        assert not list(lab.root.glob('named-config-*')) and not list(lab.root.glob('.named-backup.*'))
        assert_original(lab)

    @pytest.mark.parametrize('leaf', [pytest.param('valid backup root', id='spaces'),
        pytest.param('valid\\backup root', id='backslash'), pytest.param('valid\tbackup root', id='tab')])
    def test_literal_backup_roots_keep_valid_checksums_when_native_names_need_escaping(self, configtool_lab, leaf):
        """Filename escaping in native digest output must not change its hash."""
        lab = configtool_lab
        root = lab.folder / leaf
        result = lab.invoke('backup', root=root)
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith('BACKUP_CREATED ')
        archive = Path(result.stdout[len('BACKUP_CREATED '):].rstrip('\n'))
        assert archive.parent == root and archive.is_file()
        checksum = Path(str(archive) + '.sha256')
        before = fingerprints(archive, checksum)
        listed = lab.invoke('list', root=root)
        assert listed.returncode == 0 and listed.stdout == archive.name + '\n', listed.stderr
        assert fingerprints(archive, checksum) == before
        assert_original(lab)

    @pytest.mark.parametrize('separator', [pytest.param('\n', id='lf'), pytest.param('\r', id='cr')])
    def test_backup_root_line_terminators_are_rejected_before_any_filesystem_observation(self, configtool_lab, separator):
        """A line-delimited retention catalog must never split a configured root."""
        lab = configtool_lab
        operator = lab.folder / 'operator-archives'
        operator.mkdir(mode=0o700)
        root = Path(str(lab.folder / 'backup-prefix') + separator + str(operator))
        root.mkdir(parents=True, mode=0o700)
        name = 'named-config-20000101T000000Z-aabbccdd.tar.gz'
        protected = [root / name, root / (name + '.sha256'), operator / name, operator / (name + '.sha256')]
        for index, path in enumerate(protected):
            path.write_bytes(('preserve object %d\n' % index).encode('ascii'))
            path.chmod(0o600)
        before = fingerprints(*protected)
        entries = set(root.iterdir())
        reached = lab.folder / 'unexpected-root-observation'
        lab.command('realpath', ': > "$BIND_ROOT_OBSERVATION"\nexec /usr/bin/realpath "$@"\n')
        result = lab.invoke('backup', root=root, BIND_ROOT_OBSERVATION=str(reached))
        assert result.returncode == 2 and result.stdout == '', 'Invalid roots must fail before backup preparation'
        assert not reached.exists(), 'Reject line terminators before any native filesystem observation'
        assert set(root.iterdir()) == entries and fingerprints(*protected) == before
        assert_original(lab)

    @pytest.mark.parametrize('kind', ['dangling', 'directory'])
    def test_root_replacement_after_canonicalization_cannot_follow_operator_objects(self, configtool_lab, kind):
        """A successful canonicalizer cannot authorize a subsequently replaced path."""
        lab = configtool_lab
        root = lab.folder / 'late-backup-root'
        target = lab.folder / 'operator-object'
        if kind == 'directory':
            target.mkdir(mode=0o700)
            (target / 'keep').write_bytes(b'operator directory data\n')
        reached = lab.folder / 'root-replaced'
        lab.command('realpath', '/usr/bin/realpath "$@" || exit $?\n'
            '/usr/bin/ln -s -- "$BIND_ROOT_TARGET" "$BIND_ROOT_PATH" || exit $?\n'
            ': > "$BIND_ROOT_REPLACED"\n')
        result = lab.invoke('list', root=root, BIND_ROOT_TARGET=str(target), BIND_ROOT_PATH=str(root),
            BIND_ROOT_REPLACED=str(reached))
        assert reached.exists() and result.returncode != 0 and result.stdout == ''
        assert root.is_symlink() and os.readlink(str(root)) == str(target)
        if kind == 'directory':
            assert set(path.name for path in target.iterdir()) == {'keep'}
            assert (target / 'keep').read_bytes() == b'operator directory data\n'
        else:
            assert not target.exists()
        assert_original(lab)

    def test_regular_file_ancestor_cannot_be_followed_as_a_backup_directory(self, configtool_lab):
        lab = configtool_lab
        ancestor = lab.folder / 'regular-ancestor'
        ancestor.write_bytes(b'operator data')
        result = lab.invoke('list', root=ancestor / 'backups')
        assert result.returncode != 0 and 'Unsafe backup directory component' in result.stderr
        assert ancestor.read_bytes() == b'operator data'
        assert_original(lab)

    def test_failed_backup_name_observation_creates_no_archive(self, configtool_lab):
        """Check the filename dependency before publishing any backup artifact."""
        lab = configtool_lab
        marker = lab.folder / 'failed-basename'
        lab.command('basename', ': > "$BIND_BASENAME_FAILED"\n/usr/bin/basename "$@"\nexit 1\n')
        result = lab.invoke('backup', BIND_BASENAME_FAILED=str(marker))
        assert marker.exists(), 'The failed filename boundary must be reached'
        assert result.returncode != 0 and result.stdout == ''
        assert not list(lab.root.glob('named-config-*'))
        assert not list(lab.root.glob('.named-backup.*'))
        assert_original(lab)

    def test_missing_dns_client_is_explicitly_inconclusive(self, configtool_lab):
        lab = configtool_lab
        result = lab.invoke('check-front-axfr', '192.0.2.1', 'example.test', '192.0.2.2',
            str(lab.folder / 'tsig.key'), root=None, PATH=str(lab.binaries))
        assert result.returncode == 2 and result.stdout == '' and result.stderr == 'dig missing\n'
        assert not lab.root.exists()

    @pytest.mark.parametrize('fault', ['temporary-directory', 'stage-identity', 'safety-archive',
        'unexpected-footprint', 'oversized-footprint', 'unsafe-footprint',
        'staged-directory-link', 'staged-file-link', 'prepared-symlink', 'prepared-hardlink'])
    def test_restore_refuses_unusable_preparation_before_live_changes(self, configtool_lab, fault):
        lab = configtool_lab
        archive = restored_input(lab)
        before = fingerprints(archive, lab.configuration, lab.zones / 'original.zone')
        if fault == 'temporary-directory':
            lab.command('mktemp', '[[ ${@: -1} == */.restore-state.* ]] && exit 1\nexec /usr/bin/mktemp "$@"\n')
        elif fault == 'stage-identity':
            lab.command('stat', '[[ $1 == -c && $2 == %d:%i ]] && exit 1\nexec /usr/bin/stat "$@"\n')
        elif fault == 'safety-archive':
            lab.command('tar', '[[ $1 == --create && " $* " == *"/.restore-safety."* ]] && exit 1\n'
                'exec /usr/bin/tar "$@"\n')
        elif fault in ('prepared-symlink', 'prepared-hardlink'):
            outside = lab.folder / 'outside-staged-object'
            outside.write_bytes(b'operator prepared object\n')
            # The TAR command really completes first. Its private output then
            # changes before safe_sources validates the extraction graph.
            lab.command('tar', '/usr/bin/tar "$@" || exit $?\n'
                'if [[ $1 == --extract && ${@: -1} == */.restore-state.*/tree ]]; then\n'
                '  /usr/bin/ln ' + ('-s ' if fault == 'prepared-symlink' else '') +
                '-- "$BIND_STAGED_OUTSIDE" "${@: -1}/var/named/unsafe-prepared" || exit $?\n'
                'fi\n')
        else:
            if fault == 'unexpected-footprint':
                action = 'mkdir -p "$1/etc/foreign"\n'
            elif fault == 'unsafe-footprint':
                action = 'mkfifo "$1/var/named/unsafe-fifo"\n'
            elif fault in ('staged-directory-link', 'staged-file-link'):
                # This actual link appears after safe_sources has completed;
                # the later per-entry type checks must still reject it.
                outside = lab.folder / 'outside-staged-object'
                if fault == 'staged-directory-link':
                    outside.mkdir()
                    (outside / 'keep').write_bytes(b'operator directory data\n')
                else:
                    outside.write_bytes(b'operator file data\n')
                action = 'ln -s -- "$BIND_STAGED_OUTSIDE" "$1/var/named/unsafe-link"\n'
            else:
                action = 'for ((i=0;i<65539;i++)); do printf "%s/var/named/item%05d\\0" "$1" "$i"; done\nexit 0\n'
            lab.command('find', 'if [[ $1 == */.restore-state.*/tree ]]; then\n' + action +
                'fi\nexec /usr/bin/find "$@"\n')
        result = lab.invoke('restore', '--archive', archive.name,
            BIND_STAGED_OUTSIDE=str(lab.folder / 'outside-staged-object'))
        assert result.returncode != 0 and 'RESTORED ' not in result.stdout
        assert fingerprints(archive, lab.configuration, lab.zones / 'original.zone') == before
        assert not list(lab.root.glob('.restore-safety.*'))
        assert bool(list(lab.root.glob('.restore-state.*'))) is (fault != 'temporary-directory')
        if fault in ('staged-directory-link', 'staged-file-link'):
            retained = outside / 'keep' if outside.is_dir() else outside
            assert retained.read_bytes() == (b'operator directory data\n' if outside.is_dir() else b'operator file data\n')
            assert 'Unsafe staged restore object' in result.stderr
            workspace, = lab.root.glob('.restore-state.*')
            assert (workspace / 'tree/var/named/unsafe-link').is_symlink()
        if fault in ('prepared-symlink', 'prepared-hardlink'):
            assert outside.read_bytes() == b'operator prepared object\n'
            assert 'Restore preparation failed' in result.stderr
            workspace, = lab.root.glob('.restore-state.*')
            assert not (workspace / 'paths').exists(), 'Reject the graph before its footprint can authorize application'
            prepared = workspace / 'tree/var/named/unsafe-prepared'
            assert prepared.is_symlink() is (fault == 'prepared-symlink')
            if fault == 'prepared-hardlink':
                assert os.path.samefile(str(prepared), str(outside))

    def test_nested_named_configuration_roundtrip_preserves_unrelated_operator_files(self, configtool_lab):
        """Include actual etc/named directory entries and nested static includes."""
        lab = configtool_lab
        directory = Path('/etc/named/includes')
        directory.mkdir(parents=True)
        include = directory / 'clients.conf'
        include.write_bytes(b'acl synthetic_clients { 192.0.2.0/24; };\n')
        original = include.read_bytes()
        archive = restored_input(lab)
        include.write_bytes(b'acl operator_changed { none; };\n')
        unrelated = directory / 'operator-note'
        unrelated.write_bytes(b'preserve unrelated operator bytes\n')
        result = lab.invoke('restore', '--archive', archive.name)
        assert result.returncode == 0 and result.stdout.startswith('RESTORED '), result.stderr
        assert include.read_bytes() == original
        assert unrelated.read_bytes() == b'preserve unrelated operator bytes\n'
        assert not list(lab.root.glob('.restore-state.*')) and not list(lab.root.glob('.restore-safety.*'))
        assert_original(lab)

    def test_native_safety_extraction_failure_retains_pre_restore_archive(self, configtool_lab):
        """Keep recovery bytes when the kernel refuses TAR's safety archive read."""
        from canonical_shell_lab import RestrictedRootExec
        lab = configtool_lab
        archive = restored_input(lab)
        operator_configuration = b'operator pre-restore configuration\n'
        lab.configuration.write_bytes(operator_configuration)
        reached = lab.folder / 'safety-read-refused'
        lab.command('tar', 'if [[ $1 == --extract && " $* " == *"/.restore-safety."* ]]; then\n'
            '  for argument in "$@"; do\n'
            '    if [[ $argument == */.restore-safety.???????? ]]; then\n'
            '      /usr/bin/chmod 0000 -- "$argument" || exit $?\n'
            '      : > "$BIND_SAFETY_READ_REFUSED"\n    fi\n  done\nfi\n'
            'exec /usr/bin/tar "$@"\n')
        with RestrictedRootExec():
            result = lab.invoke('restore', '--archive', archive.name,
                BIND_ARCHIVE_FAIL_ONCE='1', BIND_SAFETY_READ_REFUSED=str(reached))
        assert reached.exists(), 'The real TAR read must fail during safety recovery, after live application'
        assert result.returncode != 0 and 'Permission denied' in result.stderr, result.stderr
        assert 'Safety recovery incomplete' in result.stderr and 'RESTORED ' not in result.stdout
        safety, = lab.root.glob('.restore-safety.*')
        assert safety.stat().st_mode & 0o777 == 0
        # The controller's restored root policy can inspect the retained bytes.
        saved = subprocess.run(['/usr/bin/tar', '-xOzf', str(safety), 'etc/named.conf'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, check=True)
        assert saved.stdout == operator_configuration
        assert list(lab.root.glob('.restore-state.*'))

    @pytest.mark.parametrize('arguments', [(), ('list', '--root'), ('restore', '--archive'), ('check-front-axfr',),
        ('list', '--force'), ('unsupported',), ('restore', '--archive', '../other.tar.gz'),
        pytest.param(('list', '--root', 'relative-backups'), id='relative-root'),
        pytest.param(('list', '--root', '/'), id='filesystem-root'),
        pytest.param(('list', '--root', '/etc'), id='configuration-root'),
        pytest.param(('list', '--root', '/var'), id='variable-data-root'),
        pytest.param(('list', '--root', '/var/named'), id='live-bind-root'),
        pytest.param(('list', '--root', '/etc/refused-backups'), id='configuration-descendant'),
        pytest.param(('list', '--root', '/var/named/refused-backups'), id='live-bind-descendant'),
        pytest.param(('list', '--root', '/var/backups/../refused-backups'), id='noncanonical-root')])
    def test_invalid_public_arguments_refuse_before_live_files_change(self, configtool_lab, arguments):
        lab = configtool_lab
        before = fingerprints(lab.configuration, lab.zones / 'original.zone')
        result = lab.invoke(*arguments, root=None)
        assert result.returncode == 2 and result.stderr.startswith('usage: bind-configtool ')
        assert fingerprints(lab.configuration, lab.zones / 'original.zone') == before
        assert not list(lab.root.glob('named-config-*'))

    @pytest.mark.parametrize('fault', ['link', 'writable', 'lock-fifo', 'empty-file-metadata', 'flock',
                                      'foreign-ancestor', 'lock-dangling'])
    def test_unsafe_state_or_failed_lock_cannot_start_backup(self, configtool_lab, fault):
        lab = configtool_lab
        root = lab.root
        if fault == 'link':
            lab.root.parent.mkdir(parents=True)
            (lab.root.parent / 'linked').symlink_to(lab.folder, target_is_directory=True)
            root = lab.root.parent / 'linked/refused-backup-root'
        elif fault == 'writable':
            lab.root.parent.mkdir(parents=True)
            lab.root.parent.chmod(0o777)
        elif fault == 'foreign-ancestor':
            lab.root.parent.mkdir(parents=True)
            os.chown(str(lab.root.parent), 1000, -1)
        elif fault == 'lock-dangling':
            lab.root.mkdir(parents=True, mode=0o700)
            (lab.root / '.lock').symlink_to(lab.folder / 'missing-lock-target')
        elif fault == 'lock-fifo':
            lab.root.mkdir(parents=True, mode=0o700)
            os.mkfifo(str(lab.root / '.lock'), mode=0o600)
        elif fault == 'empty-file-metadata':
            lab.command('stat', "[[ $1 == -c && $2 == '%u %a %h' ]] && exit 0\n"
                'exec /usr/bin/stat "$@"\n')
        else:
            lab.command('flock', "printf 'Injected lock failure\\n' >&2\nexit 1\n")
        result = lab.invoke('backup', root=root)
        assert result.returncode != 0 and 'BACKUP_CREATED ' not in result.stdout
        assert not list(lab.root.glob('named-config-*'))
        assert not (lab.folder / 'refused-backup-root').exists()
        if fault == 'foreign-ancestor':
            assert lab.root.parent.stat().st_uid == 1000 and not lab.root.exists()
        elif fault == 'lock-dangling':
            assert (lab.root / '.lock').is_symlink()
            assert not (lab.folder / 'missing-lock-target').exists()
        assert_original(lab)

    @pytest.mark.parametrize('fault', ['temporary-directory', 'output-metadata', 'nul-producer', 'capture-size-invalid'])
    def test_failed_archive_observation_never_becomes_a_valid_catalog(self, configtool_lab, fault):
        lab = configtool_lab
        archive = restored_input(lab)
        before = fingerprints(archive, Path(str(archive) + '.sha256'))
        if fault == 'temporary-directory':
            lab.command('mktemp', 'exit 1\n')
        elif fault in ('output-metadata', 'capture-size-invalid'):
            failure = 'exit 1' if fault == 'output-metadata' else 'printf "not-a-size\\n"; exit 0'
            lab.command('stat', 'if [[ ${@: -1} == */.verify.*/out ]]; then ' + failure + '; fi\n'
                'exec /usr/bin/stat "$@"\n')
        else:
            lab.command('tr', 'exit 1\n')
        result = lab.invoke('list')
        assert result.returncode != 0 and result.stdout == ''
        assert fingerprints(archive, Path(str(archive) + '.sha256')) == before
        assert not list(lab.root.glob('.verify.*'))

    @pytest.mark.parametrize('fault', ['configuration-directory', 'data-file', 'hardlink', 'symlink', 'find', 'find-special'])
    def test_unsafe_or_unreadable_sources_prevent_archive_creation(self, configtool_lab, fault):
        lab = configtool_lab
        if fault == 'configuration-directory':
            lab.configuration.unlink()
            lab.configuration.mkdir()
        elif fault == 'data-file':
            shutil.rmtree(str(lab.zones.parent))
            lab.zones.parent.write_text('regular data path\n', encoding='ascii')
        elif fault == 'hardlink':
            os.link(str(lab.configuration), str(lab.folder / 'shared-configuration'))
        elif fault == 'symlink':
            outside = lab.folder / 'outside'
            outside.write_text('external bytes\n', encoding='ascii')
            lab.configuration.unlink()
            lab.configuration.symlink_to(outside)
        elif fault == 'find-special':
            lab.command('find', '[[ $2 == "!" ]] && exit 1\nexec /usr/bin/find "$@"\n')
        else:
            lab.command('find', 'exit 1\n')
        result = lab.invoke('backup')
        assert result.returncode != 0 and 'BACKUP_CREATED ' not in result.stdout
        assert not list(lab.root.glob('named-config-*'))
        if fault == 'symlink':
            assert outside.read_text() == 'external bytes\n' and lab.configuration.is_symlink()

    @pytest.mark.parametrize('empty', [False, True])
    @pytest.mark.parametrize('fault', ['create', 'catalog'])
    def test_failed_native_tar_removes_only_its_private_partial_archive(self, configtool_lab, empty, fault):
        lab = configtool_lab
        if empty:
            lab.configuration.unlink()
            shutil.rmtree(str(lab.zones.parent))
        flag = '--create' if fault == 'create' else '-tzf'
        lab.command('tar', '[[ $1 == ' + flag + ' ]] && exit 1\nexec /usr/bin/tar "$@"\n')
        result = lab.invoke('backup')
        assert result.returncode != 0 and 'BACKUP_CREATED ' not in result.stdout
        assert not list(lab.root.glob('.named-backup.*'))
        assert not list(lab.root.glob('named-config-*'))
        if not empty:
            assert_original(lab)

    def test_empty_layout_and_force_keep_one_verified_backup_per_invocation(self, configtool_lab):
        lab = configtool_lab
        lab.configuration.unlink()
        shutil.rmtree(str(lab.zones.parent))
        result = lab.invoke('backup', '--force')
        assert result.returncode == 0 and result.stdout.startswith('BACKUP_CREATED '), result.stderr
        first = Path(result.stdout[len('BACKUP_CREATED '):].strip())
        second = lab.invoke('backup')
        assert second.returncode == 0 and second.stdout.startswith('BACKUP_CREATED '), second.stderr
        assert Path(second.stdout[len('BACKUP_CREATED '):].strip()) != first
        listing = lab.invoke('list')
        assert listing.returncode == 0 and len(listing.stdout.splitlines()) == 2

    def test_empty_restore_preserves_safety_evidence_when_bind_validation_refuses(self, configtool_lab):
        lab = configtool_lab
        lab.configuration.unlink()
        shutil.rmtree(str(lab.zones.parent))
        archive = restored_input(lab)
        # A real BIND checker cannot approve a missing configuration, including
        # during recovery of an empty layout from a first-deployment backup.
        lab.command('named-checkconf', 'exit 1\n')
        result = lab.invoke('restore', '--archive', archive.name)
        assert result.returncode != 0 and 'Safety recovery incomplete' in result.stderr
        assert not lab.configuration.exists() and not lab.zones.parent.exists()
        assert list(lab.root.glob('.restore-safety.*')) and list(lab.root.glob('.restore-state.*'))

    def test_runtime_disappearing_after_validation_triggers_safety_recovery(self, configtool_lab):
        """A successful checker status cannot authorize a later empty graph."""
        lab = configtool_lab
        archive = restored_input(lab)
        reached = lab.folder / 'runtime-removed-after-check'
        lab.command('named-checkconf', 'if [[ ! -e $BIND_RUNTIME_REMOVED ]]; then\n'
            '  /usr/bin/rm -- /etc/named.conf /var/named/zones/original.zone || exit $?\n'
            '  /usr/bin/rmdir -- /var/named/zones /var/named || exit $?\n'
            '  : > "$BIND_RUNTIME_REMOVED"\nfi\nexit 0\n')
        result = lab.invoke('restore', '--archive', archive.name, BIND_RUNTIME_REMOVED=str(reached))
        assert reached.exists(), 'The graph must actually disappear after the checker boundary'
        assert result.returncode == 1 and 'RESTORED ' not in result.stdout
        assert_original(lab)
        assert not list(lab.root.glob('.restore-state.*')) and not list(lab.root.glob('.restore-safety.*'))

    def test_zone_directory_is_preserved_when_pruning_regular_managed_files(self, configtool_lab):
        lab = configtool_lab
        archive = restored_input(lab)
        directory = lab.zones / 'operator.zone'
        directory.mkdir()
        foreign = directory / 'foreign.file'
        foreign.write_bytes(b'operator data')
        result = lab.invoke('restore', '--archive', archive.name)
        assert result.returncode == 0, result.stderr
        assert foreign.read_bytes() == b'operator data'
        assert_original(lab)

    def test_zone_replaced_by_a_symlink_during_pruning_preserves_its_operator_target(self, configtool_lab):
        """Recheck a later glob entry after a genuine earlier pruning action."""
        lab = configtool_lab
        archive = restored_input(lab)
        earlier = lab.zones / 'a-obsolete.zone'
        earlier.write_bytes(b'; Bind9-Ansible-MANAGED-ZONE\nobsolete\n')
        later = lab.zones / 'operator.zone'
        later.write_bytes(b'previous unrelated regular file\n')
        outside = lab.folder / 'operator-zone'
        outside.write_bytes(b'; Bind9-Ansible-MANAGED-ZONE\npreserve operator bytes\n')
        reached = lab.folder / 'pruning-link-inserted'
        lab.command('head', '/usr/bin/head "$@" || exit $?\n'
            'if [[ $1 == -c && ${@: -1} == /var/named/zones/a-obsolete.zone ]]; then\n'
            '  /usr/bin/rm -- /var/named/zones/operator.zone || exit $?\n'
            '  /usr/bin/ln -s -- "$BIND_PRUNING_TARGET" /var/named/zones/operator.zone || exit $?\n'
            '  : > "$BIND_PRUNING_REACHED"\nfi\n')
        result = lab.invoke('restore', '--archive', archive.name,
            BIND_PRUNING_TARGET=str(outside), BIND_PRUNING_REACHED=str(reached))
        assert reached.exists() and not earlier.exists(), 'The first managed zone must really be pruned'
        assert result.returncode == 0 and later.is_symlink() and os.readlink(str(later)) == str(outside)
        assert outside.read_bytes() == b'; Bind9-Ansible-MANAGED-ZONE\npreserve operator bytes\n'
        assert_original(lab)

    def test_new_staged_path_replaced_by_a_dangling_live_link_refuses_before_application(self, configtool_lab):
        """A missing symlink target does not make its pathname unoccupied."""
        lab = configtool_lab
        payload = lab.zones / 'restore-only.zone'
        payload.write_bytes(b'archived owned payload\n')
        archive = restored_input(lab)
        payload.unlink()
        outside = lab.folder / 'missing-operator-target'
        reached = lab.folder / 'staged-live-link-inserted'
        lab.command('find', '/usr/bin/find "$@" || exit $?\n'
            'if [[ $1 == "$BIND_STAGED_ROOT"/.restore-state.*/tree && ${@: -1} == -print0 ]]; then\n'
            '  /usr/bin/ln -s -- "$BIND_STAGED_TARGET" /var/named/zones/restore-only.zone || exit $?\n'
            '  : > "$BIND_STAGED_REACHED"\nfi\n')
        result = lab.invoke('restore', '--archive', archive.name, BIND_STAGED_ROOT=str(lab.root),
            BIND_STAGED_TARGET=str(outside), BIND_STAGED_REACHED=str(reached))
        assert reached.exists() and result.returncode != 0 and 'RESTORED ' not in result.stdout
        assert payload.is_symlink() and os.readlink(str(payload)) == str(outside) and not outside.exists()
        assert not list(lab.root.glob('.restore-safety.*')), 'Refuse before capturing or applying live data'
        assert list(lab.root.glob('.restore-state.*'))
        assert_original(lab)

    def test_retention_purges_only_timestamp_expired_archive_and_its_checksum(self, configtool_lab):
        lab = configtool_lab
        archive = restored_input(lab)
        expired = lab.root / 'named-config-20000101T000000Z-abcdef01.tar.gz'
        shutil.copy2(str(archive), str(expired))
        sidecar = Path(str(expired) + '.sha256')
        sidecar.write_text(hashlib.sha256(expired.read_bytes()).hexdigest() + '  ' + expired.name + '\n', encoding='ascii')
        sidecar.chmod(0o600)
        unrelated = lab.root / 'operator-note'
        unrelated.write_text('keep unrelated bytes\n', encoding='ascii')
        # An unparseable operator-supplied timestamp establishes no age and
        # must never be guessed into the expired-archive deletion set.
        invalid = lab.root / 'named-config-20261301T000000Z-deadbeef.tar.gz'
        shutil.copy2(str(archive), str(invalid))
        invalid_sidecar = Path(str(invalid) + '.sha256')
        invalid_sidecar.write_text(hashlib.sha256(invalid.read_bytes()).hexdigest() +
            '  ' + invalid.name + '\n', encoding='ascii')
        invalid_sidecar.chmod(0o600)
        preserved = fingerprints(invalid, invalid_sidecar)
        result = lab.invoke('backup')
        assert result.returncode == 0, result.stderr
        assert not expired.exists() and not sidecar.exists()
        assert unrelated.read_text() == 'keep unrelated bytes\n'
        assert fingerprints(invalid, invalid_sidecar) == preserved

    @pytest.mark.parametrize('other_archive', [False, True])
    def test_missing_published_archive_is_refused_before_reporting_backup_success(self, configtool_lab, other_archive):
        """Other catalog entries cannot mask loss of the just-published backup."""
        lab = configtool_lab
        reached = lab.folder / 'published-backup-removed'
        keep = lab.root / 'named-config-20990101T000000Z-abcdef01.tar.gz'
        if other_archive:
            lab.root.mkdir(parents=True, mode=0o700)
            keep.write_bytes(b'preserve unrelated operator archive\n')
            keep.chmod(0o600)
        lab.command('find', 'if [[ $1 == "$BIND_MISSING_ROOT" && $2 == -maxdepth ]]; then\n'
            '  for archive in "$BIND_MISSING_ROOT"/named-config-*.tar.gz; do\n'
            '    [[ $archive != "$BIND_KEEP_ARCHIVE" ]] || continue\n'
            '    /usr/bin/rm -- "$archive" "$archive.sha256" || exit $?\ndone\n'
            '  : > "$BIND_BACKUP_REMOVED"\nfi\nexec /usr/bin/find "$@"\n')
        result = lab.invoke('backup', BIND_MISSING_ROOT=str(lab.root), BIND_BACKUP_REMOVED=str(reached),
            BIND_KEEP_ARCHIVE=str(keep) if other_archive else '')
        assert reached.exists(), 'The real catalog must omit the newly published archive'
        assert result.returncode != 0 and 'BACKUP_CREATED ' not in result.stdout
        assert list(lab.root.glob('named-config-*')) == ([keep] if other_archive else [])
        if other_archive:
            assert keep.read_bytes() == b'preserve unrelated operator archive\n'
        assert_original(lab)

    @pytest.mark.parametrize('fault', ['stage-extraction', 'footprint', 'success-cleanup', 'recovery-cleanup',
        'identity-change', 'cleanup-owner-observation', 'cleanup-identity-observation',
        'cleanup-mode-change', 'cleanup-link-replacement', 'stage-directory'])
    def test_restore_preparation_and_cleanup_failures_preserve_recovery_evidence(self, configtool_lab, fault):
        lab = configtool_lab
        archive = restored_input(lab)
        if fault == 'stage-directory':
            lab.command('mkdir', 'if [[ ${@: -1} == */.restore-state.*/tree ]]; then\n'
                '  : > "$BIND_STAGE_DIRECTORY_FAILED"; exit 1\nfi\nexec /usr/bin/mkdir "$@"\n')
        elif fault == 'stage-extraction':
            lab.command('tar', '[[ $1 == --extract && ${@: -1} == */.restore-state.*/tree ]] && exit 1\n'
                'exec /usr/bin/tar "$@"\n')
        elif fault == 'footprint':
            lab.command('find', '[[ $1 == */.restore-state.*/tree ]] && exit 1\nexec /usr/bin/find "$@"\n')
        elif fault == 'identity-change':
            lab.command('stat', 'if [[ $1 == -c && $2 == %d:%i ]]; then\n'
                '  if [[ -e $BIND_ID_SEEN ]]; then printf "0:0\\n"; exit 0; fi\n'
                ': > "$BIND_ID_SEEN"\nfi\nexec /usr/bin/stat "$@"\n')
        elif fault in ('cleanup-owner-observation', 'cleanup-identity-observation'):
            # Keep phase observation local: do not consume the shared fail-once state.
            lab.command('named-checkconf', ': > "$BIND_STAT_READY" || exit 1\nexit 0\n')
            lab.command('stat', 'if [[ $1 == -c && $2 == "$BIND_FAILED_STAT_FORMAT" &&\n'
                '  ${@: -1} == */.restore-state.* && -e $BIND_STAT_READY ]]; then\n'
                '  : > "$BIND_STAT_FAILED"\n  /usr/bin/stat "$@"\n  exit 1\nfi\n'
                'exec /usr/bin/stat "$@"\n')
        elif fault in ('cleanup-mode-change', 'cleanup-link-replacement'):
            # Change the actual private object after extraction/validation,
            # rather than manufacturing an identity through stat output.
            action = ('chmod 0755 -- "$work"' if fault == 'cleanup-mode-change' else
                'mv -- "$work" "$BIND_RETAINED_WORK"\n'
                'ln -s -- "$BIND_RETAINED_WORK" "$work"')
            lab.command('named-checkconf', 'for work in "$BIND_BACKUP_ROOT"/.restore-state.????????; do\n'
                '  [[ -d $work && ! -L $work ]] || exit 1\n' + action +
                '\n  : > "$BIND_WORK_CHANGED"\ndone\nexit 0\n')
        else:
            lab.command('find', '[[ $1 == */.restore-state.* && $2 == -xdev ]] && exit 1\n'
                'exec /usr/bin/find "$@"\n')
        result = lab.invoke('restore', '--archive', archive.name,
            BIND_ID_SEEN=str(lab.folder / 'identity-seen'),
            BIND_STAT_FAILED=str(lab.folder / 'stat-failed'),
            BIND_STAT_READY=str(lab.folder / 'cleanup-ready'),
            BIND_BACKUP_ROOT=str(lab.root), BIND_RETAINED_WORK=str(lab.folder / 'retained-work'),
            BIND_WORK_CHANGED=str(lab.folder / 'work-changed'),
            BIND_STAGE_DIRECTORY_FAILED=str(lab.folder / 'stage-directory-failed'),
            BIND_FAILED_STAT_FORMAT='%u:%a' if fault == 'cleanup-owner-observation' else '%d:%i',
            **({'BIND_ARCHIVE_FAIL_ONCE': '1'} if fault == 'recovery-cleanup' else {}))
        assert result.returncode != 0 and 'RESTORED ' not in result.stdout
        assert list(lab.root.glob('.restore-state.*'))
        if fault == 'stage-directory':
            assert (lab.folder / 'stage-directory-failed').exists()
            workspace, = lab.root.glob('.restore-state.*')
            assert not (workspace / 'tree').exists() and not list(lab.root.glob('.restore-safety.*'))
        if fault in ('success-cleanup', 'recovery-cleanup', 'identity-change',
                'cleanup-owner-observation', 'cleanup-identity-observation',
                'cleanup-mode-change', 'cleanup-link-replacement'):
            assert list(lab.root.glob('.restore-safety.*'))
        if fault in ('cleanup-owner-observation', 'cleanup-identity-observation'):
            assert (lab.folder / 'stat-failed').exists(), 'The failed metadata boundary must be reached'
        if fault in ('cleanup-mode-change', 'cleanup-link-replacement'):
            assert (lab.folder / 'work-changed').exists()
            workspace, = lab.root.glob('.restore-state.*')
            assert workspace.is_symlink() is (fault == 'cleanup-link-replacement')
            assert (workspace / 'tree/etc/named.conf').read_text() == 'original configuration\n'
            if fault == 'cleanup-mode-change':
                assert workspace.stat().st_mode & 0o777 == 0o755
        assert_original(lab)

    def test_failed_initial_catalog_is_not_an_empty_success(self, configtool_lab):
        lab = configtool_lab
        lab.command('sort', "printf 'Injected archive catalog failure\\n' >&2\nexit 2\n")
        result = lab.invoke('list')
        assert result.returncode != 0 and 'Archive catalog read failed' in result.stderr
        assert_original(lab)

    def test_failed_directory_metadata_blocks_backup_state_creation(self, configtool_lab):
        lab = configtool_lab
        lab.command('stat', "if [[ $1 == -c && $2 == '%u %a' ]]; then printf '0 700\\n'; exit 1; fi\n"
            'exec /usr/bin/stat "$@"\n')
        result = lab.invoke('list')
        assert result.returncode != 0 and not lab.root.exists()
        lab.root.mkdir(parents=True, mode=0o700)
        lab.command('stat', "if [[ $1 == -c && $2 == '%a' ]]; then printf '700\\n'; exit 1; fi\n"
            'exec /usr/bin/stat "$@"\n')
        result = lab.invoke('list')
        assert result.returncode != 0 and not (lab.root / '.lock').exists()
        assert_original(lab)

    def test_failed_canonical_path_observation_cannot_create_backup_state(self, configtool_lab):
        """Reject failed producer output even when it looks like the exact root."""
        lab = configtool_lab
        before = fingerprints(lab.configuration, lab.zones / 'original.zone')
        lab.command('realpath', 'printf "%s\\n" "${@: -1}"\nexit 1\n')
        # Check the fault independently of the production guard under review.
        fault = subprocess.run([str(lab.binaries / 'realpath'), '-m', '--', str(lab.root)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=5)
        assert fault.returncode == 1 and fault.stdout == str(lab.root) + '\n' and fault.stderr == ''
        result = lab.invoke('list')
        assert result.returncode == 1 and result.stdout == ''
        assert 'Backup root canonicalization failed' in result.stderr
        assert not lab.root.exists()
        assert fingerprints(lab.configuration, lab.zones / 'original.zone') == before

    def test_failed_retention_catalog_cannot_report_backup_success(self, configtool_lab):
        lab = configtool_lab
        lab.command('sort', 'text=$(cat)\nif [[ $text == *"/named-config-"* ]]; then\n'
            '  printf "%s\\n" "${text%%$\'\\n\'*}"\n'
            "  printf 'Injected retention catalog failure\\n' >&2; exit 2\nfi\n"
            'printf "%s\\n" "$text" | /usr/bin/sort "$@"\n')
        result = lab.invoke('backup')
        assert result.returncode != 0 and 'BACKUP_CREATED ' not in result.stdout
        assert 'Retention catalog read failed' in result.stderr
        assert_original(lab)

    def test_failed_restore_member_catalog_preserves_live_managed_zone(self, configtool_lab):
        lab = configtool_lab
        archive = restored_input(lab)
        zone = lab.zones / 'new.zone'
        zone.write_text('; Bind9-Ansible-MANAGED-ZONE\noperator data\n', encoding='ascii')
        lab.configuration.write_text('operator configuration\n', encoding='ascii')
        before = fingerprints(zone, lab.configuration)
        lab.command('tar', 'if [[ $1 == -tzf ]]; then\n'
            "  printf 'etc/named.conf\\n'; printf 'Injected incomplete restore member catalog\\n' >&2; exit 2\nfi\n"
            'exec /usr/bin/tar "$@"\n')
        result = lab.invoke('restore', '--archive', archive.name)
        assert result.returncode != 0 and fingerprints(zone, lab.configuration) == before
        assert 'Restore member catalog read failed' in result.stderr

    def test_active_named_is_rejected_before_overwriting_configuration(self, configtool_lab):
        self._assert_named_state_refused(configtool_lab, 'active', 0)

    @pytest.mark.parametrize('state,status', [('inactive', 0), ('inactive', 2),
                                              ('active', 3), ('failed', 0), ('unknown', 2)])
    def test_incoherent_service_observation_cannot_authorize_restore(self, configtool_lab, state, status):
        self._assert_named_state_refused(configtool_lab, state, status)

    @pytest.mark.parametrize('state,status', [('failed', 3), ('unknown', 4)])
    def test_supported_nonactive_service_statuses_allow_configured_restore(self, configtool_lab, state, status):
        """Accept the supported native state/code pairs, not a string alone."""
        lab = configtool_lab
        archive = restored_input(lab)
        lab.configuration.write_text('operator change before restore\n', encoding='ascii')
        lab.command('systemctl', 'printf "%s\\n" "$BIND_TEST_NAMED_STATE"\nexit "$BIND_TEST_NAMED_RC"\n')
        result = lab.invoke('restore', '--archive', archive.name,
            BIND_TEST_NAMED_STATE=state, BIND_TEST_NAMED_RC=str(status))
        assert result.returncode == 0 and result.stdout == 'RESTORED ' + str(archive) + '\n', result.stderr
        assert not list(lab.root.glob('.restore-state.*')) and not list(lab.root.glob('.restore-safety.*'))
        assert_original(lab)

    @staticmethod
    def _assert_named_state_refused(lab, state, status):
        """Neither an inactive-looking string nor an exit code alone proves safety."""
        archive = restored_input(lab)
        lab.configuration.write_text('changed\n', encoding='ascii')
        lab.command('systemctl', 'printf "%s\\n" "$BIND_TEST_NAMED_STATE"\n'
            'exit "$BIND_TEST_NAMED_RC"\n')
        result = lab.invoke('restore', '--archive', archive.name,
            BIND_TEST_NAMED_STATE=state, BIND_TEST_NAMED_RC=str(status))
        assert result.returncode != 0 and lab.configuration.read_text() == 'changed\n'
        assert 'Stop named' in result.stderr

    @pytest.mark.parametrize('phase', ['after-stage', 'after-safety'])
    @pytest.mark.parametrize('kind', ['symlink', 'hardlink'])
    def test_late_live_destination_drift_preserves_operator_bytes_before_restore(self, configtool_lab, phase, kind):
        """Revalidate live paths after native preparation, before pruning or extraction."""
        lab = configtool_lab
        archive = restored_input(lab)
        outside = lab.folder / 'operator-zones'
        outside.mkdir(mode=0o700)
        keep = outside / 'outside.zone'
        keep.write_bytes(b'; Bind9-Ansible-MANAGED-ZONE\noperator data outside the live tree\n')
        before = fingerprints(archive, Path(str(archive) + '.sha256'), keep, lab.configuration)
        reached = lab.folder / 'live-tree-changed'
        if kind == 'symlink':
            mutation = ('/usr/bin/mv -- /var/named/zones /var/named/previous-zones\n'
                '/usr/bin/ln -s -- "$BIND_LATE_OPERATOR" /var/named/zones\n')
        else:
            mutation = ('/usr/bin/rm -- /var/named/zones/original.zone\n'
                '/usr/bin/ln -- "$BIND_LATE_OPERATOR/outside.zone" /var/named/zones/original.zone\n')
        mutation += ': > "$BIND_LATE_MARKER"\n'
        if phase == 'after-stage':
            lab.command('find', 'if [[ $1 == */.restore-state.*/tree && ! -e $BIND_LATE_MARKER ]]; then\n'
                '  /usr/bin/find "$@" || exit $?\n' + mutation + 'exit 0\nfi\nexec /usr/bin/find "$@"\n')
        else:
            lab.command('tar', 'if [[ $1 == --create && " $* " == *"/.restore-safety."* && ! -e $BIND_LATE_MARKER ]]; then\n'
                '  /usr/bin/tar "$@" || exit $?\n' + mutation + 'exit 0\nfi\nexec /usr/bin/tar "$@"\n')
        result = lab.invoke('restore', '--archive', archive.name,
            BIND_LATE_OPERATOR=str(outside), BIND_LATE_MARKER=str(reached))
        assert reached.exists(), 'The live object must change after the initial source observation'
        assert keep.exists() and fingerprints(*[Path(name) for name in before]) == before, 'Restore changed operator bytes outside its intended tree'
        assert result.returncode != 0 and 'RESTORED ' not in result.stdout, 'Unsafe late destination drift must be refused'
        assert set(path.name for path in outside.iterdir()) == {'outside.zone'}
        if kind == 'symlink':
            assert lab.zones.is_symlink() and os.readlink(str(lab.zones)) == str(outside)
            assert (lab.zones.parent / 'previous-zones/original.zone').read_bytes() == b'original zone\n'
        else:
            assert os.path.samefile(str(keep), str(lab.zones / 'original.zone'))
        assert len(list(lab.root.glob('.restore-state.*'))) == 1
        assert bool(list(lab.root.glob('.restore-safety.*'))) is (phase == 'after-safety')

    def test_symlink_destination_never_deletes_an_external_owned_zone(self, configtool_lab):
        lab = configtool_lab
        archive = restored_input(lab)
        outside = lab.folder / 'outside'
        outside.mkdir()
        keep = outside / 'keep.zone'
        keep.write_text('; Bind9-Ansible-MANAGED-ZONE\n', encoding='ascii')
        lab.zones.rename(lab.zones.parent / 'original-zones')
        lab.zones.symlink_to(outside, target_is_directory=True)
        result = lab.invoke('restore', '--archive', archive.name)
        assert result.returncode != 0 and lab.zones.is_symlink()
        assert keep.read_text() == '; Bind9-Ansible-MANAGED-ZONE\n'

    def test_general_directory_is_not_chmodded_as_backup_root(self, configtool_lab):
        lab = configtool_lab
        lab.root.mkdir(parents=True, mode=0o755)
        lab.root.chmod(0o755)
        result = lab.invoke('list')
        assert result.returncode != 0 and lab.root.stat().st_mode & 0o777 == 0o755
        assert not (lab.root / '.lock').exists()

    def test_label_failure_is_fatal_and_preserves_safety_archive(self, configtool_lab):
        lab = configtool_lab
        archive = restored_input(lab)
        result = lab.invoke('restore', '--archive', archive.name, BIND_TEST_LABEL_RC='1')
        assert result.returncode != 0 and list(lab.root.glob('.restore-safety.*'))
        assert 'Safety recovery incomplete' in result.stderr

    def test_pruning_failure_rolls_back_zones_removed_earlier_in_the_same_restore(self, configtool_lab):
        lab = configtool_lab
        archive = restored_input(lab)
        zones = [lab.zones / (name + '-obsolete.zone') for name in ('a', 'b')]
        for zone in zones:
            zone.write_text('; Bind9-Ansible-MANAGED-ZONE\n' + zone.name + '\n', encoding='ascii')
        before = fingerprints(*zones)
        lab.command('rm', '[[ ${@: -1} == /var/named/zones/b-obsolete.zone ]] && exit 1\n'
            'exec /usr/bin/rm "$@"\n')
        result = lab.invoke('restore', '--archive', archive.name)
        assert result.returncode != 0 and fingerprints(*zones) == before
        assert_original(lab)

    def test_marker_read_failure_rolls_back_prior_pruning_and_never_reports_success(self, configtool_lab):
        self._assert_marker_pipeline_failure(configtool_lab, 'reader')

    @pytest.mark.parametrize('boundary', ['line-selector', 'matcher'])
    def test_marker_consumer_failure_recovers_preceding_prunes(self, configtool_lab, boundary):
        self._assert_marker_pipeline_failure(configtool_lab, boundary)

    def _assert_marker_pipeline_failure(self, lab, boundary):
        """All three native pipeline producers must preserve earlier zone bytes."""
        archive = restored_input(lab)
        lab.configuration.write_text('live operator configuration\n', encoding='ascii')
        zones = [lab.zones / (name + '-obsolete.zone') for name in ('a', 'b')]
        for zone in zones:
            zone.write_text('; Bind9-Ansible-MANAGED-ZONE\n' + zone.name + '\n', encoding='ascii')
        before = fingerprints(lab.configuration, *zones)
        reached = lab.folder / 'marker-producer-failed'
        if boundary == 'reader':
            lab.command('head', 'if [[ ${@: -1} == /var/named/zones/b-obsolete.zone ]]; then\n'
                '  : > "$BIND_MARKER_FAILURE"\n'
                "  printf '; Bind9-Ansible-MANAGED-ZONE\\n'; printf 'Injected incomplete owned-zone marker read\\n' >&2; exit 2\nfi\n"
                'exec /usr/bin/head "$@"\n')
        else:
            command = 'head' if boundary == 'line-selector' else 'grep'
            selected = ('$1 == -n && $2 == 1' if boundary == 'line-selector' else
                        '$1 == -a && $2 == -q && $3 == -x && $4 == -F')
            # The first native pipeline really prunes a-obsolete. The second
            # consumer emits its genuine successful result, then fails; neither
            # that output nor grep's status may hide another producer's error.
            lab.command(command, 'if [[ ' + selected + ' ]]; then\n'
                '  /usr/bin/' + command + ' "$@" || exit $?\n'
                '  if [[ -e $BIND_MARKER_FAILURE.count ]]; then\n'
                '    : > "$BIND_MARKER_FAILURE"; exit 2\n  fi\n'
                '  : > "$BIND_MARKER_FAILURE.count"; exit 0\nfi\n'
                'exec /usr/bin/' + command + ' "$@"\n')
        result = lab.invoke('restore', '--archive', archive.name, BIND_MARKER_FAILURE=str(reached))
        assert reached.exists(), 'The selected producer must fail during the second marker pipeline'
        assert result.returncode != 0 and fingerprints(lab.configuration, *zones) == before
        assert 'RESTORED ' not in result.stdout
        assert not list(lab.root.glob('.restore-state.*')) and not list(lab.root.glob('.restore-safety.*'))

    def test_binary_or_second_line_marker_does_not_authorize_pruning(self, configtool_lab):
        lab = configtool_lab
        archive = restored_input(lab)
        binary, second = lab.zones / 'binary.zone', lab.zones / 'second-line.zone'
        binary.write_bytes(b'; Bind9-Ansible-MANAGED-ZONE\x00binary payload\n')
        second.write_bytes(b'\n; Bind9-Ansible-MANAGED-ZONE\n')
        before = fingerprints(binary, second)
        result = lab.invoke('restore', '--archive', archive.name)
        assert result.returncode == 0, result.stderr
        assert fingerprints(binary, second) == before

    @pytest.mark.parametrize('creates_partial_tree,metadata_failure', [
        pytest.param(False, False, id='False'), pytest.param(True, False, id='True'),
        pytest.param(True, True, id='metadata-failure'),
        pytest.param(True, 'unlink', id='rollback-unlink-failure'),
        pytest.param(True, 'late-symlink', id='late-symlink'),
        pytest.param(True, 'late-hardlink', id='late-hardlink'),
        pytest.param(True, 'identity-read', id='rollback-identity-read-failure'),
        pytest.param(True, 'identity-shape', id='rollback-identity-framing'),
        pytest.param(True, 'identity-read-second', id='rollback-second-identity-read-failure'),
        pytest.param(True, 'identity-shape-second', id='rollback-second-identity-framing'),
        pytest.param(True, 'late-disappearance', id='rollback-owned-file-disappeared'),
        pytest.param(True, 'late-symlink-second', id='rollback-second-pass-dangling-link')])
    def test_partial_extraction_rolls_back_implicit_parent_directories(self, configtool_lab,
            creates_partial_tree, metadata_failure):
        lab = configtool_lab
        archive = restored_input(lab)
        directory = lab.zones.parent / 'restore-only'
        (directory / 'nested').mkdir(parents=True)
        payload = directory / 'nested/new.zone'
        payload.write_text('archived payload\n', encoding='ascii')
        # Omit directory headers: extraction must account for implicit parents.
        result = subprocess.run(['tar', '-czf', str(archive), '-C', '/', 'etc/named.conf',
            str(payload).lstrip('/')], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        assert result.returncode == 0, result.stderr
        Path(str(archive) + '.sha256').write_text(hashlib.sha256(archive.read_bytes()).hexdigest() +
            '  ' + archive.name + '\n', encoding='ascii')
        shutil.rmtree(str(directory))
        partial = ('  mkdir -p /var/named/restore-only/nested\n'
            "  printf 'partial payload\\n' > /var/named/restore-only/nested/new.zone\n") if creates_partial_tree else ''
        if metadata_failure in ('late-symlink', 'late-hardlink'):
            # The live TAR really applies its bytes before its producer reports
            # failure. A subsequent safety extraction then precedes cleanup.
            partial = '  /usr/bin/tar "$@" || exit $?\n  : > "$BIND_ROLLBACK_READY"\n'
        lab.command('tar', 'if [[ $1 == --extract && ${@: -1} == / && " $* " == *named-config-* ]]; then\n'
            + partial + '  exit 1\nfi\nexec /usr/bin/tar "$@"\n')
        if metadata_failure is True:
            # A failed link-count read must preserve every introduced object;
            # checking all objects precedes the first rollback deletion.
            lab.command('stat', 'if [[ $1 == -c && $2 == %h &&\n'
                '  ${@: -1} == /var/named/restore-only/nested/new.zone ]]; then\n'
                '  : > "$BIND_STAT_FAILED"\n  /usr/bin/stat "$@"\n  exit 1\nfi\n'
                'exec /usr/bin/stat "$@"\n')
        elif metadata_failure == 'unlink':
            lab.command('rm', 'if [[ ${@: -1} == /var/named/restore-only/nested/new.zone ]]; then\n'
                '  : > "$BIND_STAT_FAILED"\n  exit 1\nfi\nexec /usr/bin/rm "$@"\n')
        elif metadata_failure == 'late-symlink-second':
            outside = lab.folder / 'operator-recovery'
            outside.mkdir()
            (outside / 'keep').write_bytes(b'preserve operator recovery data\n')
            operator = outside / 'target'
            # Print the genuine first-pass identity before an operator replaces
            # that owned file. The second pass must inspect the dangling link.
            lab.command('stat', 'if [[ $1 == -c && $2 == %d:%i &&\n'
                '  ${@: -1} == /var/named/restore-only/nested/new.zone ]]; then\n'
                '  /usr/bin/stat "$@" || exit $?\n'
                '  /usr/bin/rm -- "$BIND_LATE_TARGET" || exit $?\n'
                '  /usr/bin/ln -s -- "$BIND_LATE_OPERATOR" "$BIND_LATE_TARGET" || exit $?\n'
                '  : > "$BIND_STAT_FAILED"\n  exit 0\nfi\nexec /usr/bin/stat "$@"\n')
        elif metadata_failure in ('identity-read', 'identity-shape',
                                  'identity-read-second', 'identity-shape-second', 'late-disappearance'):
            lab.command('stat', 'if [[ $1 == -c && $2 == %d:%i &&\n'
                '  ${@: -1} == /var/named/restore-only/nested/new.zone ]]; then\n'
                '  if [[ $BIND_LATE_KIND == *-second && ! -e $BIND_STAT_FAILED.first ]]; then\n'
                '    : > "$BIND_STAT_FAILED.first"; exec /usr/bin/stat "$@"\nfi\n'
                '  : > "$BIND_STAT_FAILED"\n  /usr/bin/stat "$@" || exit $?\n'
                '  if [[ $BIND_LATE_KIND == late-disappearance ]]; then\n'
                '    exec /usr/bin/rm -- /var/named/restore-only/nested/new.zone\nfi\n'
                '  if [[ $BIND_LATE_KIND == identity-read* ]]; then exit 1; fi\n'
                '  printf "unexpected second metadata record\\n"\n  exit 0\nfi\nexec /usr/bin/stat "$@"\n')
        elif metadata_failure in ('late-symlink', 'late-hardlink'):
            outside = lab.folder / 'operator-recovery'
            outside.mkdir()
            (outside / 'keep').write_bytes(b'preserve operator recovery data\n')
            operator = outside / 'target'
            if metadata_failure == 'late-hardlink':
                operator.write_bytes(b'operator linked bytes\n')
            # Change a real object only after the corresponding native find
            # has completed. The later per-path checks must independently
            # reject a dangling link or a newly hardlinked regular file.
            lab.command('find', '/usr/bin/find "$@" || exit $?\n'
                'if [[ $1 == /var/named && -e $BIND_ROLLBACK_READY && ! -e $BIND_STAT_FAILED &&\n'
                '  ( ($BIND_LATE_KIND == late-hardlink && $2 == -type) ||\n'
                '    ($BIND_LATE_KIND == late-symlink && $2 == "!") ) ]]; then\n'
                '  /usr/bin/rm -- "$BIND_LATE_TARGET" || exit $?\n'
                '  if [[ $BIND_LATE_KIND == late-symlink ]]; then\n'
                '    /usr/bin/ln -s -- "$BIND_LATE_OPERATOR" "$BIND_LATE_TARGET" || exit $?\n'
                '  else /usr/bin/ln -- "$BIND_LATE_OPERATOR" "$BIND_LATE_TARGET" || exit $?; fi\n'
                '  : > "$BIND_STAT_FAILED"\nfi\n')
        result = lab.invoke('restore', '--archive', archive.name,
            BIND_STAT_FAILED=str(lab.folder / 'stat-failed'), BIND_LATE_KIND=str(metadata_failure),
            BIND_LATE_TARGET=str(payload), BIND_LATE_OPERATOR=str(lab.folder / 'operator-recovery/target'),
            BIND_ROLLBACK_READY=str(lab.folder / 'rollback-ready'))
        assert result.returncode != 0
        if metadata_failure in ('late-symlink', 'late-hardlink', 'late-symlink-second'):
            assert (lab.folder / 'stat-failed').exists(), 'The late native object replacement must be observed'
            assert 'Safety recovery incomplete' in result.stderr and 'RESTORED ' not in result.stdout
            assert (outside / 'keep').read_bytes() == b'preserve operator recovery data\n'
            if metadata_failure.startswith('late-symlink'):
                assert payload.is_symlink() and os.readlink(str(payload)) == str(operator)
                assert not operator.exists(), 'Rollback must not create or traverse the dangling operator target'
            else:
                assert payload.read_bytes() == b'operator linked bytes\n' and os.path.samefile(str(payload), str(operator))
                assert payload.stat().st_nlink == 2
            assert list(lab.root.glob('.restore-safety.*')) and list(lab.root.glob('.restore-state.*'))
        elif metadata_failure == 'late-disappearance':
            assert (lab.folder / 'stat-failed').exists(), 'The owned file must disappear after its actual identity was read'
            assert not directory.exists() and not list(lab.root.glob('.restore-state.*'))
            assert not list(lab.root.glob('.restore-safety.*'))
        elif metadata_failure:
            assert (lab.folder / 'stat-failed').exists(), 'The selected native metadata boundary must be reached'
            if isinstance(metadata_failure, str) and metadata_failure.endswith('-second'):
                assert (lab.folder / 'stat-failed.first').exists(), 'The first identity pass must succeed'
            assert payload.read_text() == 'partial payload\n'
            assert list(lab.root.glob('.restore-safety.*')) and list(lab.root.glob('.restore-state.*'))
        else:
            assert not directory.exists()
        assert_original(lab)

    @pytest.mark.parametrize('state', ['absent', 'replaced'])
    def test_rollback_cannot_delete_a_file_that_appears_after_its_absence_was_checked(self, configtool_lab, state):
        """A later successful stat cannot authorize deletion of an earlier path."""
        lab = configtool_lab
        for name in ('restore-only-a.zone', 'restore-only-b.zone'):
            (lab.zones / name).write_text('archived owned payload\n', encoding='ascii')
        archive = restored_input(lab)
        for name in ('restore-only-a.zone', 'restore-only-b.zone'):
            (lab.zones / name).unlink()
        footprint = lab.folder / 'actual-footprint'
        earlier, later = lab.folder / 'earlier-path', lab.folder / 'later-path'
        ready, inserted = lab.folder / 'partial-restore-ready', lab.folder / 'operator-created'
        identity = lab.folder / 'operator-identity'
        # Preserve the genuine NUL-delimited find output byte for byte. Choose
        # the earlier/later siblings from that actual order, never FS ordering.
        lab.command('find', 'if [[ ${@: -1} == -print0 && $1 == "$BIND_LATE_ROOT"/.restore-state.*/tree ]]; then\n'
            '  /usr/bin/find "$@" > "$BIND_LATE_FOOTPRINT" || exit $?\n'
            '  exec /usr/bin/cat -- "$BIND_LATE_FOOTPRINT"\nfi\nexec /usr/bin/find "$@"\n')
        lab.command('tar', 'if [[ $1 == --extract && ${@: -1} == / && " $* " == *named-config-* ]]; then\n'
            '  /usr/bin/tar "$@" || exit $?\n'
            '  mapfile -d "" -t entries < "$BIND_LATE_FOOTPRINT"\n  selected=()\n'
            '  for entry in "${entries[@]}"; do\n'
            '    case ${entry##*/} in restore-only-a.zone|restore-only-b.zone) selected+=("/var/named/zones/${entry##*/}");; esac\n'
            '  done\n  [[ ${#selected[@]} == 2 ]] || exit 91\n'
            '  printf "%s\\n" "${selected[0]}" > "$BIND_LATE_EARLIER"\n'
            '  printf "%s\\n" "${selected[1]}" > "$BIND_LATE_LATER"\n'
            '  if [[ $BIND_LATE_STATE == absent ]]; then /usr/bin/rm -- "${selected[0]}" || exit $?; fi\n'
            '  : > "$BIND_LATE_READY"\n  exit 1\nfi\nexec /usr/bin/tar "$@"\n')
        lab.command('stat', 'if [[ $1 == -c && $2 == %h && -e $BIND_LATE_READY && ! -e $BIND_LATE_INSERTED &&\n'
            '  ${@: -1} == "$(/usr/bin/cat -- "$BIND_LATE_LATER")" ]]; then\n'
            '  /usr/bin/stat "$@" || exit $?\n'
            '  destination=$(/usr/bin/cat -- "$BIND_LATE_EARLIER") || exit $?\n'
            '  printf "operator foreign bytes\\n" > "$BIND_LATE_STAGED"\n'
            '  /usr/bin/chmod 0640 -- "$BIND_LATE_STAGED" || exit $?\n'
            '  /usr/bin/mv -f -- "$BIND_LATE_STAGED" "$destination" || exit $?\n'
            '  /usr/bin/stat -c "%d:%i" -- "$destination" > "$BIND_LATE_IDENTITY" || exit $?\n'
            '  : > "$BIND_LATE_INSERTED"\n  exit 0\nfi\nexec /usr/bin/stat "$@"\n')
        result = lab.invoke('restore', '--archive', archive.name, BIND_LATE_ROOT=str(lab.root),
            BIND_LATE_FOOTPRINT=str(footprint), BIND_LATE_EARLIER=str(earlier), BIND_LATE_LATER=str(later),
            BIND_LATE_READY=str(ready), BIND_LATE_INSERTED=str(inserted), BIND_LATE_IDENTITY=str(identity),
            BIND_LATE_STATE=state, BIND_LATE_STAGED=str(lab.folder / 'operator-staged-file'))
        assert ready.exists() and inserted.exists(), 'The real rollback observation boundary must be reached'
        foreign = Path(earlier.read_text().strip())
        assert foreign.is_file(), 'Rollback deleted a foreign file created after its absence was checked'
        assert foreign.read_bytes() == b'operator foreign bytes\n'
        assert str(foreign.stat().st_dev) + ':' + str(foreign.stat().st_ino) == identity.read_text().strip()
        assert result.returncode != 0 and 'Safety recovery incomplete' in result.stderr
        assert list(lab.root.glob('.restore-safety.*')) and list(lab.root.glob('.restore-state.*'))
        assert_original(lab)

    def test_rollback_preserves_a_foreign_file_inside_a_new_directory(self, configtool_lab):
        lab = configtool_lab
        directory = lab.zones.parent / 'restore-only'
        directory.mkdir()
        (directory / 'new.zone').write_text('archived payload\n', encoding='ascii')
        archive = restored_input(lab)
        shutil.rmtree(str(directory))
        lab.command('named-checkconf', "printf 'foreign operator data\\n' > /var/named/restore-only/foreign.file\nexit 1\n")
        result = lab.invoke('restore', '--archive', archive.name)
        assert result.returncode != 0 and not (directory / 'new.zone').exists()
        assert (directory / 'foreign.file').read_text() == 'foreign operator data\n'
        assert list(lab.root.glob('.restore-safety.*')) and list(lab.root.glob('.restore-state.*'))

    @pytest.mark.parametrize('replacement', ['directory-for-file', 'file-for-directory'])
    def test_partial_restore_preserves_introduced_objects_with_unexpected_types(self, configtool_lab, replacement):
        """A failed extractor cannot authorize deletion using stale object types."""
        lab = configtool_lab
        directory = lab.zones.parent / 'restore-only'
        directory.mkdir()
        payload = directory / 'new.zone'
        payload.write_text('archived payload\n', encoding='ascii')
        archive = restored_input(lab)
        shutil.rmtree(str(directory))
        creation = ('mkdir -p /var/named/restore-only/new.zone' if replacement == 'directory-for-file' else
            "printf 'unexpected regular object\\n' > /var/named/restore-only")
        lab.command('tar', 'if [[ $1 == --extract && ${@: -1} == / && " $* " == *named-config-* ]]; then\n'
            '  ' + creation + '\n  exit 1\nfi\nexec /usr/bin/tar "$@"\n')
        result = lab.invoke('restore', '--archive', archive.name)
        assert result.returncode != 0 and 'Safety recovery incomplete' in result.stderr
        assert 'RESTORED ' not in result.stdout
        if replacement == 'directory-for-file':
            assert payload.is_dir() and not list(payload.iterdir())
        else:
            assert directory.is_file() and directory.read_text() == 'unexpected regular object\n'
        assert list(lab.root.glob('.restore-safety.*')) and list(lab.root.glob('.restore-state.*'))
        assert_original(lab)

    def test_rollback_refuses_a_replacement_symlink_and_preserves_recovery_evidence(self, configtool_lab):
        lab = configtool_lab
        directory = lab.zones.parent / 'restore-only'
        directory.mkdir()
        (directory / 'new.zone').write_text('archived payload\n', encoding='ascii')
        archive = restored_input(lab)
        shutil.rmtree(str(directory))
        outside = lab.folder / 'outside'
        outside.write_text('outside data\n', encoding='ascii')
        lab.command('tar', 'if [[ $1 == --extract && ${@: -1} == / && " $* " == *named-config-* ]]; then\n'
            '  mkdir -p /var/named/restore-only\n'
            '  ln -s "$BIND_TEST_OUTSIDE" /var/named/restore-only/new.zone\n  exit 1\nfi\n'
            'exec /usr/bin/tar "$@"\n')
        result = lab.invoke('restore', '--archive', archive.name, BIND_TEST_OUTSIDE=str(outside))
        assert result.returncode != 0 and (directory / 'new.zone').is_symlink()
        assert outside.read_text() == 'outside data\n'
        assert list(lab.root.glob('.restore-safety.*')) and list(lab.root.glob('.restore-state.*'))

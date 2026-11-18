"""Isolated filesystem integration for the real reverse-zone publication path."""
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

from test_reverse_serial import module as M, TestReverseSerial as ReverseTemplate

NAME = '2.0.192.in-addr.arpa'
MEMBER = 'var/named/zones/' + NAME + '.zone'


def backup(tmp_path, entries):
    path = tmp_path / 'backup.tar.gz'
    with tarfile.open(str(path), 'w:gz') as archive:
        for name, content, kind in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    path.chmod(0o600)
    return path


def test_real_cli_retains_unchanged_serial_and_advances_changed_data(tmp_path):
    old = ReverseTemplate().render(serial=25)
    archive = backup(tmp_path, [(MEMBER, old.encode(), tarfile.REGTYPE)])
    zone = tmp_path / (NAME + '.zone')
    desired = ReverseTemplate().render(value='changed.example.test.')
    zone.write_text(desired)
    M.main(['--backup', str(archive), NAME, NAME], zone_root=tmp_path)
    assert '( 26 3600' in zone.read_text()
    zone.write_text(ReverseTemplate().render())
    M.main(['--backup', str(archive), NAME], zone_root=tmp_path)
    assert zone.read_text() == old
    before = zone.read_bytes()
    M.main(['--backup', str(archive), NAME], zone_root=tmp_path)
    assert zone.read_bytes() == before
    assert not list(tmp_path.glob('.reverse-serial-*'))


@pytest.mark.parametrize('name,kind', [
    ('/etc/named.conf', tarfile.REGTYPE), ('var/named/../secret', tarfile.REGTYPE),
    ('var//named/zone', tarfile.REGTYPE), ('var/named/./zone', tarfile.REGTYPE),
    ('var/named/zone\\evil', tarfile.REGTYPE), ('foreign/file', tarfile.REGTYPE),
    ('var/named/link', tarfile.SYMTYPE), ('var/named/hard', tarfile.LNKTYPE),
    ('var/named/fifo', tarfile.FIFOTYPE), ('var/named/' + 'x' * 4096, tarfile.REGTYPE),
])
def test_foreign_noncanonical_and_special_archive_members_are_rejected(tmp_path, name, kind):
    archive = backup(tmp_path, [(name, b'', kind)])
    with pytest.raises(ValueError, match='Unsafe or excessive'):
        M.ReverseArchive.read(archive, [NAME])


def test_duplicate_member_is_never_last_write_wins(tmp_path):
    archive = backup(tmp_path, [(MEMBER, b'a', tarfile.REGTYPE), (MEMBER, b'b', tarfile.REGTYPE)])
    with pytest.raises(ValueError, match='inventory'):
        M.ReverseArchive.read(archive, [NAME])


def test_missing_gzip_footer_is_rejected_even_after_the_requested_member(tmp_path):
    archive = backup(tmp_path, [(MEMBER, ReverseTemplate().render().encode(), tarfile.REGTYPE)])
    archive.write_bytes(archive.read_bytes()[:-8])
    with pytest.raises((ValueError, EOFError, tarfile.TarError, OSError)):
        M.ReverseArchive.read(archive, [NAME])


@pytest.mark.parametrize('budget', ['MAX_MEMBERS', 'MAX_EXPANDED'])
def test_scan_budgets_refuse_before_reading_excessive_payload(tmp_path, monkeypatch, budget):
    archive = backup(tmp_path, [('var/named/irrelevant', b'abc', tarfile.REGTYPE)])
    monkeypatch.setattr(M.ReverseArchive, budget, 0)
    with pytest.raises(ValueError, match='inventory|expanded'):
        M.ReverseArchive.read(archive, [NAME])


@pytest.mark.parametrize('content', [b'bad\0data', b'\xff'])
def test_previous_zone_must_be_complete_nul_free_utf8(tmp_path, content):
    archive = backup(tmp_path, [(MEMBER, content, tarfile.REGTYPE)])
    with pytest.raises((ValueError, UnicodeError)):
        M.ReverseArchive.read(archive, [NAME])


def test_selected_zone_cannot_be_a_directory_or_exceed_bound(tmp_path, monkeypatch):
    archive = backup(tmp_path, [(MEMBER, b'', tarfile.DIRTYPE)])
    with pytest.raises(ValueError, match='previous reverse zone'):
        M.ReverseArchive.read(archive, [NAME])
    archive = backup(tmp_path, [(MEMBER, b'abc', tarfile.REGTYPE)])
    monkeypatch.setattr(M.ReverseZoneSerial, 'MAX_BYTES', 2)
    with pytest.raises(ValueError, match='previous reverse zone'):
        M.ReverseArchive.read(archive, [NAME])


def test_unrequested_contents_are_not_decoded_or_retained(tmp_path):
    archive = backup(tmp_path, [('etc/named', b'', tarfile.DIRTYPE),
                                ('etc/named.conf', b'\xff', tarfile.REGTYPE),
                                (MEMBER, b'\xff', tarfile.REGTYPE)])
    assert M.ReverseArchive.read(archive, []) == {}


@pytest.mark.parametrize('kind', ['mode', 'hardlink', 'symlink', 'directory'])
def test_unsafe_backup_is_refused(tmp_path, kind):
    archive = backup(tmp_path, [])
    if kind == 'mode':
        archive.chmod(0o644)
    elif kind == 'hardlink':
        os.link(str(archive), str(tmp_path / 'alias'))
    elif kind == 'symlink':
        other = tmp_path / 'alias'
        other.symlink_to(archive)
        archive = other
    else:
        archive = tmp_path
    with pytest.raises((ValueError, OSError)):
        M.ReverseArchive.read(archive, [])


def test_all_zone_names_are_checked_before_backup_or_publication(tmp_path):
    with pytest.raises(ValueError, match='zone name'):
        M.main(['--backup', str(tmp_path / 'missing'), NAME, '../escape'], zone_root=tmp_path)


def test_all_zone_contents_are_checked_before_first_publication(tmp_path):
    second = '3.0.192.in-addr.arpa'
    archive = backup(tmp_path, [(MEMBER, ReverseTemplate().render().encode(), tarfile.REGTYPE)])
    zone = tmp_path / (NAME + '.zone')
    desired = ReverseTemplate().render(value='changed.example.test.')
    zone.write_text(desired)
    (tmp_path / (second + '.zone')).write_text('; unmanaged\n')
    with pytest.raises(ValueError, match='unmanaged'):
        M.main(['--backup', str(archive), NAME, second], zone_root=tmp_path)
    assert zone.read_text() == desired


@pytest.mark.parametrize('content', [b'invalid\0zone', b'\xff'])
def test_current_zone_must_be_nul_free_utf8(tmp_path, content):
    path = tmp_path / 'zone'
    path.write_bytes(content)
    with pytest.raises((ValueError, UnicodeError)):
        M.ReverseZoneSerial(path)


def test_file_size_and_mode_are_checked_before_processing(tmp_path, monkeypatch):
    path = tmp_path / 'zone'
    path.write_text(ReverseTemplate().render())
    path.chmod(0o666)
    with pytest.raises(ValueError, match='Unsafe'):
        M.ReverseZoneSerial(path)
    path.chmod(0o640)
    monkeypatch.setattr(M.ReverseZoneSerial, 'MAX_BYTES', 1)
    with pytest.raises(ValueError, match='bound'):
        M.ReverseZoneSerial(path)


@pytest.mark.parametrize('text', ['missing', 'duplicate', 'overflow'])
def test_ambiguous_invalid_and_overflow_serial_are_refused(tmp_path, text):
    value = ReverseTemplate().render()
    if text == 'missing':
        value = value.replace('@ IN SOA', '; @ IN SOA')
    elif text == 'duplicate':
        value += next(line for line in value.splitlines() if '@ IN SOA' in line) + '\n'
    else:
        value = value.replace('( 10 3600', '( 4294967296 3600')
    for previous in (False, True):
        with pytest.raises(ValueError, match='serial|overflow'):
            M.ReverseZoneSerial.serial(value, previous)


def test_concurrent_operator_change_is_not_overwritten(tmp_path):
    path = tmp_path / 'zone'
    path.write_text(ReverseTemplate().render(value='changed.example.test.'))
    target = M.ReverseZoneSerial(path)
    candidate = target.prepare(ReverseTemplate().render())
    manual = ReverseTemplate().render(serial=100, value='operator.example.test.')
    path.write_text(manual)
    with pytest.raises(ValueError, match='changed before publication'):
        target.publish(candidate)
    assert path.read_text() == manual
    assert not list(tmp_path.glob('.reverse-serial-*'))


def test_cli_help_and_missing_backup_have_real_nonzero_exit_status(tmp_path):
    script = str(Path(M.__file__))
    help_result = subprocess.run([sys.executable, script, '--help'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    assert help_result.returncode == 0 and b'--backup' in help_result.stdout
    failure = subprocess.run([sys.executable, script, '--backup', str(tmp_path / 'missing')],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
    assert failure.returncode != 0
    assert b'FileNotFoundError' in failure.stderr

"""Observe refusal boundaries with bounded real streams and injected I/O faults."""
import io
import os
import runpy
import sys
import tarfile

import pytest

from test_reverse_serial import module as M, TestReverseSerial as ReverseTemplate
from test_reverse_serial_archive import backup, NAME, MEMBER


class StreamFault:
    def __init__(self, stream, mode):
        self.stream, self.mode = stream, mode

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self.stream.__exit__(*args)

    def write(self, data):
        if self.mode == 'short':
            return self.stream.write(data[:-1])
        return self.stream.write(data)

    def read(self, size):
        data = self.stream.read(size)
        return data[:-1] if self.mode == 'short-read' else data + b'x'


@pytest.mark.parametrize('mode', ['short', 'corrupt'])
def test_short_write_or_corrupt_staging_readback_never_replaces_live_data(tmp_path, monkeypatch, mode):
    path = tmp_path / 'zone'
    desired = ReverseTemplate().render(value='changed.example.test.')
    path.write_text(desired)
    original = os.fdopen
    def faulty(fd, filemode, *args, **kwargs):
        stream = original(fd, filemode, *args, **kwargs)
        return StreamFault(stream, mode) if filemode == 'w+b' else stream
    monkeypatch.setattr(os, 'fdopen', faulty)
    with pytest.raises(ValueError, match='staging'):
        M.reconcile(path, ReverseTemplate().render())
    assert path.read_text() == desired
    assert not list(tmp_path.glob('.reverse-serial-*'))


def test_truncated_read_is_not_accepted_as_a_valid_snapshot(tmp_path, monkeypatch):
    path = tmp_path / 'zone'
    path.write_text(ReverseTemplate().render())
    original = os.fdopen
    monkeypatch.setattr(os, 'fdopen', lambda fd, *args, **kwargs: StreamFault(original(fd, *args, **kwargs), 'short-read'))
    with pytest.raises(ValueError, match='Incomplete'):
        M.ReverseZoneSerial(path)


@pytest.mark.parametrize('change', ['inode', 'mtime'])
def test_change_during_read_is_detected_by_inode_and_metadata(tmp_path, monkeypatch, change):
    path = tmp_path / 'zone'
    path.write_text(ReverseTemplate().render())
    original = os.fdopen
    class Concurrent(StreamFault):
        def read(self, size):
            data = self.stream.read(size)
            if change == 'inode':
                other = tmp_path / 'replacement'
                other.write_bytes(data)
                os.replace(str(other), str(path))
            else:
                info = path.stat()
                os.utime(str(path), ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
            return data
    monkeypatch.setattr(os, 'fdopen', lambda fd, *args, **kwargs: Concurrent(original(fd, *args, **kwargs), change))
    with pytest.raises(ValueError, match='during observation'):
        M.ReverseZoneSerial(path)


def test_identical_bytes_with_changed_metadata_are_not_blindly_published(tmp_path):
    path = tmp_path / 'zone'
    path.write_text(ReverseTemplate().render(value='changed.example.test.'))
    target = M.ReverseZoneSerial(path)
    candidate = target.prepare(ReverseTemplate().render())
    info = path.stat()
    os.utime(str(path), ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
    with pytest.raises(ValueError, match='before publication'):
        target.publish(candidate)


@pytest.mark.parametrize('kind', ['directory',
    pytest.param('foreign-owner', marks=pytest.mark.skipif(os.geteuid() != 0, reason='Changing fixture ownership requires root')),
    'fifo'])
def test_current_zone_must_be_an_owned_regular_file(tmp_path, kind):
    path = tmp_path / 'zone'
    if kind == 'directory':
        path.mkdir()
    elif kind == 'fifo':
        os.mkfifo(str(path), 0o600)
    else:
        path.write_text(ReverseTemplate().render())
        os.chown(str(path), os.geteuid() + 1, -1)
    with pytest.raises((ValueError, OSError)):
        M.ReverseZoneSerial(path)


@pytest.mark.skipif(os.geteuid() != 0, reason='Changing fixture ownership requires root')
def test_foreign_owner_backup_is_refused(tmp_path):
    archive = backup(tmp_path, [])
    os.chown(str(archive), os.geteuid() + 1, -1)
    with pytest.raises(ValueError, match='private owned'):
        M.ReverseArchive.read(archive, [])


def test_fifo_backup_is_refused_before_a_blocking_read(tmp_path):
    path = tmp_path / 'fifo'
    os.mkfifo(str(path), 0o600)
    with pytest.raises(ValueError, match='regular file'):
        M.ReverseArchive.read(path, [])


def test_replaced_parent_directory_during_zone_read_is_detected(tmp_path, monkeypatch):
    parent = tmp_path / 'live'
    parent.mkdir()
    path = parent / 'zone'
    path.write_text(ReverseTemplate().render())
    original = os.fdopen
    class ParentRace(StreamFault):
        def read(self, size):
            data = self.stream.read(size)
            parent.rename(tmp_path / 'previous')
            parent.mkdir()
            path.write_bytes(data)
            return data
    monkeypatch.setattr(os, 'fdopen', lambda fd, *args, **kwargs: ParentRace(original(fd, *args, **kwargs), 'parent'))
    with pytest.raises(ValueError, match='during observation'):
        M.ReverseZoneSerial(path)


def test_replaced_parent_directory_during_backup_scan_is_detected(tmp_path, monkeypatch):
    parent = tmp_path / 'live'
    parent.mkdir()
    archive = backup(parent, [])
    scan = M.ReverseArchive.scan
    def changed(stream, selected):
        result = scan(stream, selected)
        data = archive.read_bytes()
        parent.rename(tmp_path / 'previous')
        parent.mkdir()
        archive.write_bytes(data)
        archive.chmod(0o600)
        return result
    monkeypatch.setattr(M.ReverseArchive, 'scan', changed)
    with pytest.raises(ValueError, match='backup changed'):
        M.ReverseArchive.read(archive, [])


@pytest.mark.parametrize('name,size', [('var/named/zone\0bad', 0), ('var/named/negative', -1)])
def test_inventory_guard_refuses_invalid_parser_metadata(monkeypatch, name, size):
    """Isolate the member-validation contract from tarfile's own decoding checks."""
    member = tarfile.TarInfo(name)
    member.size = size
    class Inventory:
        def __enter__(self):
            return iter([member])
        def __exit__(self, *args):
            return False
    monkeypatch.setattr(M.tarfile, 'open', lambda **kwargs: Inventory())
    with pytest.raises(ValueError, match='inventory'):
        M.ReverseArchive.scan(io.BytesIO(), set())


def test_non_text_serial_input_is_an_explicit_refusal():
    with pytest.raises(ValueError, match='unmanaged'):
        M.ReverseZoneSerial.serial(b'not text')


def test_extended_metadata_has_its_own_bounded_snapshot(tmp_path, monkeypatch):
    path = tmp_path / 'zone'
    path.write_text(ReverseTemplate().render())
    os.setxattr(str(path), 'user.bind9-test', b'owned-zone-metadata')
    monkeypatch.setattr(M.ReverseZoneSerial, 'MAX_METADATA', 1)
    with pytest.raises(ValueError, match='metadata exceeds'):
        M.ReverseZoneSerial(path)


def test_failed_metadata_readback_never_replaces_the_zone(tmp_path, monkeypatch):
    path = tmp_path / 'zone'
    desired = ReverseTemplate().render(value='changed.example.test.')
    path.write_text(desired)
    os.setxattr(str(path), 'user.bind9-test', b'owned-zone-metadata')
    original = os.getxattr
    def damaged(fd, name):
        value = original(fd, name)
        return b'wrong' if isinstance(fd, int) and '.reverse-serial-' in os.readlink('/proc/self/fd/' + str(fd)) else value
    monkeypatch.setattr(os, 'getxattr', damaged)
    with pytest.raises(ValueError, match='metadata staging'):
        M.reconcile(path, ReverseTemplate().render())
    assert path.read_text() == desired
    assert original(str(path), 'user.bind9-test') == b'owned-zone-metadata'


@pytest.mark.parametrize('payload', [b'', b'\0' * 1023, b'\0' * 1025, b'x' * 1024])
def test_tar_end_records_are_required_independently_of_gzip_validity(payload):
    stream = M.ArchiveStream(io.BytesIO(payload), 2048)
    with pytest.raises(ValueError, match='tar records/end blocks'):
        stream.finish()


def test_full_inflated_stream_budget_includes_metadata_and_padding(tmp_path, monkeypatch):
    archive = backup(tmp_path, [(MEMBER, b'abc', tarfile.REGTYPE)])
    monkeypatch.setattr(M.ReverseArchive, 'MAX_STREAM', 1024)
    with pytest.raises(ValueError, match='inflated stream'):
        M.ReverseArchive.read(archive, [NAME])


@pytest.mark.parametrize('kind', ['fstat', 'lstat'])
def test_changed_backup_is_refused_after_the_complete_scan(tmp_path, monkeypatch, kind):
    archive = backup(tmp_path, [])
    scan = M.ReverseArchive.scan
    def changed(stream, selected):
        result = scan(stream, selected)
        if kind == 'lstat':
            other = tmp_path / 'replacement'
            other.write_bytes(archive.read_bytes())
            other.chmod(0o600)
            os.replace(str(other), str(archive))
        else:
            info = archive.stat()
            os.utime(str(archive), ns=(info.st_atime_ns, info.st_mtime_ns + 1000000))
        return result
    monkeypatch.setattr(M.ReverseArchive, 'scan', changed)
    with pytest.raises(ValueError, match='backup changed'):
        M.ReverseArchive.read(archive, [])


def test_truncated_selected_member_is_refused(tmp_path, monkeypatch):
    archive = backup(tmp_path, [(MEMBER, b'abc', tarfile.REGTYPE)])
    original = tarfile.TarFile.extractfile
    monkeypatch.setattr(tarfile.TarFile, 'extractfile', lambda self, member: io.BytesIO(original(self, member).read()[:-1]))
    with pytest.raises(ValueError, match='Incomplete'):
        M.ReverseArchive.read(archive, [NAME])


def test_actual_script_entry_point_reports_help_without_mutation(monkeypatch, capsys):
    monkeypatch.setattr(sys, 'argv', [M.__file__, '--help'])
    with pytest.raises(SystemExit) as response:
        runpy.run_path(M.__file__, run_name='__main__')
    assert response.value.code == 0
    assert '--backup' in capsys.readouterr().out

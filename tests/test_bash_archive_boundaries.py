"""Read every TAR record, including data following an early end marker."""
import gzip
import io
import os
from pathlib import Path
import tarfile

import pytest

import test_archive_hardening
from test_archive_hardening import ArchiveVerifier


@pytest.fixture(autouse=True)
def source_revision(monkeypatch):
    """Allow the identical assertions to reproduce the preceding revision."""
    baseline = os.environ.get('BIND9_BASH_BASELINE')
    if baseline:
        monkeypatch.setattr(test_archive_hardening, 'SCRIPT', Path(baseline) / 'bind-configtool.sh')


def tar_stream(name, kind=tarfile.REGTYPE):
    """Build genuine GNU-compatible records without extracting any member."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        member = tarfile.TarInfo(name)
        member.type = kind
        if kind == tarfile.REGTYPE:
            member.size = 5
            archive.addfile(member, io.BytesIO(b'data\n'))
        else:
            member.linkname = '/etc/shadow'
            archive.addfile(member)
    return output.getvalue()


@pytest.mark.parametrize('name,kind', [
    ('etc/shadow', tarfile.REGTYPE),
    ('var/named/../../etc/shadow', tarfile.REGTYPE),
    ('var/named/data.zone', tarfile.SYMTYPE),
    ('var/named/first.zone', tarfile.REGTYPE),
])
def test_members_after_early_tar_end_are_checked(tmp_path, name, kind):
    verifier = ArchiveVerifier(tmp_path)
    verifier.path.write_bytes(gzip.compress(tar_stream('var/named/first.zone') + tar_stream(name, kind)))
    verifier.checksum()
    result = verifier.run()
    assert result.returncode != 0, 'A matching checksum cannot authorize hidden TAR members'


def test_zero_padding_after_tar_end_remains_supported(tmp_path):
    verifier = ArchiveVerifier(tmp_path)
    verifier.path.write_bytes(gzip.compress(tar_stream('var/named/first.zone') + bytes(20480)))
    verifier.checksum()
    result = verifier.run()
    assert result.returncode == 0, result.stderr


def test_non_tar_record_after_tar_end_is_refused(tmp_path):
    verifier = ArchiveVerifier(tmp_path)
    verifier.path.write_bytes(gzip.compress(tar_stream('var/named/first.zone') + b'X' * 512 + bytes(1024)))
    verifier.checksum()
    result = verifier.run()
    assert result.returncode != 0, 'Trailing zero blocks cannot hide a malformed TAR header'

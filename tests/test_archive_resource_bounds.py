"""A tiny sparse archive must not authorize unbounded logical extraction."""
import gzip
import hashlib
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tarfile

import pytest

from test_archive_hardening import ArchiveVerifier


@pytest.mark.parametrize('mode', ['unknown', '', '$(touch policy-must-not-execute)'])
def test_unknown_capture_policy_fails_before_allocation_or_execution(tmp_path, mode):
    """Check the private helper contract without claiming public CLI coverage."""
    source = Path(__file__).resolve().parents[1] / 'roles/bind9/files/bind-configtool.sh'
    helpers = source.read_text().split('# BEGIN ARCHIVE SAFETY HELPERS\n', 1)[1].split(
        '# END ARCHIVE SAFETY HELPERS\n', 1)[0]
    marker = tmp_path / 'producer-must-not-run'
    program = 'set -Eeuo pipefail\n' + helpers + '\nroot=$1\nbounded_capture "$2" /usr/bin/touch "$3"\n'
    result = subprocess.run(['/bin/bash', '-c', program, 'capture-contract', str(tmp_path), mode, str(marker)],
        cwd=str(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    assert result.returncode != 0 and result.stdout == b''
    assert not marker.exists() and list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('physical_size,observation', [(1025, 'native'),
    (1140860929, 'native'), (1140860929, 'decompress-fails'), (1025, 'underreported')],
    ids=['partial-record', 'stream-limit-exceeded', 'known-oversize-before-expansion', 'hint-is-not-authority'])
def test_compressed_stream_cannot_bypass_the_physical_tar_budget(tmp_path, monkeypatch, physical_size, observation):
    """Stream a small compressed fixture without allocating its expanded size."""
    verifier = ArchiveVerifier(tmp_path)
    # Keep encoded inputs identical across native/observed phases as well as
    # decoded content: no clock or temporary filename enters the gzip header.
    with verifier.path.open('wb') as compressed:
        with gzip.GzipFile(filename='', mode='wb', fileobj=compressed,
                           compresslevel=1, mtime=0) as stream:
            block = b'\0' * 65536
            blocks, remainder = divmod(physical_size, len(block))
            for _ in range(blocks):
                stream.write(block)
            stream.write(block[:remainder])
    assert verifier.path.stat().st_size < 8 * 1024 * 1024
    verifier.checksum()
    before = hashlib.sha256(verifier.path.read_bytes()).hexdigest()
    marker = tmp_path / 'unnecessary-expansion'
    if observation != 'native':
        # Keep real gzip data and decompression. Fail only an expansion which
        # the genuine footer already proves excessive, or under-report the
        # footer hint to prove that the physical stream is still checked.
        delegate = shutil.which('gzip')
        assert delegate
        binaries = tmp_path / 'bin'
        binaries.mkdir()
        wrapper = binaries / 'gzip'
        body = ('if [[ $1 == -cd ]]; then : > ' + shlex.quote(str(marker)) + '; exit 77; fi\n'
                if observation == 'decompress-fails' else
                'if [[ $1 == --list ]]; then printf "compressed uncompressed ratio name\\n 1 0 0%% stdin\\n"; exit 0; fi\n')
        wrapper.write_text('#!/bin/bash\n' + body + 'exec ' + shlex.quote(delegate) + ' "$@"\n')
        wrapper.chmod(0o755)
        monkeypatch.setenv('PATH', str(binaries) + ':' + os.environ['PATH'])
    result = verifier.run()
    assert result.returncode != 0 and 'Incomplete or oversized tar records' in result.stderr
    assert result.stdout == '' and not list(tmp_path.glob('.verify.*'))
    assert hashlib.sha256(verifier.path.read_bytes()).hexdigest() == before
    assert not marker.exists(), 'An already excessive gzip member must not be expanded'


def test_sparse_member_larger_than_the_backup_payload_budget_is_rejected(tmp_path):
    tree = tmp_path / 'source'
    member = tree / 'var/named/sparse.zone'
    member.parent.mkdir(parents=True)
    # truncate creates a hole, not a GiB allocation or a GiB test fixture.
    with member.open('wb') as stream:
        stream.truncate(1024 * 1024 * 1024 + 1)
    verifier = ArchiveVerifier(tmp_path)
    subprocess.run(['tar', '--sparse', '--create', '--gzip', '--file', str(verifier.path),
                    '--directory', str(tree), 'var/named/sparse.zone'], check=True, timeout=10)
    assert verifier.path.stat().st_size < 1024 * 1024
    verifier.checksum()
    result = verifier.run()
    assert result.returncode != 0, 'Sparse logical sizes must obey the same archive budget'


def test_directory_and_empty_regular_file_are_valid_backup_members(tmp_path):
    verifier = ArchiveVerifier(tmp_path)
    with tarfile.open(verifier.path, 'w:gz') as archive:
        directory = tarfile.TarInfo('var/named/')
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        archive.addfile(tarfile.TarInfo('var/named/empty.zone'))
    verifier.checksum()
    result = verifier.run()
    assert result.returncode == 0, result.stderr


def test_member_limit_is_checked_before_walking_each_archive_path(tmp_path):
    """Reject a real oversized flat catalog before per-member processing."""
    verifier = ArchiveVerifier(tmp_path)
    with tarfile.open(verifier.path, 'w:gz', format=tarfile.USTAR_FORMAT) as archive:
        for index in range(65537):
            archive.addfile(tarfile.TarInfo('var/named/z%05d' % index))
    verifier.checksum()
    result = verifier.run()
    assert result.returncode != 0 and 'Archive member/payload budget exceeded' in result.stderr
    assert result.stdout == '' and not list(tmp_path.glob('.verify.*'))


def test_cumulative_sparse_payload_obeys_the_same_budget(tmp_path):
    tree = tmp_path / 'source'
    names = ['var/named/first.zone', 'var/named/second.zone']
    for name in names:
        member = tree / name
        member.parent.mkdir(parents=True, exist_ok=True)
        # Each member fits; their cumulative logical payload exceeds 1 GiB.
        with member.open('wb') as stream:
            stream.truncate(600 * 1024 * 1024)
    verifier = ArchiveVerifier(tmp_path)
    subprocess.run(['tar', '--sparse', '--create', '--gzip', '--file', str(verifier.path),
        '--directory', str(tree)] + names, check=True, timeout=10)
    assert verifier.path.stat().st_size < 1024 * 1024
    verifier.checksum()
    result = verifier.run()
    assert result.returncode != 0 and 'member/payload budget' in result.stderr


def test_archive_path_length_is_bounded_before_any_extraction(tmp_path):
    verifier = ArchiveVerifier(tmp_path)
    with tarfile.open(verifier.path, 'w:gz') as archive:
        archive.addfile(tarfile.TarInfo('var/named/' + 'd/' * 2050 + 'zone'))
    verifier.checksum()
    result = verifier.run()
    assert result.returncode != 0 and 'Archive path exceeds 4096 bytes' in result.stderr


def test_complete_tar_records_still_require_the_final_zero_blocks(tmp_path):
    verifier = ArchiveVerifier(tmp_path)
    verifier.archive(['var/named/data.zone'])
    records = gzip.decompress(verifier.path.read_bytes())
    verifier.path.write_bytes(gzip.compress(records[:-1024] + b'X' * 1024))
    verifier.checksum()
    result = verifier.run()
    assert result.returncode != 0 and 'Missing tar end-of-archive blocks' in result.stderr

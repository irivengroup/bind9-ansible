"""A tiny sparse archive must not authorize unbounded logical extraction."""
import subprocess
import tarfile

from test_archive_hardening import ArchiveVerifier


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

"""Exercise the production Bash archive verifier with hostile archive metadata."""
import gzip
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'roles/bind9/files/bind-configtool.sh'
pytestmark = pytest.mark.skipif(not shutil.which('bash'), reason='Requires Bash and GNU tar')


class ArchiveVerifier:
    """Invoke the real verifier function; no extraction or system mutation occurs."""

    def __init__(self, folder):
        self.path = folder / 'named-config-20261016T120000Z-12345678.tar.gz'

    def archive(self, names):
        with tarfile.open(self.path, 'w:gz') as archive:
            for name in names:
                member = tarfile.TarInfo(name)
                member.size = 5
                archive.addfile(member, io.BytesIO(b'data\n'))
        self.checksum()

    def checksum(self):
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.path.with_suffix('.gz.sha256').write_text(digest + '  ' + self.path.name + '\n')
        self.path.chmod(0o600)
        self.path.with_suffix('.gz.sha256').chmod(0o600)

    def run(self):
        text = SCRIPT.read_text()
        function = 'verify() {\n' + text.split('verify() {\n', 1)[1].split('\n}\n', 1)[0] + '\n}\n'
        # Shared safety functions are loaded from their marked production block.
        helpers = text.split('# BEGIN ARCHIVE SAFETY HELPERS\n', 1)[-1].split('# END ARCHIVE SAFETY HELPERS\n', 1)[0] if '# BEGIN ARCHIVE SAFETY HELPERS\n' in text else ''
        program = 'set -Eeuo pipefail\nexport LC_ALL=C\nroot=$(dirname -- "$1")\n' + helpers + function + '\nverify "$1"\n'
        return subprocess.run(['bash', '-c', program, 'archive-test', str(self.path)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)


class TestArchiveRejection:
    """A matching checksum never makes malformed tar structure trustworthy."""

    def test_corrupt_tar_with_matching_checksum_is_rejected(self, tmp_path):
        verifier = ArchiveVerifier(tmp_path)
        verifier.path.write_bytes(gzip.compress(b'not a tar stream'))
        verifier.checksum()
        assert verifier.run().returncode != 0

    @pytest.mark.parametrize('names', [
        ['var/named/./data.zone'],
        ['var/named/data.zone', 'var/named/data.zone'],
        ['var/named/back\\slash.zone'],
        ['var/named/line\nbreak.zone'],
    ])
    def test_ambiguous_names_are_rejected(self, tmp_path, names):
        verifier = ArchiveVerifier(tmp_path)
        verifier.archive(names)
        assert verifier.run().returncode != 0

    def test_checksum_cannot_describe_another_file(self, tmp_path):
        verifier = ArchiveVerifier(tmp_path)
        verifier.archive(['var/named/data.zone'])
        extra = tmp_path / 'other'
        extra.write_bytes(b'unrelated')
        checksum = verifier.path.with_suffix('.gz.sha256')
        with checksum.open('a') as stream:
            stream.write(hashlib.sha256(extra.read_bytes()).hexdigest() + '  other\n')
        assert verifier.run().returncode != 0

    def test_hardlinked_archive_is_rejected(self, tmp_path):
        verifier = ArchiveVerifier(tmp_path)
        verifier.archive(['var/named/data.zone'])
        os.link(verifier.path, tmp_path / 'other-archive')
        assert verifier.run().returncode != 0

    def test_valid_regular_archive_remains_accepted(self, tmp_path):
        verifier = ArchiveVerifier(tmp_path)
        verifier.archive(['etc/named.conf', 'var/named/data.zone'])
        assert verifier.run().returncode == 0

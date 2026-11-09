"""Build paired GNU Bash interpreters for the controller's native QA observer.

This offline build never installs an interpreter on a managed server. The exact
upstream archive and observer patch are checked before extraction or execution.
An untouched interpreter supplies the differential reference for the same tests.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile


class NativeBashBuild:
    """Own a fresh private build directory and retain all failed build evidence."""

    ARCHIVE_SHA256 = '9599b22ecd1d5787ad7d3b7bf0c59f312b3396d1e281175dd1f8a4014da621ff'
    PATCH_SHA256 = '335e16f0b6b0a1e74e72fdba6ddb420e0dd6606709bc75e8cb8581909a1bdc14'
    VERSION = '5.2.37(1)-release'
    CONFIGURE = ('--without-bash-malloc', '--disable-nls', '--disable-readline')

    def __init__(self, archive, output):
        if os.name != 'posix':
            raise ValueError('Native Bash QA builds require a POSIX controller')
        self.archive, self.output = Path(archive), Path(output).absolute()
        self.folder = Path(__file__).resolve().parent
        self.patch = self.folder / 'gnu-bash-5.2.37-observer.patch'
        self.archive_bytes = self.checked_bytes(self.archive, self.ARCHIVE_SHA256, 16777216)
        self.patch_bytes = self.checked_bytes(self.patch, self.PATCH_SHA256, 1048576)
        if self.output.exists() or self.output.is_symlink():
            raise ValueError('Native QA build output must be a new private directory')
        self.environment = dict(os.environ, LC_ALL='C', CONFIG_SITE='/dev/null')
        # The recipe supplies the same verified configure options to both
        # builds; caller-specific compiler/linker overrides would break pairing.
        for key in ('CC', 'CFLAGS', 'CPPFLAGS', 'LDFLAGS', 'LIBS', 'MAKEFLAGS', 'MFLAGS',
                    'BASH_ENV', 'ENV', 'BIND9_NATIVE_BASH_FD', 'BIND9_NATIVE_BASH_SHA256'):
            self.environment.pop(key, None)
        if any(key.startswith('BASH_FUNC_') for key in self.environment):
            raise ValueError('Native QA builds refuse inherited shell functions')

    @staticmethod
    def checked_bytes(path, expected, limit):
        """Hash the same bounded bytes that will later be consumed."""
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise ValueError('Native QA build input must be a bounded regular file')
        with path.open('rb') as stream:
            value = stream.read(limit + 1)
        if len(value) > limit or hashlib.sha256(value).hexdigest() != expected:
            raise ValueError('Native QA build input checksum differs from the inspected source')
        return value

    def step(self, command, directory, label):
        with (self.output / (label + '.log')).open('x', encoding='utf-8') as stream:
            result = subprocess.run(command, cwd=directory, env=self.environment,
                stdout=stream, stderr=subprocess.STDOUT, timeout=300)
        if result.returncode:
            raise RuntimeError('Native QA build step failed; inspect ' + label + '.log')

    def extract(self, mode):
        directory = self.output / (mode + '-source')
        directory.mkdir(mode=0o700)
        # Only the previously hashed bytes are extracted, so replacing the
        # caller's archive after inspection cannot change a build's source.
        with tarfile.open(fileobj=io.BytesIO(self.archive_bytes), mode='r:gz') as archive:
            if any(not member.name.startswith('bash-5.2.37/') and member.name != 'bash-5.2.37'
                   for member in archive.getmembers()):
                raise ValueError('Unexpected root in the inspected GNU archive')
            archive.extractall(path=directory, filter='data')
        return directory / 'bash-5.2.37'

    def version(self, binary):
        result = subprocess.run([str(binary), '-c', 'printf "%s" "$BASH_VERSION"'],
            env=self.environment, capture_output=True, text=True, check=True, timeout=10)
        if result.stdout != self.VERSION or result.stderr:
            raise ValueError('Native QA interpreters must expose the same inspected Bash version')
        return result.stdout

    def run(self):
        self.output.mkdir(mode=0o700)
        patch = self.output / 'observer.patch'
        patch.write_bytes(self.patch_bytes)
        patch.chmod(0o600)
        tools = {}
        for name in ('gcc', 'make', 'patch'):
            executable = shutil.which(name)
            if executable is None:
                raise ValueError('Native QA build requires ' + name)
            result = subprocess.run([executable, '--version'], env=self.environment,
                capture_output=True, text=True, check=True, timeout=10)
            tools[name] = result.stdout.splitlines()[0]
        binaries = {}
        for mode in ('baseline', 'observed'):
            source = self.extract(mode)
            if mode == 'observed':
                self.step(['patch', '--batch', '--forward', '--fuzz=0', '-p1', '-i', str(patch)],
                          source, 'observed-patch')
            self.step(['./configure'] + list(self.CONFIGURE), source, mode + '-configure')
            self.step(['make', '-j2', 'bash'], source, mode + '-compile')
            target = self.output / (mode + '-bash')
            shutil.copyfile(str(source / 'bash'), str(target))
            target.chmod(0o500)
            binaries[mode] = {'path': target.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                              'version': self.version(target)}
        manifest = {'schema': 1, 'archive_sha256': self.ARCHIVE_SHA256, 'patch_sha256': self.PATCH_SHA256,
            'recipe_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'configure': list(self.CONFIGURE), 'tools': tools, 'binaries': binaries}
        with (self.output / 'manifest.json').open('x', encoding='utf-8') as stream:
            json.dump(manifest, stream, indent=2)
            stream.write('\n')
        print(json.dumps({'manifest': str(self.output / 'manifest.json'), 'binaries': binaries}))
        return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    return NativeBashBuild(arguments.archive, arguments.output).run()


if __name__ == '__main__':
    raise SystemExit(main())

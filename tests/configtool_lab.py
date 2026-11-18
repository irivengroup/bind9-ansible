"""Own disposable archive paths and launch the unmodified public Bash CLI.

Real TAR, checksums, locks and rollback run on the private container filesystem.
Only systemd, BIND validation and SELinux relabelling have explicit CLI doubles.
Direct launches also let the native observer attribute execution to source bytes.
"""
import os
from pathlib import Path
import subprocess

import pytest

from native_ansible_runner import NativeFilesystemScope, NativeMigrationRun


class ConfigtoolLab:
    """Refuse occupied production paths before creating any test-owned state."""

    def __init__(self, folder):
        self.scope = NativeFilesystemScope(NativeMigrationRun)
        self.folder = folder
        self.configuration = Path('/etc/named.conf')
        self.zones = Path('/var/named/zones')
        self.root = Path(NativeMigrationRun.ROOT)
        self.binaries = folder / 'bin'
        canonical = Path(__file__).resolve().parents[1] / 'roles/bind9/files/bind-configtool.sh'
        self.script = Path(os.environ.get('BIND_CONFIGTOOL_TEST_SOURCE', str(canonical)))
        self.environment = dict(os.environ, PATH=str(self.binaries) + os.pathsep + os.environ['PATH'],
            BIND_ARCHIVE_CHECKED=str(folder / 'configuration-checked'))

    def command(self, name, body):
        """Replace only a private command boundary, never a distribution binary."""
        assert name and Path(name).name == name
        path = self.binaries / name
        path.write_text('#!/bin/bash\n' + body, encoding='ascii')
        path.chmod(0o755)

    def __enter__(self):
        self.scope.__enter__()
        try:
            self.configuration.write_text('original configuration\n', encoding='ascii')
            self.zones.mkdir(parents=True)
            (self.zones / 'original.zone').write_text('original zone\n', encoding='ascii')
            self.binaries.mkdir()
            self.command('systemctl', 'printf "%s\\n" "${BIND_TEST_NAMED_STATE:-inactive}"\n'
                '[[ ${BIND_TEST_NAMED_STATE:-inactive} == active ]] && exit 0\nexit 3\n')
            self.command('restorecon', 'exit "${BIND_TEST_LABEL_RC:-0}"\n')
            self.command('named-checkconf',
                'if [[ -n ${BIND_ARCHIVE_FAIL_ONCE:-} && ! -e $BIND_ARCHIVE_CHECKED ]]; then\n'
                '  : > "$BIND_ARCHIVE_CHECKED"; exit 1\nfi\nexit 0\n')
        except BaseException:
            self.scope.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return self.scope.__exit__(exc_type, exc_value, traceback)

    def invoke(self, *arguments, **options):
        """Run original CLI bytes with the unchanged 30-second process budget."""
        root = options.pop('root', self.root)
        command = ['/bin/bash', str(self.script)] + list(arguments)
        if root is not None:
            command += ['--root', str(root)]
        return subprocess.run(command,
            env=dict(self.environment, **options), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=30)


@pytest.fixture
def configtool_lab(tmp_path):
    """Share one source-preserving integration fixture across archive scenarios."""
    with ConfigtoolLab(tmp_path) as lab:
        yield lab

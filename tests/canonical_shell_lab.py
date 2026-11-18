"""Preserve Bash source bytes while rebasing resources in a disposable root."""
from contextlib import contextmanager
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import threading


def _require_disconnected_root(flag, message):
    """Keep all native resource adapters confined to the same disposable root."""
    if (os.name != 'posix' or os.geteuid() != 0 or not Path('/.dockerenv').is_file() or
            os.environ.get(flag) != '1' or
            {path.name for path in Path('/sys/class/net').iterdir()} != {'lo'}):
        raise RuntimeError(message)


class UnprivilegedProcess:
    """Launch a real non-root child on current Python and the EL8 controller.

    Keep UID/GID/group policy equivalent without pre-exec Python callbacks.
    The native observer sees declarative user arguments on modern controllers;
    EL8's Python 3.6 uses the already installed util-linux exec adapter.
    Only the child loses privileges; no fixture paths or parent credentials
    change. This also supports the existing root-only controller CI tests.
    """

    @staticmethod
    def run(command, **options):
        if os.name != 'posix' or os.geteuid() != 0:
            raise RuntimeError('Unprivileged child execution requires POSIX root')
        if sys.version_info >= (3, 9):
            return subprocess.run(command, user=65534, group=65534, extra_groups=(), **options)
        return subprocess.run(['/usr/bin/setpriv', '--reuid=65534', '--regid=65534',
                               '--clear-groups', '--'] + list(command), **options)


class RestrictedRootExec:
    """Stop root capability uplift on child exec without changing source/UID.

    The caller thread keeps its current capabilities and restores its original
    securebits in every outcome. No locked bit or capability bounding set is
    changed. This adapter does not intercept subprocesses or claim coverage.
    """

    PR_GET_SECUREBITS = 27
    PR_SET_SECUREBITS = 28
    SECBIT_NOROOT = 1

    def __init__(self):
        self.owner = None

    def _operation(self, operation, value=0):
        result = self.call(operation, value, 0, 0, 0)
        if result == -1:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        return result

    def __enter__(self):
        if self.owner is not None:
            raise RuntimeError('Root exec policy scope is already active')
        _require_disconnected_root('BIND9_NATIVE_STATE_LAB',
            'Restricted root exec requires an explicitly enabled disconnected disposable root')
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.call = self.libc.prctl
        self.call.argtypes = [ctypes.c_int] + [ctypes.c_ulong] * 4
        self.call.restype = ctypes.c_int
        self.original = self._operation(self.PR_GET_SECUREBITS)
        self._operation(self.PR_SET_SECUREBITS, self.original | self.SECBIT_NOROOT)
        self.owner = threading.get_ident()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.owner != threading.get_ident():
            raise RuntimeError('Root exec policy must be restored by its owning thread')
        self._operation(self.PR_SET_SECUREBITS, self.original)
        if self._operation(self.PR_GET_SECUREBITS) != self.original:
            raise RuntimeError('The original root exec policy was not restored')
        self.owner = None
        return False


class CanonicalFrontEnvironment:
    """Install CLI doubles only in an explicitly selected disconnected container.

    Moving the private fixture state preserves owner/mode/inode/link tests.
    Commands and assertions stay native; neither production PATH nor source
    constants are rewritten. This adapter does not measure coverage itself.
    """

    STATE = Path('/var/lib/Bind9-Ansible')
    installed = None
    links = {}

    def __init__(self, root):
        _require_disconnected_root('BIND9_CANONICAL_BASH_LAB',
            'Canonical Bash lab requires an explicitly enabled disconnected disposable root')
        target = Path(root) / 'tests/fixtures/native_network_cli.py'
        if self.installed is None:
            commands = [Path('/usr/sbin') / name for name in ('ip', 'nmcli')]
            if any(path.exists() or path.is_symlink() for path in commands):
                raise RuntimeError('Canonical Bash lab refuses existing native network commands')
            type(self).installed = target
            type(self).links = {}
            try:
                for path in commands:
                    path.symlink_to(target)
                    type(self).links[path] = self.stamp(path)
            except BaseException:
                type(self).release()
                raise
        elif self.installed != target or any(not self.owned(path) for path in self.links):
            raise RuntimeError('Canonical Bash command boundaries changed')

    @staticmethod
    def stamp(path):
        """Bind cleanup to the exact link, including replacement-sensitive ctime."""
        value = path.lstat()
        return value.st_dev, value.st_ino, value.st_ctime_ns, value.st_mode, value.st_nlink

    @classmethod
    def owned(cls, path):
        try:
            return (path.is_symlink() and Path(os.readlink(str(path))) == cls.installed and
                    cls.stamp(path) == cls.links[path])
        except OSError:
            return False

    @classmethod
    def release(cls):
        """Release a whole session's exact fixtures so the next batch starts clean."""
        if any(not cls.owned(path) for path in cls.links):
            raise RuntimeError('Canonical Bash command identity changed; preserve boundaries for inspection')
        for path in cls.links:
            path.unlink()
        cls.links, cls.installed = {}, None

    @contextmanager
    def rebased(self, fixture_state, binaries, environment):
        """Restore each fixture object even when the native transaction fails."""
        state = self.STATE
        if state.exists() or state.is_symlink():
            raise RuntimeError('Canonical Bash state is already occupied')
        if fixture_state.parent.stat().st_dev != state.parent.stat().st_dev:
            raise RuntimeError('Canonical Bash lab requires one native private filesystem')
        if fixture_state.exists() or fixture_state.is_symlink():
            fixture_state.rename(state)
        installed = []
        try:
            for wrapper in binaries.iterdir():
                if wrapper.name in ('ip', 'nmcli', 'python3'):
                    continue
                command = Path('/usr/sbin') / wrapper.name
                if command.exists() or command.is_symlink():
                    raise RuntimeError('Canonical Bash lab refuses an occupied utility boundary')
                command.symlink_to(wrapper)
                installed.append(command)
            yield {key: value.replace(str(fixture_state), str(state)) for key, value in environment.items()}
        finally:
            for command in installed:
                command.unlink()
            if state.exists() or state.is_symlink():
                if fixture_state.exists() or fixture_state.is_symlink():
                    raise RuntimeError('Canonical Bash fixture destination was replaced')
                state.rename(fixture_state)

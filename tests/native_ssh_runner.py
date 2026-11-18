"""Genuine SSH transport restricted to a disposable container's own loopback.

No host port, host key or credential is supplied by the user. Every identity is
generated in a private temporary directory and used only by that container.
"""
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import sys
import time

from native_ansible_runner import NativeContainerRun, NativeTaskRun


class LoopbackSshEnvironment:
    """Own one bounded sshd process, private keys and a strict known-host record."""

    def __init__(self, folder, network_state):
        if (os.environ.get('BIND9_NATIVE_SSH_LAB') != '1' or
                os.environ.get('BIND9_NATIVE_STATE_LAB') != '1' or os.geteuid() != 0 or
                not Path('/.dockerenv').is_file() or
                {path.name for path in Path('/sys/class/net').iterdir()} != {'lo'}):
            raise ValueError('SSH integration requires its disconnected disposable root')
        self.folder, self.network_state = Path(folder).resolve(), Path(network_state).resolve()
        self.folder.relative_to('/tmp')
        self.network_state.relative_to('/tmp')
        if (self.folder.parent.stat().st_dev != Path('/').stat().st_dev or
                self.network_state.stat().st_dev != Path('/').stat().st_dev):
            raise ValueError('SSH fixture refuses host/volume-backed private resources')
        if not re.fullmatch(r'/[A-Za-z0-9_./-]+', str(self.network_state)):
            raise ValueError('SSH fixture state requires an unambiguous private path')
        self.process = self.log = None
        self.runtime_created = False
        self.authorized_identity = None

    def __enter__(self):
        self.folder.mkdir(mode=0o700)
        for name in ('client', 'host'):
            subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'private-qa',
                '-f', str(self.folder / name)], check=True, capture_output=True, timeout=10)
        # sshd's StrictModes checks ancestors too: /tmp is intentionally
        # writable, so keep the public authorization beneath our private /run.
        authorized = Path('/run/sshd/authorized_keys')
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as selected:
            selected.bind(('127.0.0.1', 0))
            self.port = selected.getsockname()[1]
        self.known_hosts = self.folder / 'known_hosts'
        self.known_hosts.write_text('[127.0.0.1]:' + str(self.port) + ' ' + (self.folder / 'host.pub').read_text())
        self.known_hosts.chmod(0o600)
        config = self.folder / 'sshd_config'
        config.write_text('\n'.join([
            'ListenAddress 127.0.0.1', 'Port ' + str(self.port), 'HostKey ' + str(self.folder / 'host'),
            'PidFile ' + str(self.folder / 'sshd.pid'), 'AuthorizedKeysFile ' + str(authorized),
            'PermitRootLogin prohibit-password', 'PasswordAuthentication no', 'KbdInteractiveAuthentication no',
            'AuthenticationMethods publickey', 'UsePAM yes', 'StrictModes yes', 'AllowUsers root',
            'PermitUserEnvironment no', 'Subsystem sftp internal-sftp',
            'SetEnv BIND_NETWORK_DOUBLE_STATE=' + str(self.network_state) + ' BIND9_NATIVE_STATE_LAB=1',
            'LogLevel VERBOSE', '']))
        config.chmod(0o600)
        runtime = Path('/run/sshd')
        if not runtime.exists():
            runtime.mkdir(mode=0o755)
            self.runtime_created = True
        else:
            raise ValueError('SSH fixture refuses an occupied runtime directory')
        try:
            authorized.write_bytes((self.folder / 'client.pub').read_bytes())
            authorized.chmod(0o600)
            info = authorized.lstat()
            self.authorized_identity = (info.st_dev, info.st_ino, info.st_ctime_ns, info.st_mode, info.st_nlink)
            subprocess.run(['/usr/sbin/sshd', '-t', '-f', str(config)], check=True, capture_output=True, timeout=5)
            self.log = (self.folder / 'server.log').open('xb')
            self.process = subprocess.Popen(['/usr/sbin/sshd', '-D', '-e', '-f', str(config)],
                stdout=subprocess.DEVNULL, stderr=self.log, start_new_session=True)
            for _ in range(100):
                if self.process.poll() is not None:
                    raise RuntimeError('Private SSH listener stopped during startup')
                try:
                    with socket.create_connection(('127.0.0.1', self.port), timeout=0.05):
                        break
                except OSError:
                    time.sleep(0.02)
            else:
                raise RuntimeError('Private SSH listener did not become ready')
            # An independent client verifies both authentication and the pinned
            # host key before Ansible is allowed to use this test endpoint.
            probe = subprocess.run(['/usr/bin/ssh', '-i', str(self.folder / 'client'),
                '-p', str(self.port)] + self.client_options() + ['root@127.0.0.1', 'true'],
                capture_output=True, text=True, timeout=10)
            if probe.returncode:
                raise RuntimeError('Private SSH authentication failed: ' + probe.stderr[:1024] +
                    (self.folder / 'server.log').read_text()[-2048:])
            return self
        except BaseException:
            self.__exit__(None, None, None)
            raise

    def client_options(self):
        """Pin the exact host key; never use agents, passwords or shared sockets."""
        return ['-F', '/dev/null', '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(self.known_hosts),
            '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes',
            '-o', 'IdentityAgent=none', '-o', 'ClearAllForwardings=yes',
            '-o', 'ControlMaster=no', '-o', 'ControlPersist=no', '-o', 'ConnectTimeout=5']

    def __exit__(self, exc_type, exc_value, traceback):
        if self.process is not None:
            if self.process.poll() is None:
                os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        if self.log is not None:
            self.log.close()
        if self.runtime_created:
            authorized = Path('/run/sshd/authorized_keys')
            if self.authorized_identity is not None:
                info = authorized.lstat()
                if (info.st_dev, info.st_ino, info.st_ctime_ns, info.st_mode, info.st_nlink) != self.authorized_identity:
                    raise ValueError('Private SSH authorization changed; preserve it for inspection')
                authorized.unlink()
            Path('/run/sshd').rmdir()


class NativeFrontRun(NativeContainerRun):
    """Execute canonical FRONT tasks against their disposable native state."""

    SAFE_ACTIONS = NativeTaskRun.SAFE_ACTIONS | {'ansible.builtin.command', 'ansible.builtin.script'}
    FILES = {'tasks.d/deploy/front_return_routes.yml'}
    # CanonicalFrontEnvironment owns and releases its exact CLI links at the
    # pytest session boundary; a second cleanup owner must never unlink them.
    TARGETS = ('/var/lib/Bind9-Ansible',)

    def safe_context(self, task):
        return super().safe_context(task) or (task.get('become') is False and 'delegate_to' not in task and
            task.get('name') == 'Read the real SSH peer before any route mutation' and
            task.get('ansible.builtin.command', {}).get('argv') == ['printenv', 'SSH_CONNECTION'])

    def transport_variables(self):
        values = super().transport_variables()
        values['ansible_host'] = '127.0.0.1'
        return values


class NativeSshFrontRun(NativeFrontRun):
    """Use a real loopback SSH session with the same inspected task contract."""

    def __init__(self, filename, variables, ssh=None, **kwargs):
        if ssh is not None and (type(ssh) is not LoopbackSshEnvironment or
                ssh.process is None or ssh.process.poll() is not None):
            raise ValueError('FRONT SSH requires its exact live private listener')
        self.ssh = ssh
        super().__init__(filename, variables, **kwargs)

    def run(self):
        if self.ssh is None or self.ssh.process.poll() is not None:
            raise ValueError('FRONT SSH requires its exact live private listener')
        return super().run()

    def transport_variables(self):
        if self.ssh.process.poll() is not None:
            raise ValueError('The private SSH listener is no longer active')
        return dict(ansible_connection='ssh', ansible_host='127.0.0.1', ansible_port=self.ssh.port,
            ansible_user='root', ansible_become=False, ansible_private_key_file=str(self.ssh.folder / 'client'),
            ansible_ssh_args=' '.join(map(shlex.quote, self.ssh.client_options())),
            role_path=str(self.role), ansible_python_interpreter=sys.executable)


class NativePeerCommandRun(NativeFrontRun):
    """Declare one owned printenv fault boundary for SSH refusal scenarios."""

    COMMANDS = ('/usr/local/bin/printenv',)
    TARGETS = NativeFrontRun.TARGETS + COMMANDS

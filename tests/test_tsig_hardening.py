"""Reproduce unsafe TSIG filesystem inputs without exposing secret material."""
import base64
import fcntl
import importlib.util
import os
from pathlib import Path
import runpy
import subprocess
import sys
import stat

import pytest
import yaml

SCRIPT = Path(__file__).resolve().parents[1] / 'roles/bind9/files/tsig-secret.py'
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='Requires POSIX descriptors and FIFO semantics')


class TestTsigFilesystem:
    """A malformed local object must fail promptly, without writing a new secret."""

    @staticmethod
    def run(path):
        try:
            return subprocess.run([sys.executable, str(SCRIPT), str(path)],
                                  capture_output=True, text=True, timeout=2)
        except subprocess.TimeoutExpired:
            pytest.fail('TSIG provisioning blocked on an unsafe filesystem object')

    def test_fifo_secret_fails_without_blocking(self, tmp_path):
        tmp_path.chmod(0o700)
        secret = tmp_path / 'site.key'
        os.mkfifo(secret, 0o600)
        result = self.run(secret)
        assert result.returncode != 0
        assert secret.is_fifo()

    @pytest.mark.parametrize('kind', ['fifo', 'public', 'hardlink'])
    def test_unsafe_lock_is_not_used(self, tmp_path, kind):
        tmp_path.chmod(0o700)
        secret = tmp_path / 'site.key'
        lock = tmp_path / '.site.key.lock'
        if kind == 'fifo':
            os.mkfifo(lock, 0o600)
        else:
            lock.write_text('unrelated content\n')
            lock.chmod(0o666 if kind == 'public' else 0o600)
            if kind == 'hardlink':
                os.link(lock, tmp_path / 'other.lock')
        result = self.run(secret)
        assert result.returncode != 0
        assert not secret.exists()

    def test_ancestor_symlink_is_rejected(self, tmp_path):
        (tmp_path / 'real/private').mkdir(parents=True, mode=0o700)
        (tmp_path / 'alias').symlink_to(tmp_path / 'real', target_is_directory=True)
        result = self.run(tmp_path / 'alias/private/site.key')
        assert result.returncode != 0
        assert not (tmp_path / 'real/private/site.key').exists()

    def test_hardlinked_secret_is_rejected_unchanged(self, tmp_path):
        tmp_path.chmod(0o700)
        secret = tmp_path / 'site.key'
        data = base64.b64encode(bytes(range(32))) + b'\n'
        secret.write_bytes(data)
        secret.chmod(0o600)
        os.link(secret, tmp_path / 'copy.key')
        assert self.run(secret).returncode != 0
        assert secret.read_bytes() == data

    def test_noncanonical_secret_is_not_silently_normalized(self, tmp_path):
        tmp_path.chmod(0o700)
        secret = tmp_path / 'site.key'
        data = b' ' + base64.b64encode(bytes(range(32))) + b'\t\n'
        secret.write_bytes(data)
        secret.chmod(0o600)
        assert self.run(secret).returncode != 0
        assert secret.read_bytes() == data

    @staticmethod
    def module():
        spec = importlib.util.spec_from_file_location('tsig_store_test', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_write_failure_leaves_no_published_or_temporary_secret(self, tmp_path, monkeypatch):
        tmp_path.chmod(0o700)
        module = self.module()
        def fail_sync(descriptor):
            raise OSError('Controlled disk sync failure')
        monkeypatch.setattr(module.os, 'fsync', fail_sync)
        with pytest.raises(OSError, match='Controlled disk sync failure'):
            module.provision(tmp_path / 'site.key')
        assert not (tmp_path / 'site.key').exists()
        assert not list(tmp_path.glob('*.tmp'))

    def test_lock_timeout_is_explicit(self, tmp_path):
        module = self.module()
        lock = tmp_path / 'lock'
        with lock.open('w') as owner, lock.open('r') as contender:
            fcntl.flock(owner, fcntl.LOCK_EX)
            with pytest.raises(ValueError, match='timed out'):
                module.TsigSecretStore(tmp_path / 'site.key', lock_timeout=0).acquire(contender.fileno())

    @pytest.mark.parametrize('raw', [b'', b'not-base64!', base64.b64encode(bytes(31)),
                                   b'A' * 10000, b'A' * 42 + b'B='])
    def test_invalid_existing_content_remains_unchanged(self, tmp_path, raw):
        tmp_path.chmod(0o700)
        secret = tmp_path / 'site.key'
        secret.write_bytes(raw)
        secret.chmod(0o600)
        with pytest.raises(ValueError):
            self.module().provision(secret)
        assert secret.read_bytes() == raw

    @pytest.mark.parametrize('path', ['relative.key', '/tmp/../unsafe.key'])
    def test_noncanonical_absolute_path_is_required(self, path):
        with pytest.raises(ValueError, match='absolute'):
            self.module().provision(Path(path))

    def test_current_user_must_own_the_parent(self, tmp_path):
        if os.geteuid() != 0:
            pytest.skip('Requires root to construct a foreign-owned directory')
        parent = tmp_path / 'foreign'
        parent.mkdir(mode=0o700)
        os.chown(parent, 1, -1)
        assert self.run(parent / 'site.key').returncode != 0
        assert not (parent / 'site.key').exists()

    def test_concurrent_generators_publish_one_key(self, tmp_path):
        tmp_path.chmod(0o700)
        key = tmp_path / 'site.key'
        children = [subprocess.Popen([sys.executable, str(SCRIPT), str(key)], stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True) for _ in range(6)]
        outputs = [child.communicate(timeout=10) for child in children]
        assert all(child.returncode == 0 for child in children)
        assert [stdout.strip() for stdout, _ in outputs].count('CREATED') == 1
        assert [stdout.strip() for stdout, _ in outputs].count('UNCHANGED') == 5
        assert len(base64.b64decode(key.read_bytes().strip(), validate=True)) == 32

    def test_cli_reports_idempotence_and_failure_without_secret(self, tmp_path, monkeypatch, capsys):
        tmp_path.chmod(0o700)
        key = tmp_path / 'site.key'
        monkeypatch.setattr(sys, 'argv', [str(SCRIPT), str(key)])
        runpy.run_path(str(SCRIPT), run_name='__main__')
        assert capsys.readouterr().out.strip() == 'CREATED'
        runpy.run_path(str(SCRIPT), run_name='__main__')
        assert capsys.readouterr().out.strip() == 'UNCHANGED'
        key.write_text('invalid')
        with pytest.raises(SystemExit) as error:
            runpy.run_path(str(SCRIPT), run_name='__main__')
        assert error.value.code == 1
        output = capsys.readouterr()
        assert output.out == '' and 'provisioning failed' in output.err

    def test_direct_calls_refuse_unsafe_parent_and_file(self, tmp_path):
        module = self.module()
        tmp_path.chmod(0o755)
        with pytest.raises(ValueError, match='parent'):
            module.provision(tmp_path / 'site.key')
        tmp_path.chmod(0o700)
        os.mkfifo(tmp_path / 'site.key', 0o600)
        with pytest.raises(ValueError, match='Unsafe TSIG'):
            module.provision(tmp_path / 'site.key')
        assert (tmp_path / 'site.key').is_fifo()

    def test_short_lock_contention_retries_within_deadline(self, tmp_path, monkeypatch):
        module = self.module()
        original = module.fcntl.flock
        calls = []
        def contend_once(descriptor, flags):
            calls.append(flags)
            if len(calls) == 1:
                raise BlockingIOError('Controlled brief contention')
            return original(descriptor, flags)
        monkeypatch.setattr(module.fcntl, 'flock', contend_once)
        with (tmp_path / 'lock').open('w') as stream:
            module.TsigSecretStore(tmp_path / 'site.key', lock_timeout=1).acquire(stream.fileno())
        assert len(calls) == 2

    def test_writable_ancestor_cannot_redirect_the_controller_lookup(self, tmp_path):
        parent = tmp_path / 'public/private'
        parent.mkdir(parents=True, mode=0o700)
        parent.parent.chmod(0o777)
        with pytest.raises(ValueError, match='ancestor'):
            self.module().provision(parent / 'site.key')
        assert not (parent / 'site.key').exists()

    def test_atomic_temporary_name_does_not_shorten_supported_key_names(self, tmp_path):
        tmp_path.chmod(0o700)
        key = tmp_path / ('s' * 220 + '.key')
        store = self.module()
        assert store.provision(key) is True
        assert store.provision(key) is False
        assert len(base64.b64decode(key.read_bytes().strip(), validate=True)) == 32

    def test_parent_creation_is_private_and_idempotent(self, tmp_path):
        key = tmp_path / 'new/private/site.key'
        assert self.module().TsigSecretStore(key, create_parent=True).provision() is True
        for expected in ('UNCHANGED', 'UNCHANGED'):
            result = subprocess.run([sys.executable, str(SCRIPT), str(key), '--create-parent'],
                                    capture_output=True, text=True, timeout=5)
            assert result.returncode == 0 and result.stdout.strip() == expected
        assert stat.S_IMODE(key.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(key.parent.parent.stat().st_mode) == 0o700

    def test_parent_creation_never_changes_a_redirected_directory(self, tmp_path):
        target = tmp_path / 'target'
        target.mkdir(mode=0o755)
        (tmp_path / 'alias').symlink_to(target, target_is_directory=True)
        result = subprocess.run([sys.executable, str(SCRIPT), str(tmp_path / 'alias/private/site.key'),
                                 '--create-parent'], capture_output=True, text=True, timeout=5)
        assert result.returncode != 0
        assert stat.S_IMODE(target.stat().st_mode) == 0o755
        assert list(target.iterdir()) == []

    def test_real_role_creates_and_reuses_one_private_site_secret(self, tmp_path):
        """Exercise the real TSIG task entry point, including its controller lookup."""
        key = tmp_path / 'site/private/site.key'
        interpreter = tmp_path / 'python-compatible'
        interpreter.symlink_to(sys.executable)
        role = SCRIPT.parents[1]
        playbook = tmp_path / 'tsig.yml'
        playbook.write_text(yaml.safe_dump([{
            'hosts': 'localhost', 'gather_facts': False,
            'vars': {'role_path': str(role), 'ansible_python_interpreter': str(interpreter),
                     'bind_tsig_key_name': 'test-site',
                     'bind_tsig_algorithm': 'hmac-sha256', 'bind_tsig_secret_file': str(key)},
            'tasks': [{'import_tasks': str(role / 'tasks/tasks.d/tsig.yml')}]
        }]))
        contents = []
        for _ in range(2):
            result = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(playbook)],
                                    capture_output=True, text=True, timeout=60)
            assert result.returncode == 0, result.stdout + result.stderr
            contents.append(key.read_bytes())
            assert key.read_text().strip() not in result.stdout + result.stderr
        assert contents[0] == contents[1]
        assert stat.S_IMODE(key.parent.stat().st_mode) == 0o700

    def test_real_role_refuses_symlink_before_creating_or_chmodding(self, tmp_path):
        """The old preliminary file task created through this alias before refusing it."""
        target = tmp_path / 'target'
        target.mkdir(mode=0o755)
        (tmp_path / 'alias').symlink_to(target, target_is_directory=True)
        interpreter = tmp_path / 'python-compatible'
        interpreter.symlink_to(sys.executable)
        role = SCRIPT.parents[1]
        playbook = tmp_path / 'unsafe-tsig.yml'
        playbook.write_text(yaml.safe_dump([{
            'hosts': 'localhost', 'gather_facts': False,
            'vars': {'role_path': str(role), 'ansible_python_interpreter': str(interpreter),
                     'bind_tsig_key_name': 'test-site',
                     'bind_tsig_algorithm': 'hmac-sha256',
                     'bind_tsig_secret_file': str(tmp_path / 'alias/private/site.key')},
            'tasks': [{'import_tasks': str(role / 'tasks/tasks.d/tsig.yml')}]
        }]))
        result = subprocess.run(['ansible-playbook', '-i', 'localhost,', '-c', 'local', str(playbook)],
                                capture_output=True, text=True, timeout=60)
        assert result.returncode != 0
        assert stat.S_IMODE(target.stat().st_mode) == 0o755
        assert list(target.iterdir()) == []

from pathlib import Path
import base64
import importlib.util
import os
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'roles/bind9/files/tsig-secret.py'


def test_tsig_creation_idempotence(tmp_path):
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    secret = private / 'site.key'
    a = subprocess.run([sys.executable, str(SCRIPT), str(secret)], capture_output=True, text=True)
    assert a.returncode == 0 and a.stdout.strip() == 'CREATED'
    first = secret.read_bytes()
    assert len(base64.b64decode(first.strip(), validate=True)) == 32
    assert stat.S_IMODE(secret.stat().st_mode) == 0o600
    b = subprocess.run([sys.executable, str(SCRIPT), str(secret)], capture_output=True, text=True)
    assert b.returncode == 0 and b.stdout.strip() == 'UNCHANGED'
    assert secret.read_bytes() == first
    assert first.decode().strip() not in a.stdout + b.stdout


def test_tsig_rejects_symlink_and_weak_permissions(tmp_path):
    private = tmp_path / 'private'
    private.mkdir(mode=0o700)
    real = private / 'real.key'
    real.write_text('unsafe')
    (private / 'site.key').symlink_to(real)
    result = subprocess.run([sys.executable, str(SCRIPT), str(private / 'site.key')], capture_output=True)
    assert result.returncode != 0
    private.chmod(0o755)
    result = subprocess.run([sys.executable, str(SCRIPT), str(private / 'other.key')], capture_output=True)
    assert result.returncode != 0


def test_tsig_integration_is_before_validation():
    main = (ROOT / 'roles/bind9/tasks/main.yml').read_text()
    assert main.index('import_tasks: tasks.d/tsig.yml') < main.index('import_tasks: tasks.d/validate.yml')
    tsig = (ROOT / 'roles/bind9/tasks/tasks.d/tsig.yml').read_text()
    assert 'no_log: true' in tsig
    assert 'bind_tsig_secret:' in tsig and 'bind_tsig_name:' not in tsig

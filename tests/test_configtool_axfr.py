"""Check the integrated FRONT AXFR security subcommand."""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'roles/bind9/files/bind-configtool.sh'


def invoke(tmp_path, dig_body, signed_key=True):
    dig = tmp_path / 'dig'
    dig.write_text('#!/bin/sh\n' + dig_body + '\n')
    dig.chmod(0o755)
    key = tmp_path / 'tsig.key'
    if signed_key:
        key.write_text('key "test" { secret "test"; };\n')
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'])
    return subprocess.run(['bash', str(SCRIPT), 'check-front-axfr', '192.0.2.1',
                           'example.test', '192.0.2.20', str(key)],
                          capture_output=True, text=True, env=env)


def test_explicit_refusal_for_both(tmp_path):
    result = invoke(tmp_path, 'echo "Transfer failed" >&2; exit 9')
    assert result.returncode == 0
    assert result.stdout.count('PASS:') == 2


def test_successful_axfr_is_security_failure(tmp_path):
    result = invoke(tmp_path, 'exit 0')
    assert result.returncode == 1
    assert 'FAIL' in result.stderr


def test_ambiguous_failure_is_not_a_pass(tmp_path):
    result = invoke(tmp_path, 'echo "timed out" >&2; exit 9')
    assert result.returncode == 2
    assert 'INCONCLUSIVE' in result.stderr


def test_missing_tsig_is_not_a_pass(tmp_path):
    result = invoke(tmp_path, 'echo "Transfer failed" >&2; exit 9', signed_key=False)
    assert result.returncode == 2


def test_standalone_script_removed():
    assert not (SCRIPT.parents[3] / 'scripts/check-front-axfr.sh').exists()

"""Check the integrated FRONT AXFR security subcommand."""
import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'roles/bind9/files/bind-configtool.sh'


def invoke(tmp_path, dig_body, signed_key=True, key_mode=0o600):
    dig = tmp_path / 'dig'
    dig.write_text('#!/bin/sh\n' + dig_body + '\n')
    dig.chmod(0o755)
    key = tmp_path / 'tsig.key'
    if signed_key:
        key.write_text('key "test" { secret "test"; };\n')
        key.chmod(key_mode)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'])
    return subprocess.run(['bash', str(SCRIPT), 'check-front-axfr', '192.0.2.1',
                           'example.test', '192.0.2.20', str(key)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=env)


def test_explicit_refusal_for_both(tmp_path):
    result = invoke(tmp_path, 'echo "status: REFUSED"; exit 9')
    assert result.returncode == 0
    assert result.stdout.count('DENIED:') == 2


def test_successful_axfr_is_security_failure(tmp_path):
    result = invoke(tmp_path, 'echo "example.test. 300 IN SOA ns.example.test. mail.example.test. 1 3600 900 1209600 300"; exit 0')
    assert result.returncode == 1
    assert 'FAIL' in result.stderr


def test_ambiguous_failure_is_not_a_pass(tmp_path):
    result = invoke(tmp_path, 'echo "timed out" >&2; exit 9')
    assert result.returncode == 2
    assert 'INCONCLUSIVE' in result.stderr


def test_generic_transfer_failure_connection_failure_or_empty_output_is_inconclusive(tmp_path):
    for body in ('echo "Transfer failed"; exit 9', 'echo "connection refused"; exit 9', 'exit 0',
                 'echo "status: NOTAUTH BADKEY"; exit 9'):
        result = invoke(tmp_path, body)
        assert result.returncode == 2
        assert 'INCONCLUSIVE' in result.stderr


def test_yaml_capability_detection_accepts_optional_no_spelling_in_real_dig_help(tmp_path):
    # Actual dig 9.18 help advertises +[no]yaml, not +yaml. Without
    # recognizing it, an explicit wire refusal is lost in generic text output.
    for help_text in ('+[no]yaml', '+yaml'):
        body = ('if [ "$1" = "-h" ]; then echo "' + help_text + '"; exit 0; fi\n'
                'case "$*" in *+yaml*) echo "status: REFUSED";; *) echo "Transfer failed";; esac\nexit 9')
        result = invoke(tmp_path, body)
        assert result.returncode == 0, result.stderr
        assert result.stdout.count('DENIED:') == 2


def test_missing_tsig_is_not_a_pass(tmp_path):
    result = invoke(tmp_path, 'echo "Transfer failed" >&2; exit 9', signed_key=False)
    assert result.returncode == 2


def test_publicly_readable_key_is_rejected_before_dns_queries(tmp_path):
    result = invoke(tmp_path, 'echo "status: REFUSED"; exit 9', key_mode=0o644)
    assert result.returncode == 2
    assert 'DENIED:' not in result.stdout


def test_standalone_script_removed():
    assert not (SCRIPT.parents[3] / 'scripts/check-front-axfr.sh').exists()

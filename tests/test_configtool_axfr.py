"""Check the integrated FRONT AXFR security subcommand."""
import os
import json
import subprocess
from pathlib import Path
import shlex
import sys

import pytest

SCRIPT = Path(os.environ.get('BIND9_CONFIGTOOL_BASELINE', str(
    Path(__file__).resolve().parents[1] / 'roles/bind9/files/bind-configtool.sh')))


def refusal(status='REFUSED', yaml=False):
    """Use wire-header shapes observed from actual dig 9.11 and 9.20."""
    if yaml:
        response = ('- type: MESSAGE\n  message:\n    type: AUTH_RESPONSE\n'
            '    response_message_data:\n      opcode: QUERY\n      status: ' + status + '\n'
            '      id: 7\n      flags: qr\n      QUESTION: 1\n      ANSWER: 0\n'
            '      AUTHORITY: 0\n      ADDITIONAL: 0\n; Transfer failed.\n')
    else:
        response = (';; Got answer:\n;; ->>HEADER<<- opcode: QUERY, status: ' + status + ', id: 7\n'
            ';; flags: qr; QUERY: 1, ANSWER: 0, AUTHORITY: 0, ADDITIONAL: 0\n\n; Transfer failed.\n')
    return "printf '%s' " + shlex.quote(response) + '\nexit 9'


def invoke(tmp_path, dig_body, signed_key=True, key_mode=0o600, help_text='legacy dig help', zone='example.test', key_link=False):
    """Launch the public CLI with a synthetic regular or linked TSIG object."""
    dig = tmp_path / 'dig'
    dig.write_text('#!/bin/sh\nif [ "$1" = "-h" ]; then printf "%s\\n" ' +
        shlex.quote(help_text) + '; exit 0; fi\n' + dig_body + '\n')
    dig.chmod(0o755)
    key = tmp_path / 'tsig.key'
    if signed_key:
        key.write_text('key "test" { secret "test"; };\n')
        key.chmod(key_mode)
        if key_link:
            target = tmp_path / 'operator-tsig.key'
            key.rename(target)
            key.symlink_to(target)
    env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'])
    return subprocess.run(['bash', str(SCRIPT), 'check-front-axfr', '192.0.2.1',
                           zone, '192.0.2.20', str(key)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, env=env, timeout=15)


def test_explicit_refusal_for_both(tmp_path):
    result = invoke(tmp_path, refusal())
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
                 'echo "status: NOTAUTH BADKEY"; exit 9', 'echo "status: NOTAUTH BADSIG"; exit 9',
                 'echo "status: NOTAUTH BADTIME"; exit 9'):
        result = invoke(tmp_path, body)
        assert result.returncode == 2
        assert 'INCONCLUSIVE' in result.stderr


def test_native_text_refusal_is_supported_even_when_modern_dig_advertises_yaml(tmp_path):
    # YAML omits native missing-signature diagnostics; the common text format
    # must retain them for both unsigned and signed checks on every version.
    for help_text in ('+[no]yaml', '+yaml'):
        body = 'case "$*" in *+yaml*) exit 8;; esac\n' + refusal()
        result = invoke(tmp_path, body, help_text=help_text)
        assert result.returncode == 0, result.stderr
        assert result.stdout.count('DENIED:') == 2


def test_missing_tsig_is_not_a_pass(tmp_path):
    result = invoke(tmp_path, 'echo "Transfer failed" >&2; exit 9', signed_key=False)
    assert result.returncode == 2


def test_linked_tsig_is_refused_before_any_dns_query(tmp_path):
    """A readable private target does not make a symbolic key path acceptable."""
    marker = tmp_path / 'unexpected-query'
    result = invoke(tmp_path, 'printf x > ' + shlex.quote(str(marker)) + '\n' + refusal(), key_link=True)
    assert result.returncode == 2 and 'not a symlink' in result.stderr
    assert 'DENIED:' not in result.stdout and not marker.exists()
    assert (tmp_path / 'tsig.key').is_symlink()
    assert (tmp_path / 'operator-tsig.key').read_text() == 'key "test" { secret "test"; };\n'


def test_legacy_dig_uses_explicit_comments_to_expose_wire_refusal(tmp_path):
    """dig 9.11 suppresses AXFR RCODE unless comment output is requested."""
    body = ('case "$*" in *+yaml*) echo "Invalid option: +yaml"; exit 1;; esac\n'
            'case "$*" in *+noall*+comments*+answer*) ' + refusal() + '\n;; '
            '*) echo "Transfer failed"; exit 9;; esac')
    result = invoke(tmp_path, body)
    assert result.returncode == 0, result.stderr
    assert result.stdout.count('DENIED:') == 2


def test_failed_stat_cannot_authorize_reading_a_tsig_key(tmp_path):
    """Partial metadata from a failed producer must never establish key safety."""
    stat = tmp_path / 'stat'
    stat.write_text('#!/bin/sh\nprintf "%s\\n" "' + str(os.geteuid()) + ' 600 1"\nexit 1\n')
    stat.chmod(0o755)
    result = invoke(tmp_path, 'echo "status: REFUSED"; exit 9')
    assert result.returncode == 2
    assert 'DENIED:' not in result.stdout


@pytest.mark.skipif(os.name != 'posix' or os.geteuid() != 0 or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                   reason='Requires an explicitly enabled disconnected Linux root')
def test_unreadable_key_is_refused_under_native_restricted_root_exec(tmp_path):
    """Exercise real kernel readability checks; keep the observer's UID unchanged."""
    from canonical_shell_lab import RestrictedRootExec
    key = tmp_path / 'tsig.key'
    key.write_bytes(b'synthetic unreadable test key\n')
    key.chmod(0)
    marker = tmp_path / 'unexpected-dns-query'
    command = [sys.executable, '-c',
        'import os,sys;print(os.geteuid(),int(os.access(sys.argv[1],os.R_OK)))', str(key)]
    normal = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, check=True, timeout=5)
    assert normal.stdout == '0 1\n'
    scope = RestrictedRootExec()
    with scope:
        with pytest.raises(RuntimeError, match='already active'):
            scope.__enter__()
        restricted = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, check=True, timeout=5)
        assert restricted.stdout == '0 0\n', 'The independent child must really lack the readability bypass'
        result = invoke(tmp_path, 'printf x > ' + shlex.quote(str(marker)) + '\n' + refusal(), key_mode=0)
    restored = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, check=True, timeout=5)
    assert restored.stdout == '0 1\n', 'The native caller policy must be restored for subsequent tests'
    assert result.returncode == 2 and 'readable regular file' in result.stderr
    assert 'DENIED:' not in result.stdout and not marker.exists()


@pytest.mark.skipif(os.name != 'posix' or os.geteuid() != 0 or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                   reason='Requires an explicitly enabled disconnected Linux root')
def test_unavailable_native_file_size_limit_refuses_before_dns_execution(tmp_path):
    """A stricter inherited hard limit must not hide failure to establish the requested bound."""
    evidence = tmp_path / 'file-limit.json'
    dns_marker = tmp_path / 'unexpected-dns-query'
    helper = tmp_path / 'limit-stat.py'
    # The stat facade lowers only its own Bash parent's limit. The pytest
    # controller keeps its original limits and can retain complete evidence.
    helper.write_text('import json,os,resource,sys\nfrom pathlib import Path\n'
        'marker=Path(' + repr(str(evidence)) + ')\n'
        'if not marker.exists():\n'
        '    pid=os.getppid()\n'
        '    command=Path("/proc/{}/cmdline".format(pid)).read_bytes().split(bytes([0]))\n'
        '    assert b"check-front-axfr" in command\n'
        '    before=resource.prlimit(pid,resource.RLIMIT_FSIZE,(524288,524288))\n'
        '    after=resource.prlimit(pid,resource.RLIMIT_FSIZE)\n'
        '    marker.write_text(json.dumps(dict(parent_pid=pid,before=before,after=after)))\n'
        'os.execv("/usr/bin/stat",["/usr/bin/stat"]+sys.argv[1:])\n', encoding='ascii')
    facade = tmp_path / 'stat'
    facade.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' +
        shlex.quote(str(helper)) + ' "$@"\n', encoding='ascii')
    facade.chmod(0o755)
    result = invoke(tmp_path, 'printf x > ' + shlex.quote(str(dns_marker)) + '\n' + refusal())
    assert json.loads(evidence.read_text())['after'] == [524288, 524288]
    assert result.returncode == 2 and 'INCONCLUSIVE' in result.stderr
    assert 'DENIED:' not in result.stdout and not dns_marker.exists()


@pytest.mark.parametrize('status', [0, 9])
def test_native_key_load_warning_cannot_qualify_an_unsigned_question_as_signed(tmp_path, status):
    """Real dig 9.11/9.20 can ignore an invalid -k file and still return rc0."""
    response = refusal().rsplit('\n', 1)[0]
    diagnostic = "Couldn't read key from synthetic-private.key: unexpected token"
    body = (response + '\ncase " $* " in *" -k "*) printf "%s\\n" ' +
            shlex.quote(diagnostic) + ' >&2;; esac\nexit ' + str(status))
    result = invoke(tmp_path, body)
    assert result.returncode == 2 and result.stdout.count('DENIED:') == 1
    assert 'INCONCLUSIVE' in result.stderr and diagnostic not in result.stderr


def test_failed_dns_producer_does_not_disclose_its_raw_diagnostic(tmp_path):
    marker = 'synthetic-sensitive-DNS-diagnostic-must-remain-private'
    result = invoke(tmp_path, 'printf "%s\\n" ' + shlex.quote(marker) + ' >&2\nexit 1')
    assert result.returncode == 2 and 'DENIED:' not in result.stdout
    assert 'INCONCLUSIVE' in result.stderr and marker not in result.stderr


def test_publicly_readable_key_is_rejected_before_dns_queries(tmp_path):
    result = invoke(tmp_path, 'echo "status: REFUSED"; exit 9', key_mode=0o644)
    assert result.returncode == 2
    assert 'DENIED:' not in result.stdout


def test_standalone_script_removed():
    assert not (SCRIPT.parents[3] / 'scripts/check-front-axfr.sh').exists()


@pytest.mark.parametrize('response,yaml', [
    (';; note: status: REFUSED\n', False),
    ('example.test. 1 IN TXT "status: REFUSED"\n', False),
    (';; ->>HEADER<<- opcode: QUERY, status: REFUSED, id: 7\n', False),
    (';; ->>HEADER<<- opcode: QUERY, status: REFUSED, id: 7\n'
     ';; flags: qr tc; QUERY: 1, ANSWER: 0, AUTHORITY: 0, ADDITIONAL: 0\n', False),
    (';; ->>HEADER<<- opcode: QUERY, status: REFUSED, id: 7\n'
     ';; flags: aa; QUERY: 1, ANSWER: 0, AUTHORITY: 0, ADDITIONAL: 0\n', False),
    (';; ->>HEADER<<- opcode: QUERY, status: REFUSED, id: 7\n'
     ';; ->>HEADER<<- opcode: QUERY, status: NOERROR, id: 8\n'
     ';; flags: qr; QUERY: 1, ANSWER: 0, AUTHORITY: 0, ADDITIONAL: 0\n', False),
    ('status: REFUSED\n', True),
    ('note: status: REFUSED\n', True),
    ('- type: MESSAGE\n  message:\n    type: AUTH_RESPONSE\n'
     '    response_message_data:\n      opcode: QUERY\n      status: REFUSED\n', True),
    pytest.param(';; ->>HEADER<<- opcode: QUERY, status: REFUSED, id: 7\n'
        ';; flags: qr; QUERY: 1, ANSWER: 1, AUTHORITY: 0, ADDITIONAL: 0\n',
        False, id='nonempty-answer-counter'),
])
def test_axfr_refusal_requires_one_complete_native_response_header(tmp_path, response, yaml):
    body = "printf '%s' " + shlex.quote(response) + '\nexit 9'
    result = invoke(tmp_path, body, help_text='+[no]yaml' if yaml else 'legacy dig help')
    assert result.returncode == 2 and 'DENIED:' not in result.stdout


def test_yaml_status_must_be_the_response_field_even_in_a_complete_looking_envelope(tmp_path):
    body = refusal(yaml=True).replace('      status:', '      note: status:')
    result = invoke(tmp_path, body, help_text='+[no]yaml')
    assert result.returncode == 2 and 'DENIED:' not in result.stdout


@pytest.mark.parametrize('help_text', ['legacy dig help', '+[no]yaml'])
def test_signed_refusal_without_an_authenticated_response_is_inconclusive(tmp_path, help_text):
    # Real dig 9.11 and 9.20 both return rc0 for an unsigned answer to a signed
    # question. Only native text retains this warning; YAML silently omits it.
    body = 'case "$*" in *+yaml*) ' + refusal(yaml=True) + '\n;; esac\ncase "$*" in *-k*)\n'
    body += 'printf "%s\\n" ' + shlex.quote(";; Couldn't verify signature: expected a TSIG or SIG(0)")
    body += '\n;; esac\n' + refusal()
    result = invoke(tmp_path, body, help_text=help_text)
    assert result.returncode == 2 and result.stdout.count('DENIED:') == 1


@pytest.mark.parametrize('prefix', [
    "head -c 1100000 /dev/zero | tr '\\000' 'x'; printf '\\n'\n",
    "printf '\\000'\n",
])
def test_bounded_axfr_observation_rejects_oversized_or_binary_output_before_classification(tmp_path, prefix):
    result = invoke(tmp_path, prefix + refusal())
    assert result.returncode == 2 and 'DENIED:' not in result.stdout


@pytest.mark.parametrize('zone', ['@127.0.0.99', '+trace', '-f'])
def test_zone_is_an_explicit_dig_question_and_cannot_override_query_options(tmp_path, zone):
    # Inspect real process arguments; shell quoting alone cannot prevent dig
    # from treating a positional zone as another server or an option.
    body = 'previous=\nfound=false\nfor value in "$@"; do\n'
    body += '  if [ "$previous" = -q ] && [ "$value" = ' + shlex.quote(zone) + ' ]; then found=true; fi\n'
    body += '  previous=$value\ndone\n[ "$found" = true ] || exit 8\n' + refusal()
    result = invoke(tmp_path, body, zone=zone)
    assert result.returncode == 0 and result.stdout.count('DENIED:') == 2

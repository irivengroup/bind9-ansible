"""Exercise the installed monitor contract using synthetic native DNS replies."""
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def monitor(tmp_path):
    """Only command boundaries are doubles; the production Bash is unchanged."""
    (tmp_path / 'systemctl').write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')
    (tmp_path / 'dig').write_text('''#!/bin/bash
name=${@: -2:1}; kind=${@: -1}
flags='qr aa'; status=NOERROR
if [[ $name == example.test && $PROBE_CASE == flags_after_end ]]; then flags='qr; aa'; fi
if [[ $name == example.test && $PROBE_CASE == flags_in_note ]]; then flags='qr'; fi
if [[ $PROBE_CASE == status_suffix ]]; then status=NOERRORISH; fi
printf ';; status: %s\\n;; flags: %s; QUERY: 1, ANSWER: 1\\n' "$status" "$flags"
case $kind in
 A) printf '%s. 300 IN A 127.0.0.1\\n' "$name" ;;
 PTR) printf '%s. 300 IN PTR localhost.\\n' "$name" ;;
 SOA) printf '%s. 300 IN SOA ns.test. host.test. 12 3600 900 604800 300\\n' "$name" ;;
esac
if [[ $name == example.test && $PROBE_CASE == flags_in_note ]]; then printf ';; note flags: aa\\n'; fi
''', encoding='utf-8')
    for name in ('systemctl', 'dig'):
        (tmp_path / name).chmod(0o755)
    source = Path(os.environ.get('BIND9_BASH_BASELINE', str(ROOT / 'roles/bind9/files')))

    def invoke(scenario):
        env = dict(os.environ, PATH=str(tmp_path) + os.pathsep + os.environ['PATH'], PROBE_CASE=scenario)
        return subprocess.run(['bash', str(source / 'named-healthcheck.sh'), '192.0.2.11', 'example.test'],
                              env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=10)
    return invoke


@pytest.mark.parametrize('scenario', ['flags_after_end', 'flags_in_note', 'status_suffix'])
def test_monitor_refuses_tokens_outside_their_protocol_fields(monitor, scenario):
    result = monitor(scenario)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'OK named active' not in result.stdout


def test_monitor_accepts_native_authoritative_header(monitor):
    result = monitor('valid')
    assert result.returncode == 0, result.stdout + result.stderr
    assert '1 authoritative zone(s)' in result.stdout

"""A complete-looking utility output cannot hide its nonzero exit status."""
import copy
import shutil

import pytest

from test_native_front_routes import BASE, NativeRouteLab, pytestmark as native_marks

pytestmark = native_marks


@pytest.mark.parametrize('boundary', ['capture_cat', 'expected_sort', 'metadata_stat', 'client_journal_sort'])
def test_native_producer_failure_never_commits_complete_looking_output(tmp_path, boundary):
    lab = NativeRouteLab(tmp_path)
    name = {'capture_cat': 'cat', 'expected_sort': 'sort', 'metadata_stat': 'stat',
            'client_journal_sort': 'sort'}[boundary]
    real = shutil.which(name)
    wrapper = tmp_path / 'bin' / name
    wrapper.write_text('''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys
boundary = os.environ['BIND_PRODUCER_BOUNDARY']
marker = Path(os.environ['BIND_PRODUCER_MARKER'])
options = {}
# Preserve the actual input/output of each sort. Only the exact client row
# identifies this pre-journal boundary; no call order or fake status is inferred.
if boundary == 'client_journal_sort':
    options['input'] = sys.stdin.buffer.read()
hit = False
if not marker.exists():
    if boundary == 'capture_cat':
        hit = len(sys.argv) == 2 and sys.argv[1].endswith('/out')
    elif boundary == 'metadata_stat':
        hit = sys.argv[1:3] == ['-c', '%s'] and sys.argv[-1].endswith('/operations.flock')
    elif boundary == 'client_journal_sort':
        hit = options['input'] == b'203.0.113.0/24\\n'
    else:
        state = json.loads(Path(os.environ['BIND_NETWORK_DOUBLE_STATE']).read_text())
        applied = any(not item['persistent'] and item['action'] == '+ipv4.routes' for item in state['operations'])
        if applied:
            counter = Path(os.environ['BIND_PRODUCER_COUNTER'])
            count = int(counter.read_text()) + 1 if counter.exists() else 1
            counter.write_text(str(count))
            hit = count == 3
result = subprocess.run([os.environ['BIND_PRODUCER_REAL']] + sys.argv[1:], **options)
if hit:
    assert result.returncode == 0
    marker.touch()
    print('Injected producer failure after complete output', file=sys.stderr)
    sys.exit(2)
sys.exit(result.returncode)
''', encoding='utf-8')
    wrapper.chmod(0o755)
    marker = tmp_path / 'producer-failed'
    lab.env.update(BIND_PRODUCER_BOUNDARY=boundary, BIND_PRODUCER_REAL=real,
                   BIND_PRODUCER_MARKER=str(marker), BIND_PRODUCER_COUNTER=str(tmp_path / 'producer-counter'))
    before = copy.deepcopy(lab.data)
    result = lab.run()
    assert marker.exists(), 'The native failure boundary was not executed'
    assert result.returncode == 1, result.stdout
    assert 'Injected producer failure after complete output' in result.stdout
    assert lab.data['runtime'] == BASE and not lab.data['persistent']
    assert not (lab.state / 'front-routes.pending').exists()
    if boundary == 'expected_sort':
        assert 'FRONT rollback completed' in result.stdout
    else:
        assert lab.data == before and not lab.data['operations']

"""Unimported application files must retain their full native denominators."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.version_info < (3, 11), reason='The QA controller requires Python 3.11')
coverage = pytest.importorskip('coverage')
if coverage.__version__ != '7.6.1':
    pytest.skip('Requires the inspected coverage.py parser', allow_module_level=True)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools/quality'))
from run_coverage import PairedRun


@pytest.mark.parametrize('execute_application', [False, True])
def test_native_scope_measures_unimported_non_package_files_without_creating_hits(tmp_path, execute_application):
    root = tmp_path / 'source'
    active = root / 'roles/bind9/files/active.py'
    inactive = root / 'roles/bind9/module_utils/nested/inactive.py'
    for path in (active, inactive): path.parent.mkdir(parents=True, exist_ok=True)
    active.write_text('def choose(value):\n    if value:\n        return 7\n    return 9\n', encoding='utf-8')
    inactive.write_text('def untouched(value):\n    if value:\n        return 1\n    return 2\n', encoding='utf-8')
    (root / 'playbooks').mkdir()
    script = root / 'exercise.py'
    script.write_text('from roles.bind9.files.active import choose\nassert choose(True) == 7\n'
                      if execute_application else 'assert True\n', encoding='utf-8')
    runner = PairedRun(root, tmp_path / 'unused-output', [])
    assert set(runner.native_source_scope().split(',')) == {
        'roles/bind9', 'roles/bind9/files', 'roles/bind9/module_utils/nested'}
    environment = dict(os.environ, COVERAGE_FILE=str(tmp_path / 'native.coverage'))
    environment.pop('BIND9_CONDITION_COVERAGE', None)
    subprocess.run([sys.executable, '-m', 'coverage', 'run', '--branch',
                    '--source=' + runner.native_source_scope(), str(script)],
                   cwd=root, env=environment, capture_output=True, check=True, timeout=30)
    output = tmp_path / 'report.json'
    subprocess.run([sys.executable, '-m', 'coverage', 'json', '-o', str(output)],
                   cwd=root, env=environment, capture_output=True, check=True, timeout=30)
    files = json.loads(output.read_text(encoding='utf-8'))['files']
    assert set(files) == {'roles/bind9/files/active.py', 'roles/bind9/module_utils/nested/inactive.py'}
    measured = files['roles/bind9/module_utils/nested/inactive.py']
    assert measured['executed_lines'] == measured['executed_branches'] == []
    assert measured['missing_lines'] == [1, 2, 3, 4]
    assert measured['missing_branches'] == [[2, 3], [2, 4]]
    if execute_application:
        assert files['roles/bind9/files/active.py']['executed_lines'] == [1, 2, 3]
        assert files['roles/bind9/files/active.py']['missing_lines'] == [4]
        assert files['roles/bind9/files/active.py']['missing_branches'] == [[2, 4]]
    else:
        assert files['roles/bind9/files/active.py']['executed_lines'] == []
        assert files['roles/bind9/files/active.py']['missing_lines'] == [1, 2, 3, 4]
        assert files['roles/bind9/files/active.py']['missing_branches'] == [[2, 3], [2, 4]]
    assert all(not value['excluded_lines'] for value in files.values())

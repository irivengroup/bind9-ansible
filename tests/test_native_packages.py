"""Native dependency task: no silent fallback and real EL8 idempotence.

The modern QA image deliberately has no DNF bindings. A separate EL8 image
provides installed packages and its real package manager for the success case.
"""
import importlib.util
import os
import subprocess

import pytest

from native_ansible_runner import NativePackageRun

pytestmark = pytest.mark.skipif(os.name != 'posix' or os.environ.get('BIND9_NATIVE_STATE_LAB') != '1',
                               reason='Requires an explicitly enabled disconnected clean Linux root')

PACKAGES = ['bind', 'bind-utils', 'policycoreutils-python-utils']


def test_native_dependency_task_preserves_installed_packages_or_reports_unavailable_dnf_bindings():
    mode = os.environ.get('BIND9_NATIVE_DNF_LAB', 'missing')
    assert mode in ('missing', 'installed')
    available = importlib.util.find_spec('dnf') is not None
    assert available is (mode == 'installed'), 'The image must match its explicit package-manager contract'
    before = None
    if available:
        before = subprocess.check_output(['/usr/bin/rpm', '-q'] + PACKAGES, timeout=10)
    status, result = NativePackageRun('tasks.d/deploy/packages.yml', {'ansible_pkg_mgr': 'dnf'}).run()
    if available:
        assert status == 0 and not result.failed
        assert result.ok == ['Install BIND and SELinux tooling']
        assert subprocess.check_output(['/usr/bin/rpm', '-q'] + PACKAGES, timeout=10) == before
    else:
        assert status == 2 and result.failed == ['Install BIND and SELinux tooling']
        assert result.ok == []

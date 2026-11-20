"""Run the canonical archive scenarios in a clean, disconnected container.

The public launcher retains its explicit isolation opt-in. Pytest owns case
selection and fixture cleanup, so this adapter cannot drift from parametrized
cases or erase pre-existing BIND data to prepare a laboratory.
"""
import os
from pathlib import Path

import pytest

from canonical_shell_lab import _require_disconnected_root


class BackupContainerIntegration:
    """Delegate every existing archive assertion with the real pytest status."""

    def run(self):
        _require_disconnected_root('BIND_MANAGEMENT_ISOLATED',
            'Requires an explicitly authorized disconnected disposable root container')
        tests = Path(__file__).resolve().parent
        previous = os.environ.get('BIND9_NATIVE_STATE_LAB')
        os.environ['BIND9_NATIVE_STATE_LAB'] = '1'
        try:
            # Canonical fixtures refuse occupied or mounted targets before any
            # writes. Keep their complete parametrization and normal failures.
            return pytest.main(['-p', 'no:cacheprovider', '-q',
                str(tests / 'test_native_archive_cli.py'),
                str(tests / 'test_configtool_workflows.py')])
        finally:
            if previous is None:
                os.environ.pop('BIND9_NATIVE_STATE_LAB', None)
            else:
                os.environ['BIND9_NATIVE_STATE_LAB'] = previous


if __name__ == '__main__':
    raise SystemExit(BackupContainerIntegration().run())

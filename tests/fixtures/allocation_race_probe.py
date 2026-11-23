"""Run the real allocation race in a private, unobserved functional process.

The native measurement profile deliberately refuses BASH_ENV. Its DEBUG actor
is therefore confined here; no observation or coverage receipt is emitted.
Both paired phases execute the same assertions through this isolated helper.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


class AllocationRaceProbe:
    """Own a private CLI mount while preserving the exact production bytes."""

    def __init__(self, root, parent_namespace):
        self.root = Path(root).resolve(strict=True)
        self.parent_namespace = parent_namespace

    def run(self):
        sys.path.insert(0, str(self.root / 'tests'))
        # EL8's fixed, installed QA runtime is outside platform-python's
        # system site directory. Load these explicit dependencies after -I
        # has excluded every environment-driven observer bootstrap.
        if sys.version_info < (3, 9):
            for dependency in ('/opt/bind9-legacy-qa', '/opt/bind9-legacy-qa-lib64'):
                if not Path(dependency).is_dir():
                    raise RuntimeError('Required EL8 QA dependency directory is absent')
                sys.path.append(dependency)
        from canonical_shell_lab import CanonicalFrontEnvironment, _require_disconnected_root
        _require_disconnected_root('BIND9_CANONICAL_BASH_LAB',
            'Allocation race requires an explicitly enabled disconnected disposable root')
        if os.readlink('/proc/self/ns/mnt') == self.parent_namespace:
            raise RuntimeError('Allocation race requires its own mount namespace')
        with tempfile.TemporaryDirectory(prefix='bind9-allocation-race-') as folder:
            folder = Path(folder)
            commands = folder / 'commands'
            commands.mkdir(mode=0o700)
            (commands / 'python3').symlink_to(sys.executable)
            # A bind mount in this private namespace leaves the parent lab's
            # exact owned CLI links intact. State still uses one native device.
            subprocess.run(['/usr/bin/mount', '--bind', str(commands), '/usr/sbin'],
                           check=True, timeout=10)
            try:
                from test_front_utility_failures import _assert_nonempty_observation_allocation
                case = folder / 'case'
                case.mkdir(mode=0o700)
                _assert_nonempty_observation_allocation(case, 'vanishing-file')
            finally:
                CanonicalFrontEnvironment.release()
                subprocess.run(['/usr/bin/umount', '/usr/sbin'], check=True, timeout=10)
        source = self.root / 'roles/bind9/files/bind-front-routes.sh'
        print(json.dumps({'case': 'vanishing-file',
                          'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest()}))
        return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root')
    parser.add_argument('parent_namespace')
    options = parser.parse_args()
    sys.exit(AllocationRaceProbe(options.root, options.parent_namespace).run())

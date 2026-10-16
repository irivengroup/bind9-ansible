"""Run the unmodified backup CLI inside private mount namespaces, never on a target."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(os.environ.get('BIND_CONFIGTOOL_TEST_SOURCE', str(ROOT / 'roles/bind9/files/bind-configtool.sh')))
pytestmark = pytest.mark.skipif(os.name != 'posix' or not hasattr(os, 'geteuid') or
                             os.geteuid() != 0 or not shutil.which('unshare'),
                             reason='Requires disposable Linux root and mount namespaces')

SETUP = r'''
set -euo pipefail
tool=$1
fixture=$2
mount --make-rprivate /
mount -t tmpfs tmpfs /etc
mount -t tmpfs tmpfs /var
mkdir -p /var/named/zones "$fixture/bin"
printf 'original configuration\n' > /etc/named.conf
printf 'original data\n' > /var/named/zones/example.zone
cat > "$fixture/bin/systemctl" <<'CLI'
#!/bin/bash
printf '%s\n' "${BIND_TEST_NAMED_STATE:-inactive}"
[[ ${BIND_TEST_NAMED_STATE:-inactive} == active ]] && exit 0
exit 3
CLI
cat > "$fixture/bin/named-checkconf" <<'CLI'
#!/bin/bash
exit 0
CLI
cat > "$fixture/bin/restorecon" <<'CLI'
#!/bin/bash
exit "${BIND_TEST_LABEL_RC:-0}"
CLI
chmod 755 "$fixture/bin/"*
export PATH="$fixture/bin:$PATH"
backup_root=/var/backups/bind-test
'''


class TestConfigtoolWorkflows:
    """Use real tar, checksums and files; only systemd/BIND/SELinux are doubles."""

    @staticmethod
    def execute(tmp_path, body):
        result = subprocess.run(['unshare', '--mount', '--fork', 'bash', '-s', '--', str(SCRIPT), str(tmp_path)],
                                input=SETUP + body, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr

    def test_backup_restore_preserves_unrelated_files(self, tmp_path):
        self.execute(tmp_path, r'''
created=$(bash "$tool" backup --root "$backup_root")
archive=$(basename "${created#BACKUP_CREATED }")
printf 'changed\n' > /etc/named.conf
printf 'unrelated\n' > /var/named/unrelated.file
bash "$tool" restore --root "$backup_root" --archive "$archive"
grep -qx 'original configuration' /etc/named.conf
grep -qx 'unrelated' /var/named/unrelated.file
[[ $(stat -c %a "$backup_root") == 700 ]]
[[ $(bash "$tool" list --root "$backup_root") == "$archive" ]]
''')

    def test_active_named_is_rejected_before_overwriting_configuration(self, tmp_path):
        self.execute(tmp_path, r'''
created=$(bash "$tool" backup --root "$backup_root")
archive=$(basename "${created#BACKUP_CREATED }")
printf 'changed\n' > /etc/named.conf
if BIND_TEST_NAMED_STATE=active bash "$tool" restore --root "$backup_root" --archive "$archive"; then exit 80; fi
grep -qx 'changed' /etc/named.conf
''')

    def test_symlink_destination_never_deletes_an_external_owned_zone(self, tmp_path):
        self.execute(tmp_path, r'''
created=$(bash "$tool" backup --root "$backup_root")
archive=$(basename "${created#BACKUP_CREATED }")
mkdir "$fixture/outside"
printf '; Bind9-Ansible-MANAGED-ZONE\n' > "$fixture/outside/keep.zone"
mv /var/named/zones /var/named/original-zones
ln -s "$fixture/outside" /var/named/zones
if bash "$tool" restore --root "$backup_root" --archive "$archive"; then exit 81; fi
test -f "$fixture/outside/keep.zone"
test -L /var/named/zones
''')

    def test_general_directory_is_not_chmodded_as_backup_root(self, tmp_path):
        self.execute(tmp_path, r'''
mkdir -m 755 /var/general
if bash "$tool" list --root /var/general; then exit 82; fi
[[ $(stat -c %a /var/general) == 755 ]]
''')

    def test_label_failure_is_fatal_and_preserves_safety_archive(self, tmp_path):
        self.execute(tmp_path, r'''
created=$(bash "$tool" backup --root "$backup_root")
archive=$(basename "${created#BACKUP_CREATED }")
if BIND_TEST_LABEL_RC=1 bash "$tool" restore --root "$backup_root" --archive "$archive"; then exit 83; fi
find "$backup_root" -name '.restore-safety.*' -type f | grep -q .
''')

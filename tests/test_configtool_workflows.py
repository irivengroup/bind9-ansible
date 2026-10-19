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


@pytest.fixture(scope='module', autouse=True)
def require_working_mount_namespaces():
    """Report unavailable isolation rather than running mounts on the host.

    A root UID and an installed unshare do not imply CAP_SYS_ADMIN. The tests
    still run unchanged on a namespace-capable CI runner.
    """
    result = subprocess.run(['unshare', '--mount', '--fork', 'true'],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=10)
    if result.returncode:
        if os.environ.get('BIND_TEST_REQUIRE_MOUNT_NS') == '1':
            pytest.fail('Required mount namespace isolation is unavailable: ' + result.stderr.strip())
        pytest.skip('Mount namespaces unavailable in this environment: ' + result.stderr.strip())

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
                                input=SETUP + body, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=30)
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

    def test_failed_initial_catalog_is_not_an_empty_success(self, tmp_path):
        self.execute(tmp_path, r'''
cat > "$fixture/bin/sort" <<'CLI'
#!/bin/bash
printf 'Injected archive catalog failure\n' >&2
exit 2
CLI
chmod 755 "$fixture/bin/sort"
if bash "$tool" list --root "$backup_root"; then exit 84; fi
grep -qx 'original configuration' /etc/named.conf
test -f /var/named/zones/example.zone
''')

    def test_failed_directory_metadata_blocks_backup_state_creation(self, tmp_path):
        self.execute(tmp_path, r'''
cat > "$fixture/bin/stat" <<'CLI'
#!/bin/bash
if [[ $1 == -c && $2 == '%u %a' ]]; then printf '0 700\n'; exit 1; fi
exec /usr/bin/stat "$@"
CLI
chmod 755 "$fixture/bin/stat"
if bash "$tool" list --root "$backup_root"; then exit 88; fi
test ! -e "$backup_root"
# Also reject a failed mode observation for a pre-existing private root.
mkdir -p -m 700 "$backup_root"
cat > "$fixture/bin/stat" <<'CLI'
#!/bin/bash
if [[ $1 == -c && $2 == '%a' ]]; then printf '700\n'; exit 1; fi
exec /usr/bin/stat "$@"
CLI
if bash "$tool" list --root "$backup_root"; then exit 89; fi
test ! -e "$backup_root/.lock"
grep -qx 'original configuration' /etc/named.conf
''')

    def test_failed_retention_catalog_cannot_report_backup_success(self, tmp_path):
        self.execute(tmp_path, r'''
cat > "$fixture/bin/sort" <<'CLI'
#!/bin/bash
text=$(cat)
if [[ $text == *'/named-config-'* ]]; then
  printf '%s\n' "${text%%$'\n'*}"
  printf 'Injected retention catalog failure\n' >&2
  exit 2
fi
printf '%s\n' "$text" | /usr/bin/sort "$@"
CLI
chmod 755 "$fixture/bin/sort"
if bash "$tool" backup --root "$backup_root" >"$fixture/receipt"; then exit 85; fi
if grep -q '^BACKUP_CREATED ' "$fixture/receipt"; then exit 86; fi
grep -qx 'original configuration' /etc/named.conf
''')

    def test_failed_restore_member_catalog_preserves_live_managed_zone(self, tmp_path):
        self.execute(tmp_path, r'''
created=$(bash "$tool" backup --root "$backup_root")
archive=$(basename "${created#BACKUP_CREATED }")
printf '; Bind9-Ansible-MANAGED-ZONE\noperator data\n' > /var/named/zones/new.zone
expected_zone=$(sha256sum /var/named/zones/new.zone)
printf 'operator configuration\n' > /etc/named.conf
cat > "$fixture/bin/tar" <<'CLI'
#!/bin/bash
if [[ $1 == -tzf ]]; then
  printf 'etc/named.conf\n'
  printf 'Injected incomplete restore member catalog\n' >&2
  exit 2
fi
exec /usr/bin/tar "$@"
CLI
chmod 755 "$fixture/bin/tar"
if bash "$tool" restore --root "$backup_root" --archive "$archive"; then exit 87; fi
[[ $(sha256sum /var/named/zones/new.zone) == "$expected_zone" ]]
grep -qx 'operator configuration' /etc/named.conf
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

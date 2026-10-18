#!/usr/bin/env bash
# Disposable Linux lab only: hide all privileged paths before starting Python.
set -euo pipefail
if [[ ${BIND_MANAGEMENT_ISOLATED:-} != 1 ]]; then
  exec unshare --mount --net --fork env BIND_MANAGEMENT_ISOLATED=1 bash "$0" "$@"
fi
mount --make-rprivate /
for target in /etc /var /run /usr/local/sbin; do
  mount -t tmpfs -o mode=0755 tmpfs "$target"
done
ip link set lo up
for address in 127.0.0.2 127.0.0.3 127.0.0.4 127.0.0.5 127.0.0.6; do
  ip address add "$address/32" dev lo
done
exec "${PYTHON:-python3}" "$(dirname "$0")/integration_management.py" "$@"

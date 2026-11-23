#!/usr/bin/env bash
# Requires Linux root/CAP_SYS_ADMIN: all address changes stay in a new namespace.
set -euo pipefail
if [[ ${BIND_TEST_NETNS:-} != 1 ]]; then
    exec unshare -n env BIND_TEST_NETNS=1 bash "$0" "$@"
fi
ip link set lo up
for address in 127.0.0.2 127.0.0.3 127.0.0.4 127.0.0.5 127.0.0.6; do
    ip address add "$address/32" dev lo
done
exec "${PYTHON:-python3}" "$(dirname "$0")/integration_loopback.py" "$@"

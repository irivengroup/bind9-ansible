#!/usr/bin/env bash
# Disposable Linux lab only: hide all privileged paths before starting Python.
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
python_bin=$(readlink -f -- "$(command -v "${PYTHON:-python3}")")
system_python=$(readlink -f -- /usr/bin/python3)
[[ -x $python_bin && -x $system_python ]] || { echo 'Requires the controller and native system Python executables' >&2; exit 2; }
# Always enter new namespaces; an inherited environment flag cannot bypass
# isolation and turn the temporary mounts below into operational host mounts.
exec unshare --mount --net --fork /bin/bash -s -- "$python_bin" "$system_python" "$script_dir" "$@" <<'LAB'
set -euo pipefail
python_bin=$1 system_python=$2 script_dir=$3
shift 3
mount --make-rprivate /
for target in /etc /var /run /usr/local/sbin; do
  mount -t tmpfs -o mode=0755 tmpfs "$target"
done
# EL8's installed /usr/bin/python3 traverses the now-hidden /etc/alternatives.
# Recreate only that vendor link in this new tmpfs, preserving its real target.
if [[ -L /usr/bin/python3 && $(readlink -- /usr/bin/python3) == /etc/alternatives/python3 ]]; then
  mkdir -m 0755 /etc/alternatives
  ln -s -- "$system_python" /etc/alternatives/python3
fi
ip link set lo up
for address in 127.0.0.2 127.0.0.3 127.0.0.4 127.0.0.5 127.0.0.6; do
  ip address add "$address/32" dev lo
done
export BIND_MANAGEMENT_ISOLATED=1
exec "$python_bin" "$script_dir/integration_management.py" "$@"
LAB

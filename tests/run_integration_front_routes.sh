#!/usr/bin/env bash
# Disposable Linux lab: networking, /etc, /run and /var/lib are isolated before
# starting NetworkManager. Never use a process attached to the operational bus.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Requires root for disposable Linux namespaces' >&2; exit 2; }
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
nmcli_bin=$(command -v nmcli)
manager_bin=$(command -v NetworkManager)
python_bin=$(command -v "${PYTHON:-python3}")
ansible_bin=$(command -v ansible-playbook)
lab_dir=$(mktemp -d /tmp/bind9-nm-lab.XXXXXXXX)
status=0
unshare --mount --net --pid --fork --mount-proc bash -s -- \
    "$script_dir" "$lab_dir" "$nmcli_bin" "$manager_bin" "$python_bin" "$ansible_bin" <<'LAB' || status=$?
set -euo pipefail
script_dir=$1 lab_dir=$2 nmcli_bin=$3 manager_bin=$4 python_bin=$5 ansible_bin=$6
mount --make-rprivate /
mount -t tmpfs tmpfs /etc
mount -t tmpfs tmpfs /run
mount -t tmpfs tmpfs /var/lib
mount -t sysfs sysfs /sys
mkdir -p /etc/NetworkManager/system-connections /etc/netplan /run/bind-test \
    /run/NetworkManager/system-connections /var/lib/NetworkManager
printf 'root:x:0:0:root:/root:/bin/bash\n' > /etc/passwd
printf 'root:x:0:\n' > /etc/group
export PATH="$(dirname -- "$nmcli_bin"):$(dirname -- "$python_bin"):$(dirname -- "$ansible_bin"):$PATH"
export DBUS_SYSTEM_BUS_ADDRESS=unix:path=/run/bind-test/bus
export BIND_NM_NETNS=1
cat > /run/bind-test/bus.conf <<'EOF'
<busconfig><type>system</type><listen>unix:path=/run/bind-test/bus</listen><auth>EXTERNAL</auth><policy context="default"><allow user="*"/><allow own="*"/><allow send_destination="*"/><allow receive_sender="*"/></policy></busconfig>
EOF
cat > /run/bind-test/NetworkManager.conf <<'EOF'
[main]
plugins=keyfile
dns=none
rc-manager=unmanaged
no-auto-default=*
[logging]
level=INFO
EOF
dbus-daemon --nofork --nopidfile --config-file=/run/bind-test/bus.conf > "$lab_dir/bus.log" 2>&1 &
bus_pid=$!
"$manager_bin" --no-daemon --config /run/bind-test/NetworkManager.conf \
    --pid-file /run/bind-test/nm.pid > "$lab_dir/networkmanager.log" 2>&1 &
manager_pid=$!
trap 'kill "$manager_pid" "$bus_pid" 2>/dev/null || true; wait "$manager_pid" "$bus_pid" 2>/dev/null || true' EXIT
for attempt in {1..30}; do
    if nmcli -g VERSION general > /dev/null 2>&1; then break; fi
    sleep 0.2
done
nmcli -g VERSION general
nmcli connection add type dummy ifname front0 con-name front ipv4.method manual \
    ipv4.addresses 198.51.100.2/24 ipv4.gateway 198.51.100.1 ipv4.route-metric 500 ipv6.method disabled
nmcli connection add type dummy ifname back0 con-name back ipv4.method manual \
    ipv4.addresses 10.0.0.2/24 ipv4.gateway 10.0.0.1 ipv4.route-metric 100 ipv6.method disabled
nmcli connection add type dummy ifname adm0 con-name adm ipv4.method manual \
    ipv4.addresses 192.0.2.2/24 ipv6.method disabled
for profile in front back adm; do nmcli connection up "$profile"; done
nmcli connection modify front +ipv4.routes '172.16.0.0/24 198.51.100.1 150'
nmcli device modify front0 +ipv4.routes '172.16.0.0/24 198.51.100.1 150'
# Disposable fixture: the system default is exclusively BACK; FRONT retains
# its own configured gateway without an active default route.
ip -4 route del default via 198.51.100.1 dev front0 metric 500 table main
"$python_bin" "$script_dir/integration_front_routes.py" --workarea /run/bind-test
LAB
if [[ $status -ne 0 ]]; then
    echo "Lab failed; isolated daemon logs retained in $lab_dir" >&2
    exit "$status"
fi
# mktemp created this exact bounded directory; cleanup cannot target source data.
[[ $lab_dir == /tmp/bind9-nm-lab.* && -d $lab_dir && ! -L $lab_dir ]] || exit 2
rm -rf -- "$lab_dir"

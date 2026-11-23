#!/bin/bash
# Docker-only boot adapter: NetworkManager owns all persistent routes.
# This adapter never adds routes, activates profiles, or repairs them after boot.
set -Eeuo pipefail
[[ ${container-} == docker ]]
suffix=${BIND9_RECIPE_NODE_SUFFIX:?Missing laboratory node identity}
[[ $suffix == 11 || $suffix == 12 ]]
adm='' front='' back=''
for address in /sys/class/net/*/address; do
    read -r mac < "$address"
    device=${address%/address}
    device=${device##*/}
    case $mac in
        "02:42:9a:00:00:$suffix") adm=$device;;
        "02:42:9a:00:01:$suffix") front=$device;;
        "02:42:9a:00:02:$suffix") back=$device;;
    esac
done
[[ -n $adm && -n $front && -n $back && $adm != "$front" && $adm != "$back" && $front != "$back" ]]
for device in "$adm" "$front" "$back"; do ip link set dev "$device" down; done
ip link set dev "$adm" name recipe_adm
ip link set dev "$front" name recipe_front
ip link set dev "$back" name recipe_back
ip link set dev recipe_adm name eth0
ip link set dev recipe_front name eth1
ip link set dev recipe_back name eth2
for device in eth0 eth1 eth2; do ip link set dev "$device" up; done
# Windows-to-container copies do not carry POSIX file modes. No profile is
# rewritten: its exact bytes, UUID, routes and autoconnect setting survive boot.
chmod 0600 /etc/NetworkManager/system-connections/bind9-lab-*.nmconnection
mount -o remount,rw /sys/fs/cgroup
ssh-keygen -A
exec /sbin/init

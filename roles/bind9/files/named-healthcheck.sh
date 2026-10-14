#!/usr/bin/env bash
set -Eeuo pipefail
[[ $# -ge 1 ]] || { echo 'usage: named-healthcheck LOCAL_BACK_IP [ZONE...]' >&2; exit 2; }
ip=$1; shift
systemctl is-active --quiet named || { echo 'CRITICAL named inactive'; exit 2; }
# Check the package-owned loopback data through the local-only BIND view.
check_loopback() {
  local transport=$1 name=$2 type=$3 expected=$4 result
  result=$(dig -b 127.0.0.1 @127.0.0.1 "$transport" +time=3 +tries=1 +norecurse +noall +comments +answer "$name" "$type") || {
    echo "CRITICAL loopback query failed: $name $type $transport"; exit 2;
  }
  if ! grep -q 'status: NOERROR' <<<"$result" ||
     ! grep -Eq 'flags:.*\baa\b' <<<"$result" ||
     ! grep -Eq "[[:space:]]IN[[:space:]]+$type[[:space:]]+$expected[[:space:]]*$" <<<"$result"; then
    echo "CRITICAL loopback answer invalid: $name $type $transport"; exit 2
  fi
}
for transport in +notcp +tcp; do
  check_loopback "$transport" localhost A '127[.]0[.]0[.]1'
  check_loopback "$transport" 1.0.0.127.in-addr.arpa PTR 'localhost[.]'
done
for zone in "$@"; do
  result=$(dig -b "$ip" @"$ip" +time=3 +tries=1 +norecurse +noall +comments +answer "$zone" SOA) || { echo "CRITICAL DNS query failed: $zone"; exit 2; }
  grep -q 'status: NOERROR' <<<"$result" || { echo "CRITICAL DNS status: $zone"; exit 2; }
  grep -Eq 'flags:.*\baa\b' <<<"$result" || { echo "CRITICAL non-authoritative: $zone"; exit 2; }
done
echo "OK named active, native loopback UDP/TCP, ${#@} authoritative zone(s)"

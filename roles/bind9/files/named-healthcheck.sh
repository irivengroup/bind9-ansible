#!/usr/bin/env bash
set -Eeuo pipefail
[[ $# -ge 1 ]] || { echo 'usage: named-healthcheck LOCAL_BACK_IP [ZONE...]' >&2; exit 2; }
ip=$1; shift
systemctl is-active --quiet named || { echo 'CRITICAL named inactive'; exit 2; }
# dig is invoked with +noall +comments +answer. Each health question expects
# exactly one record; never accept a truncated or contradictory response.
complete_answer() {
  local response=$1
  [[ $(grep -c 'status:' <<<"$response") == 1 ]] &&
    grep -q 'status: NOERROR' <<<"$response" &&
    [[ $(grep -c '^;; flags:' <<<"$response") == 1 ]] &&
    ! grep -Eq '^;; flags:([^;]*[[:space:]])tc([[:space:]]|;)' <<<"$response" &&
    grep -Eq '^;; flags:.*ANSWER: 1([,[:space:]]|$)' <<<"$response" &&
    [[ $(grep -Ec '^[^;[:space:]]' <<<"$response") == 1 ]]
}
# Check the package-owned loopback data through the IPv4 local listener.
check_loopback() {
  local transport=$1 name=$2 type=$3 expected=$4 result
  result=$(dig -r -4 -b 127.0.0.1 @127.0.0.1 "$transport" +ignore +time=3 +tries=1 +norecurse +noall +comments +answer "$name" "$type") || {
    echo "CRITICAL loopback query failed: $name $type $transport"; exit 2;
  }
  if ! complete_answer "$result" ||
     ! grep -Eq 'flags:.*\baa\b' <<<"$result" ||
     ! grep -Eq "[[:space:]]IN[[:space:]]+${type}[[:space:]]+${expected}[[:space:]]*$" <<<"$result"; then
    echo "CRITICAL loopback answer invalid: $name $type $transport"; exit 2
  fi
}
for transport in +notcp +tcp; do
  check_loopback "$transport" localhost A '127[.]0[.]0[.]1'
  check_loopback "$transport" 1.0.0.127.in-addr.arpa PTR 'localhost[.]'
done
for zone in "$@"; do
  result=$(dig -r -4 -b "$ip" @"$ip" +tcp +time=3 +tries=1 +norecurse +noall +comments +answer "$zone" SOA) || { echo "CRITICAL DNS query failed: $zone"; exit 2; }
  complete_answer "$result" || { echo "CRITICAL DNS incomplete/status: $zone"; exit 2; }
  grep -Eq 'flags:.*\baa\b' <<<"$result" || { echo "CRITICAL non-authoritative: $zone"; exit 2; }
  awk -v owner="${zone%.}." 'tolower($1) == tolower(owner) && $3 == "IN" && $4 == "SOA" { found=1 } END { exit !found }' <<<"$result" || {
    echo "CRITICAL SOA owner/type: $zone"; exit 2;
  }
done
echo "OK named active, native loopback UDP/TCP, ${#@} authoritative zone(s)"

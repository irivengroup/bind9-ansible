#!/bin/bash
# Additive FRONT routes on EL8. One process owns observation, writes and recovery.
# No interface reactivation, route replacement, shell evaluation, Python or jq.
set -Eeuo pipefail
export LC_ALL=C
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 077
STATE=/var/lib/Bind9-Ansible
PENDING=$STATE/front-routes.pending
WORK=''
MUTATING=false
PREPARED=false
CHANGED_RESULT=false
ACTION=plan FRONT='' BACK='' ADM='' SSH_PEER='' METRIC=100 DEFER=false TOKEN=''
declare -a CLIENTS=() PEERS=() ADDRESSES=() PROTECTED=() PROFILE=() KERNEL=() OPS=()
declare -a LEGACY_P=() LEGACY_R=()
LEGACY_PROFILE_SHA='' LEGACY_KERNEL_SHA='' LEGACY_JOURNAL_SHA='' LEGACY_DIR_ID='' LEGACY_FILE_ID=''
LEGACY_CHANGED=false

die() { printf 'FRONT: %s\n' "$*" >&2; exit 1; }
# Reserve rc 2 for deliberate success, never an unhandled native command error.
changed_result() { CHANGED_RESULT=true; exit 2; }
trim() { local s=$1; s="${s#"${s%%[![:space:]]*}"}"; REPLY="${s%"${s##*[![:space:]]}"}"; }
uint() { [[ $1 =~ ^(0|[1-9][0-9]{0,9})$ ]] && (( 10#$1 <= 4294967295 )); }
ip_number() {
    local ip=$1 a b c d part
    [[ $ip =~ ^(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})\.(0|[1-9][0-9]{0,2})$ ]] || die "Invalid IPv4: $ip"
    IFS=. read -r a b c d <<< "$ip"
    for part in "$a" "$b" "$c" "$d"; do (( 10#$part <= 255 )) || die "Invalid IPv4 octet: $ip"; done
    NUMBER=$(( (10#$a<<24) + (10#$b<<16) + (10#$c<<8) + 10#$d ))
}
network() {
    local input=$1 ip=${1%/*} prefix=32 mask
    [[ $input != */* ]] || prefix=${input##*/}
    if ! uint "$prefix" || (( prefix > 32 )); then die "Invalid prefix: $input"; fi
    ip_number "$ip"
    mask=$(( prefix == 0 ? 0 : (4294967295 << (32-prefix)) & 4294967295 ))
    LOW=$(( NUMBER & mask )); HIGH=$(( LOW | (4294967295 ^ mask) )); PREFIX=$prefix
    CIDR="$((LOW>>24)).$(((LOW>>16)&255)).$(((LOW>>8)&255)).$((LOW&255))/$prefix"
}
overlap() { (( $1 <= $4 && $3 <= $2 )); }
secure_dir() {
    local path=$1 parent=${1%/*}
    [[ $path == /* && $path != / && $path != *'/../'* ]] || die 'Invalid private state path'
    [[ -d $parent && ! -L $parent ]] || die "Unsafe state ancestor: $parent"
    [[ $(stat -c '%u' -- "$parent") == 0 ]] || die "Untrusted state ancestor: $parent"
    local mode; mode=$(stat -c '%a' -- "$parent"); (( (8#$mode & 0022) == 0 )) || die "Writable state ancestor: $parent"
    if [[ ! -e $path && ! -L $path ]]; then
        # Another process may initialize this shared state directory first.
        # Accept only its subsequently validated directory, never repair modes.
        local creation_error
        if ! creation_error=$(mkdir -m 0700 -- "$path" 2>&1); then
            [[ -d $path && ! -L $path ]] || die "Cannot create private directory $path: $creation_error"
        fi
    fi
    [[ -d $path && ! -L $path && $(stat -c '%u:%a' -- "$path") == 0:700 ]] || die "Unsafe private directory: $path"
}
safe_file() {
    [[ -f $1 && ! -L $1 && $(stat -c '%u:%a:%h' -- "$1") == 0:600:1 ]] || die "Unsafe journal file: $1"
    (( $(stat -c '%s' -- "$1") <= 1048576 )) || die "Oversized journal file: $1"
}
journal_count() (
    # Count entries without findutils or filename-delimited text parsing.
    shopt -s nullglob dotglob
    local directory=${1:-$PENDING}
    local entries=("$directory"/*)
    printf '%s' "${#entries[@]}"
)
assert_lock() {
    safe_file "$LOCK"
    [[ $(stat -Lc '%d:%i:%s' /proc/self/fd/9) == $(stat -c '%d:%i:%s' "$LOCK") && $(stat -c '%s' "$LOCK") == 0 ]] || die 'Transient operation lock changed; no further routing writes permitted'
}
capture() {
    local variable=$1; shift
    # The file-size limit makes excessive output fail, rather than silently truncate.
    if ! (ulimit -f 2048; timeout -k 5 30 "$@") >"$WORK/out" 2>"$WORK/err"; then
        printf 'FRONT command failed: %s\n' "$*" >&2
        head -c 2048 "$WORK/err" >&2
        return 1
    fi
    (( $(stat -c '%s' "$WORK/out") <= 1048576 )) || die 'Observation exceeds 1 MiB'
    [[ $(tr -d '\000' <"$WORK/out" | wc -c) == $(wc -c <"$WORK/out") ]] || die 'NUL in native observation'
    printf -v "$variable" '%s' "$(cat "$WORK/out")"
}
durable() {
    local dest=$1 data=$2 temporary
    temporary=$(mktemp "$PENDING/.write.XXXXXXXX")
    printf '%s\n' "$data" >"$temporary"
    chmod 0600 "$temporary"
    sync -- "$temporary"
    mv -f -- "$temporary" "$PENDING/$dest"
    sync -- "$PENDING"
}
canonical_profile() {
    local text=$1 entry field key value fields rest net gw metric attrs
    PROFILE=()
    trim "$text"; text=$REPLY
    [[ -n $text && $text != -- ]] || return 0
    if [[ $text == *'{'* || $text == *'}'* ]]; then
        rest=$text
        while [[ -n $rest ]]; do
            [[ $rest =~ ^[[:space:],\;]*\{([^\{\}]+)\}(.*)$ ]] || die 'Unsupported NetworkManager route representation'
            entry=${BASH_REMATCH[1]}; rest=${BASH_REMATCH[2]}
            net='' gw=0.0.0.0 metric=-1 attrs=''
            declare -A seen=()
            IFS=, read -ra fields <<< "$entry"
            for field in "${fields[@]}"; do
                [[ $field == *=* ]] || die 'Malformed profile route field'
                trim "${field%%=*}"; key=$REPLY; trim "${field#*=}"; value=$REPLY
                [[ -n $key && -n $value && ! ${seen[$key]+yes} ]] || die 'Duplicate or empty profile route field'
                seen[$key]=1
                case $key in ip) net=$value;; nh) gw=$value;; mt) metric=$value;; *) attrs+="$key=$value;";; esac
            done
            append_profile "$net" "$gw" "$metric" "$attrs"
            trim "$rest"; rest=$REPLY
        done
    else
        IFS=, read -ra fields <<< "$text"
        for entry in "${fields[@]}"; do
            read -ra parts <<< "$entry"
            (( ${#parts[@]} >= 1 )) || die 'Empty profile route'
            net=${parts[0]}; gw=0.0.0.0 metric=-1 attrs=''
            local offset=1
            if [[ ${parts[$offset]:-} == *.* && ${parts[$offset]:-} != *=* ]]; then gw=${parts[$offset]}; ((offset+=1)); fi
            if [[ ${parts[$offset]:-} =~ ^-?[0-9]+$ ]]; then metric=${parts[$offset]}; ((offset+=1)); fi
            for field in "${parts[@]:$offset}"; do
                [[ $field == *=* ]] || die 'Unsupported profile route attribute'
                attrs+="$field;"
            done
            append_profile "$net" "$gw" "$metric" "$attrs"
        done
    fi
}
append_profile() {
    local net=$1 gw=$2 metric=$3 attrs=$4
    network "$net"; net=$CIDR; ip_number "$gw"
    [[ $metric == -1 ]] || uint "$metric" || die 'Invalid profile metric'
    PROFILE+=("$net|$gw|$metric|$attrs")
}
canonical_kernel() {
    local text=$1 line net gw dev metric extra token offset
    KERNEL=()
    while IFS= read -r line; do
        [[ -n $line ]] || continue
        read -ra parts <<< "$line"
        net=${parts[0]}; gw=0.0.0.0 dev='' metric=0 extra='' offset=1
        case $net in blackhole|unreachable|prohibit|throw|local|broadcast|multicast|nat) extra="$net;"; net=${parts[1]:-}; offset=2;; esac
        [[ $net != default ]] || net=0.0.0.0/0
        network "$net"; net=$CIDR
        while (( offset < ${#parts[@]} )); do
            token=${parts[$offset]}; ((offset+=1))
            case $token in
                via) gw=${parts[$offset]:-}; ip_number "$gw"; ((offset+=1));;
                dev) dev=${parts[$offset]:-}; ((offset+=1));;
                metric) metric=${parts[$offset]:-}; uint "$metric" || die 'Invalid runtime metric'; ((offset+=1));;
                proto|scope|src)
                    [[ -n ${parts[$offset]:-} ]] || die 'Incomplete runtime route'
                    if [[ $token != proto || ${parts[$offset]} != boot ]] && [[ $token != scope || ${parts[$offset]} != global ]]; then
                        extra+="$token=${parts[$offset]};"
                    fi
                    ((offset+=1));;
                *) extra+="$token;";;
            esac
        done
        KERNEL+=("$net|$gw|$metric|$dev|$extra")
    done <<< "$text"
}
rows() { if (( $# )); then printf '%s\n' "$@" | sort; fi; }
observe() {
    capture ADDRESS_TEXT ip -o -4 address show up
    local index dev family address rest hits=0 back_hits=0 adm_hits=0 front_network='' back_dev='' adm_dev='' protected_devices='|'
    ADDRESSES=() PROTECTED=()
    while read -r index dev family address rest; do
        [[ $family == inet ]] || continue
        dev=${dev%:}; dev=${dev%@*}
        [[ $dev =~ ^[a-zA-Z0-9_.:-]+$ ]] || die 'Unsupported interface name'
        network "$address"
        ADDRESSES+=("${address%/*}")
        if [[ ${address%/*} == "$FRONT" ]]; then DEVICE=$dev; front_network=$CIDR; ((hits+=1)); fi
        if [[ ${address%/*} == "$BACK" ]]; then back_dev=$dev; PROTECTED+=("$CIDR"); protected_devices+="$dev|"; ((back_hits+=1)); fi
        if [[ ${address%/*} == "$ADM" ]]; then adm_dev=$dev; PROTECTED+=("$CIDR"); protected_devices+="$dev|"; ((adm_hits+=1)); fi
    done <<< "$ADDRESS_TEXT"
    (( hits == 1 && back_hits == 1 && adm_hits == 1 )) && [[ -n $back_dev && -n $adm_dev && $protected_devices != *"|$DEVICE|"* ]] || die 'FRONT must be uniquely present/up and distinct from BACK/ADM interfaces'
    FRONT_NETWORK=$front_network
    local link=''
    capture link ip -o link show dev "$DEVICE"
    [[ $link != *' master '* ]] || die 'Enslaved FRONT interface is unsupported'
    capture UUID nmcli -g GENERAL.CON-UUID device show "$DEVICE"
    [[ $UUID =~ ^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$ ]] || die 'FRONT has no unique active profile UUID'
    capture CONFIGURED_GW nmcli -g ipv4.gateway connection show uuid "$UUID"
    capture ACTIVE_GW nmcli -g IP4.GATEWAY device show "$DEVICE"
    capture PROFILE_TEXT nmcli -g ipv4.routes connection show uuid "$UUID"
    capture TABLE nmcli -g ipv4.route-table connection show uuid "$UUID"
    [[ $TABLE == 0 || $TABLE == 254 ]] || die 'Only main-table FRONT profiles are supported'
    capture RULES_TEXT ip -o -4 rule show
    [[ $RULES_TEXT =~ ^0:[[:space:]]+from[[:space:]]all[[:space:]]lookup[[:space:]]local$'\n'32766:[[:space:]]+from[[:space:]]all[[:space:]]lookup[[:space:]]main$'\n'32767:[[:space:]]+from[[:space:]]all[[:space:]]lookup[[:space:]]default$ ]] || die 'Custom IPv4 policy rules require operator-managed routing'
    capture KERNEL_TEXT ip -o -4 route show table main
    [[ -n $KERNEL_TEXT ]] || die 'Empty main routing table observation; no route plan accepted'
    canonical_profile "$PROFILE_TEXT"
    canonical_kernel "$KERNEL_TEXT"
    for dev in CONFIGURED_GW ACTIVE_GW; do
        trim "${!dev}"; printf -v "$dev" '%s' "$REPLY"
        [[ ${!dev} != -- ]] || printf -v "$dev" ''
        [[ -z ${!dev} ]] || ip_number "${!dev}"
    done
    [[ -z $CONFIGURED_GW || -z $ACTIVE_GW || $CONFIGURED_GW == "$ACTIVE_GW" ]] || die 'Configured and active FRONT gateways disagree; no routes changed'
    GATEWAY=${CONFIGURED_GW:-$ACTIVE_GW}
    if [[ -z $GATEWAY ]]; then
        local net gw metric attrs
        for rest in "${PROFILE[@]}"; do
            IFS='|' read -r net gw metric attrs <<< "$rest"
            if [[ $net == 0.0.0.0/0 && $gw != 0.0.0.0 && -z $attrs ]]; then
                [[ -z $GATEWAY || $GATEWAY == "$gw" ]] || die 'Ambiguous gateways in FRONT profile routes'
                GATEWAY=$gw
            fi
        done
    fi
    if [[ -n $GATEWAY ]]; then
        # A next-hop must not be any address owned by this server, including
        # aliases/secondary addresses unrelated to the inventory FRONT address.
        for address in "${ADDRESSES[@]}"; do
            [[ $GATEWAY != "$address" ]] || die 'FRONT gateway is a local server address'
        done
        network "$FRONT_NETWORK"; local low=$LOW high=$HIGH
        ip_number "$GATEWAY"
        (( (PREFIX >= 31 && NUMBER >= low && NUMBER <= high) || (NUMBER > low && NUMBER < high) )) && [[ $GATEWAY != "$FRONT" ]] || die 'FRONT gateway is not a usable on-link address'
    fi
    OBSERVED_PROFILE=$(rows "${PROFILE[@]}"); OBSERVED_KERNEL=$(rows "${KERNEL[@]}")
}
coverage() {
    # Any overlapping specific route must agree. Broader/default routes on other
    # devices may remain: the missing, more-specific destination is additive.
    local store=$1 target=$2 tlo=$3 thi=$4 tp=$5 row net gw mt device attrs lo hi prefix acceptable
    COVERED=false COVER_METRIC='' EXACT_COUNT=0
    local -a selected=()
    if [[ $store == P ]]; then selected=("${PROFILE[@]}"); else selected=("${KERNEL[@]}"); fi
    for row in "${selected[@]}"; do
        if [[ $store == P ]]; then IFS='|' read -r net gw mt attrs <<< "$row"; device=$DEVICE;
        else IFS='|' read -r net gw mt device attrs <<< "$row"; fi
        network "$net"; lo=$LOW hi=$HIGH prefix=$PREFIX
        acceptable=false
        [[ -z $attrs ]] && acceptable=true
        if [[ $store == R && $attrs == 'proto=static;' ]]; then acceptable=true; fi
        if [[ $store == R && $gw == 0.0.0.0 && $attrs == 'proto=kernel;scope=link;' ]]; then acceptable=true; fi
        if overlap "$lo" "$hi" "$tlo" "$thi"; then
            if (( lo == tlo && hi == thi )); then ((EXACT_COUNT+=1)); fi
            if (( prefix >= tp )); then
                if [[ $device != "$DEVICE" || $gw != "$GATEWAY" ]] || ! $acceptable; then
                    die "Conflicting $store route for $target; existing route preserved"
                fi
            fi
            if (( lo == tlo && hi == thi )) && [[ $device == "$DEVICE" && $gw == "$GATEWAY" ]] && $acceptable; then COVERED=true; COVER_METRIC=$mt; fi
        fi
    done
    (( EXACT_COUNT <= 1 )) || die "Duplicate $store routes for $target"
}
collapse_clients() {
    # Preserve the previous CIDR-union contract without a Python filter. Merge
    # only adjacent aligned siblings; never widen the authorized destinations.
    local client row lo hi prefix last previous size sorted
    local -a input=() lows=() highs=() prefixes=()
    WANTED=()
    for client in "${CLIENTS[@]}"; do
        network "$client"
        [[ $client != */* || $client == "$CIDR" ]] || die "Noncanonical client subnet: $client"
        (( PREFIX > 0 )) || die 'A client default route is forbidden'
        input+=("$LOW|$HIGH|$PREFIX")
    done
    # Process substitutions hide producer failures from the consuming loop.
    # Capture and check the complete bounded input before planning any write.
    sorted=$(if (( ${#input[@]} )); then printf '%s\n' "${input[@]}" | sort -t'|' -k1,1n -k3,3n; fi) || die 'Client normalization failed; no routes changed'
    while IFS='|' read -r lo hi prefix; do
        [[ -n $lo ]] || continue
        last=$(( ${#lows[@]}-1 ))
        if (( last >= 0 && lo >= lows[last] && hi <= highs[last] )); then continue; fi
        lows+=("$lo"); highs+=("$hi"); prefixes+=("$prefix")
        while (( ${#lows[@]} >= 2 )); do
            last=$(( ${#lows[@]}-1 )); previous=$((last-1)); size=$((highs[last]-lows[last]+1))
            if (( prefixes[last] == prefixes[previous] && highs[previous]+1 == lows[last] && lows[previous] % (2*size) == 0 )); then
                highs[previous]=${highs[last]}; prefixes[previous]=$((prefixes[last]-1))
                unset 'lows[last]' 'highs[last]' 'prefixes[last]'
            else break; fi
        done
    done <<< "$sorted"
    for ((last=0;last<${#lows[@]};last++)); do
        ip_string "${lows[$last]}"; WANTED+=("$REPLY/${prefixes[$last]}")
    done
}
build_plan() {
    local client target lo hi prefix range probe directly=false
    OPS=()
    PROTECTED+=("$BACK/32" "$ADM/32" "0.0.0.0/8" "127.0.0.0/8" "169.254.0.0/16" "224.0.0.0/4" "240.0.0.0/4")
    for range in "${PEERS[@]}"; do PROTECTED+=("$range/32"); done
    normalize_ssh
    [[ -z $SSH_IPV4 ]] || PROTECTED+=("$SSH_IPV4/32")
    declare -A destinations=()
    collapse_clients
    for client in "${WANTED[@]}"; do
        network "$client"; target=$CIDR lo=$LOW hi=$HIGH prefix=$PREFIX
        (( prefix > 0 )) || die 'A client default route is forbidden'
        [[ $client != */* || $client == "$target" ]] || die "Noncanonical client subnet: $client"
        [[ ! ${destinations[$target]+yes} ]] || continue
        destinations[$target]=1
        for range in "${PROTECTED[@]}"; do
            network "$range"; overlap "$lo" "$hi" "$LOW" "$HIGH" && die "Client $target overlaps protected network $range"
        done
        network "$FRONT_NETWORK"; directly=false
        if (( lo >= LOW && hi <= HIGH )); then directly=true; fi
        if ! $directly && overlap "$lo" "$hi" "$LOW" "$HIGH"; then die "Client $target partially overlaps the FRONT connected network"; fi
        if $directly; then
            local gateway_saved=$GATEWAY; GATEWAY=0.0.0.0
            coverage R "$target" "$lo" "$hi" "$prefix"
            GATEWAY=$gateway_saved
            probe=$(( prefix < 31 ? lo+1 : lo ))
            ip_string "$probe"; check_path "$REPLY" false
            continue
        fi
        [[ -n $GATEWAY ]] || die 'No IPv4 gateway on the active FRONT profile/device; no global gateway fallback'
        coverage P "$target" "$lo" "$hi" "$prefix"
        local profile_covered=$COVERED route_metric=${COVER_METRIC:-$METRIC}
        coverage R "$target" "$lo" "$hi" "$prefix"
        if $COVERED && ! $profile_covered; then die "Runtime-only route $target has no persistent ownership; existing route preserved"; fi
        if [[ $route_metric == -1 ]]; then
            if $COVERED; then route_metric=$COVER_METRIC;
            else
                capture route_metric nmcli -g ipv4.route-metric connection show uuid "$UUID"
                if [[ $route_metric == -1 ]]; then
                    local row net gw mt dev attrs inherited=''
                    for row in "${KERNEL[@]}"; do
                        IFS='|' read -r net gw mt dev attrs <<< "$row"
                        if [[ $net == "$FRONT_NETWORK" && $gw == 0.0.0.0 && $dev == "$DEVICE" ]]; then
                            [[ -z $inherited || $inherited == "$mt" ]] || die 'Ambiguous effective FRONT metric'
                            inherited=$mt
                        fi
                    done
                    route_metric=$inherited
                fi
                uint "$route_metric" || die 'No effective metric could be observed on the active FRONT profile/device'
            fi
        fi
        if $COVERED && $profile_covered && [[ $COVER_METRIC != "$route_metric" ]]; then die "Persistent and runtime metrics disagree for $target; existing routes preserved"; fi
        $profile_covered || OPS+=("P|$target|$GATEWAY|$METRIC")
        $COVERED || OPS+=("R|$target|$GATEWAY|$route_metric")
    done
}
ip_string() { REPLY="$(( $1>>24 )).$(( ($1>>16)&255 )).$(( ($1>>8)&255 )).$(( $1&255 ))"; }
normalize_ssh() {
    SSH_IPV4=''
    [[ -n $SSH_PEER ]] || return 0
    SSH_PEER=${SSH_PEER,,}
    if [[ $SSH_PEER =~ ^::ffff:([0-9a-f]{1,4}):([0-9a-f]{1,4})$ ]]; then
        ip_string "$(( (16#${BASH_REMATCH[1]} << 16) + 16#${BASH_REMATCH[2]} ))"; SSH_PEER=$REPLY
    elif [[ $SSH_PEER == ::ffff:* && $SSH_PEER == *.* ]]; then SSH_PEER=${SSH_PEER#::ffff:}; fi
    if [[ $SSH_PEER != *:* ]]; then ip_number "$SSH_PEER"; SSH_IPV4=$SSH_PEER;
    else [[ $SSH_PEER =~ ^[0-9a-fA-F:.]+$ ]] || die 'Invalid SSH peer'; fi
}
check_path() {
    local probe=$1 via=$2 result
    capture result ip -4 route get "$probe" from "$FRONT"
    [[ " $result " == *" dev $DEVICE "* ]] || die "Wrong FRONT return device for $probe"
    if $via; then [[ " $result " == *" via $GATEWAY "* ]] || die "Wrong FRONT gateway for $probe";
    else [[ " $result " != *' via '* ]] || die "Unexpected gateway for directly connected client $probe"; fi
}
verify() {
    local before_p=$BASE_PROFILE before_r=$BASE_KERNEL op store net gw mt expected_p expected_r
    expected_p=$before_p; expected_r=$before_r
    for op in "${OPS[@]}"; do
        IFS='|' read -r store net gw mt <<< "$op"
        if [[ $store == P ]]; then expected_p+=$'\n'"$net|$gw|$mt|";
        else expected_r+=$'\n'"$net|$gw|$mt|$BASE_DEVICE|"; fi
    done
    observe
    [[ $UUID == "$BASE_UUID" && $DEVICE == "$BASE_DEVICE" && $GATEWAY == "$BASE_GATEWAY" && $ADDRESS_TEXT == "$BASE_ADDRESSES" && $RULES_TEXT == "$BASE_RULES" ]] || die 'FRONT profile, addresses or gateway changed during operation'
    [[ $(printf '%s\n' "$expected_p" | sed '/^$/d' | sort) == "$OBSERVED_PROFILE" && $(printf '%s\n' "$expected_r" | sed '/^$/d' | sort) == "$OBSERVED_KERNEL" ]] || die 'Routing stores differ from the additive plan; preserve the journal'
    [[ ${1:-true} == true ]] || return 0
    local client lo hi prefix
    for client in "${CLIENTS[@]}"; do
        network "$client"; lo=$LOW hi=$HIGH prefix=$PREFIX
        ip_string "$(( prefix < 31 ? lo+1 : lo ))"; local probe=$REPLY
        network "$FRONT_NETWORK"
        if (( lo >= LOW && hi <= HIGH )); then check_path "$probe" false; else check_path "$probe" true; fi
    done
}
prepare() {
    mkdir -m 0700 "$PENDING"
    TOKEN=$(cat /proc/sys/kernel/random/uuid)
    durable meta "$TOKEN|$UUID|$DEVICE|$GATEWAY|$FRONT|$BACK|$ADM|$METRIC"
    durable operations "$(printf '%s\n' "${OPS[@]}")"
    durable profile "$OBSERVED_PROFILE"
    durable kernel "$OBSERVED_KERNEL"
    durable addresses "$ADDRESS_TEXT"
    durable rules "$RULES_TEXT"
    durable clients "$(rows "${CLIENTS[@]}")"
    (cd "$PENDING"; sha256sum meta operations profile kernel addresses rules clients >checksums)
    sync -- "$PENDING/checksums" "$PENDING"
    durable progress '0 done'
    PREPARED=true
}
load_journal() {
    secure_dir "$PENDING"
    local file count expected token
    for file in meta operations profile kernel addresses rules clients checksums progress; do safe_file "$PENDING/$file"; done
    count=$(journal_count)
    [[ $count == 9 ]] || die 'Unexpected recovery journal contents; preserve them for inspection'
    expected=$(cd "$PENDING"; sha256sum meta operations profile kernel addresses rules clients)
    [[ $expected == "$(cat "$PENDING/checksums")" ]] || die 'Recovery journal checksum mismatch'
    IFS='|' read -r token BASE_UUID BASE_DEVICE BASE_GATEWAY FRONT BACK ADM METRIC <"$PENDING/meta"
    [[ $token =~ ^[0-9a-f-]{36}$ && $BASE_UUID =~ ^[0-9a-fA-F-]{36}$ && $BASE_DEVICE =~ ^[a-zA-Z0-9_.:-]+$ ]] || die 'Malformed route journal identity'
    [[ -z $TOKEN || $TOKEN == "$token" ]] || die 'Transaction token does not own this pending operation'
    TOKEN=$token
    ip_number "$FRONT"; ip_number "$BACK"; ip_number "$ADM"; ip_number "$BASE_GATEWAY"; uint "$METRIC" || die 'Invalid journal metric'
    mapfile -t OPS <"$PENDING/operations"; mapfile -t CLIENTS <"$PENDING/clients"
    BASE_PROFILE=$(cat "$PENDING/profile"); BASE_KERNEL=$(cat "$PENDING/kernel")
    BASE_ADDRESSES=$(cat "$PENDING/addresses"); BASE_RULES=$(cat "$PENDING/rules")
    local op store net gw mt extra
    (( ${#OPS[@]} <= 1024 )) || die 'Oversized operation journal'
    for op in "${OPS[@]}"; do
        IFS='|' read -r store net gw mt extra <<< "$op"
        if [[ $store != P && $store != R ]] || [[ -n $extra || $gw != "$BASE_GATEWAY" ]] || ! uint "$mt"; then
            die 'Invalid journal operation'
        fi
        network "$net"; [[ $CIDR == "$net" && $PREFIX -gt 0 ]] || die 'Invalid journal destination'
    done
    read -r PROGRESS PHASE extra <"$PENDING/progress"
    uint "$PROGRESS" && (( PROGRESS <= ${#OPS[@]} )) && [[ $PHASE == 'done' || $PHASE == 'intent' ]] && [[ -z ${extra:-} ]] || die 'Malformed journal progress'
}
remove_journal() {
    # Never recursively delete a journal directory: unexpected evidence stays.
    local file
    assert_lock
    for file in meta operations profile kernel addresses rules clients checksums progress; do safe_file "$PENDING/$file"; done
    [[ $(journal_count) == 9 ]] || die 'Unexpected journal entry prevents cleanup'
    for file in meta operations profile kernel addresses rules clients checksums progress; do rm -- "$PENDING/$file"; done
    rmdir -- "$PENDING"
    sync -- "$STATE"
}
mutate() {
    local operation=$1 inverse=$2 store net gw mt
    IFS='|' read -r store net gw mt <<< "$operation"
    assert_lock
    if [[ $store == P ]]; then
        local direction=+ipv4.routes; $inverse && direction=-ipv4.routes
        local text="$net $gw"; [[ $mt == -1 ]] || text+=" $mt"
        capture discard nmcli --wait 10 connection modify uuid "$BASE_UUID" "$direction" "$text"
    else
        local verb=add; $inverse && verb=del
        capture discard ip -4 route "$verb" "$net" via "$gw" dev "$BASE_DEVICE" metric "$mt" table main
    fi
}
legacy_recovery() {
    # Controller-side legacy validation is retained. This process rechecks its
    # exact observations under the same kernel lock as all new operations.
    local legacy=$STATE/front-routes.lock file=$STATE/front-routes.lock/plan.json
    local value raw store spec net gw mt row index matches expected_p expected_r
    if [[ $ACTION == legacy-cleanup && ! -e $legacy && ! -L $legacy ]]; then return 0; fi
    [[ -d $legacy && ! -L $legacy && $(stat -c '%u:%a' "$legacy") == 0:700 ]] || die 'Unsafe legacy journal directory'
    [[ $(stat -c '%d:%i' "$legacy") == "$LEGACY_DIR_ID" ]] || die 'Legacy journal directory ownership changed'
    safe_file "$file"
    [[ $(stat -c '%d:%i' "$file") == "$LEGACY_FILE_ID" ]] || die 'Legacy journal file ownership changed'
    value=$(sha256sum "$file"); value=${value%% *}
    [[ $value == "$LEGACY_JOURNAL_SHA" && $(journal_count "$legacy") == 1 ]] || die 'Legacy journal changed or contains operator evidence'
    if [[ $ACTION == legacy-cleanup ]]; then
        assert_lock
        rm -- "$file"
        rmdir -- "$legacy"
        sync -- "$STATE"
        LEGACY_CHANGED=true
        return 0
    fi
    [[ ! -e $PENDING && ! -L $PENDING ]] || die 'Both legacy and current FRONT journals exist; preserve both'
    ip_number "$FRONT"; ip_number "$BACK"; ip_number "$ADM"
    local expected_uuid=$TOKEN expected_gateway=$GATEWAY
    observe
    [[ $UUID == "$expected_uuid" && $GATEWAY == "$expected_gateway" ]] || die 'Legacy FRONT profile or gateway changed'
    capture raw ip -j -4 route show table main
    value=$(printf '%s' "$raw" | sha256sum); value=${value%% *}
    [[ $value == "$LEGACY_KERNEL_SHA" ]] || die 'Legacy runtime observation changed before removal'
    capture raw nmcli -g ipv4.routes connection show uuid "$UUID"
    value=$(printf '%s' "$raw" | sha256sum); value=${value%% *}
    [[ $value == "$LEGACY_PROFILE_SHA" ]] || die 'Legacy persistent observation changed before removal'
    local -a expected_profile=("${PROFILE[@]}") expected_kernel=("${KERNEL[@]}")
    OPS=()
    normalize_ssh
    for store in P R; do
        local -a specs=()
        if [[ $store == P ]]; then specs=("${LEGACY_P[@]}"); else specs=("${LEGACY_R[@]}"); fi
        for spec in "${specs[@]}"; do
            read -ra parts <<< "$spec"
            (( ${#parts[@]} == 2 || ${#parts[@]} == 3 )) || die 'Invalid legacy route arguments'
            net=${parts[0]} gw=${parts[1]}
            if (( ${#parts[@]} == 3 )); then mt=${parts[2]}; elif [[ $store == P ]]; then mt=-1; else mt=0; fi
            network "$net"; [[ $net == "$CIDR" && $PREFIX -gt 0 && $gw == "$GATEWAY" ]] || die 'Invalid legacy route identity'
            if [[ -n $SSH_IPV4 ]]; then
                local lo=$LOW hi=$HIGH; ip_number "$SSH_IPV4"
                (( NUMBER < lo || NUMBER > hi )) || die 'Legacy recovery would affect the current SSH peer'
            fi
            [[ $mt == -1 && $store == P ]] || uint "$mt" || die 'Invalid legacy route metric'
            matches=0
            if [[ $store == P ]]; then
                for index in "${!expected_profile[@]}"; do
                    if [[ ${expected_profile[$index]} == "$net|$gw|$mt|" ]]; then unset 'expected_profile[index]'; ((matches+=1)); fi
                done
            else
                for index in "${!expected_kernel[@]}"; do
                    row=${expected_kernel[$index]}
                    if [[ $row == "$net|$gw|$mt|$DEVICE|" || $row == "$net|$gw|$mt|$DEVICE|proto=static;" ]]; then
                        unset 'expected_kernel[index]'; ((matches+=1))
                    fi
                done
            fi
            (( matches == 1 )) || die 'A legacy route changed before native removal'
            OPS+=("$store|$net|$gw|$mt")
        done
    done
    expected_p=$(rows "${expected_profile[@]}"); expected_r=$(rows "${expected_kernel[@]}")
    BASE_UUID=$UUID BASE_DEVICE=$DEVICE
    for row in "${OPS[@]}"; do mutate "$row" true; done
    if (( ${#OPS[@]} )); then LEGACY_CHANGED=true; fi
    observe
    [[ $OBSERVED_PROFILE == "$expected_p" && $OBSERVED_KERNEL == "$expected_r" ]] || die 'Legacy recovery stores changed; original journal preserved'
    printf 'Legacy FRONT removals completed under the process lock.\n'
}
rollback() {
    load_journal
    normalize_ssh
    if [[ -n $SSH_IPV4 ]]; then
        ip_number "$SSH_IPV4"; local ssh_number=$NUMBER route destination
        for route in "${OPS[@]}"; do
            destination=${route#*|}; destination=${destination%%|*}
            network "$destination"
            (( ssh_number < LOW || ssh_number > HIGH )) || die 'Recovery would affect the current SSH peer; journal preserved'
        done
    fi
    local n=$PROGRESS op store net gw mt current_p current_r expected_p=$BASE_PROFILE expected_r=$BASE_KERNEL
    local -a owned=()
    for ((n=0;n<PROGRESS;n++)); do
        op=${OPS[$n]}; owned+=("$op"); IFS='|' read -r store net gw mt <<< "$op"
        if [[ $store == P ]]; then expected_p+=$'\n'"$net|$gw|$mt|"; else expected_r+=$'\n'"$net|$gw|$mt|$BASE_DEVICE|"; fi
    done
    observe
    [[ $UUID == "$BASE_UUID" && $DEVICE == "$BASE_DEVICE" && $GATEWAY == "$BASE_GATEWAY" && $ADDRESS_TEXT == "$BASE_ADDRESSES" && $RULES_TEXT == "$BASE_RULES" ]] || die 'Live FRONT context changed; rollback refused'
    current_p=$(printf '%s\n' "$expected_p" | sed '/^$/d' | sort)
    current_r=$(printf '%s\n' "$expected_r" | sed '/^$/d' | sort)
    [[ $OBSERVED_PROFILE == "$current_p" && $OBSERVED_KERNEL == "$current_r" ]] || die 'Routes changed externally; no rollback deletion attempted'
    # An intent with unchanged stores proves that command did not add a route.
    # If a crash left an unacknowledged addition, the comparison above refuses
    # to guess its ownership and keeps the journal for operator inspection.
    [[ $PHASE == 'done' ]] || durable progress "$PROGRESS done"
    for ((n=PROGRESS;n>0;n--)); do
        durable progress "$n intent"
        mutate "${owned[$((n-1))]}" true
        durable progress "$((n-1)) done"
    done
    OPS=(); verify false
    remove_journal
    MUTATING=false
    printf 'FRONT rollback completed; preexisting routes preserved.\n'
}
# Invoked by EXIT; ShellCheck cannot follow this trap callback (SC2329).
# shellcheck disable=SC2329
finish() {
    local rc=$1 recovery_rc=0
    trap - EXIT ERR INT TERM
    if (( rc == 2 )) && ! $CHANGED_RESULT; then
        printf 'FRONT internal command failed with rc 2; refusing a success result.\n' >&2
        rc=1
    fi
    if (( rc != 0 && rc != 2 )) && $MUTATING; then
        printf 'FRONT operation failed; attempting guarded rollback.\n' >&2
        # Recovery checks must exit on their own failure without aborting cleanup
        # of the caller's temporary work directory.
        set +e
        (set -Eeuo pipefail; rollback)
        recovery_rc=$?
        if (( recovery_rc != 0 )); then printf 'FRONT recovery required: %s\n' "$PENDING" >&2; fi
    elif (( rc != 0 && rc != 2 )) && $PREPARED; then
        # No native write was attempted. This invocation may remove its own
        # complete prepared journal, even if a fresh observation refused drift.
        set +e
        (set -Eeuo pipefail; remove_journal)
        recovery_rc=$?
        if (( recovery_rc != 0 )); then printf 'Prepared journal retained: %s\n' "$PENDING" >&2; fi
    fi
    if ! cleanup; then
        printf 'FRONT temporary work cleanup failed; inspect %s.\n' "$WORK" >&2
        rc=1
    fi
    exit "$rc"
}
# Called through finish(), whose entry point is the EXIT trap.
# shellcheck disable=SC2329
cleanup() {
    [[ -n $WORK ]] || return 0
    rm -f -- "$WORK/out" "$WORK/err" || return 1
    rmdir -- "$WORK"
}

# Cover prerequisite/lock acquisition failures as well as routing mutations.
trap 'finish $?' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
while (( $# )); do
    case $1 in
        --action) ACTION=${2:?}; shift 2;; --front-ip) FRONT=${2:?}; shift 2;;
        --back-ip) BACK=${2:?}; shift 2;; --adm-ip) ADM=${2:?}; shift 2;;
        --peer-back-ip) PEERS+=("${2:?}"); shift 2;; --ssh-peer) SSH_PEER=${2-}; shift 2;;
        --client) CLIENTS+=("${2:?}"); shift 2;; --metric) METRIC=${2:?}; shift 2;;
        --token) TOKEN=${2:?}; shift 2;; --defer-commit) DEFER=true; shift;;
        --legacy-profile-sha) LEGACY_PROFILE_SHA=${2:?}; shift 2;;
        --legacy-kernel-sha) LEGACY_KERNEL_SHA=${2:?}; shift 2;;
        --legacy-journal-sha) LEGACY_JOURNAL_SHA=${2:?}; shift 2;;
        --legacy-dir-id) LEGACY_DIR_ID=${2:?}; shift 2;; --legacy-file-id) LEGACY_FILE_ID=${2:?}; shift 2;;
        --legacy-gateway) GATEWAY=${2:?}; shift 2;;
        --legacy-profile-route) LEGACY_P+=("${2:?}"); shift 2;; --legacy-runtime-route) LEGACY_R+=("${2:?}"); shift 2;;
        --help) printf 'Actions: plan, apply, verify, commit, rollback, recover. IPv4 routes only; rc 0 unchanged, 2 changed/planned, 1 refused.\n'; exit 0;;
        *) die "Unknown argument: $1";;
    esac
done
[[ $EUID == 0 ]] || die 'Root is required'
[[ $ACTION =~ ^(plan|apply|verify|commit|rollback|recover|legacy-rollback|legacy-cleanup)$ ]] || die 'Unsupported route action'
for required in ip nmcli flock timeout stat mktemp sync sha256sum sort tr wc sed cat head chmod mv rm rmdir mkdir; do
    command -v "$required" >/dev/null 2>&1 || die "Missing native prerequisite: $required; install the corresponding EL8 system package before retrying"
done
secure_dir "$STATE"
# Existence is never ownership. Kernel flock is released when the process exits.
LOCK=$STATE/operations.flock
if [[ ! -e $LOCK && ! -L $LOCK ]]; then (set -o noclobber; : >"$LOCK") 2>/dev/null || true; fi
safe_file "$LOCK"
[[ $(stat -c '%s' -- "$LOCK") == 0 ]] || die 'Operation lock file must remain empty'
exec 9<>"$LOCK"
[[ $(stat -Lc '%d:%i' /proc/self/fd/9) == $(stat -c '%d:%i' "$LOCK") ]] || die 'Operation lock changed while opening'
flock -n 9 || die 'Another route or management operation is active; retry after it ends'
WORK=$(mktemp -d "$STATE/.route-work.XXXXXXXX")
if [[ $ACTION == legacy-rollback || $ACTION == legacy-cleanup ]]; then
    legacy_recovery
    $LEGACY_CHANGED && changed_result
    exit 0
fi
if [[ $ACTION == recover || $ACTION == rollback || $ACTION == commit || $ACTION == verify ]]; then
    [[ -d $PENDING && ! -L $PENDING ]] || die 'No pending FRONT transaction to recover'
    [[ $ACTION == recover || -n $TOKEN ]] || die 'Transaction token is required'
    load_journal
    case $ACTION in
        recover|rollback) rollback; changed_result;;
        verify) verify; exit 0;;
        commit) (( PROGRESS == ${#OPS[@]} )) && [[ $PHASE == 'done' ]] || die 'Incomplete transaction cannot commit'; verify; remove_journal; printf 'FRONT transaction committed.\n'; exit 0;;
    esac
fi
[[ ! -e $STATE/front-routes.lock && ! -L $STATE/front-routes.lock ]] || die 'Legacy FRONT journal found: use routes-recover for legacy recovery before a new operation'
[[ ! -e $PENDING && ! -L $PENDING ]] || die 'Pending FRONT journal: inspect it, then use routes-recover; it is not a stale process lock'
ip_number "$FRONT"; ip_number "$BACK"; ip_number "$ADM"
[[ $FRONT != "$BACK" && $FRONT != "$ADM" && $BACK != "$ADM" ]] || die 'FRONT/BACK/ADM addresses must differ'
for peer in "${PEERS[@]}"; do ip_number "$peer"; done
if ! uint "$METRIC" || (( METRIC == 0 )); then die 'Metric must be an integer from 1 to 4294967295'; fi
(( ${#CLIENTS[@]} <= 512 )) || die 'At most 512 client destinations per operation'
observe
build_plan
printf 'FRONT device=%s profile=%s gateway=%s additions=%s\n' "$DEVICE" "$UUID" "${GATEWAY:-none}" "${#OPS[@]}"
for op in "${OPS[@]}"; do printf '  %s\n' "$op"; done
if [[ $ACTION == plan ]]; then (( ${#OPS[@]} == 0 )) && exit 0; changed_result; fi
(( ${#OPS[@]} )) || { BASE_PROFILE=$OBSERVED_PROFILE BASE_KERNEL=$OBSERVED_KERNEL BASE_UUID=$UUID BASE_DEVICE=$DEVICE BASE_GATEWAY=$GATEWAY BASE_ADDRESSES=$ADDRESS_TEXT BASE_RULES=$RULES_TEXT; verify; exit 0; }
BASE_PROFILE=$OBSERVED_PROFILE BASE_KERNEL=$OBSERVED_KERNEL BASE_UUID=$UUID BASE_DEVICE=$DEVICE BASE_GATEWAY=$GATEWAY BASE_ADDRESSES=$ADDRESS_TEXT BASE_RULES=$RULES_TEXT
prepare
# Re-observe before publication; a new profile or unrelated route is a conflict.
observe
[[ $OBSERVED_PROFILE == "$BASE_PROFILE" && $OBSERVED_KERNEL == "$BASE_KERNEL" && $UUID == "$BASE_UUID" && $DEVICE == "$BASE_DEVICE" && $GATEWAY == "$BASE_GATEWAY" && $ADDRESS_TEXT == "$BASE_ADDRESSES" && $RULES_TEXT == "$BASE_RULES" ]] || die 'Routing changed after planning; no native write attempted'
MUTATING=true
count=0
for op in "${OPS[@]}"; do
    durable progress "$count intent"
    mutate "$op" false
    ((count+=1))
    durable progress "$count done"
done
verify
MUTATING=false
if $DEFER; then printf 'transaction: %s\n' "$TOKEN"; else remove_journal; fi
changed_result

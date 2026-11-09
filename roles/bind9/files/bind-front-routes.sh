#!/bin/bash
# Additive FRONT routes on EL8. One process owns observation, writes and recovery.
# No interface reactivation, route replacement, shell evaluation, Python or jq.
set -Eeuo pipefail
export LC_ALL=C
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 077
# Glob inventories count actual entries, including an empty directory as zero.
shopt -s nullglob
readonly STATE=/var/lib/Bind9-Ansible
PENDING=$STATE/front-routes.pending
WORK=''
RETIRE_FROM='' RETIRE_TO='' RETIRE_ID=''
MUTATING=false
PREPARED=false
CHANGED_RESULT=false
ACTION=plan FRONT='' BACK='' ADM='' SSH_PEER='' METRIC=100 DEFER=false TOKEN=''
declare -a CLIENTS=() PEERS=() ADDRESSES=() PROTECTED=() PROFILE=() KERNEL=() OPS=()

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
    # Callers supply the fixed state path or an allocation already validated
    # inside that namespace. Recheck live ancestors and ownership here.
    local path=$1 parent=${1%/*}
    [[ -d $parent && ! -L $parent ]] || die "Unsafe state ancestor: $parent"
    local owner mode metadata
    owner=$(stat -c '%u' -- "$parent") || die 'State ancestor owner read failed'
    [[ $owner == 0 ]] || die "Untrusted state ancestor: $parent"
    mode=$(stat -c '%a' -- "$parent") || die 'State ancestor mode read failed'
    if [[ ! $mode =~ ^[0-7]{3,4}$ ]] || (( (8#$mode & 0022) != 0 )); then
        die "Writable state ancestor: $parent"
    fi
    if [[ ! -e $path && ! -L $path ]]; then
        # Another process may initialize this shared state directory first.
        # Accept only its subsequently validated directory, never repair modes.
        local creation_error
        if ! creation_error=$(mkdir -m 0700 -- "$path" 2>&1); then
            [[ -d $path && ! -L $path ]] || die "Cannot create private directory $path: $creation_error"
        fi
    fi
    [[ -d $path && ! -L $path ]] || die "Unsafe private directory: $path"
    metadata=$(stat -c '%u:%a' -- "$path") || die 'Private state metadata read failed'
    [[ $metadata == 0:700 ]] || die "Unsafe private directory: $path"
}
safe_file() {
    local metadata size
    [[ -f $1 && ! -L $1 ]] || die "Unsafe journal file: $1"
    metadata=$(stat -c '%u:%a:%h' -- "$1") || die 'Journal metadata read failed'
    [[ $metadata == 0:600:1 ]] || die "Unsafe journal file: $1"
    size=$(stat -c '%s' -- "$1") || die 'Journal size read failed'
    if ! uint "$size" || (( size > 1048576 )); then die "Oversized journal file: $1"; fi
}
allocate_temporary() {
    # Publish REPLY only after ownership is established. In particular, never
    # arm EXIT cleanup with an untrusted path returned by a successful mktemp.
    local prefix=$1 kind=$2 path suffix size
    if [[ $kind == directory ]]; then path=$(mktemp -d "${prefix}XXXXXXXX") || return 1;
    else path=$(mktemp "${prefix}XXXXXXXX") || return 1; fi
    [[ $path == "$prefix"* ]] || return 1
    suffix=${path#"$prefix"}
    [[ $suffix =~ ^[A-Za-z0-9]{8}$ && ! -L $path ]] || return 1
    if [[ $kind == directory ]]; then
        [[ -d $path ]] || return 1
        secure_dir "$path"
        # A name seen during expansion invalidates a fresh allocation even if
        # another actor removes it before a later filesystem existence check.
        local -a entry=("$path"/* "$path"/.[!.]* "$path"/..?*)
        (( ${#entry[@]} == 0 )) || return 1
    else
        safe_file "$path"
        size=$(stat -c '%s' -- "$path") || return 1
        [[ $size == 0 ]] || return 1
    fi
    REPLY=$path
}
journal_count() (
    # Count entries without findutils or filename-delimited text parsing.
    shopt -s nullglob dotglob
    local entries=("$PENDING"/*)
    printf '%s' "${#entries[@]}"
)
assert_lock() {
    safe_file "$LOCK"
    local descriptor path size
    descriptor=$(stat -Lc '%d:%i:%s' /proc/self/fd/9) || die 'Operation descriptor metadata read failed'
    path=$(stat -c '%d:%i:%s' "$LOCK") || die 'Operation lock metadata read failed'
    size=$(stat -c '%s' "$LOCK") || die 'Operation lock size read failed'
    [[ $descriptor == "$path" && $size == 0 ]] || die 'Transient operation lock changed; no further routing writes permitted'
}
capture() {
    # Return through REPLY; callers bind their own variables explicitly rather
    # than allowing a helper to assign a dynamically named variable.
    local capture_size capture_no_nul_count capture_full_count capture_value
    # The file-size limit makes excessive output fail, rather than silently truncate.
    if ! (ulimit -f 2048; timeout -k 5 30 "$@") >"$WORK/out" 2>"$WORK/err"; then
        printf 'FRONT command failed: %s\n' "$*" >&2
        head -c 2048 "$WORK/err" >&2
        die 'Native observation failed; no incomplete result accepted'
    fi
    capture_size=$(stat -c '%s' "$WORK/out") || die 'Observation size read failed'
    if ! uint "$capture_size" || (( capture_size > 1048576 )); then die 'Observation exceeds 1 MiB'; fi
    capture_no_nul_count=$(tr -d '\000' <"$WORK/out" | wc -c) || die 'Observation byte validation failed'
    capture_full_count=$(wc -c <"$WORK/out") || die 'Observation byte count failed'
    [[ $capture_no_nul_count == "$capture_full_count" ]] || die 'NUL in native observation'
    capture_value=$(cat "$WORK/out") || die 'Observation content read failed'
    REPLY=$capture_value
}
journal_text() {
    # Every current journal file is LF-terminated once by durable()/sha256sum.
    # Bash strips trailing LF/NUL in substitutions: validate bytes first, then
    # require the original size so hidden records cannot disappear on loading.
    local target=$1 file=$2 data='' journal_size
    safe_file "$file"
    capture cat -- "$file"; data=$REPLY
    journal_size=$(stat -c '%s' -- "$file") || die 'Journal framing size read failed'
    [[ $journal_size == "$(( ${#data} + 1 ))" ]] || die "Malformed journal text framing: $file"
    printf -v "$target" '%s' "$data"
}
durable() {
    local dest=$1 data=$2 temporary
    allocate_temporary "$PENDING/.write." file || die 'Unsafe journal temporary allocation'
    temporary=$REPLY
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
            if [[ ${parts[$offset]:-} != *=* && ${parts[$offset]:-} == *.* ]]; then gw=${parts[$offset]}; ((offset+=1)); fi
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
        declare -A seen=()
        while (( offset < ${#parts[@]} )); do
            token=${parts[$offset]}; ((offset+=1))
            case $token in
                via|dev|metric|proto|scope|src)
                    [[ ! ${seen[$token]+yes} ]] || die "Duplicate runtime route field: $token"
                    seen[$token]=1;;
            esac
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
rows() { if (( $# )); then printf '%s\n' "$@" | sed '/^$/d' | sort; fi; }
select_front_route_match() {
    # LPM is numeric, not a lexical sort of route strings. Lower metrics break
    # equal-prefix ties. Native aliases can repeat one connected path with
    # different local preferred sources; that still identifies one device.
    local row net gw mt dev attrs address best_prefix=-1 best_metric=4294967296 ambiguous=false
    local chosen_gw chosen_device chosen_attrs
    MATCH_KEY='' MATCH_DEVICE=''
    ip_number "$FRONT"; address=$NUMBER
    for row in "${KERNEL[@]}"; do
        IFS='|' read -r net gw mt dev attrs <<< "$row"
        [[ $dev != lo ]] || continue
        network "$net"
        (( address >= LOW && address <= HIGH )) || continue
        if (( PREFIX > best_prefix || (PREFIX == best_prefix && mt < best_metric) )); then
            best_prefix=$PREFIX best_metric=$mt MATCH_KEY=$row MATCH_DEVICE=$dev ambiguous=false
        elif (( PREFIX == best_prefix && mt == best_metric )); then
            IFS='|' read -r _ chosen_gw _ chosen_device chosen_attrs <<< "$MATCH_KEY"
            if [[ $row == "$MATCH_KEY" ]]; then
                continue
            elif [[ $dev == "$chosen_device" && $gw == 0.0.0.0 && $chosen_gw == 0.0.0.0 ]] &&
                    connected_attributes "$attrs" && connected_attributes "$chosen_attrs"; then
                # Stable selection also tolerates a different kernel row order
                # between the bounded match and full main-table observations.
                if [[ $row < $MATCH_KEY ]]; then MATCH_KEY=$row; fi
            else
                ambiguous=true
            fi
        fi
    done
    [[ -n $MATCH_KEY && -n $MATCH_DEVICE ]] || die 'No usable non-loopback routing match for the FRONT address'
    ! $ambiguous || die 'Ambiguous equally ranked FRONT routing matches; no routes changed'
}
discover_front_table_gateway() {
    # The active connection identifies the device; only that device's main
    # IPv4 routing table supplies its gateway. Profile gateway properties are
    # intentionally not consulted: they may be empty or stale on never-default
    # interfaces. A route's protocol/attributes do not change its next-hop.
    local row net gw metric dev attrs required=false
    for net in "${CLIENTS[@]}"; do
        network "$net"
        connected_range "$LOW" "$HIGH"
        if ! $DIRECTLY; then required=true; break; fi
    done
    # Directly connected clients need no inferred next-hop, even when unrelated
    # FRONT destinations legitimately use different routers.
    $required || return 0
    for row in "${KERNEL[@]}"; do
        IFS='|' read -r net gw metric dev attrs <<< "$row"
        [[ $dev == "$DEVICE" && $gw != 0.0.0.0 ]] || continue
        [[ -z $GATEWAY || $GATEWAY == "$gw" ]] || die 'Ambiguous gateways in FRONT routes; no routes changed'
        GATEWAY=$gw
    done
}
connected_range() {
    # A secondary /32 can share a device whose primary address supplies the
    # connected subnet. Never borrow a network from another interface.
    local low=$1 high=$2 subnet
    DIRECTLY=false PARTIAL=false
    for subnet in "${FRONT_CONNECTED[@]}"; do
        network "$subnet"
        if (( low >= LOW && high <= HIGH )); then DIRECTLY=true;
        elif overlap "$low" "$high" "$LOW" "$HIGH"; then PARTIAL=true; fi
    done
}
connected_attributes() {
    # ip route show normally includes a preferred local source on the native
    # kernel/link route. Retain every other opaque-attribute refusal.
    local attrs=$1 source address
    [[ $attrs != 'proto=kernel;scope=link;' ]] || return 0
    [[ $attrs =~ ^proto=kernel\;scope=link\;src=([0-9.]+)\;$ ]] || return 1
    source=${BASH_REMATCH[1]}
    for address in "${FRONT_SOURCES[@]}"; do
        [[ $source != "$address" ]] || return 0
    done
    return 1
}
observe() {
    capture ip -o -4 address show up; ADDRESS_TEXT=$REPLY
    local _index dev family address rest hits=0 back_hits=0 adm_hits=0 protected_devices='|'
    ADDRESSES=() PROTECTED=()
    local -a address_rows=()
    while read -r _index dev family address rest; do
        [[ $family == inet ]] || continue
        dev=${dev%:}; dev=${dev%@*}
        [[ $dev =~ ^[a-zA-Z0-9_.:-]+$ ]] || die 'Unsupported interface name'
        network "$address"
        ADDRESSES+=("${address%/*}")
        address_rows+=("$dev|$CIDR|${address%/*}")
        if [[ ${address%/*} == "$FRONT" ]]; then DEVICE=$dev; ((hits+=1)); fi
        if [[ ${address%/*} == "$BACK" ]]; then PROTECTED+=("$CIDR"); protected_devices+="$dev|"; ((back_hits+=1)); fi
        if [[ ${address%/*} == "$ADM" ]]; then PROTECTED+=("$CIDR"); protected_devices+="$dev|"; ((adm_hits+=1)); fi
    done <<< "$ADDRESS_TEXT"
    # Each matching device already passed the nonempty strict name parser;
    # unique address counts establish BACK/ADM presence without rechecking it.
    (( hits == 1 && back_hits == 1 && adm_hits == 1 )) && [[ $protected_devices != *"|$DEVICE|"* ]] || die 'FRONT must be uniquely present/up and distinct from BACK/ADM interfaces'
    FRONT_CONNECTED=() FRONT_SOURCES=()
    local row subnet local_source
    for row in "${address_rows[@]}"; do
        IFS='|' read -r dev subnet local_source <<< "$row"
        if [[ $dev == "$DEVICE" ]]; then FRONT_CONNECTED+=("$subnet"); FRONT_SOURCES+=("$local_source"); fi
    done
    local address_device=$DEVICE selected_match selected_device
    capture ip -o -4 route show match "$FRONT"; MATCH_TEXT=$REPLY
    canonical_kernel "$MATCH_TEXT"
    select_front_route_match
    selected_match=$MATCH_KEY selected_device=$MATCH_DEVICE
    [[ $selected_device == "$address_device" ]] || die 'The best FRONT routing match does not use the interface owning its address'
    DEVICE=$selected_device
    local link=''
    capture ip -o link show dev "$DEVICE"; link=$REPLY
    [[ $link != *' master '* ]] || die 'Enslaved FRONT interface is unsupported'
    capture nmcli -g GENERAL.CON-UUID device show "$DEVICE"; UUID=$REPLY
    [[ $UUID =~ ^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$ ]] || die 'FRONT has no unique active profile UUID'
    capture nmcli -g ipv4.routes connection show uuid "$UUID"; PROFILE_TEXT=$REPLY
    capture nmcli -g ipv4.route-table connection show uuid "$UUID"; TABLE=$REPLY
    [[ $TABLE == 0 || $TABLE == 254 ]] || die 'Only main-table FRONT profiles are supported'
    capture ip -o -4 rule show; RULES_TEXT=$REPLY
    [[ $RULES_TEXT =~ ^0:[[:space:]]+from[[:space:]]all[[:space:]]lookup[[:space:]]local$'\n'32766:[[:space:]]+from[[:space:]]all[[:space:]]lookup[[:space:]]main$'\n'32767:[[:space:]]+from[[:space:]]all[[:space:]]lookup[[:space:]]default$ ]] || die 'Custom IPv4 policy rules require operator-managed routing'
    capture ip -o -4 route show table main; KERNEL_TEXT=$REPLY
    [[ -n $KERNEL_TEXT ]] || die 'Empty main routing table observation; no route plan accepted'
    canonical_profile "$PROFILE_TEXT"
    canonical_kernel "$KERNEL_TEXT"
    select_front_route_match
    [[ $MATCH_DEVICE == "$selected_device" && $MATCH_KEY == "$selected_match" ]] || die 'FRONT routing match disagrees with the complete main table; no routes changed'
    GATEWAY=''
    discover_front_table_gateway
    if [[ -n $GATEWAY ]]; then
        # A next-hop must not be any address owned by this server, including
        # aliases/secondary addresses unrelated to the inventory FRONT address.
        for address in "${ADDRESSES[@]}"; do
            [[ $GATEWAY != "$address" ]] || die 'FRONT gateway is a local server address'
        done
        local usable=false gateway_number
        ip_number "$GATEWAY"; gateway_number=$NUMBER
        for subnet in "${FRONT_CONNECTED[@]}"; do
            network "$subnet"
            if (( (PREFIX >= 31 && gateway_number >= LOW && gateway_number <= HIGH) ||
                  (gateway_number > LOW && gateway_number < HIGH) )); then usable=true; break; fi
        done
        $usable || die 'FRONT gateway is not a usable on-link address'
    fi
    OBSERVED_PROFILE=$(rows "${PROFILE[@]}") || die 'Persistent route normalization failed'
    OBSERVED_KERNEL=$(rows "${KERNEL[@]}") || die 'Kernel route normalization failed'
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
        if [[ $store == R && $device == "$DEVICE" && $gw == 0.0.0.0 ]] && connected_attributes "$attrs"; then acceptable=true; fi
        if overlap "$lo" "$hi" "$tlo" "$thi"; then
            if (( prefix >= tp )); then
                if [[ $device != "$DEVICE" || $gw != "$GATEWAY" ]] || ! $acceptable; then
                    die "Conflicting $store route for $target; existing route preserved"
                fi
                # An exact canonical range has the same prefix, so the conflict
                # checks above already establish its device, next-hop and attrs.
                if (( lo == tlo && hi == thi )); then
                    ((EXACT_COUNT+=1)); COVERED=true; COVER_METRIC=$mt
                fi
            fi
        fi
    done
    (( EXACT_COUNT <= 1 )) || die "Duplicate $store routes for $target"
}
collapse_clients() {
    # Build candidate CIDRs without a Python filter. Merge
    # only adjacent aligned siblings; never widen the authorized destinations.
    local client row lo hi prefix last previous size sorted
    local -a input=() lows=() highs=() prefixes=()
    local -A authorized_rows=()
    WANTED=()
    for client in "${CLIENTS[@]}"; do
        network "$client"
        [[ $client != */* || $client == "$CIDR" ]] || die "Noncanonical client subnet: $client"
        (( PREFIX > 0 )) || die 'A client default route is forbidden'
        row="$LOW|$HIGH|$PREFIX"
        input+=("$row"); authorized_rows[$row]=0
    done
    # Process substitutions hide producer failures from the consuming loop.
    # Capture and check the complete bounded input before planning any write.
    sorted=$(if (( ${#input[@]} )); then printf '%s\n' "${input[@]}" | sort -t'|' -k1,1n -k3,3n; fi) || die 'Client normalization failed; no routes changed'
    while IFS= read -r row; do
        [[ -n $row ]] || continue
        # Sorting cannot grant permission to route a different subnet. Validate
        # the complete numeric row before using it as a key or in arithmetic.
        # Repeated and out-of-order authorized rows remain supported.
        [[ $row =~ ^[0-9]+\|[0-9]+\|[0-9]+$ && ${authorized_rows[$row]+yes} ]] ||
            die "Noncanonical client subnet: unauthorized normalization row $row"
        authorized_rows[$row]=1
        IFS='|' read -r lo hi prefix <<< "$row"
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
    for row in "${input[@]}"; do
        [[ ${authorized_rows[$row]} == 1 ]] || die 'Client normalization omitted an authorized subnet; no routes changed'
    done
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
        # collapse_clients accepts only authorized canonical rows and merges
        # aligned siblings. Repeated rows can still produce duplicate targets.
        [[ ! ${destinations[$target]+yes} ]] || continue
        destinations[$target]=1
        for range in "${PROTECTED[@]}"; do
            network "$range"; overlap "$lo" "$hi" "$LOW" "$HIGH" && die "Client $target overlaps protected network $range"
        done
        connected_range "$lo" "$hi"; directly=$DIRECTLY
        if ! $directly && $PARTIAL; then die "Client $target partially overlaps a FRONT connected network"; fi
        if $directly; then
            local gateway_saved=$GATEWAY; GATEWAY=0.0.0.0
            coverage R "$target" "$lo" "$hi" "$prefix"
            GATEWAY=$gateway_saved
            probe=$(( prefix < 31 ? lo+1 : lo ))
            ip_string "$probe"; check_path "$REPLY" false
            continue
        fi
        [[ -n $GATEWAY ]] || die "No IPv4 gateway in the FRONT device routing table; no profile/global gateway fallback (device=$DEVICE profile=$UUID destination=$target)"
        coverage P "$target" "$lo" "$hi" "$prefix"
        local profile_covered=$COVERED route_metric=${COVER_METRIC:-$METRIC}
        coverage R "$target" "$lo" "$hi" "$prefix"
        if $COVERED && ! $profile_covered; then die "Runtime-only route $target has no persistent ownership; existing route preserved"; fi
        if [[ $route_metric == -1 ]]; then
            if $COVERED; then route_metric=$COVER_METRIC;
            else
                capture nmcli -g ipv4.route-metric connection show uuid "$UUID"; route_metric=$REPLY
                if [[ $route_metric == -1 ]]; then
                    local row net gw mt dev attrs candidate_subnet front_number inherited=''
                    ip_number "$FRONT"; front_number=$NUMBER
                    for row in "${KERNEL[@]}"; do
                        IFS='|' read -r net gw mt dev attrs <<< "$row"
                        if [[ $gw == 0.0.0.0 && $dev == "$DEVICE" ]]; then
                            # A secondary /32 has no separate connected main
                            # route. Its profile can inherit the primary link
                            # metric, but contradictory link metrics are refused.
                            for candidate_subnet in "${FRONT_CONNECTED[@]}"; do
                                if [[ $net == "$candidate_subnet" ]]; then
                                    network "$candidate_subnet"
                                    (( front_number >= LOW && front_number <= HIGH )) || continue
                                    [[ -z $inherited || $inherited == "$mt" ]] || die 'Ambiguous effective FRONT metric'
                                    inherited=$mt
                                fi
                            done
                        fi
                    done
                    route_metric=$inherited
                fi
                uint "$route_metric" || die 'No effective metric could be observed on the active FRONT profile/device'
            fi
        fi
        if $profile_covered && $COVERED && [[ $COVER_METRIC != "$route_metric" ]]; then die "Persistent and runtime metrics disagree for $target; existing routes preserved"; fi
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
    capture ip -4 route get "$probe" from "$FRONT"; result=$REPLY
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
    [[ $UUID == "$BASE_UUID" && $DEVICE == "$BASE_DEVICE" && $ADDRESS_TEXT == "$BASE_ADDRESSES" && $RULES_TEXT == "$BASE_RULES" ]] || die 'FRONT profile, addresses or rules changed during operation'
    expected_p=$(rows "$expected_p") || die 'Expected persistent route normalization failed'
    expected_r=$(rows "$expected_r") || die 'Expected kernel route normalization failed'
    [[ $expected_p == "$OBSERVED_PROFILE" && $expected_r == "$OBSERVED_KERNEL" ]] || die 'Routing stores differ from the additive plan; preserve the journal'
    # A predecessor journal may have taken its gateway from a profile property
    # while its baseline table had no via route. After removing its last owned
    # route, exact restoration of BOTH stores proves rollback completion; the
    # table-derived gateway can then legitimately be empty. Apply/commit still
    # require the same gateway and the live return-path checks below.
    [[ ${1:-true} == true ]] || return 0
    [[ $GATEWAY == "$BASE_GATEWAY" ]] || die 'FRONT gateway changed during operation'
    local client lo hi prefix
    for client in "${CLIENTS[@]}"; do
        network "$client"; lo=$LOW hi=$HIGH prefix=$PREFIX
        ip_string "$(( prefix < 31 ? lo+1 : lo ))"; local probe=$REPLY
        connected_range "$lo" "$hi"
        if $DIRECTLY; then check_path "$probe" false; else check_path "$probe" true; fi
    done
}
prepare() {
    local sorted_clients completed
    # Count the glob directly, without a subshell/stdout parser. Every matching
    # object counts, including a dangling symlink; completed evidence is kept.
    local completed_entries=("$STATE"/.front-routes.retired.*)
    completed=${#completed_entries[@]}
    if (( completed >= 4096 )); then
        die 'Archive completed FRONT journals before opening another transaction (limit 4096)'
    fi
    sorted_clients=$(rows "${CLIENTS[@]}") || die 'Client journal normalization failed'
    mkdir -m 0700 "$PENDING"
    TOKEN=$(cat /proc/sys/kernel/random/uuid)
    # Establish the same identity required by load_journal before publishing
    # metadata or mutating routes; a successful producer can still be invalid.
    [[ $TOKEN =~ ^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$ ]] || die 'Invalid native transaction nonce; no route write attempted'
    durable meta "$TOKEN|$UUID|$DEVICE|$GATEWAY|$FRONT|$BACK|$ADM|$METRIC"
    durable operations "$(printf '%s\n' "${OPS[@]}")"
    durable profile "$OBSERVED_PROFILE"
    durable kernel "$OBSERVED_KERNEL"
    durable addresses "$ADDRESS_TEXT"
    durable rules "$RULES_TEXT"
    durable clients "$sorted_clients"
    (cd "$PENDING"; sha256sum meta operations profile kernel addresses rules clients >checksums)
    sync -- "$PENDING/checksums" "$PENDING"
    durable progress '0 done'
    PREPARED=true
}
load_journal() {
    secure_dir "$PENDING"
    local count expected token meta_record operation_records client_records checksums_record progress_record
    count=$(journal_count)
    [[ $count == 9 ]] || die 'Unexpected recovery journal contents; preserve them for inspection'
    journal_text meta_record "$PENDING/meta"
    journal_text operation_records "$PENDING/operations"
    journal_text client_records "$PENDING/clients"
    journal_text checksums_record "$PENDING/checksums"
    journal_text progress_record "$PENDING/progress"
    journal_text BASE_PROFILE "$PENDING/profile"
    journal_text BASE_KERNEL "$PENDING/kernel"
    journal_text BASE_ADDRESSES "$PENDING/addresses"
    journal_text BASE_RULES "$PENDING/rules"
    expected=$(cd "$PENDING"; sha256sum meta operations profile kernel addresses rules clients)
    [[ $expected == "$checksums_record" ]] || die 'Recovery journal checksum mismatch'
    [[ $meta_record != *$'\n'* && $meta_record =~ ^[^\|]+(\|[^\|]+){7}$ ]] || die 'Malformed route journal metadata'
    IFS='|' read -r token BASE_UUID BASE_DEVICE BASE_GATEWAY FRONT BACK ADM METRIC <<< "$meta_record"
    [[ $token =~ ^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$ && $BASE_UUID =~ ^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$ && $BASE_DEVICE =~ ^[a-zA-Z0-9_.:-]+$ ]] || die 'Malformed route journal identity'
    [[ -z $TOKEN || $TOKEN == "$token" ]] || die 'Transaction token does not own this pending operation'
    TOKEN=$token
    ip_number "$FRONT"; ip_number "$BACK"; ip_number "$ADM"; ip_number "$BASE_GATEWAY"
    if ! uint "$METRIC" || (( METRIC == 0 )); then die 'Invalid journal metric'; fi
    # A here-string gives mapfile one entry even for an empty string. Require
    # actual evidence before counting the independently bounded lists.
    [[ -n $operation_records ]] || die 'Invalid operation journal size'
    [[ -n $client_records ]] || die 'Invalid journal client count'
    mapfile -t OPS <<< "$operation_records"; mapfile -t CLIENTS <<< "$client_records"
    (( ${#OPS[@]} <= 1024 )) || die 'Invalid operation journal size'
    (( ${#CLIENTS[@]} <= 512 )) || die 'Invalid journal client count'
    # Rebuild the same exact CIDR union as planning, including aggregated
    # siblings. An operation outside that union has no recorded ownership.
    collapse_clients
    local op store net gw mt key destination
    declare -A allowed=() seen_operations=()
    for destination in "${WANTED[@]}"; do allowed[$destination]=1; done
    for op in "${OPS[@]}"; do
        [[ $op =~ ^[PR]\|[^\|]+\|[^\|]+\|[0-9]+$ ]] || die 'Invalid journal operation fields'
        IFS='|' read -r store net gw mt <<< "$op"
        if [[ $gw != "$BASE_GATEWAY" ]] || ! uint "$mt"; then
            die 'Invalid journal operation'
        fi
        network "$net"; [[ $CIDR == "$net" && $PREFIX -gt 0 ]] || die 'Invalid journal destination'
        [[ ${allowed[$net]+yes} ]] || die 'Journal operation outside recorded client union'
        key="$store|$net"
        [[ ! ${seen_operations[$key]+yes} ]] || die 'Duplicate journal operation ownership'
        seen_operations[$key]=1
        [[ $store != P ]] || (( mt > 0 )) || die 'Invalid persistent journal metric'
    done
    [[ $progress_record =~ ^(0|[1-9][0-9]{0,9})[[:blank:]](done|intent)$ ]] || die 'Malformed journal progress'
    read -r PROGRESS PHASE <<< "$progress_record"
    if ! uint "$PROGRESS" || (( PROGRESS > ${#OPS[@]} )); then die 'Malformed journal progress'; fi
}
restore_retirement() {
    # A rename can have taken effect even when its command fails or is
    # interrupted. Restore the complete inode, never recreate individual files
    # and never overwrite an object that appeared at the original pathname.
    local current original
    # Both callers require an armed retirement. An absent identity cannot
    # silently report successful compensation; the identity checks below fail.
    if [[ -d $RETIRE_FROM && ! -L $RETIRE_FROM ]]; then
        original=$(stat -c '%d:%i' -- "$RETIRE_FROM") || return 1
        [[ $original == "$RETIRE_ID" ]] || return 1
        PENDING=$RETIRE_FROM
        # The empty reservation was released before rename. Its inode number
        # can already belong to a new foreign directory: never use that stale
        # identity to delete anything at the destination during compensation.
        if [[ -e $RETIRE_TO || -L $RETIRE_TO ]]; then
            printf 'FRONT retirement destination retained for inspection: %s.\n' "$RETIRE_TO" >&2
        fi
    else
        [[ ! -e $RETIRE_FROM && ! -L $RETIRE_FROM && -d $RETIRE_TO && ! -L $RETIRE_TO ]] || return 1
        current=$(stat -c '%d:%i' -- "$RETIRE_TO") || return 1
        [[ $current == "$RETIRE_ID" ]] || return 1
        mv -T -n -- "$RETIRE_TO" "$RETIRE_FROM" || return 1
        [[ -d $RETIRE_FROM && ! -L $RETIRE_FROM && ! -e $RETIRE_TO && ! -L $RETIRE_TO ]] || return 1
        original=$(stat -c '%d:%i' -- "$RETIRE_FROM") || return 1
        [[ $original == "$RETIRE_ID" ]] || return 1
        PENDING=$RETIRE_FROM
    fi
    sync -- "$STATE" || return 1
    RETIRE_FROM='' RETIRE_TO='' RETIRE_ID=''
}
remove_journal() {
    # Unlinking nine files could destroy recovery metadata before a later
    # failure. Retire the whole verified directory atomically, retaining every
    # file as private evidence; completion never depends on deleting that data.
    local file retirement original_id empty_id observed failure='Journal retirement rename failed'
    assert_lock
    for file in meta operations profile kernel addresses rules clients checksums progress; do safe_file "$PENDING/$file"; done
    [[ $(journal_count) == 9 ]] || die 'Unexpected journal entry prevents cleanup'
    allocate_temporary "$STATE/.front-routes.retired.$TOKEN." directory || die 'Unsafe journal retirement allocation'
    retirement=$REPLY
    original_id=$(stat -c '%d:%i' -- "$PENDING") || die 'Journal retirement identity read failed'
    empty_id=$(stat -c '%d:%i' -- "$retirement") || die 'Retirement directory identity read failed'
    [[ $original_id =~ ^[0-9]+:[0-9]+$ && $empty_id =~ ^[0-9]+:[0-9]+$ && $original_id != "$empty_id" ]] || die 'Invalid journal retirement identities'
    RETIRE_FROM=$PENDING RETIRE_TO=$retirement RETIRE_ID=$original_id
    # Release only the owned empty reservation, then use no-clobber rename.
    # An object appearing in that interval must never be overwritten.
    if rmdir -- "$retirement" && mv -T -n -- "$PENDING" "$retirement"; then
        failure='Journal retirement identity changed'
        if [[ ! -e $RETIRE_FROM && ! -L $RETIRE_FROM && -d $retirement && ! -L $retirement ]]; then
            failure='Journal retirement identity read failed'
            if observed=$(stat -c '%d:%i' -- "$retirement"); then
                failure='Journal retirement identity changed'
                if [[ $observed == "$original_id" ]]; then
                    PENDING=$retirement
                    failure='Journal retirement sync failed'
                    if sync -- "$STATE"; then
                        RETIRE_FROM='' RETIRE_TO='' RETIRE_ID=''
                        return 0
                    fi
                fi
            fi
        fi
    fi
    if ! restore_retirement; then
        printf 'FRONT retirement compensation requires inspection: %s and %s; complete journal data was not unlinked.\n' "$RETIRE_FROM" "$RETIRE_TO" >&2
    fi
    die "$failure"
}
mutate() {
    local operation=$1 inverse=$2 store net gw mt
    IFS='|' read -r store net gw mt <<< "$operation"
    assert_lock
    if [[ $store == P ]]; then
        local direction=+ipv4.routes; $inverse && direction=-ipv4.routes
        # Planning and journal loading both require an explicit positive P
        # metric; the former automatic (-1) formatter is no longer reachable.
        local text="$net $gw $mt"
        capture nmcli --wait 10 connection modify uuid "$BASE_UUID" "$direction" "$text"
    else
        local verb=add; $inverse && verb=del
        capture ip -4 route "$verb" "$net" via "$gw" dev "$BASE_DEVICE" metric "$mt" table main
    fi
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
    [[ $UUID == "$BASE_UUID" && $DEVICE == "$BASE_DEVICE" &&
       ( $GATEWAY == "$BASE_GATEWAY" || -z $GATEWAY ) &&
       $ADDRESS_TEXT == "$BASE_ADDRESSES" && $RULES_TEXT == "$BASE_RULES" ]] || die 'Live FRONT context changed; rollback refused'
    current_p=$(rows "$expected_p") || die 'Rollback persistent route normalization failed'
    current_r=$(rows "$expected_r") || die 'Rollback kernel route normalization failed'
    [[ $OBSERVED_PROFILE == "$current_p" && $OBSERVED_KERNEL == "$current_r" ]] || die 'Routes changed externally; no rollback deletion attempted'
    if [[ -z $GATEWAY ]]; then
        # A predecessor could acknowledge only its persistent add before the
        # first runtime add. Exact stores above establish its unchanged kernel
        # baseline. Without an observed gateway, authorize only inverse P writes.
        for op in "${owned[@]}"; do
            [[ $op == P'|'* ]] || die 'Unavailable FRONT gateway cannot authorize runtime rollback; journal preserved'
        done
    fi
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
    trap - EXIT ERR HUP INT TERM
    if [[ -n $RETIRE_ID ]]; then
        if ! restore_retirement; then
            printf 'FRONT interrupted retirement requires inspection: %s and %s.\n' "$RETIRE_FROM" "$RETIRE_TO" >&2
        fi
    fi
    if (( rc == 2 )) && ! $CHANGED_RESULT; then
        printf 'FRONT internal command failed with rc 2; refusing a success result.\n' >&2
        rc=1
    fi
    if (( rc != 0 && rc != 2 )) && $MUTATING; then
        printf 'FRONT operation failed; attempting guarded rollback.\n' >&2
        # Recovery checks must exit on their own failure without aborting cleanup
        # of the caller's temporary work directory.
        set +e
        # Successful work cleanup may precede a refused journal commit. Create
        # a new private observation workspace before guarded recovery reads it.
        if [[ -z $WORK ]]; then
            if allocate_temporary "$STATE/.route-work." directory; then WORK=$REPLY;
            else recovery_rc=$?; fi
        fi
        if (( recovery_rc == 0 )); then
            (set -Eeuo pipefail; rollback)
            recovery_rc=$?
        fi
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
    rmdir -- "$WORK" || return 1
    WORK=''
}

# Cover prerequisite/lock acquisition failures as well as routing mutations.
trap 'finish $?' EXIT
# An SSH hangup must enter guarded recovery rather than abandon owned routes.
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
while (( $# )); do
    case $1 in
        --action) ACTION=${2:?}; shift 2;; --front-ip) FRONT=${2:?}; shift 2;;
        --back-ip) BACK=${2:?}; shift 2;; --adm-ip) ADM=${2:?}; shift 2;;
        --peer-back-ip) PEERS+=("${2:?}"); shift 2;;
        --ssh-peer)
            # An explicitly empty peer remains supported for local invocations.
            (( $# >= 2 )) || die 'Missing value for --ssh-peer'
            SSH_PEER=$2; shift 2;;
        --client) CLIENTS+=("${2:?}"); shift 2;; --metric) METRIC=${2:?}; shift 2;;
        --token) TOKEN=${2:?}; shift 2;; --defer-commit) DEFER=true; shift;;
        --help) printf 'Actions: plan, apply, verify, commit, rollback, recover. IPv4 routes only; rc 0 unchanged, 2 changed/planned, 1 refused.\n'; exit 0;;
        *) die "Unknown argument: $1";;
    esac
done
[[ $EUID == 0 ]] || die 'Root is required'
[[ $ACTION =~ ^(plan|apply|verify|commit|rollback|recover)$ ]] || die 'Unsupported route action'
readonly ACTION
for required in ip nmcli flock timeout stat mktemp sync sha256sum sort tr wc sed cat head chmod mv rm rmdir mkdir; do
    command -v "$required" >/dev/null 2>&1 || die "Missing native prerequisite: $required; install the corresponding EL8 system package before retrying"
done
secure_dir "$STATE"
# Existence is never ownership. Kernel flock is released when the process exits.
LOCK=$STATE/operations.flock
if [[ ! -e $LOCK && ! -L $LOCK ]]; then (set -o noclobber; : >"$LOCK") 2>/dev/null || true; fi
safe_file "$LOCK"
lock_size=$(stat -c '%s' -- "$LOCK") || die 'Operation lock size read failed'
[[ $lock_size == 0 ]] || die 'Operation lock file must remain empty'
exec 9<>"$LOCK"
lock_descriptor=$(stat -Lc '%d:%i' /proc/self/fd/9) || die 'Operation descriptor identity read failed'
lock_path=$(stat -c '%d:%i' "$LOCK") || die 'Operation lock identity read failed'
[[ $lock_descriptor == "$lock_path" ]] || die 'Operation lock changed while opening'
flock -n 9 || die 'Another route or management operation is active; retry after it ends'
allocate_temporary "$STATE/.route-work." directory || die 'Unsafe observation temporary allocation'
WORK=$REPLY
if [[ $ACTION == recover || $ACTION == rollback || $ACTION == commit || $ACTION == verify ]]; then
    [[ -d $PENDING && ! -L $PENDING ]] || die 'No pending FRONT transaction to recover'
    [[ $ACTION == recover || -n $TOKEN ]] || die 'Transaction token is required'
    load_journal
    if [[ $ACTION == recover || $ACTION == rollback ]]; then
        rollback; changed_result
    elif [[ $ACTION == verify ]]; then
        verify; exit 0
    else
        # The immutable recovery action can only be commit at this point.
        (( PROGRESS == ${#OPS[@]} )) && [[ $PHASE == 'done' ]] || die 'Incomplete transaction cannot commit'
        verify; remove_journal; printf 'FRONT transaction committed.\n'; exit 0
    fi
fi
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
[[ $DEVICE == "$BASE_DEVICE" && $OBSERVED_PROFILE == "$BASE_PROFILE" && $OBSERVED_KERNEL == "$BASE_KERNEL" && $UUID == "$BASE_UUID" && $GATEWAY == "$BASE_GATEWAY" && $ADDRESS_TEXT == "$BASE_ADDRESSES" && $RULES_TEXT == "$BASE_RULES" ]] || die 'Routing changed after planning; no native write attempted'
MUTATING=true
count=0
for op in "${OPS[@]}"; do
    durable progress "$count intent"
    mutate "$op" false
    ((count+=1))
    durable progress "$count done"
done
verify
# Keep the complete journal and guarded rollback armed until temporary cleanup
# and publication finish. A cleanup failure must never leave successful writes
# behind while returning a failed addition to Ansible.
cleanup || die 'Temporary work cleanup failed before FRONT transaction commit'
if $DEFER; then printf 'transaction: %s\n' "$TOKEN"; else remove_journal; fi
MUTATING=false
changed_result

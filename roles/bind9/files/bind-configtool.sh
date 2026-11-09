#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
export LC_ALL=C
# BEGIN ARCHIVE SAFETY HELPERS
valid_octal_mode() {
  # stat output is data: reject expressions before Bash arithmetic can expand it.
  [[ $1 =~ ^(0|[1-7][0-7]{0,3})$ ]]
}
private_file() {
  # Never read secrets/archives through links, FIFOs or files shared by inode.
  local file=$1 owner mode links observed metadata_pattern='^([0-9]+) ([^[:space:]]+) ([0-9]+)$'
  [[ -f $file && ! -L $file ]] || { echo 'Expected a private regular file' >&2; return 1; }
  observed=$(stat -c '%u %a %h' -- "$file") || return 1
  # A first-line split silently ignores additional records from the producer.
  # Parse the complete response before trusting any ownership or mode field.
  [[ $observed =~ $metadata_pattern ]] || { echo 'Unsafe file ownership, permissions or link count' >&2; return 1; }
  owner=${BASH_REMATCH[1]} mode=${BASH_REMATCH[2]} links=${BASH_REMATCH[3]}
  if [[ $owner != "$EUID" || $links != 1 ]] || ! valid_octal_mode "$mode" || ! (( (8#$mode & 077) == 0 )); then
    echo 'Unsafe file ownership, permissions or link count' >&2; return 1;
  fi
}
allocate_temporary() {
  # mktemp stdout cannot authorize access to an existing operator object.
  # Validate the exact namespace, private metadata and emptiness before use.
  local prefix=$1 kind=$2 path suffix metadata entry
  if [[ $kind == directory ]]; then path=$(mktemp -d "${prefix}XXXXXXXX") || return 1;
  else path=$(mktemp "${prefix}XXXXXXXX") || return 1; fi
  [[ $path == "$prefix"* ]] || { echo 'Unexpected temporary path' >&2; return 1; }
  suffix=${path#"$prefix"}
  [[ $suffix =~ ^[A-Za-z0-9]{8}$ && ! -L $path ]] || { echo 'Unsafe temporary path' >&2; return 1; }
  if [[ $kind == directory ]]; then
    [[ -d $path ]] || return 1
    metadata=$(stat -c '%u:%a' -- "$path") || return 1
    [[ $metadata == "$EUID:700" ]] || return 1
    # Include hidden entries without changing the caller's glob settings.
    for entry in "$path"/* "$path"/.[!.]* "$path"/..?*; do
      [[ ! -e $entry && ! -L $entry ]] || return 1
    done
  else
    private_file "$path" || return 1
    metadata=$(stat -c '%a:%s' -- "$path") || return 1
    [[ $metadata == 600:0 ]] || return 1
  fi
  printf '%s' "$path"
}
bounded_capture() (
  # Temporary output is private and bounded before it becomes a Bash string.
  # A failing producer, timeout or cleanup never becomes an empty observation.
  local mode=$1 work size status=0 parent=/tmp limit duration blocks non_nul
  # Policies are immutable data. An unknown key fails under nounset before
  # allocating a workspace or executing a producer; there is no default policy.
  local -Ar policies=([archive]='16777216 60 32768' [dns]='1048576 15 2048')
  shift
  read -r limit duration blocks <<< "${policies[$mode]}"
  [[ $mode != archive ]] || parent=$root
  work=$(allocate_temporary "$parent/.verify." directory) || return 1
  trap 'rm -f -- "$work/out" "$work/err" && rmdir -- "$work" || exit 1' EXIT
  (ulimit -f "$blocks" && timeout --kill-after=5 "$duration" "$@") >"$work/out" 2>"$work/err" || status=$?
  # dig can report an explicit wire refusal with rc 9. Timeouts, resource
  # failures and every other producer failure stay inconclusive, even if a
  # plausible response prefix was printed before that failure.
  if [[ $status != 0 ]] && [[ $mode != dns || $status != 9 ]]; then
    if [[ $mode == archive ]]; then head -c 2048 -- "$work/err" >&2; fi
    echo 'Native observation failed or exceeded its time/output budget' >&2
    return 1
  fi
  # dig can ignore an unreadable/invalid -k file, query unsigned and return rc0.
  # Any client diagnostic makes DNS evidence inconclusive; never echo its text.
  if [[ $mode == dns && -s $work/err ]]; then
    echo 'Native DNS client reported a diagnostic; no result accepted' >&2
    return 1
  fi
  size=$(stat -c '%s' -- "$work/out") || return 1
  if [[ ! $size =~ ^(0|[1-9][0-9]{0,9})$ ]] || (( size > limit )); then
    echo 'Invalid or oversized native observation' >&2; return 1
  fi
  non_nul=$(tr -d '\000' <"$work/out" | wc -c) || return 1
  non_nul=${non_nul//[[:space:]]/}
  [[ $non_nul == "$size" ]] || { echo 'Binary native observation refused' >&2; return 1; }
  cat -- "$work/out"
)
# END ARCHIVE SAFETY HELPERS
usage() { echo 'usage: bind-configtool {backup [--force]|list|restore --archive FILE} [--root ABSOLUTE_DIR] | check-front-axfr FRONT_DNS_IP ZONE CLIENT_SOURCE_IP ROOT_ONLY_TSIG_KEYFILE' >&2; exit 2; }
classify_axfr_response() {
  # Set the caller's AXFR_STATUS from the native text common to EL8 and recent
  # dig. YAML hides missing-signature warnings and cannot qualify a signed test.
  # A status mentioned in a note or RDATA is not a wire refusal.
  local response=$1 line statuses=0 headers=0 flags=0 invalid=false transferred=false signature_failed=false
  local header_re='^;; ->>HEADER<<- opcode: QUERY, status: (REFUSED|NOTAUTH), id: [0-9]+$'
  local flags_re='^;; flags: ([a-z]+ )*[a-z]+; QUERY: 1, ANSWER: 0, AUTHORITY: 0, ADDITIONAL: [0-9]+$'
  local qr_re='^;; flags: ([a-z]+ )*qr( |;)' tc_re='^;; flags: ([a-z]+ )*tc( |;)'
  AXFR_STATUS=inconclusive
  while IFS= read -r line; do
    [[ $line != *status:* ]] || ((statuses+=1))
    [[ ! $line =~ [[:space:]]IN[[:space:]]+SOA[[:space:]] ]] || transferred=true
    [[ $line != ";; Couldn't verify signature:"* ]] || signature_failed=true
    if [[ $line == ';; ->>HEADER<<-'* ]]; then
      ((headers+=1))
      [[ $line =~ $header_re ]] || invalid=true
    elif [[ $line == ';; flags:'* ]]; then
      ((flags+=1))
      [[ $line =~ $flags_re && $line =~ $qr_re && ! $line =~ $tc_re ]] || invalid=true
    fi
  done <<< "$response"
  if [[ $transferred == true ]]; then AXFR_STATUS=transferred; return 0; fi
  if [[ $signature_failed == true || $response == *BADKEY* || $response == *BADSIG* || $response == *BADTIME* ]]; then
    AXFR_STATUS=authentication_failed; return 0
  fi
  if [[ $invalid == false ]] && (( statuses == 1 && headers == 1 && flags == 1 )); then
    AXFR_STATUS=refused
  fi
}
[[ $# -ge 1 ]] || usage
readonly action=$1; shift
check_front_axfr() {
  (($# == 4)) || usage
  local server=$1 zone=$2 source=$3 keyfile=$4 label output AXFR_STATUS
  command -v dig >/dev/null 2>&1 || { echo 'dig missing' >&2; return 2; }
  [[ -f $keyfile && -r $keyfile && ! -L $keyfile ]] || { echo 'TSIG keyfile must be a readable regular file, not a symlink' >&2; return 2; }
  private_file "$keyfile" || { echo 'TSIG keyfile must be private and owned by the invoking user' >&2; return 2; }
  # Never echo the key or use dig -y: the secret must not appear in process arguments.
  for label in unsigned signed; do
    # Explicit -q/-t keep a zone beginning with @, + or - in the question field.
    local -a args=(-r -4 +time=3 +tries=1 -b "$source" "@$server" -q "$zone" -t AXFR)
    # Common text output retains native TSIG verification diagnostics, including
    # unsigned responses to a signed question; +yaml suppresses that warning.
    args+=(+noall +comments +answer)
    [[ $label == signed ]] && args+=(-k "$keyfile")
    # A wire refusal can make dig exit nonzero; classify its DNS response.
    output=$(bounded_capture dns dig "${args[@]}") || {
      echo "INCONCLUSIVE: bounded FRONT AXFR observation failed ($label)" >&2; return 2;
    }
    classify_axfr_response "$output"
    if [[ $AXFR_STATUS == transferred ]]; then
      echo "FAIL: FRONT AXFR succeeded ($label)" >&2
      return 1
    fi
    if [[ $AXFR_STATUS == authentication_failed ]]; then
      echo "INCONCLUSIVE: TSIG authentication failed ($label)" >&2
      return 2
    fi
    if [[ $AXFR_STATUS == refused ]]; then
      echo "DENIED: FRONT AXFR explicitly refused ($label)"
    else
      echo "INCONCLUSIVE: FRONT AXFR did not return one complete explicit denial ($label)" >&2
      return 2
    fi
  done
}
if [[ $action == check-front-axfr ]]; then
  check_front_axfr "$@"
  exit $?
fi
root=/var/backups/Bind9-Ansible/bind
archive_name=
# Reject action-specific options before opening or creating backup state.
while (($#)); do
  case "$1" in
    --root) (($# >= 2)) || usage; root=$2; shift 2 ;;
    --force) [[ $action == backup ]] || usage; shift ;;
    --archive) [[ $action == restore && $# -ge 2 ]] || usage; archive_name=$2; shift 2 ;;
    *) usage ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo 'Root required' >&2; exit 1; }
case $action in backup|list|restore) ;; *) usage ;; esac
[[ $root == /* && $root != / && $root != /etc && $root != /var && $root != /var/named &&
   $root != /etc/* && $root != /var/named/* && $root != *$'\n'* && $root != *$'\r'* ]] || usage
# A plausible partial stdout cannot establish successful canonicalization.
# Reuse the existing archive-observation deadline before creating any state.
canonical_root=$(timeout --kill-after=5 60 realpath -m -- "$root") || {
  echo 'Backup root canonicalization failed; no backup state created' >&2; exit 1;
}
[[ $root == "$canonical_root" ]] || usage
# Check every existing ancestor before mkdir/chmod can follow an unexpected link.
probe=$root
while [[ $probe != / ]]; do
  if [[ -e $probe || -L $probe ]]; then
    [[ -d $probe && ! -L $probe ]] || { echo 'Unsafe backup directory component' >&2; exit 1; }
    ancestor_metadata=$(stat -c '%u %a' -- "$probe") || { echo 'Backup ancestor metadata read failed' >&2; exit 1; }
    ancestor_pattern='^([0-9]+) ([^[:space:]]+)$'
    [[ $ancestor_metadata =~ $ancestor_pattern ]] || {
      echo 'Backup ancestors must be root-owned and not writable by others, except sticky ancestors' >&2; exit 1;
    }
    owner=${BASH_REMATCH[1]} mode=${BASH_REMATCH[2]}
    if [[ $owner != 0 ]] || ! valid_octal_mode "$mode" || ! (( (8#$mode & 022) == 0 || (8#$mode & 01000) != 0 )); then
      echo 'Backup ancestors must be root-owned and not writable by others, except sticky ancestors' >&2; exit 1;
    fi
  fi
  probe=$(dirname -- "$probe")
done
# Never chmod an existing general-purpose directory supplied as --root.
if [[ -d $root ]]; then
  root_mode=$(stat -c '%a' -- "$root") || { echo 'Backup root metadata read failed' >&2; exit 1; }
  [[ $root_mode == 700 ]] || { echo 'Existing backup root must have mode 0700' >&2; exit 1; }
fi
mkdir -p -- "$root"
if [[ -e $root/.lock || -L $root/.lock ]]; then private_file "$root/.lock"; fi
exec 9<>"$root/.lock"
private_file "$root/.lock"
flock -w 30 -x 9 || { echo 'Backup lock timed out; inspect the active operation' >&2; exit 1; }
shopt -s nullglob
archives=("$root"/named-config-????????T??????Z-????????.tar.gz)
# Lexicographic order matches UTC timestamps.
# Check the producer before accepting a complete, possibly empty catalog.
catalog=$(printf '%s\n' "${archives[@]}" | sed '/^$/d' | sort -r) || { echo 'Archive catalog read failed' >&2; exit 1; }
sorted=()
if [[ -n $catalog ]]; then mapfile -t sorted <<< "$catalog"; fi
verify() {
  local file=$1 entry listing details expected actual duplicate tar_bytes tail_bytes checksum_size archive_size checksum_name
  local gzip_listing gzip_size gzip_pattern=$'^[^\n]+\n[[:space:]]*[0-9]+[[:space:]]+(0|[1-9][0-9]{0,9})[[:space:]]+[^\n]+$'
  local mode owner size rest count=0 payload=0 max_payload=1073741824 max_stream=1140860928 max_members=65536
  local footprint_count=0 ordered previous='' lower upper middle prefix shared slashes
  local parent_key previous_parent=''
  local -a checksum_lines=() member_names=()
  private_file "$file" && private_file "$file.sha256" || return 1
  # Match the reverse reconciler's existing 1 GiB payload / 65,536-member
  # budget, allowing bounded TAR metadata and padding in the physical stream.
  archive_size=$(stat -c '%s' -- "$file") || return 1
  if [[ ! $archive_size =~ ^(0|[1-9][0-9]{0,9})$ ]] || (( archive_size > max_stream )); then
    echo 'Invalid or oversized compressed archive' >&2; return 1
  fi
  checksum_size=$(stat -c '%s' -- "$file.sha256") || return 1
  if [[ ! $checksum_size =~ ^(0|[1-9][0-9]*)$ ]] || (( checksum_size > 512 )); then
    echo 'Invalid or oversized checksum file' >&2; return 1;
  fi
  mapfile -t checksum_lines < "$file.sha256"
  (( ${#checksum_lines[@]} == 1 )) || { echo 'Checksum must describe exactly one archive' >&2; return 1; }
  expected=${checksum_lines[0]:0:64}
  [[ $expected =~ ^[0-9a-f]{64}$ ]] || { echo 'Invalid checksum record' >&2; return 1; }
  # A failed filename producer must not authorize extraction of its partial output.
  checksum_name=$(basename -- "$file") || { echo 'Archive filename observation failed' >&2; return 1; }
  [[ ${checksum_lines[0]} == "$expected  $checksum_name" ]] || {
    echo 'Invalid checksum record' >&2; return 1;
  }
  # Hash stdin so GNU filename escaping cannot prefix the digest with '\'.
  # Root line terminators are rejected before the line-delimited catalog runs.
  actual=$(sha256sum <"$file") || return 1
  [[ ${actual%% *} == "$expected" ]] || { echo 'Checksum mismatch' >&2; return 1; }
  # The final gzip member's declared size is a refusal hint only. It can wrap
  # modulo 2^32 or omit preceding members, so it never replaces the bounded
  # decompression, physical record count or complete TAR checks below. Reject
  # an already excessive declaration before spending CPU expanding a bomb.
  gzip_listing=$(bounded_capture archive gzip --list -- "$file") || return 1
  [[ $gzip_listing =~ $gzip_pattern ]] || { echo 'Malformed gzip size observation' >&2; return 1; }
  gzip_size=${BASH_REMATCH[1]}
  (( gzip_size <= max_stream )) || { echo 'Incomplete or oversized tar records' >&2; return 1; }
  # GNU tar can accept a short non-tar stream as an empty archive. Require
  # complete 512-byte records and the two zero end-of-archive blocks as well.
  tar_bytes=$(timeout --kill-after=5 60 gzip -cd -- "$file" | head -c "$((max_stream+1))" | wc -c) || return 1
  # Observations are decimal data, never arithmetic expressions supplied by a
  # producer. The tail accepts only the exact zero count it must establish.
  if [[ ! $tar_bytes =~ ^(0|[1-9][0-9]{0,9})$ ]] ||
      ! (( tar_bytes <= max_stream && tar_bytes >= 1024 && tar_bytes % 512 == 0 )); then
    echo 'Incomplete or oversized tar records' >&2; return 1
  fi
  tail_bytes=$(timeout --kill-after=5 60 gzip -cd -- "$file" | head -c "$((max_stream+1))" | tail -c 1024 | tr -d '\000' | wc -c) || return 1
  [[ $tail_bytes == 0 ]] || { echo 'Missing tar end-of-archive blocks' >&2; return 1; }
  # Capture producer status explicitly: process substitution hides tar failures.
  # Scan beyond zero end markers: concatenated records must not hide an unsafe
  # path, link or duplicate from validation while sharing a valid checksum.
  listing=$(bounded_capture archive tar --list --ignore-zeros --gzip --absolute-names --quoting-style=escape --file "$file") || {
    echo 'Unreadable or truncated tar archive' >&2; return 1;
  }
  details=$(bounded_capture archive tar --list --ignore-zeros --verbose --gzip --absolute-names --numeric-owner --quoting-style=escape --file "$file") || return 1
  if [[ -z $listing ]]; then
    [[ -z $details ]] || { echo 'Inconsistent empty archive catalog' >&2; return 1; }
    return 0  # A first deployment may back up an empty layout.
  fi
  # Enforce the member ceiling before iterating over a potentially large
  # catalog. The separate expanded-footprint ceiling still counts parents.
  mapfile -t member_names <<< "$listing"
  (( ${#member_names[@]} <= max_members )) || { echo 'Archive member/payload budget exceeded' >&2; return 1; }
  while IFS= read -r entry; do
    (( ${#entry} <= 4096 )) || { echo 'Archive path exceeds 4096 bytes' >&2; return 1; }
    [[ $entry != *\\* ]] || { echo 'Escaped/control characters in archive path' >&2; return 1; }
    entry=${entry%/}
    case /$entry/ in *'/../'*|*'/./'*|*'//'*) echo 'Non-canonical archive path' >&2; return 1 ;; esac
    case "$entry" in
      etc/named.conf|etc/named|etc/named/*|var/named|var/named/*) ;;
      *) echo "Unsafe archive entry: $entry" >&2; return 1 ;;
    esac
  done <<< "$listing"
  # Sort once: complete path components shared with the preceding member are
  # exactly the ancestors already counted. Appending a virtual slash to every
  # member keeps directory/child keys adjacent even beside names containing !.
  # Binary search bounds comparisons by
  # path length rather than materializing every implicit directory in Bash.
  ordered=$(printf '%s\n' "$listing" | sed 's#/$##;s#$#/#' | sort) || return 1
  duplicate=$(printf '%s\n' "$ordered" | uniq -d) || return 1
  [[ -z $duplicate ]] || { echo 'Duplicate archive members' >&2; return 1; }
  while IFS= read -r entry; do
    parent_key=${entry%/*/}
    # Consecutive siblings add exactly one object. Avoid repeated prefix
    # searches for large flat catalogs without retaining a per-path index.
    if [[ $parent_key == "$previous_parent" ]]; then
      footprint_count=$((footprint_count+1))
    else
      lower=0; upper=${#previous}
      if (( upper > ${#entry} )); then upper=${#entry}; fi
      while (( lower < upper )); do
        middle=$(((lower+upper+1)/2))
        if [[ ${previous:0:middle} == "${entry:0:middle}" ]]; then lower=$middle;
        else upper=$((middle-1)); fi
      done
      prefix=${entry:0:lower}; slashes=${prefix//[^\/]/}; shared=${#slashes}
      slashes=${entry//[^\/]/}
      footprint_count=$((footprint_count+${#slashes}-shared))
    fi
    previous_parent=$parent_key
    (( footprint_count <= max_members+2 )) || { echo 'Archive extraction footprint exceeds its object budget' >&2; return 1; }
    previous=$entry
  done <<< "$ordered"
  while IFS= read -r entry; do
    [[ $entry == [-d]* ]] || { echo 'Unsupported archive member type' >&2; return 1; }
    read -r mode owner size rest <<< "$entry"
    if [[ ! $size =~ ^(0|[1-9][0-9]{0,9})$ ]] || (( size > max_payload )); then
      echo 'Invalid or oversized archive member' >&2; return 1
    fi
    count=$((count+1)); payload=$((payload+size))
    (( count <= ${#member_names[@]} && payload <= max_payload )) || { echo 'Archive member/payload budget exceeded' >&2; return 1; }
  done <<< "$details"
  # Successful producers can still disagree. Never accept omitted or extra
  # detail records as a complete observation of the validated member catalog.
  (( count == ${#member_names[@]} )) || { echo 'Inconsistent archive catalogs' >&2; return 1; }
}

safe_sources() {
  # Reject unsafe extraction destinations as well as unsafe backup sources.
  local item unsafe prefix=${1:-}
  for item in /etc/named.conf /etc/named /var/named; do
    item=$prefix$item
    [[ ! -L $item ]] || { echo "Refusing source/destination symlink: $item" >&2; return 1; }
    [[ ! -e $item ]] && continue
    if [[ $item == "$prefix/etc/named.conf" ]]; then
      [[ -f $item ]] || { echo 'named.conf must be a regular file' >&2; return 1; }
    else
      [[ -d $item ]] || { echo 'BIND data paths must be directories' >&2; return 1; }
    fi
    unsafe=$(find "$item" -type f -links +1 -print -quit) || return 1
    [[ -z $unsafe ]] || { echo 'Hardlinked BIND source/destination file' >&2; return 1; }
    unsafe=$(find "$item" ! -type f ! -type d -print -quit) || return 1
    [[ -z $unsafe ]] || { echo 'Special BIND source/destination object' >&2; return 1; }
  done
}

restore_labels() {
  local item
  local -a paths=()
  for item in /etc/named.conf /etc/named /var/named; do [[ ! -e $item ]] || paths+=("$item"); done
  # Called only after configuration validation. A now-empty graph is drift,
  # not successful relabelling; let the caller restore its safety archive.
  (( ${#paths[@]} > 0 )) || { echo 'BIND runtime disappeared before relabelling' >&2; return 1; }
  restorecon -RF "${paths[@]}"
}
# Every backup invocation creates a uniquely named archive. --force is accepted
# for compatibility but no longer changes the backup frequency.
backup() {
  local stamp target partial item cutoff timestamp epoch catalog target_name checksum checksum_partial index cleanup_failed
  safe_sources
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  target="$root/named-config-$stamp-$(od -An -N4 -tx1 /dev/urandom | tr -d " \n").tar.gz"
  [[ ! -e $target && ! -L $target && ! -e $target.sha256 && ! -L $target.sha256 ]] || {
    echo "Backup archive already exists or its checksum name is occupied: $target" >&2; return 1;
  }
  # Resolve once before creating an archive; command substitutions in arguments
  # otherwise hide a failing producer and can publish an incomplete backup pair.
  target_name=$(basename -- "$target") || { echo 'Backup filename observation failed' >&2; return 1; }
  partial=$(allocate_temporary "$root/.named-backup." file) || return 1
  local -a members=()
  for item in etc/named.conf etc/named var/named; do [[ ! -e /$item ]] || members+=("$item"); done
  if ((${#members[@]})); then
    tar --create --gzip --acls --xattrs --selinux --numeric-owner --file "$partial" --directory / -- "${members[@]}" || { rm -f -- "$partial"; return 1; }
  else
    tar --create --gzip --file "$partial" --files-from /dev/null || { rm -f -- "$partial"; return 1; }
  fi
  tar -tzf "$partial" --ignore-zeros >/dev/null || { rm -f -- "$partial"; return 1; }
  checksum_partial=$(allocate_temporary "$root/.named-backup." file) || { rm -f -- "$partial"; return 1; }
  # Hash private bytes through stdin: GNU's output then contains no filename
  # escaping, even when the configured backup root contains special characters.
  if ! checksum=$(sha256sum <"$partial") || [[ ! $checksum =~ ^[0-9a-f]{64}[[:blank:]]{2}-$ ]]; then
    echo 'Backup checksum production failed; no archive published' >&2
    rm -f -- "$partial" "$checksum_partial"; return 1
  fi
  printf '%s  %s\n' "${checksum:0:64}" "$target_name" >"$checksum_partial" || {
    rm -f -- "$partial" "$checksum_partial"; return 1;
  }
  # Atomic, no-clobber links protect against an object appearing after the
  # initial check. -T prevents a newly created directory becoming a container.
  # Keep private inode references until BOTH public names have been created.
  if ln -T -- "$partial" "$target" && ln -T -- "$checksum_partial" "$target.sha256"; then
    rm -- "$partial" "$checksum_partial"
  else
    local -a publication_sources=("$partial" "$checksum_partial") publication_targets=("$target" "$target.sha256")
    cleanup_failed=false
    for index in 0 1; do
      if [[ ! -L ${publication_targets[$index]} && ${publication_targets[$index]} -ef ${publication_sources[$index]} ]]; then
        rm -- "${publication_targets[$index]}" || cleanup_failed=true
      fi
    done
    if [[ $cleanup_failed == true ]]; then
      echo "Backup publication recovery required; private inode references retained: $partial $checksum_partial" >&2
      return 1
    fi
    rm -- "$partial" "$checksum_partial"
    echo 'Backup publication refused; preexisting objects preserved' >&2
    return 1
  fi
  verify "$target"
  catalog=$(find "$root" -maxdepth 1 -type f -name 'named-config-????????T??????Z-????????.tar.gz' -printf '%p\n' | sort -r) || {
    echo 'Retention catalog read failed; no archive purged' >&2; return 1;
  }
  # Require the exact newly published row before retention. Other archives
  # cannot mask its disappearance, and glob-like root bytes remain literal.
  [[ $'\n'$catalog$'\n' == *$'\n'"$target"$'\n'* ]] || {
    echo 'Published backup missing from retention catalog' >&2; return 1;
  }
  # Apply a 30-day UTC retention window based on the filename timestamp.
  # File mtimes are deliberately ignored because copying changes them.
  cutoff=$(date -u -d '30 days ago' +%s)
  while IFS= read -r item; do
    timestamp=$(basename "$item")
    timestamp=${timestamp#named-config-}
    timestamp=${timestamp%%-*}
    if epoch=$(date -u -d "${timestamp:0:8} ${timestamp:9:2}:${timestamp:11:2}:${timestamp:13:2} UTC" +%s 2>/dev/null); then
      if (( epoch < cutoff )); then
        rm -f -- "$item" "$item.sha256"
      fi
    fi
  done <<< "$catalog"
  echo "BACKUP_CREATED $target"
}
restore() {
  local target safety item member_listing work stage work_identity entry index
  local -a staged_paths=() introduced_paths=() introduced_types=()
  [[ $archive_name =~ ^named-config-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.tar\.gz$ ]] || usage
  target="$root/$archive_name"
  verify "$target"
  safe_sources
  local service_state service_rc=0
  service_state=$(systemctl is-active named) || service_rc=$?
  [[ $service_rc == 3 || $service_rc == 4 ]] && [[ $service_state == inactive || $service_state == failed || $service_state == unknown ]] || {
    echo 'Stop named and confirm its inactive state before restore' >&2; return 1;
  }
  # Refuse a failed/partial member list before creating safety data or deleting
  # any live project-owned zone. A process substitution would hide tar errors.
  member_listing=$(tar -tzf "$target" --ignore-zeros | sed 's#^\./##') || {
    echo 'Restore member catalog read failed; live data preserved' >&2; return 1;
  }
  # Inspect the actual extraction footprint, including implicit parents, in a
  # private tree before touching live data. The verified payload is <= 1 GiB.
  work=$(allocate_temporary "$root/.restore-state." directory) || return 1
  # Allocation already validates the namespace. Freeze its result so cleanup
  # can concentrate on live ownership/type/identity instead of parsing it again.
  readonly work
  work_identity=$(stat -c '%d:%i' -- "$work") || return 1
  stage=$work/tree
  if ! mkdir -m 0700 -- "$stage" ||
     ! timeout --kill-after=5 60 tar --extract --ignore-zeros --gzip --acls --xattrs --selinux --numeric-owner --same-owner -f "$target" -C "$stage" ||
     ! safe_sources "$stage"; then
    echo "Restore preparation failed; private evidence retained: $work" >&2; return 1
  fi
  if ! (ulimit -f 32768; timeout --kill-after=5 60 find "$stage" -mindepth 1 -depth -print0) >"$work/paths"; then
    echo "Restore footprint read failed; private evidence retained: $work" >&2; return 1
  fi
  mapfile -d '' -t staged_paths < "$work/paths"
  (( ${#staged_paths[@]} <= 65538 )) || { echo "Restore footprint exceeds its member budget: $work" >&2; return 1; }
  for entry in "${staged_paths[@]}"; do
    item=${entry#"$stage/"}
    case $item in
      etc|var) continue ;;
      etc/named.conf|etc/named|etc/named/*|var/named|var/named/*) ;;
      *) echo "Unexpected staged restore path; evidence retained: $work" >&2; return 1 ;;
    esac
    if [[ ! -e /$item && ! -L /$item ]]; then
      introduced_paths+=("/$item")
      if [[ -d $entry && ! -L $entry ]]; then introduced_types+=(directory);
      elif [[ -f $entry && ! -L $entry ]]; then introduced_types+=(file);
      else echo "Unsafe staged restore object: $work" >&2; return 1; fi
    fi
  done
  # Native staging observations do not freeze the live destination graph.
  # Refuse drift before capturing safety data from newly unsafe destinations.
  safe_sources || {
    echo "Live restore destinations changed after preparation; private evidence retained: $work" >&2; return 1;
  }
  safety=$(allocate_temporary "$root/.restore-safety." file) || return 1
  local -a members=()
  for item in etc/named.conf etc/named var/named; do [[ ! -e /$item ]] || members+=("$item"); done
  if ((${#members[@]})); then
    tar --create --gzip --acls --xattrs --selinux --numeric-owner -f "$safety" -C / -- "${members[@]}" || { rm -f -- "$safety"; return 1; }
  else
    tar -czf "$safety" -T /dev/null
  fi
  # No live write has happened yet. Do not run rollback over an external path
  # replacement merely because it appeared during native safety capture.
  safe_sources || {
    echo "Live restore destinations changed before application; safety archive retained: $safety; private evidence: $work" >&2; return 1;
  }
  # Remove only project-marked zone files absent from the backup, never unrelated files.
  # The current BIND service must be stopped before manual restore.
  local -a archived_files=()
  if [[ -n $member_listing ]]; then mapfile -t archived_files <<< "$member_listing"; fi
  apply_restore_files() {
    # Every mutation is explicitly checked: conditional function calls suppress
    # Bash errexit. Pruning failures must reach the same safety recovery as tar.
    local existing relative preserved marker='; Bind9-Ansible-MANAGED-ZONE' IFS=' '
    local -a marker_status=()
    for existing in /var/named/zones/*.zone; do
      [[ -f "$existing" && ! -L "$existing" ]] || continue
      relative=${existing#/}
      preserved=false
      for item in "${archived_files[@]}"; do
        if [[ $item == "$relative" ]]; then preserved=true; break; fi
      done
      if [[ $preserved == false ]]; then
        # Read only enough bytes to distinguish the exact first-line marker.
        # Binary NULs are data, not implicit line endings. A producer/grep
        # error must trigger safety recovery, never mean "unmanaged file".
        if timeout --kill-after=5 5 head -c "$((${#marker}+1))" -- "$existing" |
           head -n 1 | grep -a -q -x -F "$marker"; then
          rm -f -- "$existing" || return 1
        else
          marker_status=("${PIPESTATUS[@]}")
          # Exact native status vector: both readers succeeded and grep found
          # no marker. The pinned join separator also preserves the arity check.
          [[ "${marker_status[*]}" == '0 0 1' ]] || return 1
        fi
      fi
    done
    tar --extract --ignore-zeros --gzip --acls --xattrs --selinux --numeric-owner --same-owner -f "$target" -C / || return 1
    named-checkconf -z /etc/named.conf || return 1
    restore_labels || return 1
  }
  cleanup_restore_workspace() {
    # Only the private workspace created by this invocation is disposable.
    local owner_mode identity
    [[ ! -L $work ]] || return 1
    owner_mode=$(stat -c '%u:%a' -- "$work") || return 1
    [[ $owner_mode == 0:700 ]] || return 1
    identity=$(stat -c '%d:%i' -- "$work") || return 1
    [[ $identity == "$work_identity" ]] || return 1
    find "$work" -xdev -depth -delete
  }
  removal_target_identity() {
    # Recheck the type/link policy at each deletion, retaining a native identity
    # for the earlier validation pass. Directory ctime legitimately changes as
    # owned children are removed, so it is not an ownership token.
    local path=$1 expected_type=$2 link_count identity
    [[ ! -L $path ]] || return 1
    if [[ $expected_type == directory ]]; then [[ -d $path ]] || return 1;
    else
      [[ -f $path ]] || return 1
      link_count=$(stat -c '%h' -- "$path") || return 1
      [[ $link_count == 1 ]] || return 1
    fi
    identity=$(stat -c '%d:%i' -- "$path") || return 1
    [[ $identity =~ ^[0-9]+:[0-9]+$ ]] || return 1
    REPLY=$identity
  }
  remove_introduced_paths() {
    # Validate every current object before deleting any of them. Unexpected
    # links/types preserve the safety archive and staged recovery evidence.
    local -a removal_identities=()
    safe_sources || return 1
    for ((index=0;index<${#introduced_paths[@]};index++)); do
      item=${introduced_paths[$index]}
      # Absence never authorizes removing a later object at the same pathname.
      removal_identities[index]=''
      [[ -e $item || -L $item ]] || continue
      removal_target_identity "$item" "${introduced_types[$index]}" || return 1
      removal_identities[index]=$REPLY
    done
    # find's depth order ensures children precede parents; never recursively
    # remove a live directory containing unrelated data introduced by an operator.
    for ((index=0;index<${#introduced_paths[@]};index++)); do
      item=${introduced_paths[$index]}
      [[ -e $item || -L $item ]] || continue
      [[ -n ${removal_identities[$index]} ]] || return 1
      removal_target_identity "$item" "${introduced_types[$index]}" || return 1
      [[ $REPLY == "${removal_identities[$index]}" ]] || return 1
      if [[ ${introduced_types[$index]} == directory ]]; then rmdir -- "$item" || return 1;
      else rm -- "$item" || return 1; fi
    done
  }
  # BIND validation follows extraction; service inactivity was checked above.
  if apply_restore_files; then
    cleanup_restore_workspace || { echo "Restore completed but private workspace cleanup failed: $work" >&2; return 1; }
    rm -f -- "$safety"
    echo "RESTORED $target"
  else
    echo 'Restore failed; recovering pre-restore safety archive' >&2
    if ! tar --extract --ignore-zeros --gzip --acls --xattrs --selinux --numeric-owner --same-owner -f "$safety" -C / ||
       ! remove_introduced_paths ||
       ! named-checkconf -z /etc/named.conf || ! restore_labels; then
      echo "Safety recovery incomplete; preserved private archive: $safety; restore evidence: $work" >&2
      return 1
    fi
    cleanup_restore_workspace || { echo "Safety recovery completed but private workspace cleanup failed: $work" >&2; return 1; }
    rm -f -- "$safety"
    return 1
  fi
}
# The action was validated before opening state and has remained readonly.
# Express the remaining enum value directly instead of checking it again.
if [[ $action == backup ]]; then
  backup
elif [[ $action == list ]]; then
  for item in "${sorted[@]}"; do verify "$item"; basename "$item"; done
else
  restore
fi

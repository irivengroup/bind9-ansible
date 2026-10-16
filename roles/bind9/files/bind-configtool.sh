#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
export LC_ALL=C
# BEGIN ARCHIVE SAFETY HELPERS
private_file() {
  # Never read secrets/archives through links, FIFOs or files shared by inode.
  local file=$1 owner mode links
  [[ -f $file && ! -L $file ]] || { echo 'Expected a private regular file' >&2; return 1; }
  read -r owner mode links < <(stat -c '%u %a %h' -- "$file")
  [[ $owner == "$EUID" && $links == 1 ]] && (( (8#$mode & 077) == 0 )) || {
    echo 'Unsafe file ownership, permissions or link count' >&2; return 1;
  }
}
# END ARCHIVE SAFETY HELPERS
usage() { echo 'usage: bind-configtool {backup [--force]|list|restore --archive FILE} [--root ABSOLUTE_DIR] | check-front-axfr FRONT_DNS_IP ZONE CLIENT_SOURCE_IP ROOT_ONLY_TSIG_KEYFILE' >&2; exit 2; }
[[ $# -ge 1 ]] || usage
action=$1; shift
check_front_axfr() {
  (($# == 4)) || usage
  local server=$1 zone=$2 source=$3 keyfile=$4 label output dig_help
  command -v dig >/dev/null 2>&1 || { echo 'dig missing' >&2; return 2; }
  [[ -f $keyfile && -r $keyfile && ! -L $keyfile ]] || { echo 'TSIG keyfile must be a readable regular file, not a symlink' >&2; return 2; }
  private_file "$keyfile" || { echo 'TSIG keyfile must be private and owned by the invoking user' >&2; return 2; }
  dig_help=$(dig -h 2>&1) || true
  # Never echo the key or use dig -y: the secret must not appear in process arguments.
  for label in unsigned signed; do
    local -a args=(+time=3 +tries=1 -b "$source" "@$server" "$zone" AXFR)
    # Recent dig exposes the actual AXFR response RCODE in YAML. Older dig
    # can print only "Transfer failed"; that is deliberately inconclusive.
    [[ $dig_help == *'+[no]yaml'* || $dig_help == *'+yaml'* ]] && args+=(+yaml)
    [[ $label == signed ]] && args+=(-k "$keyfile")
    # A wire refusal can make dig exit nonzero; classify its DNS response.
    output=$(dig "${args[@]}" 2>&1) || true
    if grep -Eq '[[:space:]]IN[[:space:]]+SOA[[:space:]]' <<< "$output"; then
      echo "FAIL: FRONT AXFR succeeded ($label)" >&2
      return 1
    fi
    if grep -Eq 'BADKEY|BADSIG|BADTIME' <<< "$output"; then
      echo "INCONCLUSIVE: TSIG authentication failed ($label)" >&2
      return 2
    fi
    if grep -Eq 'status: (REFUSED|NOTAUTH)([,[:space:]]|$)' <<< "$output"; then
      echo "DENIED: FRONT AXFR explicitly refused ($label)"
    else
      echo "INCONCLUSIVE: FRONT AXFR did not return an explicit denial ($label): $output" >&2
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
while (($#)); do
  case "$1" in
    --root) (($# >= 2)) || usage; root=$2; shift 2 ;;
    --force) [[ $action == backup ]] || usage; shift ;;
    --archive) (($# >= 2)) || usage; archive_name=$2; shift 2 ;;
    *) usage ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo 'Root required' >&2; exit 1; }
case $action in backup|list|restore) ;; *) usage ;; esac
[[ $root == /* && $root != / && $root != /etc && $root != /var && $root != /var/named &&
   $root != /etc/* && $root != /var/named/* && $root == "$(realpath -m -- "$root")" ]] || usage
# Check every existing ancestor before mkdir/chmod can follow an unexpected link.
probe=$root
while [[ $probe != / ]]; do
  if [[ -e $probe || -L $probe ]]; then
    [[ -d $probe && ! -L $probe ]] || { echo 'Unsafe backup directory component' >&2; exit 1; }
    read -r owner mode < <(stat -c '%u %a' -- "$probe")
    [[ $owner == 0 ]] && (( (8#$mode & 022) == 0 || (8#$mode & 01000) != 0 )) || {
      echo 'Backup ancestors must be root-owned and not writable by others, except sticky ancestors' >&2; exit 1;
    }
  fi
  probe=$(dirname -- "$probe")
done
# Never chmod an existing general-purpose directory supplied as --root.
if [[ -d $root ]]; then
  [[ $(stat -c '%a' -- "$root") == 700 ]] || { echo 'Existing backup root must have mode 0700' >&2; exit 1; }
fi
mkdir -p -- "$root"
if [[ -e $root/.lock || -L $root/.lock ]]; then private_file "$root/.lock"; fi
exec 9<>"$root/.lock"
private_file "$root/.lock"
flock -w 30 -x 9 || { echo 'Backup lock timed out; inspect the active operation' >&2; exit 1; }
shopt -s nullglob
archives=("$root"/named-config-????????T??????Z-????????.tar.gz)
# Lexicographic order matches UTC timestamps.
mapfile -t sorted < <(printf '%s\n' "${archives[@]}" | sed '/^$/d' | sort -r)
verify() {
  local file=$1 entry listing details expected actual duplicate tar_bytes tail_bytes
  local -a checksum_lines=()
  private_file "$file" && private_file "$file.sha256" || return 1
  (( $(stat -c '%s' -- "$file.sha256") <= 512 )) || { echo 'Oversized checksum file' >&2; return 1; }
  mapfile -t checksum_lines < "$file.sha256"
  (( ${#checksum_lines[@]} == 1 )) || { echo 'Checksum must describe exactly one archive' >&2; return 1; }
  expected=${checksum_lines[0]:0:64}
  [[ $expected =~ ^[0-9a-f]{64}$ && ${checksum_lines[0]} == "$expected  $(basename -- "$file")" ]] || {
    echo 'Invalid checksum record' >&2; return 1;
  }
  actual=$(sha256sum -- "$file") || return 1
  [[ ${actual%% *} == "$expected" ]] || { echo 'Checksum mismatch' >&2; return 1; }
  # GNU tar can accept a short non-tar stream as an empty archive. Require
  # complete 512-byte records and the two zero end-of-archive blocks as well.
  tar_bytes=$(gzip -cd -- "$file" | wc -c) || return 1
  (( tar_bytes >= 1024 && tar_bytes % 512 == 0 )) || { echo 'Incomplete tar records' >&2; return 1; }
  tail_bytes=$(gzip -cd -- "$file" | tail -c 1024 | tr -d '\000' | wc -c) || return 1
  (( tail_bytes == 0 )) || { echo 'Missing tar end-of-archive blocks' >&2; return 1; }
  # Capture producer status explicitly: process substitution hides tar failures.
  listing=$(tar --list --gzip --absolute-names --quoting-style=escape --file "$file") || {
    echo 'Unreadable or truncated tar archive' >&2; return 1;
  }
  details=$(tar --list --verbose --gzip --absolute-names --numeric-owner --quoting-style=escape --file "$file") || return 1
  [[ -n $listing ]] || return 0  # A first deployment may back up an empty layout.
  while IFS= read -r entry; do
    [[ $entry != *\\* ]] || { echo 'Escaped/control characters in archive path' >&2; return 1; }
    entry=${entry%/}
    case /$entry/ in *'/../'*|*'/./'*|*'//'*) echo 'Non-canonical archive path' >&2; return 1 ;; esac
    case "$entry" in
      etc/named.conf|etc/named|etc/named/*|var/named|var/named/*) ;;
      *) echo "Unsafe archive entry: $entry" >&2; return 1 ;;
    esac
  done <<< "$listing"
  duplicate=$(printf '%s\n' "$listing" | sed 's#/$##' | sort | uniq -d) || return 1
  [[ -z $duplicate ]] || { echo 'Duplicate archive members' >&2; return 1; }
  while IFS= read -r entry; do
    [[ $entry == [-d]* ]] || { echo 'Unsupported archive member type' >&2; return 1; }
  done <<< "$details"
}

safe_sources() {
  # Reject unsafe extraction destinations as well as unsafe backup sources.
  local item unsafe
  for item in /etc/named.conf /etc/named /var/named; do
    [[ ! -L $item ]] || { echo "Refusing source/destination symlink: $item" >&2; return 1; }
    [[ ! -e $item ]] && continue
    if [[ $item == /etc/named.conf ]]; then
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
  (( ${#paths[@]} == 0 )) || restorecon -RF "${paths[@]}"
}
# Every backup invocation creates a uniquely named archive. --force is accepted
# for compatibility but no longer changes the backup frequency.
backup() {
  local stamp target partial item cutoff timestamp epoch
  safe_sources
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  target="$root/named-config-$stamp-$(od -An -N4 -tx1 /dev/urandom | tr -d " \n").tar.gz"
  [[ ! -e "$target" ]] || { echo "Backup archive already exists: $target" >&2; return 1; }
  partial=$(mktemp "$root/.named-backup.XXXXXXXX")
  local -a members=()
  for item in etc/named.conf etc/named var/named; do [[ ! -e /$item ]] || members+=("$item"); done
  if ((${#members[@]})); then
    tar --create --gzip --acls --xattrs --selinux --numeric-owner --file "$partial" --directory / -- "${members[@]}" || { rm -f -- "$partial"; return 1; }
  else
    tar --create --gzip --file "$partial" --files-from /dev/null || { rm -f -- "$partial"; return 1; }
  fi
  tar -tzf "$partial" >/dev/null || { rm -f -- "$partial"; return 1; }
  mv -- "$partial" "$target"
  (cd "$root" && sha256sum -- "$(basename "$target")" >"$(basename "$target").sha256")
  chmod 0600 -- "$target" "$target.sha256"
  verify "$target"
  mapfile -t sorted < <(find "$root" -maxdepth 1 -type f -name 'named-config-????????T??????Z-????????.tar.gz' -printf '%p\n' | sort -r)
  # Apply a 30-day UTC retention window based on the filename timestamp.
  # File mtimes are deliberately ignored because copying changes them.
  cutoff=$(date -u -d '30 days ago' +%s)
  for item in "${sorted[@]}"; do
    timestamp=$(basename "$item")
    timestamp=${timestamp#named-config-}
    timestamp=${timestamp%%-*}
    if epoch=$(date -u -d "${timestamp:0:8} ${timestamp:9:2}:${timestamp:11:2}:${timestamp:13:2} UTC" +%s 2>/dev/null); then
      if (( epoch < cutoff )); then
        rm -f -- "$item" "$item.sha256"
      fi
    fi
  done
  echo "BACKUP_CREATED $target"
}
restore() {
  local target safety item
  [[ $archive_name =~ ^named-config-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}\.tar\.gz$ ]] || usage
  target="$root/$archive_name"
  verify "$target"
  safe_sources
  local service_state service_rc=0
  service_state=$(systemctl is-active named) || service_rc=$?
  [[ $service_rc == 3 || $service_rc == 4 ]] && [[ $service_state == inactive || $service_state == failed || $service_state == unknown ]] || {
    echo 'Stop named and confirm its inactive state before restore' >&2; return 1;
  }
  safety=$(mktemp "$root/.restore-safety.XXXXXXXX")
  local -a members=()
  for item in etc/named.conf etc/named var/named; do [[ ! -e /$item ]] || members+=("$item"); done
  if ((${#members[@]})); then
    tar --create --gzip --acls --xattrs --selinux --numeric-owner -f "$safety" -C / -- "${members[@]}" || { rm -f -- "$safety"; return 1; }
  else
    tar -czf "$safety" -T /dev/null
  fi
  # Remove only project-marked zone files absent from the backup, never unrelated files.
  # The current BIND service must be stopped before manual restore.
  local -a archived_files=()
  mapfile -t archived_files < <(tar -tzf "$target" | sed 's#^\./##')
  local existing relative preserved
  for existing in /var/named/zones/*.zone; do
    [[ -f "$existing" && ! -L "$existing" ]] || continue
    relative=${existing#/}
    preserved=false
    for item in "${archived_files[@]}"; do
      if [[ $item == "$relative" ]]; then preserved=true; break; fi
    done
    if [[ $preserved == false ]] && head -n 1 -- "$existing" | grep -q '^; Bind9-Ansible-MANAGED-ZONE$'; then
      rm -f -- "$existing"
    fi
  done
  # BIND validation follows extraction; service inactivity was checked above.
  if tar --extract --gzip --acls --xattrs --selinux --numeric-owner --same-owner -f "$target" -C / && named-checkconf -z /etc/named.conf && restore_labels; then
    rm -f -- "$safety"
    echo "RESTORED $target"
  else
    echo 'Restore failed; recovering pre-restore safety archive' >&2
    if ! tar --extract --gzip --acls --xattrs --selinux --numeric-owner --same-owner -f "$safety" -C / ||
       ! named-checkconf -z /etc/named.conf || ! restore_labels; then
      echo "Safety recovery incomplete; preserved private archive: $safety" >&2
      return 1
    fi
    rm -f -- "$safety"
    return 1
  fi
}
case "$action" in
  backup) backup ;;
  list) for item in "${sorted[@]}"; do verify "$item"; basename "$item"; done ;;
  restore) restore ;;
  *) usage ;;
esac

#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
usage() { echo 'usage: bind-configtool {backup [--force]|list|restore --archive FILE} [--root ABSOLUTE_DIR] | check-front-axfr FRONT_DNS_IP ZONE CLIENT_SOURCE_IP ROOT_ONLY_TSIG_KEYFILE' >&2; exit 2; }
[[ $# -ge 1 ]] || usage
action=$1; shift
check_front_axfr() {
  (($# == 4)) || usage
  local server=$1 zone=$2 source=$3 keyfile=$4 label output status
  command -v dig >/dev/null 2>&1 || { echo 'dig missing' >&2; return 2; }
  [[ -f $keyfile && -r $keyfile && ! -L $keyfile ]] || { echo 'TSIG keyfile must be a readable regular file, not a symlink' >&2; return 2; }
  # Never echo the key or use dig -y: the secret must not appear in process arguments.
  for label in unsigned signed; do
    local -a args=(+time=3 +tries=1 -b "$source" "@$server" "$zone" AXFR)
    [[ $label == signed ]] && args+=(-k "$keyfile")
    status=0
    output=$(dig "${args[@]}" 2>&1) || status=$?
    if [[ $status -eq 0 ]]; then
      echo "FAIL: FRONT AXFR succeeded ($label)" >&2
      return 1
    fi
    if grep -Eiq 'Transfer failed|REFUSED|NOTAUTH|connection refused' <<< "$output"; then
      echo "PASS: FRONT AXFR denied ($label)"
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
force=false
while (($#)); do
  case "$1" in
    --root) (($# >= 2)) || usage; root=$2; shift 2 ;;
    --force) [[ $action == backup ]] || usage; force=true; shift ;;
    --archive) (($# >= 2)) || usage; archive_name=$2; shift 2 ;;
    *) usage ;;
  esac
done
[[ $EUID -eq 0 ]] || { echo 'Root required' >&2; exit 1; }
[[ $root == /* && $root != / && $root != /etc && $root != /var && $root != /var/named && ! -L $root ]] || usage
mkdir -p -- "$root"
chmod 0700 -- "$root"
exec 9>"$root/.lock"
flock -x 9
shopt -s nullglob
archives=("$root"/named-config-????????T??????Z-????????.tar.gz)
# Lexicographic order matches UTC timestamps.
mapfile -t sorted < <(printf '%s\n' "${archives[@]}" | sed '/^$/d' | sort -r)
verify() {
  local file=$1 entry
  [[ -f $file && -f $file.sha256 && ! -L $file && ! -L $file.sha256 ]] || { echo 'Archive or checksum missing' >&2; return 1; }
  (cd "$root" && sha256sum --check --status -- "$(basename "$file").sha256") || { echo 'Checksum mismatch' >&2; return 1; }
  while IFS= read -r entry; do
    entry=${entry#./}
    [[ "$entry" != /* && "$entry" != *"/../"* && "$entry" != "../"* && "$entry" != *"/.." && "$entry" != *"//"* ]] || { echo "Unsafe archive path: $entry" >&2; return 1; }
    case "$entry" in
      etc/named.conf|etc/named|etc/named/*|var/named|var/named/*) ;;
      *) echo "Unsafe archive entry: $entry" >&2; return 1 ;;
    esac
  done < <(tar -tzf "$file")
  # Refuse links, devices and other special members.
  if tar -tvzf "$file" | grep -Eq '^[lhbcp]'; then echo 'Unsupported archive member' >&2; return 1; fi
}
# Every backup invocation creates a uniquely named archive. --force is accepted
# for compatibility but no longer changes the backup frequency.
backup() {
  local stamp target partial item cutoff timestamp epoch
  for item in /etc/named.conf /etc/named /var/named; do
    [[ ! -L $item ]] || { echo "Refusing source symlink: $item" >&2; return 1; }
  done
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
  # Validate before replacing files. Stop named externally before manual restore.
  if tar --extract --gzip --acls --xattrs --selinux --numeric-owner --same-owner -f "$target" -C / && named-checkconf -z /etc/named.conf; then
    restorecon -RF /etc/named.conf /etc/named /var/named 2>/dev/null || true
    rm -f -- "$safety"
    echo "RESTORED $target"
  else
    echo 'Restore failed; recovering pre-restore safety archive' >&2
    tar --extract --gzip --acls --xattrs --selinux --numeric-owner --same-owner -f "$safety" -C /
    restorecon -RF /etc/named.conf /etc/named /var/named 2>/dev/null || true
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

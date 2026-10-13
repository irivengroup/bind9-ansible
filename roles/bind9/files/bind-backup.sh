#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
# Single-language BIND backup, migration snapshot and restore utility.
if [[ ${1:-} == snapshot ]]; then
[[ $# -eq 3 && $1 == snapshot ]] || { echo 'usage: bind-backup.sh snapshot BACKUP_ROOT STATE_DIR' >&2; exit 2; }
backup_root=$2 state_dir=$3
[[ $backup_root = /* && $state_dir = /* ]] || exit 2
for dir in "$backup_root" "$state_dir"; do
  case "$dir" in /|/etc|/var|/etc/named|/etc/named/*|/var/named|/var/named/*) exit 2;; esac
  [[ ! -L "$dir" ]] || exit 1
done
for path in /etc/named.conf /etc/named /var/named; do
  [[ ! -L "$path" ]] || { echo "Refusing symlink: $path" >&2; exit 1; }
done
mkdir -p -- "$backup_root" "$state_dir"
chmod 0700 -- "$backup_root" "$state_dir"
exec 9>"$state_dir/.lock"
flock -x 9
# Preserve the first baseline only; later runs never overwrite it.
marker="$state_dir/baseline.sha256"
if [[ -f $marker ]]; then
  [[ -f "$state_dir/baseline.path" ]] || { echo 'Baseline metadata missing' >&2; exit 1; }
  baseline=$(cat -- "$state_dir/baseline.path")
  [[ -f "$baseline" && -f "$baseline.sha256" ]] || { echo 'Baseline archive missing' >&2; exit 1; }
  (cd "$backup_root" && sha256sum --check --status -- "$(basename "$baseline").sha256") || {
    echo 'Baseline checksum verification failed' >&2; exit 1;
  }
  echo 'SNAPSHOT_EXISTS'
  exit 0
fi
stamp=$(date -u +%Y%m%dT%H%M%SZ)
target="$backup_root/bind-baseline-$stamp-$$.tar.gz"
partial="$target.partial"
trap 'rm -f -- "$partial"' EXIT
entries=()
for path in etc/named.conf etc/named var/named; do
  [[ ! -e /$path ]] || entries+=("$path")
done
if ((${#entries[@]})); then
  tar --create --gzip --acls --xattrs --selinux --numeric-owner --file "$partial" --directory / -- "${entries[@]}"
else
  tar --create --gzip --file "$partial" --files-from /dev/null
fi
tar --list --gzip --file "$partial" >/dev/null
mv -- "$partial" "$target"
sha256sum -- "$target" >"$target.sha256"
(cd "$backup_root" && sha256sum --check --status -- "$(basename "$target").sha256")
chmod 0600 -- "$target" "$target.sha256"
printf '%s\n' "$target" >"$state_dir/baseline.path"
sha256sum -- "$target" >"$marker"
chmod 0600 -- "$state_dir/baseline.path" "$marker"
echo "SNAPSHOT_CREATED $target"

fi

usage() { echo 'usage: bind-backup.sh {backup|list|restore --archive FILE} [--root ABSOLUTE_DIR] | snapshot BACKUP_ROOT STATE_DIR' >&2; exit 2; }
[[ $# -ge 1 ]] || usage
action=$1; shift
root=/var/backups/Bind9-Ansible/bind
archive_name=
while (($#)); do
  case "$1" in
    --root) (($# >= 2)) || usage; root=$2; shift 2 ;;
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
archives=("$root"/named-config-????????T??????Z.tar.gz)
# Lexicographic order matches UTC timestamps.
mapfile -t sorted < <(printf '%s\n' "${archives[@]}" | sed '/^$/d' | sort -r)
verify() {
  local file=$1 entry
  [[ -f $file && -f $file.sha256 && ! -L $file && ! -L $file.sha256 ]] || { echo 'Archive or checksum missing' >&2; return 1; }
  (cd "$root" && sha256sum --check --status -- "$(basename "$file").sha256") || { echo 'Checksum mismatch' >&2; return 1; }
  while IFS= read -r entry; do
    entry=${entry#./}
    case "$entry" in
      etc/named.conf|etc/named|etc/named/*|var/named|var/named/*) ;;
      *) echo "Unsafe archive entry: $entry" >&2; return 1 ;;
    esac
  done < <(tar -tzf "$file")
  # Refuse links, devices and other special members.
  if tar -tvzf "$file" | grep -Eq '^[lhbcp]'; then echo 'Unsupported archive member' >&2; return 1; fi
}
backup() {
  local hour stamp target partial item
  hour=$(date -u +%Y%m%dT%H)
  for item in "${sorted[@]}"; do
    if [[ $(basename "$item") == named-config-${hour}* ]]; then
      verify "$item"
      echo "BACKUP_REUSED $item"
      return
    fi
  done
  for item in /etc/named.conf /etc/named /var/named; do
    [[ ! -L $item ]] || { echo "Refusing source symlink: $item" >&2; return 1; }
  done
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  target="$root/named-config-$stamp.tar.gz"
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
  mapfile -t sorted < <(find "$root" -maxdepth 1 -type f -name 'named-config-????????T??????Z.tar.gz' -printf '%p\n' | sort -r)
  for item in "${sorted[@]:5}"; do rm -f -- "$item" "$item.sha256"; done
  echo "BACKUP_CREATED $target"
}
restore() {
  local target safety item
  [[ $archive_name =~ ^named-config-[0-9]{8}T[0-9]{6}Z\.tar\.gz$ ]] || usage
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
  # Validate before replacing files. Stop named externally before manual restore.
  if tar --extract --gzip --acls --xattrs --selinux --numeric-owner --same-owner -f "$target" -C / && named-checkconf -z /etc/named.conf; then
    restorecon -RF /etc/named.conf /etc/named /var/named 2>/dev/null || true
    rm -f -- "$safety"
    echo "RESTORED $target"
  else
    echo 'Restore failed; recovering pre-restore snapshot' >&2
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

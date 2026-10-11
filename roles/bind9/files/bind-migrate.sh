#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $# -eq 3 && $1 == snapshot ]] || { echo 'usage: bind-migrate snapshot BACKUP_ROOT STATE_DIR' >&2; exit 2; }
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

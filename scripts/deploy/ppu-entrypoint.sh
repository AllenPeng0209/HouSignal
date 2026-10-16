#!/bin/sh
# Only transport for the existing private runtimes. Never print connection data.
set -eu
umask 077

fail() {
    printf '%s\n' 'PPU SSH setup is invalid; verify private files, ownership and permissions.' >&2
    exit 1
}

# GNU stat in the image; the fallback permits local verification on macOS.
metadata() {
    stat -c '%u %a %h' "$1" 2>/dev/null || stat -f '%u %Lp %l' "$1" 2>/dev/null
}

private_dir=${PPU_SSH_DIR:-/run/ppu-ssh}
target=${PPU_SSH_TARGET-ppu-runtime}
case "$private_dir" in /*) ;; *) fail ;; esac
case "$target" in ''|-*|*[!A-Za-z0-9_.-]*) fail ;; esac
[ -d "$private_dir" ] && [ ! -L "$private_dir" ] || fail
physical_dir=$(CDPATH='' cd -P "$private_dir" 2>/dev/null && pwd -P) || fail
[ "$physical_dir" = "$private_dir" ] || fail
owner=$(id -u)
set -- $(metadata "$private_dir")
[ "$#" -eq 3 ] && [ "$1" = "$owner" ] && [ "$2" = 700 ] || fail

for name in config id_ed25519 known_hosts; do
    file=$private_dir/$name
    [ -f "$file" ] && [ ! -L "$file" ] && [ -s "$file" ] || fail
    set -- $(metadata "$file")
    [ "$#" -eq 3 ] && [ "$1" = "$owner" ] && [ "$3" = 1 ] || fail
    case "$2" in 400|600) ;; *) fail ;; esac
done

exec ssh -F "$private_dir/config" -i "$private_dir/id_ed25519" -N -T \
    -o BatchMode=yes -o IdentitiesOnly=yes -o ForwardAgent=no \
    -o ExitOnForwardFailure=yes -o StrictHostKeyChecking=yes \
    -o "UserKnownHostsFile=$private_dir/known_hosts" \
    -o GlobalKnownHostsFile=/dev/null -o UpdateHostKeys=no \
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
    -o ConnectTimeout=15 -o ConnectionAttempts=1 \
    -L 127.0.0.1:18891:127.0.0.1:8891 \
    -L 127.0.0.1:18889:127.0.0.1:8879 \
    "$target" 2>/dev/null

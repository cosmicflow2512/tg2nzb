#!/bin/sh
# tg2sab entrypoint - sets up runtime user/group and drops privileges via gosu.
# Honors PUID/PGID env vars (Unraid standard: 99:100 = nobody:users).
set -e

PUID="${PUID:-99}"
PGID="${PGID:-100}"

# Run as root if explicitly requested.
if [ "${PUID}" = "0" ] && [ "${PGID}" = "0" ]; then
    mkdir -p /config /data
    exec "$@"
fi

# Ensure group with the requested GID exists.
if ! getent group "${PGID}" >/dev/null 2>&1; then
    groupadd -g "${PGID}" tg2sab
fi

# Ensure user with the requested UID exists.
if ! getent passwd "${PUID}" >/dev/null 2>&1; then
    useradd -u "${PUID}" -g "${PGID}" -d /home/tg2sab -M -s /bin/sh tg2sab
fi

# Make sure bind-mounted volumes belong to the runtime user.
mkdir -p /config /data
chown -R "${PUID}:${PGID}" /config /data 2>/dev/null || true

echo "[entrypoint] Running as UID=${PUID} GID=${PGID}"
exec gosu "${PUID}:${PGID}" "$@"

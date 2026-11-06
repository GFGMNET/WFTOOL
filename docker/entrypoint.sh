#!/bin/sh
# Folders mounted from the host may belong to root; hand them to the app user, then drop root.
set -e
if [ "$(id -u)" = "0" ]; then
  mkdir -p /data/import
  chown -R 10001:10001 /data /keys 2>/dev/null || true
  exec setpriv --reuid=10001 --regid=10001 --init-groups -- "$@"
fi
exec "$@"

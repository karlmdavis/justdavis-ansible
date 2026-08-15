#!/bin/bash

##
# Restarts Dovecot after Certbot renews the server's certificate.
#
# Dovecot reads its `ssl_cert`/`ssl_key` files exactly once, when its long-lived master process
# starts, and never re-reads them. Without this hook, Dovecot keeps serving whichever certificate was
# current when it last started, until something unrelated happens to restart it. In August 2026 that
# left it serving a certificate that had expired two days earlier, even though the renewed
# certificate had been on disk since July.
#
# Installed into `/etc/letsencrypt/renewal-hooks/deploy/`, which Certbot runs after every successful
# renewal, in addition to the lineage's own `renew_hook`.
##

set -e
set -o pipefail

# Certbot sets this to the `/etc/letsencrypt/live/<lineage>` directory that was just renewed. Other
# lineages are not ours to act on.
if [ "${RENEWED_LINEAGE}" != "/etc/letsencrypt/live/{{ domain }}" ]; then
  exit 0
fi

# This role's hosts are expected to run Dovecot, but exiting quietly beats failing the whole renewal
# if that ever stops being true.
if ! systemctl cat dovecot.service > /dev/null 2>&1; then
  echo 'Certbot deploy hook (dovecot): dovecot.service is not installed; nothing to do.'
  exit 0
fi

# `try-restart` is a no-op when Dovecot is not currently running, which keeps this safe to re-run.
echo 'Certbot deploy hook (dovecot): restarting dovecot.'
systemctl try-restart dovecot.service
echo 'Certbot deploy hook (dovecot): done.'

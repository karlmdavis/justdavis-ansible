#!/bin/bash

##
# Reloads Postfix after Certbot renews the server's certificate.
#
# Postfix already picks up renewed certificates on its own, because `smtpd` is spawned per-connection
# and re-reads `smtpd_tls_cert_file` each time. This hook makes that guaranteed rather than
# incidental, so a future change to how Postfix is run cannot silently reintroduce the stale
# certificate problem that hit Dovecot.
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

# This role's hosts are expected to run Postfix, but exiting quietly beats failing the whole renewal
# if that ever stops being true.
if ! systemctl cat postfix.service > /dev/null 2>&1; then
  echo 'Certbot deploy hook (postfix): postfix.service is not installed; nothing to do.'
  exit 0
fi

# `try-reload-or-restart` is a no-op when Postfix is not currently running, which keeps this safe to
# re-run.
echo 'Certbot deploy hook (postfix): reloading postfix.'
systemctl try-reload-or-restart postfix.service
echo 'Certbot deploy hook (postfix): done.'

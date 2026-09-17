#!/bin/bash

##
# Reloads Apache after Certbot renews the server's certificate.
#
# Apache already picks up renewed certificates in practice, but only as a side effect of the `reload`
# in its logrotate `postrotate` block. This hook makes that explicit, so the certificate stays correct
# even if log rotation is changed or disabled.
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

# This role's hosts are expected to run Apache, but exiting quietly beats failing the whole renewal if
# that ever stops being true.
if ! systemctl cat apache2.service > /dev/null 2>&1; then
  echo 'Certbot deploy hook (apache): apache2.service is not installed; nothing to do.'
  exit 0
fi

# `try-reload-or-restart` is a no-op when Apache is not currently running, which keeps this safe to
# re-run.
echo 'Certbot deploy hook (apache): reloading apache2.'
systemctl try-reload-or-restart apache2.service
echo 'Certbot deploy hook (apache): done.'

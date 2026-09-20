#!/bin/bash
# Restart Dovecot after Certbot renews the certificate. Dovecot reads ssl_cert/ssl_key once, when its
# master process starts, so a renewal alone leaves it serving the old certificate: in August 2026 it
# served one that had expired two days earlier. Certbot runs every executable in renewal-hooks/deploy/
# after any renewal, so act only on our lineage.
[ "${RENEWED_LINEAGE}" = "/etc/letsencrypt/live/{{ domain }}" ] || exit 0
systemctl try-restart dovecot.service

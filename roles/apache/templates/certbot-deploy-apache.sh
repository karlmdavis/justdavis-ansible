#!/bin/bash
# Reload Apache after Certbot renews the certificate. Apache already picks it up as a side effect of the
# reload in its logrotate postrotate; this makes it explicit. Certbot runs every executable in
# renewal-hooks/deploy/ after any renewal, so act only on our lineage.
[ "${RENEWED_LINEAGE}" = "/etc/letsencrypt/live/{{ domain }}" ] || exit 0
systemctl try-reload-or-restart apache2.service

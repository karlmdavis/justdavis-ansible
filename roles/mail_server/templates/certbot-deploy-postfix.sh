#!/bin/bash
# Reload Postfix after Certbot renews the certificate. Postfix already picks it up because smtpd is
# spawned per connection and re-reads the certificate file; this makes it explicit. Certbot runs every
# executable in renewal-hooks/deploy/ after any renewal, so act only on our lineage.
[ "${RENEWED_LINEAGE}" = "/etc/letsencrypt/live/{{ domain }}" ] || exit 0
systemctl try-reload-or-restart postfix.service
